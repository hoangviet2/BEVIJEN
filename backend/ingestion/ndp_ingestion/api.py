"""Configurable GET/offset profile inferred from fixtures, NOT verified API spec."""
from __future__ import annotations

import logging
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Callable

from .core import RawPayload, parse_page, utc_now

log = logging.getLogger(__name__)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # Do not forward authentication to redirect destinations.


class HttpFailure(Exception):
    def __init__(self, status: int, retry_after: str | None = None):
        self.status = status
        self.retry_after = retry_after
        super().__init__(f"HTTP status {status}")


def http_get(url: str, headers: dict, timeout: float) -> bytes:
    request = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.build_opener(NoRedirect()).open(request, timeout=timeout) as response:
            return response.read()
    except urllib.error.HTTPError as exc:
        failure = HttpFailure(exc.code, exc.headers.get('Retry-After'))
        exc.close()
        raise failure from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise ConnectionError("HTTP transport failed") from None


@dataclass
class ApiConfig:
    endpoint: str
    headers: dict = field(default_factory=dict, repr=False)
    query: dict = field(default_factory=dict)
    page_size: int = 100
    skip_param: str = "skip"
    take_param: str = "take"
    since_param: str | None = None
    since: str | None = None
    timeout: float = 30
    retries: int = 3
    max_pages: int = 10000
    backoff_seconds: float = 1
    max_retry_delay: float = 60

    def __post_init__(self):
        parsed = urllib.parse.urlsplit(self.endpoint)
        if parsed.scheme != 'https' or not parsed.netloc or parsed.username or parsed.password or parsed.fragment or parsed.query:
            raise ValueError("Endpoint must be HTTPS without credentials, query, or fragment; use query configuration")
        if self.page_size <= 0 or self.max_pages <= 0 or self.timeout <= 0 or self.retries < 0:
            raise ValueError("Invalid API limits")
        if self.backoff_seconds < 0 or self.max_retry_delay < 0:
            raise ValueError("Invalid retry delay")
        if not self.skip_param or not self.take_param or self.skip_param == self.take_param:
            raise ValueError("Distinct pagination parameter names are required")
        if self.since is not None and not self.since_param:
            raise ValueError("since requires a documented since parameter name")
        reserved = {self.skip_param, self.take_param}
        if self.since_param:
            if self.since_param in reserved:
                raise ValueError("Incremental parameter conflicts with pagination")
            reserved.add(self.since_param)
        if reserved.intersection(self.query):
            raise ValueError("Query overrides managed pagination/incremental parameters")


class ApiSource:
    def __init__(self, config: ApiConfig, transport: Callable = http_get, sleep: Callable = time.sleep):
        self.config, self.transport, self.sleep = config, transport, sleep

    def _request(self, url: str) -> bytes:
        c = self.config
        for attempt in range(c.retries + 1):
            try:
                return self.transport(url, {"Accept": "application/json", **c.headers}, c.timeout)
            except (HttpFailure, ConnectionError, TimeoutError, OSError) as exc:
                retryable = not isinstance(exc, HttpFailure) or exc.status in {408, 429, 500, 502, 503, 504}
                if not retryable or attempt == c.retries:
                    raise
                delay = min(c.max_retry_delay, c.backoff_seconds * 2 ** attempt)
                if isinstance(exc, HttpFailure) and exc.retry_after:
                    try:
                        delay = max(delay, float(exc.retry_after))
                    except ValueError:
                        from email.utils import parsedate_to_datetime
                        from datetime import datetime, timezone
                        try:
                            delay = max(delay, (parsedate_to_datetime(exc.retry_after) - datetime.now(timezone.utc)).total_seconds())
                        except (ValueError, TypeError, OverflowError):
                            pass
                if delay > c.max_retry_delay:
                    raise RuntimeError("Retry delay exceeds configured limit; retry run later") from None
                log.warning("http_retry attempt=%s", attempt + 1)
                self.sleep(delay)
        raise AssertionError("unreachable")

    def fetch(self):
        c = self.config
        skip = 0
        initial_total = None
        page_fingerprints = set()
        for page in range(c.max_pages):
            query = {**c.query, c.skip_param: skip, c.take_param: c.page_size}
            if c.since is not None:
                query[c.since_param] = c.since
            url = c.endpoint + '?' + urllib.parse.urlencode(query)
            body = self._request(url)
            yield RawPayload(body, utc_now())  # Caller archives before we parse/validate.
            doc, records = parse_page(body)
            meta = doc.get('input')
            if not isinstance(meta, dict):
                raise ValueError("Expected sample-compatible input pagination metadata")
            total, returned, echoed_skip = (meta.get(k) for k in ('countAvailable', 'countReturned', 'skip'))
            if any(type(v) is not int or v < 0 for v in (total, returned, echoed_skip)):
                raise ValueError("Invalid pagination counts")
            if echoed_skip != skip or returned != len(records) or skip + returned > total:
                raise ValueError("Inconsistent pagination response")
            if initial_total is None:
                initial_total = total
            elif total != initial_total:
                raise ValueError("Dataset changed during pagination; replay required")
            if records:
                import hashlib
                import json
                fingerprint = hashlib.sha256(json.dumps([r.get('id') if isinstance(r, dict) else None for r in records]).encode()).hexdigest()
                if fingerprint in page_fingerprints:
                    raise ValueError("Repeated page; pagination made no logical progress")
                page_fingerprints.add(fingerprint)
            skip += returned
            log.info("page_received page=%s count=%s", page, returned)
            if skip == total:
                return
            if returned == 0:
                raise ValueError("Empty page before advertised total")
        raise ValueError("Maximum page limit exceeded; run incomplete")
