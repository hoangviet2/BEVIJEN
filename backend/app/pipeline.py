"""Pipeline A — data processing (design §5).

fetch ─► raw S3 ─► map to HSDS ─► normalise ─► validate ─┬─► quarantine (reason)
                                                         └─► dedup ─► commit ─► pre-flags ─► enqueue verify

Counts (R1.7) per run and per entity type: received = stored + quarantined,
where stored includes records skipped because they were unchanged.
"""

from __future__ import annotations

import logging
from collections import Counter
from typing import Any

from hsds_transform.coerce import coerce_dataset
from hsds_transform.convert import convert_records
from ndp_ingestion.core import ingest

from .adapters import Adapter, BlobRawStore
from .context import Context
from .dedup import DupKind, classify
from .hsds.normalize import Fix, normalize
from .hsds.render import ORPHAN, Rows, group_by_organization, render_service
from .hsds.validate import validate_service
from .identity import content_hash, fuzzy_match_key
from .store.commit import CommitRejected, commit, now_iso
from .verify.findings import most_severe
from .verify.rules import preflag

log = logging.getLogger(__name__)

# Tables whose rows belong to exactly one service (removed with a quarantined service).
_SERVICE_CHILDREN = ("service_at_location", "phone", "schedule", "language", "service_area", "required_document",
                     "cost_option", "url", "contact", "funding", "service_capacity")


class IndexLookup:
    """dedup.IndexLookup over the index table."""

    def __init__(self, ctx: Context):
        self.t = ctx.stores.index

    def get_index_item(self, service_id):
        return self.t.get(service_id)

    def find_by_content_hash(self, h):
        rows = self.t.query(h, index="content-hash-index", limit=1)
        return rows[0] if rows else None

    def find_by_match_key(self, k):
        rows = self.t.query(k, index="match-key-index", limit=2) if k else []
        return rows[0] if rows else None


def _drop_service(rows: Rows, sid: str) -> dict:
    removed: dict[str, Any] = {"service": rows.get("service", {}).pop(sid, None)}
    for table in _SERVICE_CHILDREN:
        for rid in [r for r, row in rows.get(table, {}).items() if row.get("service_id") == sid]:
            removed.setdefault(table, []).append(rows[table].pop(rid))
    for rid in [r for r, row in rows.get("attribute", {}).items() if row.get("link_id") == sid]:
        rows["attribute"].pop(rid)
    return removed


def _reconcile(ctx: Context, rows: Rows, source: str) -> list[tuple[str, str, Any, Any]]:
    """Priority 4 (§14.5): a field last written by a person/agent is not
    overwritten by the source; a `clash` finding is raised instead."""
    clashes = []
    for table, tbl in rows.items():
        for rid, row in tbl.items():
            item = ctx.repo.get_item(table, rid)
            if not item or str(item.get("updated_by", "")).startswith("source:"):
                continue
            stored = item["data"]
            sid = rid if table == "service" else row.get("service_id")
            for col in set(row) | set(stored):
                if col == "id" or row.get(col) == stored.get(col):
                    continue
                if sid:
                    clashes.append((sid, f"{table}.{col}", stored.get(col), row.get(col)))
                if col in stored:
                    row[col] = stored[col]
                else:
                    row.pop(col, None)
    return clashes


