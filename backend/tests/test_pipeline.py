"""Pipeline A on the supplied NDP and ODRS samples (design §5, §11 quirks)."""

from app.adapters import ndp_sample, odrs_file
from app.hsds.validate import validate_service
from app.pipeline import run_ingest

from conftest import NDP_1, NDP_2, ODRS, service_named


def test_ndp_sample_counts_and_raw_archive(ctx, ndp_loaded):
    run = ndp_loaded
    c = run["counts"]
    assert run["status"] == "completed"
    assert c["organizations_received"] == 10
    assert c["services_received"] == 20
    assert c["services_received"] == c["services_stored"] + c.get("services_quarantined", 0)  # R1.7
    raw = [k for k in ctx.stores.blobs.list("raw/ndp/") if k.endswith("page-0.json")]
    assert raw and ctx.stores.blobs.get_bytes(raw[0]) == NDP_1.read_bytes()  # archived byte-for-byte


def test_every_stored_document_is_valid_hsds(ctx, ndp_loaded):
    for item in ctx.repo.index_items():
        doc = ctx.stores.blobs.get_json(item["s3_key"])
        assert validate_service(doc).valid, item["service_name"]
        # S3 copy and the DynamoDB rebuild agree
        assert ctx.repo.render_service(item["service_id"]) == doc


def test_reingest_unchanged_is_skipped(ctx, ndp_loaded):
    again = run_ingest(ctx, ndp_sample([NDP_1]))
    assert again["counts"]["services_skipped_unchanged"] == 20
    assert "services_inserted" not in again["counts"]


def test_second_page_adds_more(ctx, ndp_loaded):
    run = run_ingest(ctx, ndp_sample([NDP_2]))
    assert run["counts"]["services_inserted"] > 0
    assert len(list(ctx.repo.index_items())) > 20


def test_quirk_phone_named_website_becomes_url(ctx, ndp_loaded):
    assert ndp_loaded["fixes"]["phone_was_url"] == 2
    urls = ctx.repo.list_type("url")
    assert any(u["url"] == "https://my.commute.org" for u in urls)
    assert not any("commute.org" in str(p.get("number")) for p in ctx.repo.list_type("phone"))


def test_quirk_various_locations_has_no_address_and_needs_a_person(ctx, ndp_loaded):
    assert ndp_loaded["fixes"]["address_incomplete_dropped"] == 2
    item = service_named(ctx, "Partner Food Pantries")
    cats = {f["category"] for f in ctx.findings.for_service(item["service_id"], "open")}
    assert "needs_manual" in cats


def test_quirk_text_schedule_becomes_monthly_rrule(ctx, ndp_loaded):
    rows = ctx.repo.list_type("schedule")
    assert any(r.get("freq") == "MONTHLY" and r.get("byday") == "2SU" and r.get("opens_at") == "13:30"
               and r.get("closes_at") == "15:00" for r in rows)
    assert any(r.get("byday") == "2WE,4WE" for r in rows)


def test_quirk_stale_record_is_preflagged(ctx, ndp_loaded):
    item = service_named(ctx, "Homeless Shelter")
    cats = {f["category"] for f in ctx.findings.for_service(item["service_id"], "open")}
    assert "stale" in cats


def test_html_is_stripped(ctx, ndp_loaded):
    for svc in ctx.repo.list_type("service"):
        assert "<p>" not in (svc.get("description") or "")


def test_odrs_sample(ctx):
    run = run_ingest(ctx, odrs_file([ODRS]))
    c = run["counts"]
    assert c["services_received"] == 50
    assert c["services_received"] == c["services_stored"] + c.get("services_quarantined", 0)
    item = next(iter(ctx.repo.index_items()))
    doc = ctx.stores.blobs.get_json(item["s3_key"])
    assert doc["organization"]["description"].endswith("Description not available.")
    cats = {f["field"] for f in ctx.findings.for_service(item["service_id"], "open")}
    assert "organization.description" in cats  # §12.3: verifier should draft one


def test_reingest_does_not_overwrite_a_person_edit_and_raises_a_clash(ctx, ndp_loaded, client):
    item = service_named(ctx, "Legal Assistance")
    sid = item["service_id"]
    r = client.patch(f"/records/{sid}", json=[{"op": "replace", "path": f"/service/{sid}/eligibility_description",
                                              "value": "San Mateo and Santa Clara residents"}])
    assert r.status_code == 200, r.text
    run_ingest(ctx, ndp_sample([NDP_1]))
    assert ctx.repo.get("service", sid)["eligibility_description"] == "San Mateo and Santa Clara residents"
    clash = [f for f in ctx.findings.for_service(sid, "open") if f["category"] == "clash"]
    assert clash and clash[0]["field"] == "service.eligibility_description"


def test_new_and_changed_services_get_verify_jobs(ctx, ndp_loaded):
    jobs = ctx.stores.verify_q.receive(100)
    assert len(jobs) == 20 and all(j.body["mode"] == "new" for j in jobs)


def test_ingest_without_verify_stores_and_preflags_but_queues_no_agent_runs(ctx):
    run = run_ingest(ctx, odrs_file([ODRS]), enqueue=False)
    assert run["counts"]["services_stored"] == 50
    assert ctx.stores.verify_q.receive(10) == []
    assert ctx.findings.list(category="missing_information")  # rule pre-flags still ran


def test_odrs_shared_location_children_are_not_copied_per_opportunity(ctx):
    # ODRS repeats a location (with its address and hours) inside every opportunity offered there,
    # and one opportunity can list the same location twice; each must become one row, not one per mention.
    import json as _json
    run_ingest(ctx, odrs_file([ODRS]))
    for item in ctx.repo.index_items():
        doc = ctx.repo.render_service(item["service_id"])
        for loc in [sal["location"] for sal in doc.get("service_at_locations", [])] + doc["organization"].get("locations", []):
            for key in ("addresses", "schedules"):
                rows = [_json.dumps({k: v for k, v in r.items() if k != "id"}, sort_keys=True) for r in loc.get(key, [])]
                assert len(rows) == len(set(rows)), (loc.get("name"), key, len(rows))
