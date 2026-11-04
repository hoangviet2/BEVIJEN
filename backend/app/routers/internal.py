"""Internal API for specialists and admins (design §9.2–9.3)."""

from __future__ import annotations

import json
from datetime import datetime, timezone

from fastapi import APIRouter, Body, Depends, File, Header, HTTPException, Query, UploadFile
from fastapi.responses import PlainTextResponse

from .. import adapters
from ..auth import User, ctx_dep, current_user, require
from ..context import Context
from ..hsds.validate import validate_object
from ..intake import IntakeService
from ..models import (BatchIntakeRequest, BatchIntakeResult, FindingDismiss, FindingEdit, IngestNdpRequest,
                      IngestOdrsRequest, IntakeRequest, IntakeResult, Outcome, VerifyBatchRequest)
from ..pipeline import after_commit, run_ingest
from ..records import EditError, apply_patch
from ..store.commit import CommitRejected, commit
from ..tasks.service import TaskError, _commit_field
from ..verify.findings import FindingError
from ..verify.worker import enqueue_recheck

router = APIRouter()


# --- ingest -------------------------------------------------------------------------
@router.post("/ingest/ndp", tags=["Ingest"])
def ingest_ndp(body: IngestNdpRequest, ctx: Context = Depends(ctx_dep), user: User = Depends(require("admin"))):
    if body.mode == "sample":
        if not body.s3_key:
            raise HTTPException(400, "sample mode needs s3_key (or use /ingest/ndp/upload)")
        adapter = adapters.ndp_s3(ctx.stores.blobs, body.s3_key)
    else:
        adapter = adapters.ndp_mock(ctx.settings, body.url, body.since)
    return run_ingest(ctx, adapter, {"mode": body.mode, "by": user.email})


@router.post("/ingest/ndp/upload", tags=["Ingest"])
async def ingest_ndp_upload(file: UploadFile = File(...), ctx: Context = Depends(ctx_dep),
                            user: User = Depends(require("admin"))):
    body = await file.read()
    try:
        source = adapters.sniff(body)
    except ValueError:
        raise HTTPException(400, "file is not JSON") from None
    adapter = adapters.from_bytes([body], source)
    return run_ingest(ctx, adapter, {"mode": "upload", "file": file.filename, "by": user.email})


@router.post("/ingest/odrs", tags=["Ingest"])
def ingest_odrs(body: IngestOdrsRequest, ctx: Context = Depends(ctx_dep), user: User = Depends(require("admin"))):
    return run_ingest(ctx, adapters.odrs_api(ctx.settings, body.max_pages), {"by": user.email})


@router.get("/ingest/runs", tags=["Ingest"])
def list_runs(kind: str = "ingest", ctx: Context = Depends(ctx_dep), user: User = Depends(current_user)):
    return [{k: v for k, v in r.items() if k != "results"} for r in ctx.runs.list(kind)]


@router.get("/ingest/runs/{run_id}", tags=["Ingest"])
def get_run(run_id: str, ctx: Context = Depends(ctx_dep), user: User = Depends(current_user)):
    run = ctx.runs.get(run_id)
    if not run:
        raise HTTPException(404, "run not found")
    return run


@router.get("/quarantine", tags=["Ingest"])
def list_quarantine(source: str = "", run_id: str = "", ctx: Context = Depends(ctx_dep),
                    user: User = Depends(current_user)):
    prefix = "quarantine/" + (f"{source}/" if source else "") + (f"{run_id}/" if source and run_id else "")
    keys = ctx.stores.blobs.list(prefix)
    return [{"key": k, **{x: (ctx.stores.blobs.get_json(k) or {}).get(x) for x in ("service_id", "errors")}}
            for k in keys if not run_id or f"/{run_id}/" in k][:500]


@router.post("/quarantine/{source}/{run_id}/{service_id}/retry", tags=["Ingest"])
def retry_quarantine(source: str, run_id: str, service_id: str, ctx: Context = Depends(ctx_dep),
                     user: User = Depends(require("admin"))):
    """Re-map the run's archived raw pages with the current adapter and re-validate."""
    run = ctx.runs.get(run_id)
    if not run:
        raise HTTPException(404, "run not found")
    keys = [k for k in ctx.stores.blobs.list(f"raw/{source}/") if f"/{run_id}/" in k and not k.endswith(".meta.json")]
    if not keys:
        raise HTTPException(404, "no archived raw pages for this run")
    pages = [ctx.stores.blobs.get_bytes(k) for k in keys]
    return run_ingest(ctx, adapters.from_bytes(pages, source), {"retry_of": run_id, "by": user.email})


