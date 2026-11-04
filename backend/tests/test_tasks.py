"""Pipeline B: flag crawler, ZIP buckets, leases, resolution (design §6)."""

import pytest

from app.tasks import service as tasks
from app.tasks.crawler import crawl

from conftest import service_named


def _open_tasks(ctx):
    return [t for t in ctx.stores.tasks.scan() if t["PK"].startswith("TASK#") and t["status"] == "open"]


def test_one_task_per_facility_and_rerun_is_idempotent(ctx, ndp_loaded):
    first = crawl(ctx)
    assert first["created"] > 0
    n = len(_open_tasks(ctx))
    again = crawl(ctx)
    assert again["created"] == 0 and len(_open_tasks(ctx)) == n
    locs = [t["location_id"] for t in _open_tasks(ctx)]
    assert len(locs) == len(set(locs))


def test_buckets_include_zips_and_county_for_various_locations(ctx, ndp_loaded):
    crawl(ctx)
    zips = {b["zip"] for b in tasks.zip_board(ctx)}
    assert "ZIP#94060" in zips or "ZIP#94044" in zips
    assert any(z.startswith("COUNTY#") for z in zips), zips


def test_priority_is_lowest_p_of_findings(ctx, ndp_loaded):
    sid = service_named(ctx, "Legal Assistance")["service_id"]
    ctx.findings.create(service_id=sid, category="actively_bad", field="website", model_id="m", confidence="high",
                        evidence=[{"url": "https://x", "snippet": "404"}])
    crawl(ctx)
    t = next(t for t in _open_tasks(ctx) if sid in t["service_ids"])
    assert t["priority"] == 1


def test_claim_lease_blocks_second_volunteer(ctx, ndp_loaded):
    crawl(ctx)
    t = _open_tasks(ctx)[0]
    tasks.claim(ctx, t["task_id"], "a@x.org")
    with pytest.raises(tasks.TaskError):
        tasks.claim(ctx, t["task_id"], "b@x.org")
    tasks.release(ctx, t["task_id"], "a@x.org")
    assert tasks.claim(ctx, t["task_id"], "b@x.org")["assignee"] == "b@x.org"


def test_resolve_apply_commits_with_user_and_verifies(ctx, ndp_loaded):
    sid = service_named(ctx, "Legal Assistance")["service_id"]
    for f in ctx.findings.for_service(sid, "open"):  # start from a clean slate for this service
        ctx.findings.set_status(f["finding_id"], "dismissed", by="test")
    f = ctx.findings.create(service_id=sid, category="discrepancy", field="service_areas[].name",
                            current_value=["San Mateo"], proposed_value=["San Mateo", "Santa Clara"],
                            evidence=[{"url": "https://law.stanford.edu/clc", "snippet": "San Mateo and Santa Clara"}],
                            model_id="m", confidence="high", what_to_ask=["Do you serve Santa Clara County?"])
    crawl(ctx)
    task = next(t for t in _open_tasks(ctx) if f["finding_id"] in t["finding_ids"])
    out = tasks.resolve(ctx, task["task_id"], user="jane@1degree.org", method="phone", notes="spoke to desk",
                        findings=[{"finding_id": fid, "outcome": "apply" if fid == f["finding_id"] else "dismiss"}
                                  for fid in task["finding_ids"]])
    assert out["task"]["status"] == "done"
    doc = ctx.repo.render_service(sid)
    assert sorted(a["name"] for a in doc["service_areas"]) == ["San Mateo", "Santa Clara"]
    hist = ctx.repo.service_history(sid)
    assert any(m["updated_by"] == "user:jane@1degree.org" for m in hist)
    idx = ctx.repo.index_item(sid)
    assert idx["verification_status"] == "verified" and idx["verified_method"] == "phone"
    assert any("Confirmed by phone" in n["text"] for n in ctx.memory.notes(idx["org_id"]))


def test_unreachable_three_times_escalates(ctx, ndp_loaded):
    crawl(ctx)
    t = _open_tasks(ctx)[0]
    fid = t["finding_ids"][0]
    for _ in range(3):
        out = tasks.resolve(ctx, t["task_id"], user="a@x.org", method="phone",
                            findings=[{"finding_id": fid, "outcome": "unreachable"}])
    assert out["task"]["status"] == "snoozed" and out["task"]["escalated"] and out["task"]["priority"] == 1


def test_invalid_edit_is_rejected(ctx, ndp_loaded):
    sid = service_named(ctx, "Legal Assistance")["service_id"]
    with pytest.raises(tasks.TaskError):
        tasks._commit_field(ctx, sid, "status", "maybe", "user:x")  # not an HSDS status enum value


def test_crawler_closes_tasks_resolved_elsewhere(ctx, ndp_loaded):
    crawl(ctx)
    t = _open_tasks(ctx)[0]
    for fid in t["finding_ids"]:
        ctx.findings.set_status(fid, "dismissed", by="test")
    assert crawl(ctx)["closed"] >= 1
    assert ctx.stores.tasks.get(t["PK"])["status"] == "done"
