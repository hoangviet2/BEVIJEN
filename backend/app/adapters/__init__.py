"""Source adapters (design §5 step 1–2, tech doc 5.4 contract).

An adapter only knows how to *fetch* pages for one source. Archiving, mapping
to HSDS, validation, dedup and commit are shared (app/pipeline.py).

    adapter = ndp_sample(path) | ndp_s3(key) | ndp_mock(settings) | odrs_api(settings) | odrs_file(path)
    adapter.source        stable source name, part of every derived id
    adapter.record_field  "data" (NDP) | "opportunities" (ODRS)
    adapter.fetch()       yields ndp_ingestion RawPayload pages

Fetching reuses the `ndp_ingestion` package (paging, retries, redirect refusal,
credential redaction); mapping reuses `hsds_transform`.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable

from ndp_ingestion.core import FileSource, RawPayload, utc_now

from ..config import Settings
from ..store.backend import Blobs


@dataclass
class Adapter:
    source: str
    record_field: str
    fetch: Callable[[], Iterable[RawPayload]]


class BlobRawStore:
    """ndp_ingestion RawStore writing to S3 with the design's layout (§4.2):
    raw/{source}/{yyyy}/{mm}/{dd}/{run_id}/page-{n}.json — never modified."""

    def __init__(self, blobs: Blobs, run_id: str):
        self.blobs, self.run_id = blobs, run_id

    def put(self, payload: RawPayload, source_name: str, run_id: str, page: int) -> str:
        d = datetime.now(timezone.utc)
        key = f"raw/{source_name}/{d:%Y/%m/%d}/{self.run_id}/page-{page}.json"
        self.blobs.put_bytes(key, payload.body, payload.content_type)
        self.blobs.put_json(key.replace(".json", ".meta.json"), {
            "source_name": source_name, "retrieved_at": payload.retrieved_at,
            "sha256": hashlib.sha256(payload.body).hexdigest()})
        return f"s3://{self.blobs.bucket}/{key}"


def ndp_sample(paths: list[str | Path]) -> Adapter:
    src = FileSource(paths, "data")
    return Adapter("ndp", "data", src.fetch)


def from_bytes(pages: list[bytes], source: str = "ndp") -> Adapter:
    """Pages already in hand (upload, quarantine retry)."""
    field = "opportunities" if source == "odrs" else "data"
    return Adapter(source, field, lambda: (RawPayload(b, utc_now()) for b in pages))


def ndp_s3(blobs: Blobs, key: str) -> Adapter:
    def fetch():
        body = blobs.get_bytes(key)
        if body is None:
            raise FileNotFoundError(f"s3://{blobs.bucket}/{key} not found")
        yield RawPayload(body, utc_now())
    return Adapter("ndp", "data", fetch)


def ndp_mock(settings: Settings, url: str | None = None, since: str | None = None) -> Adapter:
    from ndp_ingestion.api import ApiConfig, ApiSource

    from .. import aws
    headers = {}
    if settings.ndp_token_secret:
        headers["Authorization"] = f"Bearer {aws.secret(settings.ndp_token_secret)}"
    cfg = ApiConfig(endpoint=url or settings.ndp_mock_url, headers=headers, since=since,
                    since_param="since" if since else None)
    return Adapter("ndp", "data", ApiSource(cfg).fetch)


def odrs_api(settings: Settings, max_pages: int = 10000) -> Adapter:
    from ndp_ingestion.odrs import OdrsApiSource, OdrsConfig

    from .. import aws
    # The key travels in the query string (§12.1); ndp_ingestion never logs URLs.
    cfg = OdrsConfig(endpoint=settings.odrs_host.rstrip("/") + "/v1/opportunities",
                     api_key=aws.secret(settings.odrs_api_key_secret), max_pages=max_pages, page_size=100)
    return Adapter("odrs", "opportunities", OdrsApiSource(cfg).fetch)


def odrs_file(paths: list[str | Path]) -> Adapter:
    src = FileSource(paths, "opportunities")
    return Adapter("odrs", "opportunities", src.fetch)


def sniff(body: bytes) -> str:
    doc = json.loads(body)
    return "odrs" if isinstance(doc, dict) and "opportunities" in doc else "ndp"
