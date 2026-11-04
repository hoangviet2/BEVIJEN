"""HSDS API for consumers (design §9.1, HSDS 3.2 API reference).

Only `publish_state = published` records are served; keys with a
`source_filter` only see that source (R3.4). Every response object is
validated against the pinned schema before it leaves (AGENT.md §8) and schedule
times get their UTC offset for the response date (§14.3).
"""

from __future__ import annotations

import math

from fastapi import APIRouter, Depends, HTTPException, Query

from ..auth import Consumer, consumer, ctx_dep
from ..context import Context
from ..hsds.render import render
from ..hsds.schedules import with_offsets
from ..hsds.validate import validate_object
from ..search import search as do_search

router = APIRouter(tags=["HSDS API"])


def _serve(ctx: Context, object_type: str, doc: dict | None) -> dict:
    if doc is None:
        raise HTTPException(404, f"{object_type} not found")
    out = with_offsets(doc, ctx.settings.local_timezone)
    check = validate_object(object_type, out)
    if not check.valid:  # never serve invalid HSDS
        raise HTTPException(500, {"error": "stored record failed HSDS validation", "details": check.errors[:5]})
    return out


def _page(items: list, page: int, per_page: int) -> dict:
    total = len(items)
    pages = max(1, math.ceil(total / per_page)) if per_page else 1
    chunk = items[(page - 1) * per_page: page * per_page]
    return {"total_items": total, "total_pages": pages, "page_number": page, "size": len(chunk),
            "first_page": page == 1, "last_page": page >= pages, "empty": not chunk, "contents": chunk}


def _published(ctx: Context, who: Consumer) -> list[dict]:
    return ctx.repo.published_services(who.source_filter)


def _minimal(doc: dict) -> dict:
    keep = ("id", "organization_id", "name", "description", "status", "url", "email")
    out = {k: doc[k] for k in keep if k in doc}
    if "organization" in doc:
        out["organization"] = {k: doc["organization"][k] for k in ("id", "name", "description") if k in doc["organization"]}
    return out


@router.get("/services")
def list_services(page: int = Query(1, ge=1), per_page: int = Query(50, ge=1, le=500),
                  modified_after: str | None = None, organization_id: str | None = None,
                  taxonomy_term_id: str | None = None, taxonomy_id: str | None = None,
                  postal_code: str | None = None, search: str | None = None, minimal: bool = False,
                  full: bool = True, ctx: Context = Depends(ctx_dep), who: Consumer = Depends(consumer)):
    items = _published(ctx, who)
    if organization_id:
        items = [i for i in items if i.get("org_id") == organization_id]
    if modified_after:
        items = [i for i in items if (i.get("last_seen_at") or "") > modified_after]
    if postal_code:
        items = [i for i in items if i.get("primary_zip") == postal_code[:5]]
    shape = (lambda d: _minimal(d)) if minimal else (lambda d: _serve(ctx, "service", d))

    if not (search or taxonomy_id or taxonomy_term_id):  # page the index, load only that page from S3
        out = _page(items, page, per_page)
        window = items[(page - 1) * per_page: page * per_page]
        out["contents"] = [shape(d) for d in (ctx.stores.blobs.get_json(i["s3_key"]) for i in window) if d]
        return out

    if search:
        allowed = {i["service_id"] for i in items}
        docs = [d for d in do_search(ctx, search, postal_code, "keyword", who.source_filter) if d["id"] in allowed]
    else:
        docs = [d for d in (ctx.stores.blobs.get_json(i["s3_key"]) for i in items) if d]
    if taxonomy_term_id or taxonomy_id:
        def has(d):
            terms = [a.get("taxonomy_term") or {} for a in d.get("attributes") or []]
            return any((taxonomy_term_id and t.get("id") == taxonomy_term_id) or
                       (taxonomy_id and t.get("taxonomy_id") == taxonomy_id) for t in terms)
        docs = [d for d in docs if has(d)]
    return _page([shape(d) for d in docs], page, per_page)


