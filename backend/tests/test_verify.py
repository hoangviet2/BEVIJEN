"""Verify worker: verdict -> index mapping (design §5 table), retries, memory."""

import pytest

from app.verify.agent_client import AgentOutputError, check_verify_output, parse_json_text
from app.verify.worker import apply_verdict, drain_verify, enqueue_recheck, verify_one

from conftest import service_named


def test_verdicts_map_to_index(ctx, ndp_loaded):
    a = service_named(ctx, "Legal Assistance")["service_id"]
    b = service_named(ctx, "Dining Room")["service_id"]
    ctx.agents.verdicts = {a: "discrepancy", b: "actively_bad"}
    drain_verify(ctx)
    assert ctx.repo.index_item(a)["unverified_reason"] == "discrepancy"
    assert ctx.repo.index_item(b)["unverified_reason"] == "actively_bad"
    ok = service_named(ctx, "Clothes Closet")
    assert ok["verification_status"] == "verified" and ok["verified_by"] == "agent:verifier-v1"
    assert ok["last_check_category"] == "verified" and ok["last_check_model"] == "stub-model"


def test_verified_supersedes_stale_flag(ctx, ndp_loaded):
    sid = service_named(ctx, "Homeless Shelter")["service_id"]
    drain_verify(ctx)
    assert not [f for f in ctx.findings.for_service(sid, "open") if f["category"] == "stale"]


def test_bad_output_becomes_check_failed_and_two_in_a_row_flag(ctx, ndp_loaded):
    sid = service_named(ctx, "Legal Assistance")["service_id"]
    ctx.agents.verdicts = {sid: "bad-output"}
    verify_one(ctx, sid)
    assert ctx.repo.index_item(sid)["last_check_category"] == "check_failed"
    assert not [f for f in ctx.findings.for_service(sid, "open") if f["category"] == "check_failed"]
    verify_one(ctx, sid)
    assert [f for f in ctx.findings.for_service(sid, "open") if f["category"] == "check_failed"]


def test_memory_note_is_saved_for_the_org(ctx, ndp_loaded):
    item = service_named(ctx, "Legal Assistance")
    verify_one(ctx, item["service_id"])
    assert any(n["text"] == "checked by stub" for n in ctx.memory.notes(item["org_id"]))


def test_agent_mode_off_records_skipped(ctx, ndp_loaded):
    sid = service_named(ctx, "Legal Assistance")["service_id"]
    apply_verdict(ctx, sid, None, run_id="r", mode="recheck")
    assert ctx.repo.index_item(sid)["last_check_category"] == "skipped"
    assert ctx.repo.index_item(sid)["verification_status"] == "unverified"


def test_recheck_enqueues_only_old_checks(ctx, ndp_loaded):
    drain_verify(ctx)
    assert enqueue_recheck(ctx, older_than_days=30) == 0  # all checked just now
    assert enqueue_recheck(ctx, older_than_days=0) == 20


def test_output_schema_checks():
    with pytest.raises(AgentOutputError):
        check_verify_output({"verdict": "looks fine"}, "s1")
    with pytest.raises(AgentOutputError):
        check_verify_output({"verdict": "verified", "service_id": "other"}, "s1")
    assert check_verify_output({"verdict": "verified"}, "s1")["service_id"] == "s1"


def test_parse_json_text_tolerates_fences_and_prose():
    assert parse_json_text('Done.\n```json\n{"verdict": "verified"}\n```') == {"verdict": "verified"}
    assert parse_json_text('Result: {"a": 1}') == {"a": 1}


def test_discovery_over_jwt_uses_cached_cognito_token_and_live_endpoint(monkeypatch):
    import io
    import json as _json
    import urllib.request
    from app import aws
    from app.config import Settings
    from app.verify.agent_client import AgentCoreClient
    s = Settings()
    object.__setattr__(s, "discovery_auth", "jwt")
    object.__setattr__(s, "discovery_qualifier", "live")
    object.__setattr__(s, "cognito_domain", "re-1.auth.us-west-2.amazoncognito.com")
    object.__setattr__(s, "agent_m2m_client_id", "cid")
    object.__setattr__(s, "discovery_agent_arn", "arn:aws:bedrock-agentcore:us-west-2:1:runtime/re_discovery_agent-x")
    monkeypatch.setattr(aws, "secret", lambda _id, *a, **k: "csecret")
    calls = []

    class R(io.BytesIO):
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def fake(req, timeout=0):
        calls.append(req)
        if req.full_url.endswith("/oauth2/token"):
            return R(b'{"access_token": "tok", "expires_in": 3600}')
        return R(_json.dumps({"run_id": "dr-1", "queries": [], "candidates_seen": 0, "matched_existing": 0,
                              "created": [], "flagged_possible_duplicates": [], "rejected": []}).encode())
    monkeypatch.setattr(urllib.request, "urlopen", fake)
    client = AgentCoreClient.__new__(AgentCoreClient)
    client.settings = s
    for _ in range(2):
        client.discover({"run_id": "dr-1", "zip": "94590"})
    token_calls = [c for c in calls if c.full_url.endswith("/oauth2/token")]
    invokes = [c for c in calls if "/invocations" in c.full_url]
    assert len(token_calls) == 1 and len(invokes) == 2  # token cached
    assert invokes[0].get_header("Authorization") == "Bearer tok"
    assert invokes[0].full_url.endswith("runtime%2Fre_discovery_agent-x/invocations?qualifier=live")