# --- handoff intake (already-HSDS records) ------------------------------------------------
@router.post("/records", response_model=IntakeResult, tags=["Records"])
def post_record(body: IntakeRequest, ctx: Context = Depends(ctx_dep), user: User = Depends(require("admin"))):
    return IntakeService(ctx).intake(body.source, body.native_id, body.record)


@router.post("/records/batch", response_model=BatchIntakeResult, tags=["Records"])
def post_batch(body: BatchIntakeRequest, ctx: Context = Depends(ctx_dep), user: User = Depends(require("admin"))):
    pairs, early = [], []
    for record in body.records:
        nid = record.get("id") or record.get("native_id")
        if not nid:
            early.append(IntakeResult(outcome=Outcome.QUARANTINED, errors=["record has no 'id' to derive a native id from"]))
        else:
            pairs.append((str(nid), record))
    _, results = IntakeService(ctx).intake_many(body.source, pairs)
    results = early + results
    q = sum(r.outcome is Outcome.QUARANTINED for r in results)
    return BatchIntakeResult(received=len(results), stored=len(results) - q, quarantined=q, results=results)


@router.get("/records", tags=["Records"])
def list_records(status: str | None = None, reason: str | None = None, source: str | None = None,
                 zip: str | None = None, publish_state: str | None = None, limit: int = Query(500, le=5000),
                 ctx: Context = Depends(ctx_dep), user: User = Depends(current_user)):
    out = []
    for i in ctx.repo.index_items():
        if (status and i.get("verification_status") != status) or (reason and i.get("unverified_reason") != reason) \
                or (source and i.get("source") != source) or (zip and i.get("primary_zip") != zip) \
                or (publish_state and i.get("publish_state") != publish_state):
            continue
        out.append(i)
    return sorted(out, key=lambda i: i.get("service_name") or "")[:limit]


@router.get("/records/{service_id}", tags=["Records"])
def get_record(service_id: str, ctx: Context = Depends(ctx_dep), user: User = Depends(current_user)):
    item = ctx.repo.index_item(service_id)
    if item is None:
        raise HTTPException(404, "record not found")
    rows = ctx.repo.service_rows(service_id) or {}
    versions = {k: v for k, v in ctx.repo.versions_for_org(item["org_id"]).items()} if item.get("org_id") else {}
    return {"index": item, "hsds": ctx.repo.render_service(service_id), "rows": rows, "versions": versions,
            "findings": ctx.findings.for_service(service_id)}


@router.patch("/records/{service_id}", tags=["Records"])
def patch_record(service_id: str, ops: list[dict] = Body(...), if_match: str = Header(default=""),
                 ctx: Context = Depends(ctx_dep), user: User = Depends(current_user)):
    """JSON-Patch-lite on flat HSDS objects: [{"op":"replace","path":"/service/<id>/name","value":"…"}].
    `If-Match: <version>` of the service item guards against concurrent edits."""
    item = ctx.repo.get_item("service", service_id)
    rows = ctx.repo.service_rows(service_id)
    if not item or not rows:
        raise HTTPException(404, "record not found")
    if if_match and str(item.get("version")) != if_match.strip('"'):
        raise HTTPException(412, f"version is {item.get('version')}; reload and retry")
    try:
        changes, removed = apply_patch(rows, ops)
        idx = ctx.repo.index_item(service_id) or {}
        result = commit(ctx.stores, ctx.settings, changes, source=idx.get("source", "unknown"),
                        actor=f"user:{user.email}", services=[service_id], retire=removed)
    except EditError as exc:
        raise HTTPException(400, str(exc)) from None
    except CommitRejected as exc:
        raise HTTPException(422, {"error": "edit would make the record invalid HSDS", "details": exc.errors}) from None
    return {"service_id": service_id, "items_written": result.items_written, "metadata_rows": result.metadata_rows,
            "hsds": result.documents.get(service_id)}


@router.get("/records/{service_id}/history", tags=["Records"])
def record_history(service_id: str, ctx: Context = Depends(ctx_dep), user: User = Depends(current_user)):
    return ctx.repo.service_history(service_id)


@router.get("/exports/hsds", tags=["Records"], response_class=PlainTextResponse)
def export_hsds(ctx: Context = Depends(ctx_dep), user: User = Depends(require("admin"))):
    """Validated NDJSON of every published service; also written to exports/{date}/."""
    lines, bad = [], []
    for item in ctx.repo.published_services():
        doc = ctx.stores.blobs.get_json(item["s3_key"])
        if not doc:
            continue
        check = validate_object("service", doc)
        (lines if check.valid else bad).append(json.dumps(doc, ensure_ascii=False) if check.valid else item["service_id"])
    body = "\n".join(lines) + ("\n" if lines else "")
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    ctx.stores.blobs.put_bytes(f"exports/{day}/hsds-services.ndjson", body.encode(), "application/x-ndjson")
    return PlainTextResponse(body, media_type="application/x-ndjson", headers={"X-Invalid-Skipped": str(len(bad))})


