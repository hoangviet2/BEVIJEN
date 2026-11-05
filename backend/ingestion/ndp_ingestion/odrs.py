"""ODRS opportunity ingestion; no HSDS transformation or org/service splitting."""
from dataclasses import dataclass, field
from urllib.parse import parse_qsl, urlencode, urlsplit

from .api import ApiConfig, ApiSource
from .core import RawPayload, parse_page, utc_now


@dataclass
class OdrsConfig(ApiConfig):
    api_key: str = field(default='', repr=False)

    def __post_init__(self):
        super().__post_init__()
        if not self.api_key:
            raise ValueError('ODRS_API_KEY is required')
        if {'api_key', 'page', 'per_page'}.intersection(self.query):
            raise ValueError('Query overrides managed ODRS parameters')


class OdrsApiSource(ApiSource):
    record_field = 'opportunities'

    def fetch(self):
        c = self.config
        endpoint = urlsplit(c.endpoint)
        query = {**c.query, 'per_page': c.page_size, 'page': 1}
        total = None
        received = 0
        expected_page = 1
        seen_ids = set()
        for _ in range(c.max_pages):
            # Never trust credentials, scheme, host or port carried by paging URLs.
            url = c.endpoint + '?' + urlencode({**query, 'api_key': c.api_key})
            body = self._request(url)
            yield RawPayload(body, utc_now())
            doc, records = parse_page(body, self.record_field)
            paging = doc.get('paging')
            if not isinstance(paging, dict):
                raise ValueError('Missing ODRS paging')
            count, current, pages = (paging.get(k) for k in ('total_count', 'current_page', 'total_pages'))
            if any(type(v) is not int or v < 0 for v in (count, current, pages)) or current != expected_page:
                raise ValueError('Invalid ODRS pagination')
            if total is None:
                total = count
            elif total != count:
                raise ValueError('ODRS dataset changed; replay required')
            ids = [str(r.get('id')) for r in records if isinstance(r, dict) and r.get('id') is not None]
            if seen_ids.intersection(ids):
                raise ValueError('ODRS repeated records across pages')
            seen_ids.update(ids)
            received += len(records)
            if received > total:
                raise ValueError('ODRS count exceeded')
            next_page = paging.get('next_page')
            if not next_page:
                if received != total or current != max(pages, 1):
                    raise ValueError('ODRS export truncated')
                return
            if not records or current >= pages or received >= total or not isinstance(next_page, str):
                raise ValueError('Invalid next page')
            target = urlsplit(next_page)
            # Staging paging links omit :3002. Keep the configured endpoint/port.
            if target.scheme not in ('', endpoint.scheme) or target.hostname not in (None, endpoint.hostname):
                raise ValueError('ODRS next page changed origin')
            if target.username or target.password or target.fragment or target.path != endpoint.path:
                raise ValueError('Invalid next page path')
            if target.port not in (None, endpoint.port):
                raise ValueError('ODRS next page changed port')
            pairs = parse_qsl(target.query, keep_blank_values=True)
            if len({k for k, _ in pairs}) != len(pairs):
                raise ValueError('Ambiguous pagination query')
            query = dict(pairs)
            query.pop('api_key', None)
            try:
                expected_page += 1
                if int(query.get('page', '0')) != expected_page:
                    raise ValueError('Nonsequential ODRS page')
            except (ValueError, TypeError):
                raise ValueError('Invalid page number') from None
        raise ValueError('Maximum page limit exceeded; run incomplete')
