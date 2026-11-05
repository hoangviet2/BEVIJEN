"""HTTP API: HSDS consumer endpoints, auth modes, agent tool endpoints."""

from app.auth import issue_api_key
from app.hsds.validate import validate_object

from conftest import NDP_1, TOOL_HEADERS, make_ctx, service_named


def upload(client):
    r = client.post("/ingest/ndp/upload", files={"file": ("s.json", NDP_1.read_bytes(), "application/json")})
    assert r.status_code == 200, r.text
    return r.json()


def test_services_page_shape_and_validity(client):
    upload(client)
    page = client.get("/services", params={"per_page": 5, "page": 2}).json()
    assert page["total_items"] == 20 and page["total_pages"] == 4 and page["page_number"] == 2
    assert page["size"] == 5 and not page["first_page"] and not page["last_page"]
    for s in page["contents"]:
        assert validate_object("service", s).valid
        for sch in s.get("schedules") or []:
            if "opens_at" in sch:
                assert sch["opens_at"][-6] in "+-"  # offset added on the way out


def test_filters_and_single_objects(client, ctx):
    upload(client)
    assert client.get("/services", params={"postal_code": "94060"}).json()["total_items"] >= 1
    org_id = service_named(ctx, "Legal Assistance")["org_id"]
    assert client.get("/services", params={"organization_id": org_id}).json()["total_items"] >= 1
    assert client.get(f"/organizations/{org_id}").json()["id"] == org_id
    term = client.get("/taxonomy_terms").json()["contents"][0]
    assert client.get("/services", params={"taxonomy_term_id": term["id"]}).json()["total_items"] >= 1
    assert client.get("/services/does-not-exist").status_code == 404
    assert client.get("/search", params={"q": "legal"}).json()["total_items"] >= 1


def test_held_records_are_not_served(client, ctx):
    upload(client)
    item = service_named(ctx, "Legal Assistance")
    item["publish_state"] = "held"
    ctx.stores.index.put(item)
    assert client.get(f"/services/{item['service_id']}").status_code == 404
    assert client.get("/services").json()["total_items"] == 19


def test_cognito_mode_requires_tokens_and_api_keys():
    from fastapi.testclient import TestClient

    from app import context as cm
    from app.main import app
    ctx = make_ctx(auth_mode="cognito")
    cm.set_context(ctx)
    try:
        c = TestClient(app)
        assert c.get("/services").status_code == 401
        assert c.get("/records").status_code == 401
        key = issue_api_key(ctx, "one-degree", source_filter="ndp")
        assert c.get("/services", headers={"Authorization": f"Bearer {key}"}).status_code == 200
        assert c.get("/services", headers={"Authorization": "Bearer nope"}).status_code == 401
        assert c.get("/health").status_code == 200
    finally:
        cm.set_context(None)


def test_roles(client):
    assert client.post("/ingest/odrs", json={}, headers={"x-dev-groups": "specialist"}).status_code == 403
    assert client.post("/tasks/crawl", headers={"x-dev-groups": "specialist"}).status_code == 403
    assert client.get("/tasks/zips", headers={"x-dev-groups": "specialist"}).status_code == 200


def test_tool_endpoints_need_the_tool_key(client, ctx):
    upload(client)
    sid = service_named(ctx, "Legal Assistance")["service_id"]
    assert client.get(f"/tools/records/{sid}").status_code == 401
    rec = client.get(f"/tools/records/{sid}", headers=TOOL_HEADERS).json()
    assert rec["service"]["id"] == sid and "open_findings" in rec and "memory_notes" in rec


