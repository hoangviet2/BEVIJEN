"""The one write path for HSDS data (design §4.1, AGENT.md §8).

commit(rows, actor):
  1. diff the incoming rows against what is stored for the same organizations,
  2. assemble every affected service document and validate it (HSDS 3.2.3),
     -> any invalid document rejects the whole commit (`CommitRejected`),
  3. write the changed entity items (optimistic version check) and one HSDS
     `metadata` row per changed field (change history, R2.10),
  4. write the assembled documents to S3 `hsds/{source}/{service_id}.json`
     (bucket versioning keeps history),
  5. upsert the service index items.

DynamoDB TransactWriteItems caps at 100 items, and an organization graph is
often larger, so items go through batch writes; the index item is written last
and is what the API trusts, so a crash mid-commit leaves the previous document
served until the commit is retried (commits are idempotent).
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from hsds_transform.mapper import is_missing

from ..config import Settings
from ..hsds.render import ORPHAN, Rows, org_of, pk, render_service, rows_to_items, zip_of_location
from ..hsds.validate import validate_service
from ..identity import content_hash, fuzzy_match_key
from . import Stores
from .backend import ConditionFailed
from .hsds_repo import HsdsRepo

_META_NS = uuid.UUID("0e3b6f0a-5a43-4f1e-8c43-8f7b0b1a9d21")


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class CommitRejected(Exception):
    def __init__(self, errors: dict[str, list[str]]):
        self.errors = errors
        super().__init__("; ".join(f"{k}: {v[:3]}" for k, v in errors.items()))


@dataclass
class CommitResult:
    service_ids: list[str] = field(default_factory=list)
    items_written: int = 0
    metadata_rows: int = 0
    documents: dict[str, dict] = field(default_factory=dict)


def s3_key(source: str, service_id: str) -> str:
    return f"hsds/{source}/{service_id}.json"


def _merge(stored: Rows, incoming: Rows) -> Rows:
    merged: Rows = {t: dict(v) for t, v in stored.items()}
    for table, tbl in incoming.items():
        merged.setdefault(table, {})
        for rid, row in tbl.items():
            merged[table][rid] = row
    return merged


def _changes(stored: Rows, incoming: Rows) -> list[tuple[str, str, str, str, object, object]]:
    """(table, id, action, field, previous, replacement) for every changed field."""
    out = []
    for table, tbl in incoming.items():
        for rid, row in tbl.items():
            old = stored.get(table, {}).get(rid)
            if old is None:
                out.append((table, rid, "create", "", None, None))
                continue
            for col in sorted(set(old) | set(row)):
                a, b = old.get(col), row.get(col)
                if is_missing(a) and is_missing(b):
                    continue
                if a != b:
                    out.append((table, rid, "update", col, a, b))
    return out


def _stored_org(repo: HsdsRepo, row: dict) -> str | None:
    """Organization of a partial change-set row, via its stored parent."""
    if row.get("organization_id"):
        return row["organization_id"]
    for col, table in (("service_id", "service"), ("location_id", "location")):
        if row.get(col):
            parent = repo.get(table, row[col])
            if parent and parent.get("organization_id"):
                return parent["organization_id"]
    return None


def commit(stores: Stores, settings: Settings, rows: Rows, *, source: str, actor: str,
           index_updates: dict[str, dict] | None = None, services: list[str] | None = None,
           retire: list[tuple[str, str]] | None = None) -> CommitResult:
    """Write HSDS rows. `services` limits which service documents are (re)built;
    default = every service in `rows` plus services touched through shared rows.
    `retire` removes rows (table, id); their metadata history stays."""
    repo = HsdsRepo(stores)
    now = now_iso()
    index_updates = index_updates or {}

    org_ids = {org_of(t, r, rows) or _stored_org(repo, r) for t, tbl in rows.items() for r in tbl.values()}
    org_ids |= {(repo.get("service", s) or {}).get("organization_id") for s in services or []}
    org_ids = {o for o in org_ids if o and o not in ("SHARED", ORPHAN)}
    stored: Rows = {}
    versions: dict[str, int] = {}
    for org_id in org_ids:
        for t, tbl in repo.org_rows(org_id).items():
            stored.setdefault(t, {}).update(tbl)
        versions.update(repo.versions_for_org(org_id))
    # shared rows (taxonomy) may exist already
    for t in ("taxonomy", "taxonomy_term"):
        for rid in rows.get(t, {}):
            item = repo.get_item(t, rid)
            if item:
                stored.setdefault(t, {})[rid] = item["data"]
                versions[item["PK"]] = int(item.get("version", 0))

    merged = _merge(stored, rows)
    retire = [(t, r) for t, r in (retire or []) if r in merged.get(t, {})]
    retired_rows = {(t, r): merged[t].pop(r) for t, r in retire}
    service_ids = services or sorted(rows.get("service", {}))
    if not services:  # services whose children changed are affected too
        for t, tbl in [*rows.items(), *(((t, {r: row}) for (t, r), row in retired_rows.items()))]:
            for row in tbl.values():
                sid = row.get("service_id")
                if sid and sid in merged.get("service", {}) and sid not in service_ids:
                    service_ids.append(sid)

    # validate before anything is written
    docs: dict[str, dict] = {}
    errors: dict[str, list[str]] = {}
    for sid in service_ids:
        doc = render_service(sid, merged)
        result = validate_service(doc or {})
        if doc is None or not result.valid:
            errors[sid] = result.errors or ["service not found"]
        else:
            docs[sid] = doc
    if errors:
        raise CommitRejected(errors)

    changes = _changes(stored, rows) + [(t, r, "delete", "*", row, None) for (t, r), row in retired_rows.items()]
    changed_keys = {(t, rid) for t, rid, *_ in changes}
    items = [i for i in rows_to_items(merged, source=source, actor=actor, now=now, versions=versions)
             if (i["entity_type"], i["data"]["id"]) in changed_keys]
    # entity items: optimistic check on single-item edits, batch for bulk
    if len(items) == 1:
        it = items[0]
        try:
            stores.hsds.put(it, expected_version=versions.get(it["PK"], 0))
        except ConditionFailed:
            raise CommitRejected({it["PK"]: ["edited concurrently; reload and retry"]}) from None
    elif items:
        stores.hsds.put_many(items)

    meta_items = []
    for table, rid, action, col, prev, repl in changes:
        if table == "metadata":
            continue
        mid = str(uuid.uuid5(_META_NS, f"{table}|{rid}|{col}|{now}|{actor}"))
        meta = {"id": mid, "resource_id": rid, "resource_type": table, "last_action_date": now,
                "last_action_type": action, "field_name": col or "*",
                "previous_value": "" if prev is None else json.dumps(prev, default=str)[:2000],
                "replacement_value": "" if repl is None else json.dumps(repl, default=str)[:2000],
                "updated_by": actor}
        row = merged.get(table, {}).get(rid) or retired_rows.get((table, rid)) or {}
        org_id = org_of(table, row, {**merged, table: {**merged.get(table, {}), rid: row}})
        meta_items.append({"PK": pk(table, rid), "SK": f"META#{now}#{mid}", "entity_type": "metadata",
                           "org_pk": pk("organization", org_id) if org_id not in (None, "SHARED") else "SHARED",
                           "entity_sk": f"METADATA#{mid}", "data": meta})
    if meta_items:
        stores.hsds.put_many(meta_items)
    for table, rid in retired_rows:
        stores.hsds.delete(pk(table, rid), "CURRENT")

    result = CommitResult(service_ids=list(docs), items_written=len(items), metadata_rows=len(meta_items),
                          documents=docs)
    for sid, doc in docs.items():
        doc_source = (index_updates.get(sid) or {}).get("source") or source
        key = s3_key(doc_source, sid)
        stores.blobs.put_json(key, doc)
        existing = stores.index.get(sid) or {}
        locs = [s["location_id"] for s in merged.get("service_at_location", {}).values()
                if s.get("service_id") == sid and s.get("location_id")]
        zips = [z for z in (zip_of_location(l, merged) for l in locs) if z]
        item = {
            **existing,
            "service_id": sid,
            "source": existing.get("source", doc_source),
            "s3_key": key,
            "org_id": doc["organization"]["id"],
            "org_name": doc["organization"].get("name"),
            "service_name": doc.get("name"),
            "service_status": doc.get("status"),
            "location_ids": locs,
            "primary_zip": zips[0] if zips else existing.get("primary_zip"),
            "content_hash": content_hash(doc),
            "hash_pk": content_hash(doc),
            "fuzzy_match_key": fuzzy_match_key(doc),
            "match_pk": fuzzy_match_key(doc),
            "first_seen_at": existing.get("first_seen_at", now),
            "last_seen_at": now,
            "updated_by": actor,
            "verification_status": existing.get("verification_status", "unverified"),
            "publish_state": existing.get("publish_state", "published"),
            "open_flag_count": existing.get("open_flag_count", 0),
        }
        item.update(index_updates.get(sid) or {})
        item["status_pk"] = item["verification_status"]
        stores.index.put(item)
    return result
