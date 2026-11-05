"""Flag crawler (design §6.1): open findings -> one task per facility, by ZIP.

Runs every 15 minutes (EventBridge -> SQS `re-jobs` {type: crawl}) and on
demand (`POST /tasks/crawl`, `python -m app.cli crawl`).

Dedup: an item PK=DEDUPE#LOC#<location_id> points at the facility's current
open task. It is created with a conditional put, so two crawler runs can never
open two tasks for one facility; it is deleted when the task is done.
"""

from __future__ import annotations

import uuid
from collections import defaultdict

from ..context import Context
from ..store.backend import ConditionFailed
from ..store.commit import now_iso
from ..verify.findings import PRIORITY
from .zip_resolver import resolve_bucket

OPEN_STATES = ("open", "claimed", "snoozed")


def task_keys(task: dict) -> dict:
    task["zip_status"] = f"{task['zip']}#{task['status']}"
    task["sort"] = f"P{task['priority']}#{task['created_at']}"
    return task


def _facility(rows, location_id: str) -> dict:
    loc = rows.get("location", {}).get(location_id) or {}
    addr = next((a for a in rows.get("address", {}).values() if a.get("location_id") == location_id
                 and a.get("address_type") == "physical"), None) or next(
        (a for a in rows.get("address", {}).values() if a.get("location_id") == location_id), None)
    phones = [p.get("number") for p in rows.get("phone", {}).values()
              if p.get("location_id") == location_id or (p.get("organization_id") and p.get("organization_id") == loc.get("organization_id"))]
    org = rows.get("organization", {}).get(loc.get("organization_id")) or {}
    return {"name": loc.get("name") or org.get("name"), "organization": org.get("name"),
            "address": ", ".join(str(addr[k]) for k in ("address_1", "city", "state_province", "postal_code") if addr and addr.get(k)) if addr else None,
            "latitude": loc.get("latitude"), "longitude": loc.get("longitude"),
            "description": loc.get("description"), "phones": [p for p in phones if p][:5]}


def crawl(ctx: Context) -> dict:
    by_service: dict[str, list[dict]] = defaultdict(list)
    for f in ctx.findings.list(status="open"):
        by_service[f["service_id"]].append(f)

    facilities: dict[str, dict] = {}  # location_id -> {rows, service_ids, findings}
    for sid, findings in by_service.items():
        rows = ctx.repo.service_rows(sid)
        if not rows:
            continue
        loc_ids = [s["location_id"] for s in rows.get("service_at_location", {}).values() if s.get("service_id") == sid]
        org_level = [f for f in findings if str(f.get("field", "")).startswith(("organization", "website"))]
        if not loc_ids or org_level:
            svc = rows["service"][sid]
            loc_ids = loc_ids or [l for l, loc in rows.get("location", {}).items()
                                  if loc.get("organization_id") == svc.get("organization_id")]
        if not loc_ids:
            loc_ids = [f"NOLOC#{sid}"]
        for lid in loc_ids:
            fac = facilities.setdefault(lid, {"rows": rows, "service_ids": set(), "findings": {}})
            fac["service_ids"].add(sid)
            for f in findings:
                fac["findings"][f["finding_id"]] = f

    created = updated = 0
    now = now_iso()
    for lid, fac in facilities.items():
        rows, findings = fac["rows"], list(fac["findings"].values())
        dedupe_pk = f"DEDUPE#LOC#{lid}"
        pointer = ctx.stores.tasks.get(dedupe_pk)
        task = ctx.stores.tasks.get(f"TASK#{pointer['task_id']}") if pointer else None
        if task and task["status"] not in OPEN_STATES:
            ctx.stores.tasks.delete(dedupe_pk)
            task = None
        cats = sorted({f["category"] for f in findings})
        priority = min(PRIORITY.get(c, 5) for c in cats)
        asks = list(dict.fromkeys(q for f in findings for q in f.get("what_to_ask") or []))[:10]
        if task is None:
            org_id = next(iter(rows.get("organization", {})), None)
            task_id = str(uuid.uuid4())
            try:
                ctx.stores.tasks.put({"PK": dedupe_pk, "task_id": task_id}, if_not_exists=True)
            except ConditionFailed:
                continue  # another crawler won; it will pick these findings up
            task = {"PK": f"TASK#{task_id}", "task_id": task_id, "location_id": lid, "org_id": org_id,
                    "zip": resolve_bucket(lid, rows, ctx.settings, sorted(fac["service_ids"])),
                    "status": "open", "created_at": now, "attempts": 0, "notes": [],
                    "facility": _facility(rows, lid)}
            created += 1
        else:
            updated += 1
        task.update(service_ids=sorted(set(task.get("service_ids", [])) | fac["service_ids"]),
                    finding_ids=sorted(set(task.get("finding_ids", [])) | set(fac["findings"])),
                    categories=cats, priority=priority, what_to_ask=asks, updated_at=now)
        ctx.stores.tasks.put(task_keys(task))

    # close tasks whose findings were all resolved elsewhere
    closed = 0
    for task in ctx.stores.tasks.scan():
        if not task["PK"].startswith("TASK#") or task["status"] not in OPEN_STATES:
            continue
        still_open = [fid for fid in task.get("finding_ids", []) if (ctx.findings.get(fid) or {}).get("status") == "open"]
        if not still_open:
            task.update(status="done", resolution="resolved elsewhere", updated_at=now)
            ctx.stores.tasks.put(task_keys(task))
            ctx.stores.tasks.delete(f"DEDUPE#LOC#{task['location_id']}")
            closed += 1
    return {"facilities": len(facilities), "created": created, "updated": updated, "closed": closed}