def test_record_finding_rules(client, ctx):
    upload(client)
    sid = service_named(ctx, "Legal Assistance")["service_id"]
    base = {"service_id": sid, "category": "discrepancy", "field": "eligibility_description",
            "current_value": "x", "proposed_value": "y", "confidence": "high", "model_id": "m"}
    assert client.post("/tools/findings", headers=TOOL_HEADERS, json=base).status_code == 422  # no evidence
    ok = client.post("/tools/findings", headers=TOOL_HEADERS,
                     json={**base, "evidence": [{"url": "https://a.org", "snippet": "y"}]})
    assert ok.status_code == 200
    low = client.post("/tools/findings", headers=TOOL_HEADERS,
                      json={**base, "confidence": "low", "evidence": [{"url": "https://a.org", "snippet": "y"}]})
    assert low.status_code == 422  # low confidence only as needs_manual
    flag = client.post("/tools/flags", headers=TOOL_HEADERS,
                       json={"service_id": sid, "reason": "site down", "what_to_ask": ["Are you open?"]})
    assert flag.status_code == 200


def test_finding_apply_and_dismiss_endpoints(client, ctx):
    upload(client)
    sid = service_named(ctx, "Legal Assistance")["service_id"]
    f = ctx.findings.create(service_id=sid, category="discrepancy", field="eligibility_description",
                            proposed_value="Anyone in San Mateo or Santa Clara County", model_id="m",
                            evidence=[{"url": "https://a.org", "snippet": "Anyone in San Mateo or Santa Clara County"}])
    assert client.post(f"/findings/{f['finding_id']}/apply").status_code == 200
    assert client.get(f"/services/{sid}").json()["eligibility_description"].startswith("Anyone in San Mateo")
    assert ctx.findings.get(f["finding_id"])["status"] == "applied"


def test_patch_with_stale_version_is_refused(client, ctx):
    upload(client)
    sid = service_named(ctx, "Legal Assistance")["service_id"]
    op = [{"op": "replace", "path": f"/service/{sid}/name", "value": "Legal Help"}]
    assert client.patch(f"/records/{sid}", json=op, headers={"If-Match": "99"}).status_code == 412
    assert client.patch(f"/records/{sid}", json=op, headers={"If-Match": "1"}).status_code == 200
    bad = [{"op": "replace", "path": f"/service/{sid}/status", "value": "sort of open"}]
    assert client.patch(f"/records/{sid}", json=bad).status_code == 422


def test_ops_endpoints(client):
    upload(client)
    assert client.get("/ready").status_code == 200
    m = client.get("/metrics").json()
    assert m["services"] == 20 and m["recent_ingest_runs"]
    export = client.get("/exports/hsds")
    assert len(export.text.strip().splitlines()) == 20


def test_auth_callback_redirects_to_ui_with_tokens_in_fragment(ctx, monkeypatch):  # ctx fixture resets the global context
    import io
    import urllib.request
    from app.main import create_app
    from app import context as context_mod

    class R(io.BytesIO):
        def __enter__(self): return self
        def __exit__(self, *a): return False
    monkeypatch.setattr(urllib.request, "urlopen",
                        lambda req, timeout=0: R(b'{"id_token": "idt", "access_token": "act", "expires_in": 3600}'))
    cctx = make_ctx(auth_mode="cognito")
    object.__setattr__(cctx.settings, "cognito_domain", "re-1.auth.us-west-2.amazoncognito.com")
    context_mod.set_context(cctx)
    from fastapi.testclient import TestClient
    c = TestClient(create_app(), base_url="https://testserver")
    c.cookies.set("re_state", "s1")
    r = c.get("/auth/callback", params={"code": "c", "state": "s1"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/ui/#id_token=idt&access_token=act&expires_in=3600"
    assert c.get("/auth/callback", params={"code": "c", "state": "other"}, follow_redirects=False).status_code == 400


def test_cors_is_off_by_default_and_on_for_configured_origins(monkeypatch):
    from fastapi.testclient import TestClient
    from app.config import get_settings
    from app.main import create_app
    pre = {"Origin": "http://localhost:5173", "Access-Control-Request-Method": "GET"}
    assert "access-control-allow-origin" not in TestClient(create_app()).options("/health", headers=pre).headers
    monkeypatch.setenv("CORS_ORIGINS", "http://localhost:5173")
    get_settings.cache_clear()
    try:
        r = TestClient(create_app()).options("/health", headers=pre)
        assert r.headers["access-control-allow-origin"] == "http://localhost:5173"
    finally:
        monkeypatch.delenv("CORS_ORIGINS")
        get_settings.cache_clear()
