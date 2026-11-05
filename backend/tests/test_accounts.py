"""Sign-up requests, admin approval, and role-less users being refused (AUTH_MODE=dev directory)."""

from unittest.mock import MagicMock

from app import accounts

from conftest import make_ctx

ADMIN = {"x-dev-user": "boss@x.org", "x-dev-groups": "admin"}
SPEC = {"x-dev-user": "ana@x.org", "x-dev-groups": "specialist"}


def test_signup_approve_and_revoke(client):
    body = {"email": "Ana@X.org", "name": "Ana", "organization": "211", "reason": "call center"}
    assert client.post("/auth/signup", json=body).status_code == 202
    assert client.post("/auth/signup", json=body).status_code == 202  # idempotent, no enumeration
    assert client.post("/auth/signup", json={"email": "nope", "name": "x"}).status_code == 422

    assert client.get("/admin/signups", headers=SPEC).status_code == 403
    pending = client.get("/admin/signups", params={"status": "pending"}, headers=ADMIN).json()
    assert [p["email"] for p in pending] == ["ana@x.org"] and "PK" not in pending[0]

    r = client.post("/admin/signups/ana@x.org/approve", json={"role": "lead"}, headers=ADMIN)
    assert r.status_code == 200 and r.json()["status"] == "approved" and r.json()["decided_by"] == "boss@x.org"
    assert client.post("/admin/signups/ana@x.org/approve", json={}, headers=ADMIN).status_code == 409
    assert client.get("/admin/users", headers=ADMIN).json()[0]["groups"] == ["lead"]

    assert client.post("/admin/users/ana@x.org/role", json={"role": None}, headers=ADMIN).json()["groups"] == []
    assert client.post("/admin/users/boss@x.org/role", json={"role": "lead"}, headers=ADMIN).status_code == 409  # not yourself


def test_reject_and_pending_cap(client, monkeypatch):
    client.post("/auth/signup", json={"email": "b@x.org", "name": "B"})
    assert client.post("/admin/signups/b@x.org/reject", json={"reason": "unknown"}, headers=ADMIN).json()["status"] == "rejected"
    assert client.post("/admin/signups/zz@x.org/reject", json={}, headers=ADMIN).status_code == 404
    monkeypatch.setattr(accounts, "MAX_PENDING", 1)
    assert client.post("/auth/signup", json={"email": "c@x.org", "name": "C"}).status_code == 202
    assert client.post("/auth/signup", json={"email": "d@x.org", "name": "D"}).status_code == 429


def test_users_without_a_role_are_refused(client):
    assert client.get("/tasks/zips", headers={"x-dev-user": "new@x.org", "x-dev-groups": ""}).status_code == 403
    assert client.get("/tasks/zips", headers=SPEC).status_code == 200


def test_cognito_approve_creates_user_and_sets_one_group(monkeypatch):
    ctx = make_ctx(auth_mode="cognito", cognito_user_pool_id="pool")
    idp = MagicMock()
    idp.admin_list_groups_for_user.return_value = {"Groups": [{"GroupName": "admin"}]}
    monkeypatch.setattr(accounts, "_idp", lambda c: (idp, "pool"))
    accounts.request_signup(ctx, "e@x.org", "E")
    accounts.approve(ctx, "e@x.org", "specialist", "boss@x.org")
    assert idp.admin_create_user.call_args.kwargs["Username"] == "e@x.org"
    idp.admin_remove_user_from_group.assert_called_once_with(UserPoolId="pool", Username="e@x.org", GroupName="admin")
    idp.admin_add_user_to_group.assert_called_once_with(UserPoolId="pool", Username="e@x.org", GroupName="specialist")