# --- verification and findings ---------------------------------------------------------
@router.post("/verify/batch", tags=["Verify"])
def verify_batch(body: VerifyBatchRequest, ctx: Context = Depends(ctx_dep), user: User = Depends(require("admin"))):
    run = ctx.runs.start("verify", body.model_dump())
    run["counts"] = {"queued": enqueue_recheck(ctx, body.older_than_days or 0, body.source, body.zip, run["run_id"])}
    return ctx.runs.save(run)


@router.post("/verify/{service_id}", tags=["Verify"])
def verify_now(service_id: str, ctx: Context = Depends(ctx_dep), user: User = Depends(current_user)):
    if not ctx.repo.index_item(service_id):
        raise HTTPException(404, "record not found")
    ctx.stores.verify_q.send({"service_id": service_id, "run_id": f"manual:{user.email}", "mode": "recheck"})
    return {"queued": service_id}


@router.get("/verify/runs/{run_id}", tags=["Verify"])
def verify_run(run_id: str, ctx: Context = Depends(ctx_dep), user: User = Depends(current_user)):
    run = ctx.runs.get(run_id)
    if not run:
        raise HTTPException(404, "run not found")
    verdicts: dict[str, int] = {}
    for i in ctx.repo.index_items():
        if i.get("last_check_run") == run_id:
            verdicts[i.get("last_check_category", "?")] = verdicts.get(i.get("last_check_category", "?"), 0) + 1
    return {**run, "verdicts": verdicts, "checked": sum(verdicts.values())}


@router.get("/findings", tags=["Findings"])
def list_findings(category: str | None = None, status: str = "open", zip: str | None = None,
                  source: str | None = None, ctx: Context = Depends(ctx_dep), user: User = Depends(current_user)):
    rows = ctx.findings.list(category, status)
    if zip or source:
        idx = {i["service_id"]: i for i in ctx.repo.index_items()}
        rows = [f for f in rows if (not zip or idx.get(f["service_id"], {}).get("primary_zip") == zip)
                and (not source or idx.get(f["service_id"], {}).get("source") == source)]
    return rows


@router.get("/findings/{finding_id}", tags=["Findings"])
def get_finding(finding_id: str, ctx: Context = Depends(ctx_dep), user: User = Depends(current_user)):
    f = ctx.findings.get(finding_id)
    if not f:
        raise HTTPException(404, "finding not found")
    for ev in f.get("evidence") or []:
        if ev.get("s3_key"):
            ev["download_url"] = ctx.stores.blobs.presign(ev["s3_key"])
    return f


def _apply(ctx: Context, finding_id: str, value, user: User, status: str) -> dict:
    f = ctx.findings.get(finding_id)
    if not f or f["status"] != "open":
        raise HTTPException(404, "open finding not found")
    if value is None:
        raise HTTPException(400, "finding has no proposed value")
    try:
        _commit_field(ctx, f["service_id"], f["field"], value, f"user:{user.email}")
    except TaskError as exc:
        raise HTTPException(422, str(exc)) from None
    out = ctx.findings.set_status(finding_id, status, by=f"user:{user.email}", value=value)
    after_commit(ctx, f["service_id"], run_id=f"finding:{finding_id}", enqueue=False)
    return out


@router.post("/findings/{finding_id}/apply", tags=["Findings"])
def apply_finding(finding_id: str, ctx: Context = Depends(ctx_dep), user: User = Depends(current_user)):
    f = ctx.findings.get(finding_id) or {}
    return _apply(ctx, finding_id, f.get("proposed_value"), user, "applied")


@router.post("/findings/{finding_id}/edit", tags=["Findings"])
def edit_finding(finding_id: str, body: FindingEdit, ctx: Context = Depends(ctx_dep), user: User = Depends(current_user)):
    return _apply(ctx, finding_id, body.value, user, "edited")


@router.post("/findings/{finding_id}/dismiss", tags=["Findings"])
def dismiss_finding(finding_id: str, body: FindingDismiss, ctx: Context = Depends(ctx_dep),
                    user: User = Depends(current_user)):
    try:
        return ctx.findings.set_status(finding_id, "dismissed", by=f"user:{user.email}", reason=body.reason)
    except FindingError as exc:
        raise HTTPException(404, str(exc)) from None