@router.get("/services/{service_id}")
def get_service(service_id: str, ctx: Context = Depends(ctx_dep), who: Consumer = Depends(consumer)):
    item = ctx.repo.index_item(service_id)
    if not item or item.get("publish_state", "published") != "published" or (
            who.source_filter and item.get("source") != who.source_filter):
        raise HTTPException(404, "service not found")
    return _serve(ctx, "service", ctx.stores.blobs.get_json(item["s3_key"]))


def _org_ids(ctx: Context, who: Consumer) -> set[str]:
    return {i.get("org_id") for i in _published(ctx, who)}


@router.get("/organizations")
def list_organizations(page: int = Query(1, ge=1), per_page: int = Query(50, ge=1, le=500),
                       ctx: Context = Depends(ctx_dep), who: Consumer = Depends(consumer)):
    ids = sorted(i for i in _org_ids(ctx, who) if i)
    window = ids[(page - 1) * per_page: page * per_page]
    out = _page(ids, page, per_page)
    out["contents"] = [_serve(ctx, "organization", render("organization", oid, ctx.repo.org_rows(oid))) for oid in window]
    return out


@router.get("/organizations/{org_id}")
def get_organization(org_id: str, ctx: Context = Depends(ctx_dep), who: Consumer = Depends(consumer)):
    if org_id not in _org_ids(ctx, who):
        raise HTTPException(404, "organization not found")
    return _serve(ctx, "organization", render("organization", org_id, ctx.repo.org_rows(org_id)))


@router.get("/service_at_locations")
def list_sals(page: int = Query(1, ge=1), per_page: int = Query(50, ge=1, le=500),
              ctx: Context = Depends(ctx_dep), who: Consumer = Depends(consumer)):
    allowed = {i["service_id"] for i in _published(ctx, who)}
    sals = [s for s in ctx.repo.list_type("service_at_location") if s.get("service_id") in allowed]
    return _page(sals, page, per_page)


@router.get("/service_at_locations/{sal_id}")
def get_sal(sal_id: str, ctx: Context = Depends(ctx_dep), who: Consumer = Depends(consumer)):
    sal = ctx.repo.get("service_at_location", sal_id)
    allowed = {i["service_id"] for i in _published(ctx, who)}
    if not sal or sal.get("service_id") not in allowed:
        raise HTTPException(404, "service_at_location not found")
    rows = ctx.repo.service_rows(sal["service_id"])
    return _serve(ctx, "service_at_location", render("service_at_location", sal_id, rows))


@router.get("/taxonomies")
def list_taxonomies(page: int = Query(1, ge=1), per_page: int = Query(50, ge=1, le=500),
                    ctx: Context = Depends(ctx_dep), who: Consumer = Depends(consumer)):
    return _page(ctx.repo.list_type("taxonomy"), page, per_page)


@router.get("/taxonomies/{tid}")
def get_taxonomy(tid: str, ctx: Context = Depends(ctx_dep), who: Consumer = Depends(consumer)):
    return _serve(ctx, "taxonomy", ctx.repo.get("taxonomy", tid))


@router.get("/taxonomy_terms")
def list_terms(page: int = Query(1, ge=1), per_page: int = Query(50, ge=1, le=500), taxonomy_id: str | None = None,
               ctx: Context = Depends(ctx_dep), who: Consumer = Depends(consumer)):
    terms = ctx.repo.list_type("taxonomy_term")
    if taxonomy_id:
        terms = [t for t in terms if t.get("taxonomy_id") == taxonomy_id]
    return _page(terms, page, per_page)


@router.get("/taxonomy_terms/{tid}")
def get_term(tid: str, ctx: Context = Depends(ctx_dep), who: Consumer = Depends(consumer)):
    return _serve(ctx, "taxonomy_term", ctx.repo.get("taxonomy_term", tid))


@router.get("/search")
def search(q: str, postal_code: str | None = None, mode: str = Query("keyword", pattern="^(keyword|semantic)$"),
           page: int = Query(1, ge=1), per_page: int = Query(20, ge=1, le=100),
           ctx: Context = Depends(ctx_dep), who: Consumer = Depends(consumer)):
    docs = do_search(ctx, q, postal_code, mode, who.source_filter)
    return _page([_serve(ctx, "service", d) for d in docs], page, per_page)