def process_rows(ctx: Context, rows: Rows, source: str, run: dict, fixes: list[Fix] | None = None,
                 *, actor: str | None = None, index_extra: dict | None = None, mode: str = "new",
                 enqueue: bool = True) -> dict:
    """Validate → dedup → commit → pre-flag → enqueue, for one mapped dataset."""
    actor = actor or f"source:{source}"
    counts: Counter = run.setdefault("counts_c", Counter())
    results: list[dict] = run.setdefault("results", [])
    lookup = IndexLookup(ctx)

    for org_id, group in group_by_organization(rows).items():
        valid: list[str] = []
        for sid in list(group.get("service", {})):
            counts["services_received"] += 1
            doc = render_service(sid, group) if org_id != ORPHAN else None
            check = validate_service(doc) if doc else None
            if doc is None or not check.valid:
                errors = check.errors if check else ["orphan_service: no organization"]
                removed = _drop_service(group, sid)
                ctx.stores.blobs.put_json(f"quarantine/{source}/{run['run_id']}/{sid}.json",
                                          {"service_id": sid, "errors": errors, "mapped": removed, "at": now_iso()})
                counts["services_quarantined"] += 1
                results.append({"service_id": sid, "outcome": "quarantined", "errors": errors[:10]})
                continue
            valid.append(sid)
        if org_id == ORPHAN or not valid:
            continue

        clashes = _reconcile(ctx, group, source)
        decisions = {}
        for sid in valid:
            doc = render_service(sid, group)
            decisions[sid] = classify(lookup, service_id=sid, content_hash=content_hash(doc),
                                      match_key=fuzzy_match_key(doc))
        index_updates = {}
        for sid, d in decisions.items():
            upd = {"source": source, **(index_extra or {})}
            if d.kind is DupKind.POSSIBLE_DUPLICATE:
                upd.update(verification_status="unverified", unverified_reason="possible_duplicate",
                           possible_duplicate_of=d.existing_item.get("service_id"))
            index_updates[sid] = upd
        try:
            commit(ctx.stores, ctx.settings, group, source=source, actor=actor, index_updates=index_updates,
                   services=valid)
        except CommitRejected as exc:
            for sid in valid:
                ctx.stores.blobs.put_json(f"quarantine/{source}/{run['run_id']}/{sid}.json",
                                          {"service_id": sid, "errors": exc.errors.get(sid, [str(exc)])})
                counts["services_quarantined"] += 1
                results.append({"service_id": sid, "outcome": "quarantined", "errors": exc.errors.get(sid, [])[:10]})
            continue

        for sid, field, stored, incoming in clashes:
            ctx.findings.create(service_id=sid, category="clash", field=field, current_value=stored,
                                proposed_value=incoming, model_id="rules", source=source, confidence="high",
                                note=f"{source} sent a different value than the last person/agent edit")
        for sid, d in decisions.items():
            counts["services_stored"] += 1
            if d.kind is DupKind.IDENTICAL_PAYLOAD:
                counts["services_skipped_unchanged"] += 1
                results.append({"service_id": sid, "outcome": "skipped_duplicate"})
                continue
            outcome = {DupKind.EXACT_EXISTING: "updated", DupKind.POSSIBLE_DUPLICATE: "possible_duplicate"}.get(
                d.kind, "inserted")
            counts[f"services_{outcome}"] += 1
            after_commit(ctx, sid, run_id=run["run_id"], mode=mode, enqueue=enqueue,
                         extra_reasons=["possible_duplicate"] if d.kind is DupKind.POSSIBLE_DUPLICATE else [])
            results.append({"service_id": sid, "outcome": outcome,
                            **({"duplicate_of": d.existing_item.get("service_id")}
                               if d.kind is DupKind.POSSIBLE_DUPLICATE else {})})
    return run


def after_commit(ctx: Context, service_id: str, *, run_id: str, mode: str = "new",
                 extra_reasons: list[str] | None = None, enqueue: bool = True) -> None:
    """Pre-flag a new/changed service, set its index status, queue a verify job."""
    doc = ctx.repo.render_service(service_id)
    if not doc:
        return
    preflag(doc, ctx.settings, ctx.findings)
    open_cats = [f["category"] for f in ctx.findings.for_service(service_id, status="open")]
    reason = most_severe(open_cats + (extra_reasons or []))
    item = ctx.stores.index.get(service_id) or {}
    item.update(verification_status="unverified", status_pk="unverified", unverified_reason=reason,
                open_flag_count=len(open_cats), missing_fields=[f["field"] for f in
                                                                ctx.findings.for_service(service_id, "open")
                                                                if f["category"] == "missing_information"])
    ctx.stores.index.put(item)
    if enqueue:
        ctx.stores.verify_q.send({"service_id": service_id, "run_id": run_id, "mode": mode})


def map_envelopes(envelopes: list, source: str):
    """Map each source record under its own stable label, so ids the transform
    derives from position ("record #0 of <label>") never change between runs."""
    ds = None
    for env in envelopes:
        ds = convert_records([env.payload], source, f"{source}:{env.original_record_id}", dataset=ds)
    if ds is None:
        ds = convert_records([], source)
    coerce_dataset(ds)
    return ds


def run_ingest(ctx: Context, adapter: Adapter, params: dict | None = None, *, enqueue: bool = True) -> dict:
    run = ctx.runs.start("ingest", {"source": adapter.source, **(params or {})})
    envelopes = []
    summary = ingest(adapter, BlobRawStore(ctx.stores.blobs, run["run_id"]), envelopes.append, adapter.source)
    run["acquisition"] = {k: summary.to_dict()[k] for k in
                          ("status", "payloads_archived", "records_received", "records_handed_off",
                           "duplicates_skipped", "record_errors")}
    run["errors"] = summary.errors[:50]
    ds = map_envelopes(envelopes, adapter.source)
    received_rows = {t: len(v) for t, v in ds.rows.items()}
    fixes = normalize(ds, adapter.source)
    run["fixes"] = dict(Counter(f.code for f in fixes))
    run["errors"] += [{"stage": "mapping", **e} for e in ds.errors][:50]
    process_rows(ctx, ds.rows, adapter.source, run, fixes, enqueue=enqueue)
    counts = run.pop("counts_c")
    counts["organizations_received"] = run["acquisition"]["records_received"]
    run["counts"] = dict(counts)
    run["entities"] = {t: {"mapped": n, "after_normalize": len(ds.rows.get(t, {}))}
                       for t, n in received_rows.items()}
    status = "completed" if summary.status == "completed" and not ds.errors else "completed_with_errors"
    if summary.status == "incomplete":
        status = "incomplete"
    run["results"] = run.get("results", [])[:500]
    return ctx.runs.finish(run, status)
