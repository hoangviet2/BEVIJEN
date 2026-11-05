"""Pipeline C: discovery runs and the create_record fence (design §7, AGENT.md §3.6)."""

import pytest

from app.discovery import service as disc
from app.verify.worker import drain_verify

from conftest import TOOL_HEADERS, make_ctx

EVIDENCE = [{"url": "https://vallejofoodpantry.org/", "snippet": "Vallejo Community Food Pantry, 123 Georgia St"},
            {"url": "https://www.solanocounty.gov/food", "snippet": "Vallejo Community Food Pantry (707) 555-0100"}]


def candidate(name="Vallejo Community Food Pantry"):
    return {"name": "Free groceries", "status": "active", "description": "Free groceries every Tuesday.",
            "organization": {"name": name, "description": "A volunteer food pantry.",
                             "website": "https://vallejofoodpantry.org"},
            "phones": [{"number": "(707) 555-0100"}],
            "service_at_locations": [{"location": {"location_type": "physical", "name": "Pantry",
                                                   "addresses": [{"address_1": "123 Georgia St", "city": "Vallejo",
                                                                  "state_province": "CA", "postal_code": "94590",
                                                                  "country": "US", "address_type": "physical"}]}}]}


def start(ctx):
    return disc.start_run(ctx, zip_code="94590", county=None, categories=["food"], requested_by="lead", enqueue=False)


def test_create_is_held_then_published_after_independent_check(ctx):
    run = start(ctx)
    out = disc.create_record(ctx, hsds=candidate(), evidence=EVIDENCE, run_id=run["run_id"], caller="discovery-agent")
    item = ctx.repo.index_item(out["service_id"])
    assert item["publish_state"] == "held" and item["source"] == "agent-discovery"
    assert item["unverified_reason"] == "agent_discovered"
    assert ctx.repo.history("service", out["service_id"])[0]["updated_by"] == "agent:discovery-v1"
    jobs = ctx.stores.verify_q.receive(10)
    assert [j.body["mode"] for j in jobs] == ["discovery_check"]
    ctx.stores.verify_q.nack(jobs[0])
    drain_verify(ctx)  # stub verifier: verified
    assert ctx.repo.index_item(out["service_id"])["publish_state"] == "published"


def test_failed_independent_check_makes_a_new_facility_task(ctx):
    run = start(ctx)
    out = disc.create_record(ctx, hsds=candidate(), evidence=EVIDENCE, run_id=run["run_id"], caller="discovery-agent")
    ctx.agents.default = "needs_manual"
    drain_verify(ctx)
    assert ctx.repo.index_item(out["service_id"])["publish_state"] == "held"
    assert [f for f in ctx.findings.for_service(out["service_id"], "open") if f["category"] == "new_facility"]


@pytest.mark.parametrize("kwargs,status", [
    ({"caller": "verifier-agent"}, 403),
    ({"evidence": EVIDENCE[:1]}, 400),                                    # one source only
    ({"evidence": [{"url": "https://a.org", "snippet": ""}, EVIDENCE[1]]}, 400),
    ({"run_id": "dr-unknown"}, 404),
])
def test_fence(ctx, kwargs, status):
    run = start(ctx)
    args = {"hsds": candidate(), "evidence": EVIDENCE, "run_id": run["run_id"], "caller": "discovery-agent", **kwargs}
    with pytest.raises(disc.DiscoveryError) as e:
        disc.create_record(ctx, **args)
    assert e.value.status == status


def test_existing_facility_is_not_created_twice(ctx):
    run = start(ctx)
    disc.create_record(ctx, hsds=candidate(), evidence=EVIDENCE, run_id=run["run_id"], caller="discovery-agent")
    with pytest.raises(disc.DiscoveryError) as e:
        disc.create_record(ctx, hsds=candidate(), evidence=EVIDENCE, run_id=run["run_id"], caller="discovery-agent")
    assert e.value.status == 409


def test_cap_and_kill_switch():
    ctx = make_ctx(discovery_max_creates=1)
    run = start(ctx)
    disc.create_record(ctx, hsds=candidate(), evidence=EVIDENCE, run_id=run["run_id"], caller="discovery-agent")
    other = candidate("Fairfield Free Meals")
    other["organization"]["website"] = "https://fairfieldmeals.org"
    other["phones"] = [{"number": "(707) 555-0199"}]
    with pytest.raises(disc.DiscoveryError) as e:
        disc.create_record(ctx, hsds=other, evidence=EVIDENCE, run_id=run["run_id"], caller="discovery-agent")
    assert e.value.status == 429
    off = make_ctx(discovery_enabled=False)
    with pytest.raises(disc.DiscoveryError):
        start(off)


def test_reject_keeps_record_but_inactive(ctx):
    run = start(ctx)
    out = disc.create_record(ctx, hsds=candidate(), evidence=EVIDENCE, run_id=run["run_id"], caller="discovery-agent")
    disc.set_publish_state(ctx, out["service_id"], "rejected", "lead@x.org")
    assert ctx.repo.get("service", out["service_id"])["status"] == "inactive"
    assert disc.list_candidates(ctx, "rejected")[0]["service_id"] == out["service_id"]


def test_execute_run_through_tool_endpoint(ctx, client):
    run = start(ctx)

    def agent_creates(payload):
        r = client.post("/tools/records", headers=TOOL_HEADERS,
                        json={"run_id": payload["run_id"], "hsds": candidate(), "evidence": EVIDENCE})
        assert r.status_code == 200, r.text

    ctx.agents.on_discover = agent_creates
    done = disc.execute_run(ctx, run["run_id"])
    assert done["status"] == "completed" and done["counts"]["created"] == 1


def test_match_existing_scores(ctx):
    run = start(ctx)
    disc.create_record(ctx, hsds=candidate(), evidence=EVIDENCE, run_id=run["run_id"], caller="discovery-agent")
    strong = disc.match_existing(ctx, domain="www.vallejofoodpantry.org")
    assert strong and strong[0]["strength"] == "strong"
    weak = disc.match_existing(ctx, name="Vallejo Community Food Pantry")
    assert weak and weak[0]["strength"] == "weak"
    assert disc.match_existing(ctx, name="Totally Different Clinic") == []
