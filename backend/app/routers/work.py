"""Specialist tasks (design §9.4) and discovery (§9.5)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from ..auth import User, ctx_dep, current_user, require
from ..context import Context
from ..discovery import service as discovery
from ..models import AssignTask, DiscoveryRunRequest, ResolveRecord, ResolveTask, SnoozeTask
from ..tasks import service as tasks
from ..tasks.crawler import crawl

router = APIRouter()


def _task(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except tasks.TaskError as exc:
        status = 404 if "not found" in str(exc) else 409
        raise HTTPException(status, str(exc)) from None


@router.post("/tasks/crawl", tags=["Tasks"])
def run_crawl(ctx: Context = Depends(ctx_dep), user: User = Depends(require("admin"))):
    tasks.wake_snoozed(ctx)
    return crawl(ctx)


@router.get("/tasks/zips", tags=["Tasks"])
def zips(ctx: Context = Depends(ctx_dep), user: User = Depends(current_user)):
    return tasks.zip_board(ctx)


@router.get("/tasks", tags=["Tasks"])
def list_tasks(zip: str | None = None, status: str = "open", priority: int | None = None,
               category: str | None = None, assignee: str | None = None,
               ctx: Context = Depends(ctx_dep), user: User = Depends(current_user)):
    who = user.email if assignee == "me" else assignee
    return tasks.list_tasks(ctx, zip_code=zip, status=status, priority=priority, category=category, assignee=who)


@router.get("/tasks/{task_id}", tags=["Tasks"])
def get_task(task_id: str, ctx: Context = Depends(ctx_dep), user: User = Depends(current_user)):
    return _task(tasks.task_detail, ctx, task_id)


@router.post("/tasks/{task_id}/claim", tags=["Tasks"])
def claim(task_id: str, ctx: Context = Depends(ctx_dep), user: User = Depends(current_user)):
    return _task(tasks.claim, ctx, task_id, user.email)


@router.post("/tasks/{task_id}/release", tags=["Tasks"])
def release(task_id: str, ctx: Context = Depends(ctx_dep), user: User = Depends(current_user)):
    return _task(tasks.release, ctx, task_id, user.email)


@router.post("/tasks/{task_id}/resolve", tags=["Tasks"])
def resolve(task_id: str, body: ResolveTask, ctx: Context = Depends(ctx_dep), user: User = Depends(current_user)):
    return _task(tasks.resolve, ctx, task_id, user=user.email, method=body.method, notes=body.notes,
                 findings=[f.model_dump() for f in body.findings])


@router.post("/records/{service_id}/resolve", tags=["Records"])
def resolve_record(service_id: str, body: ResolveRecord, ctx: Context = Depends(ctx_dep),
                   user: User = Depends(current_user)):
    """Specialist verification of one record: one outcome per open finding (confirmed | apply | edit |
    dismiss | closed). When nothing is left open the record becomes verified, with assured_date = today."""
    return _task(tasks.resolve_record, ctx, service_id, user=user.email, method=body.method, notes=body.notes,
                 findings=[f.model_dump() for f in body.findings])


@router.post("/tasks/{task_id}/snooze", tags=["Tasks"])
def snooze(task_id: str, body: SnoozeTask, ctx: Context = Depends(ctx_dep), user: User = Depends(current_user)):
    return _task(tasks.snooze, ctx, task_id, body.days, body.reason, user.email)


@router.post("/tasks/{task_id}/assign", tags=["Tasks"])
def assign(task_id: str, body: AssignTask, ctx: Context = Depends(ctx_dep), user: User = Depends(require("lead"))):
    return _task(tasks.assign, ctx, task_id, body.assignee)


# --- discovery -------------------------------------------------------------------------
def _disc(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except discovery.DiscoveryError as exc:
        raise HTTPException(exc.status, str(exc)) from None


@router.post("/discovery/runs", tags=["Discovery"])
def start_discovery(body: DiscoveryRunRequest, ctx: Context = Depends(ctx_dep), user: User = Depends(require("lead"))):
    return _disc(discovery.start_run, ctx, zip_code=body.zip, county=body.county, categories=body.categories or None,
                 requested_by=user.email)


@router.get("/discovery/runs/{run_id}", tags=["Discovery"])
def discovery_run(run_id: str, ctx: Context = Depends(ctx_dep), user: User = Depends(current_user)):
    run = ctx.runs.get(run_id)
    if not run or run.get("kind") != "discovery":
        raise HTTPException(404, "run not found")
    return run


@router.get("/discovery/candidates", tags=["Discovery"])
def candidates(state: str | None = None, ctx: Context = Depends(ctx_dep), user: User = Depends(current_user)):
    return discovery.list_candidates(ctx, state)


@router.post("/discovery/candidates/{service_id}/publish", tags=["Discovery"])
def publish(service_id: str, ctx: Context = Depends(ctx_dep), user: User = Depends(require("lead"))):
    return _disc(discovery.set_publish_state, ctx, service_id, "published", user.email)


@router.post("/discovery/candidates/{service_id}/reject", tags=["Discovery"])
def reject(service_id: str, ctx: Context = Depends(ctx_dep), user: User = Depends(require("lead"))):
    return _disc(discovery.set_publish_state, ctx, service_id, "rejected", user.email)
