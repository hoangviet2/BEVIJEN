"""Search (design Priority 5, §14.6).

With OPENSEARCH_ENDPOINT set: an OpenSearch Service index rebuilt from the
store (droppable), keyword (multi_match) and semantic (k-NN on Bedrock Titan
embeddings) modes, requests signed with SigV4 using the server's role.
Without it: a keyword scan over published service documents (small datasets,
local dev).
"""

from __future__ import annotations

import json
import logging
import urllib.request

from .ai import embed
from .context import Context

log = logging.getLogger(__name__)


def _text(doc: dict) -> str:
    org = doc.get("organization") or {}
    parts = [doc.get("name"), doc.get("description"), doc.get("eligibility_description"), org.get("name"),
             org.get("description")]
    parts += [a.get("name") for a in doc.get("service_areas") or []]
    parts += [(a.get("taxonomy_term") or {}).get("name") for a in doc.get("attributes") or []]
    return " ".join(str(p) for p in parts if p)


def _zips(doc: dict) -> list[str]:
    return sorted({str(a.get("postal_code"))[:5] for sal in doc.get("service_at_locations") or []
                   for a in (sal.get("location") or {}).get("addresses") or [] if a.get("postal_code")})


class OpenSearch:
    def __init__(self, ctx: Context):
        self.ctx = ctx
        self.base = ctx.settings.opensearch_endpoint.rstrip("/")
        self.index = ctx.settings.opensearch_index

    def _req(self, method: str, path: str, body: dict | None = None) -> dict:
        import boto3
        from botocore.auth import SigV4Auth
        from botocore.awsrequest import AWSRequest
        data = json.dumps(body).encode() if body is not None else None
        req = AWSRequest(method=method, url=f"{self.base}{path}", data=data,
                         headers={"Content-Type": "application/json"})
        creds = boto3.Session().get_credentials().get_frozen_credentials()
        SigV4Auth(creds, "es", self.ctx.settings.aws_region).add_auth(req)
        r = urllib.request.Request(req.url, data=data, method=method, headers=dict(req.headers))
        try:
            with urllib.request.urlopen(r, timeout=30) as resp:
                return json.loads(resp.read() or b"{}")
        except urllib.error.HTTPError as exc:
            if method == "DELETE" and exc.code == 404:
                return {}
            raise

    def rebuild(self) -> dict:
        self._req("DELETE", f"/{self.index}")
        self._req("PUT", f"/{self.index}", {
            "settings": {"index": {"knn": True}},
            "mappings": {"properties": {
                "service_id": {"type": "keyword"}, "source": {"type": "keyword"}, "zips": {"type": "keyword"},
                "name": {"type": "text"}, "text": {"type": "text"},
                "embedding": {"type": "knn_vector", "dimension": 512,
                              "method": {"name": "hnsw", "engine": "faiss", "space_type": "innerproduct"}}}}})
        n = 0
        for item in self.ctx.repo.published_services():
            doc = self.ctx.stores.blobs.get_json(item["s3_key"])
            if not doc:
                continue
            text = _text(doc)
            self._req("PUT", f"/{self.index}/_doc/{item['service_id']}", {
                "service_id": item["service_id"], "source": item.get("source"), "zips": _zips(doc),
                "name": doc.get("name"), "text": text, "embedding": embed(self.ctx.settings, text)})
            n += 1
        return {"indexed": n}

    def search(self, q: str, postal_code: str | None, mode: str, size: int = 20) -> list[str]:
        filt = [{"term": {"zips": postal_code}}] if postal_code else []
        if mode == "semantic":
            query = {"knn": {"embedding": {"vector": embed(self.ctx.settings, q), "k": size,
                                           **({"filter": {"bool": {"filter": filt}}} if filt else {})}}}
        else:
            query = {"bool": {"must": [{"multi_match": {"query": q, "fields": ["name^3", "text"]}}], "filter": filt}}
        resp = self._req("POST", f"/{self.index}/_search", {"size": size, "query": query, "_source": ["service_id"]})
        return [h["_source"]["service_id"] for h in resp.get("hits", {}).get("hits", [])]


def search(ctx: Context, q: str, postal_code: str | None = None, mode: str = "keyword",
           source_filter: str | None = None) -> list[dict]:
    allowed = {i["service_id"]: i for i in ctx.repo.published_services(source_filter)}
    if ctx.settings.opensearch_endpoint:
        ids = OpenSearch(ctx).search(q, postal_code, mode)
        return [d for d in (ctx.stores.blobs.get_json(allowed[i]["s3_key"]) for i in ids if i in allowed) if d]
    terms = [t for t in q.lower().split() if t]
    scored = []
    for item in allowed.values():
        doc = ctx.stores.blobs.get_json(item["s3_key"])
        if not doc or (postal_code and postal_code not in _zips(doc)):
            continue
        text = _text(doc).lower()
        score = sum(text.count(t) for t in terms)
        if score or not terms:
            scored.append((score, doc))
    return [d for _, d in sorted(scored, key=lambda x: -x[0])[:50]]
