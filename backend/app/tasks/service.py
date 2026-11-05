"""Specialist task operations (design §6.2–6.3)."""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

from ..context import Context
from ..records import EditError, apply_field
from ..store.commit import CommitRejected, commit, now_iso
from .crawler import OPEN_STATES, task_keys

OUTCOMES = ("confirmed", "apply", "edit", "dismiss", "closed", "unreachable")
METHODS = ("phone", "visit", "email", "web")


class TaskError(ValueError):
    pass


def _ts(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def get_task(ctx: Context, task_id: str) -> dict:
    task = ctx.stores.tasks.get(f"TASK#{task_id}")
    if not task:
        raise TaskError("task not found")
    return task


def zip_board(ctx: Context) -> list[dict]:
    board: dict[str, dict] = defaultdict(lambda: {"open": 0, **{f"p{i}": 0 for i in range(1, 6)}})
    for t in ctx.stores.tasks.scan():
        if t["PK"].startswith("TASK#") and t["status"] in OPEN_STATES:
            b = board[t["zip"]]
            b["open"] += 1
            b[f"p{t['priority']}"] += 1
    return [{"zip": z, **v} for z, v in sorted(board.items())]


def list_tasks(ctx: Context, *, zip_code: str | None = None, status: str = "open", priority: int | None = None,
               category: str | None = None, assignee: str | None = None) -> list[dict]:
    if zip_code:
        bucket = zip_code if "#" in zip_code or zip_code in ("VIRTUAL", "UNKNOWN") else f"ZIP#{zip_code}"
        rows = ctx.stores.tasks.query(f"{bucket}#{status}", index="zip-index")
    elif assignee:
        rows = [t for t in ctx.stores.tasks.query(assignee, index="assignee-index") if t["status"] == status]
    else:
        rows = sorted((t for t in ctx.stores.tasks.scan() if t["PK"].startswith("TASK#") and t["status"] == status),
                      key=lambda t: t["sort"])
    if priority:
        rows = [t for t in rows if t["priority"] == priority]
    if category:
        rows = [t for t in rows if category in t.get("categories", [])]
    if assignee and zip_code:
        rows = [t for t in rows if t.get("assignee") == assignee]
    return rows


def task_detail(ctx: Context, task_id: str) -> dict:
    task = get_task(ctx, task_id)
    findings = [f for f in (ctx.findings.get(fid) for fid in task.get("finding_ids", [])) if f]
    for f in findings:
        for ev in f.get("evidence") or []:
            if ev.get("s3_key"):
                ev["download_url"] = ctx.stores.blobs.presign(ev["s3_key"])
    services = {}
    for sid in task.get("service_ids", []):
        services[sid] = ctx.repo.render_service(sid)
    return {**task, "findings": findings, "services": services,
            "memory": ctx.memory.notes(task["org_id"]) if task.get("org_id") else []}


def claim(ctx: Context, task_id: str, user: str) -> dict:
    task = get_task(ctx, task_id)
    now = datetime.now(timezone.utc)
    lease = task.get("lease_until")
    if task.get("assignee") not in (None, user) and lease and lease > _ts(now) and task["status"] == "claimed":
        raise TaskError(f"already claimed until {lease}")
    if task["status"] not in OPEN_STATES:
        raise TaskError(f"task is {task['status']}")
    task.update(status="claimed", assignee=user, lease_until=_ts(now + timedelta(hours=ctx.settings.lease_hours)),
                updated_at=_ts(now))
    ctx.stores.tasks.put(task_keys(task))
    return task


def release(ctx: Context, task_id: str, user: str) -> dict:
    task = get_task(ctx, task_id)
    if task.get("assignee") != user:
        raise TaskError("only the assignee can release")
    task.update(status="open", lease_until=None, updated_at=now_iso())
    task.pop("assignee", None)
    ctx.stores.tasks.put(task_keys(task))
    return task


def assign(ctx: Context, task_id: str, assignee: str) -> dict:
    task = get_task(ctx, task_id)
    task.update(assignee=assignee, updated_at=now_iso())
    ctx.stores.tasks.put(task_keys(task))
    return task


def snooze(ctx: Context, task_id: str, days: int, reason: str, user: str) -> dict:
    task = get_task(ctx, task_id)
    until = _ts(datetime.now(timezone.utc) + timedelta(days=days))
    task.update(status="snoozed", snoozed_until=until, updated_at=now_iso())
    task.setdefault("notes", []).append({"by": user, "at": now_iso(), "text": f"snoozed: {reason}"})
    ctx.stores.tasks.put(task_keys(task))
    return task


def wake_snoozed(ctx: Context) -> int:
    now, n = now_iso(), 0
    for t in ctx.stores.tasks.scan():
        if t["PK"].startswith("TASK#") and t["status"] == "snoozed" and (t.get("snoozed_until") or "") <= now:
            t["status"] = "open"
            ctx.stores.tasks.put(task_keys(t))
            n += 1
    return n


def _apply_outcomes(ctx: Context, findings: list[dict], allowed: set, actor: str, method: str,
                    scope: str) -> tuple[list[dict], bool]:
    """Applies one outcome per finding (shared by task and record resolve). Returns (results, unreachable)."""
    results = []
    unreachable = False
    for entry in findings:
        fid, outcome = entry.get("finding_id"), entry.get("outcome")
        if outcome not in OUTCOMES:
            raise TaskError(f"outcome must be one of {OUTCOMES}")
        if fid not in allowed:
            raise TaskError(f"finding {fid} is not on this {scope}")
        f = ctx.findings.get(fid)
        if not f or f["status"] != "open":
            results.append({"finding_id": fid, "skipped": "not open"})
            continue
        sid = f["service_id"]
        if outcome == "unreachable":
            unreachable = True
            results.append({"finding_id": fid, "outcome": outcome})
            continue
        if outcome == "dismiss":
            ctx.findings.set_status(fid, "dismissed", by=actor, reason=entry.get("reason", "dismissed"))
        elif outcome == "confirmed":
            _commit_field(ctx, sid, "assured_date", date.today().isoformat(), actor)
            ctx.findings.set_status(fid, "dismissed", by=actor, reason=f"record confirmed by {method}")
        elif outcome in ("apply", "edit"):
            value = entry.get("value") if outcome == "edit" else f.get("proposed_value")
            if value is None:
                raise TaskError(f"finding {fid} has no value to apply")
            _commit_field(ctx, sid, f["field"], value, actor)
            ctx.findings.set_status(fid, "applied" if outcome == "apply" else "edited", by=actor, value=value)
        elif outcome == "closed":
            _commit_field(ctx, sid, "status", entry.get("value") or "defunct", actor)
            ctx.findings.set_status(fid, "applied", by=actor, value=entry.get("value") or "defunct")
        results.append({"finding_id": fid, "outcome": outcome})
    return results, unreachable


def _mark_verified(ctx: Context, service_id: str, actor: str, method: str) -> dict:
    item = ctx.stores.index.get(service_id) or {"service_id": service_id}
    item.update(verification_status="verified", status_pk="verified", unverified_reason=None,
                verified_by=actor, verified_method=method, verified_at=now_iso(), open_flag_count=0)
    ctx.stores.index.put(item)
    return item


def resolve(ctx: Context, task_id: str, *, user: str, method: str, findings: list[dict], notes: str = "") -> dict:
    """One outcome per finding; HSDS changes are committed with the user as actor."""
    if method not in METHODS:
        raise TaskError(f"method must be one of {METHODS}")
    task = get_task(ctx, task_id)
    actor = f"user:{user}"
    results, unreachable = _apply_outcomes(ctx, findings, set(task.get("finding_ids", [])), actor, method, "task")

    if notes:
        task.setdefault("notes", []).append({"by": user, "at": now_iso(), "text": notes})
    remaining = [fid for fid in task["finding_ids"] if (ctx.findings.get(fid) or {}).get("status") == "open"]
    if unreachable:
        task["attempts"] = int(task.get("attempts", 0)) + 1
        task.update(status="snoozed", snoozed_until=_ts(datetime.now(timezone.utc) + timedelta(days=ctx.settings.snooze_days)))
        if task["attempts"] >= 3:
            task.update(escalated=True, priority=1)
    elif not remaining:
        task.update(status="done", resolution=f"resolved by {method}", resolved_by=actor, resolved_at=now_iso())
        ctx.stores.tasks.delete(f"DEDUPE#LOC#{task['location_id']}")
        for sid in task.get("service_ids", []):
            if ctx.findings.open_count(sid) == 0:
                _mark_verified(ctx, sid, actor, method)
        if task.get("org_id"):
            ctx.memory.add(task["org_id"], f"Confirmed by {method} on {date.today().isoformat()} by {user}."
                           + (f" Notes: {notes}" if notes else ""), author=actor)
    else:
        task["status"] = "claimed" if task.get("assignee") else "open"
    task["updated_at"] = now_iso()
    ctx.stores.tasks.put(task_keys(task))
    return {"task": task, "results": results, "open_findings": remaining}


def resolve_record(ctx: Context, service_id: str, *, user: str, method: str, findings: list[dict],
                   notes: str = "") -> dict:
    """A specialist verifies one record outside the call queue: one outcome per open finding; when none
    are left open the record becomes verified and assured_date is set to today (HSDS: date last verified)."""
    if method not in METHODS:
        raise TaskError(f"method must be one of {METHODS}")
    item = ctx.stores.index.get(service_id)
    if not item:
        raise TaskError("record not found")
    if any(e.get("outcome") == "unreachable" for e in findings):
        raise TaskError("unreachable only applies to call-queue tasks")
    actor = f"user:{user}"
    allowed = {f["finding_id"] for f in ctx.findings.for_service(service_id)}  # resolved ones are skipped
    results, _ = _apply_outcomes(ctx, findings, allowed, actor, method, "record")
    remaining = sorted(f["finding_id"] for f in ctx.findings.for_service(service_id) if f["status"] == "open")
    if not remaining:
        _commit_field(ctx, service_id, "assured_date", date.today().isoformat(), actor)
        item = _mark_verified(ctx, service_id, actor, method)
        if item.get("org_id"):
            ctx.memory.add(item["org_id"], f"Verified by {method} on {date.today().isoformat()} by {user}."
                           + (f" Notes: {notes}" if notes else ""), author=actor)
    return {"index": ctx.stores.index.get(service_id), "results": results, "open_findings": remaining}


def _commit_field(ctx: Context, service_id: str, field: str, value, actor: str) -> None:
    rows = ctx.repo.service_rows(service_id)
    if not rows:
        raise TaskError("service not found")
    try:
        changes, retire = apply_field(rows, service_id, field, value)
        source = (ctx.stores.index.get(service_id) or {}).get("source", "unknown")
        commit(ctx.stores, ctx.settings, changes, source=source, actor=actor, services=[service_id],
               retire=[(t, r) for t in changes for r in retire])
    except EditError as exc:
        raise TaskError(str(exc)) from None
    except CommitRejected as exc:
        raise TaskError(f"the change would make the record invalid HSDS: {exc}") from None
