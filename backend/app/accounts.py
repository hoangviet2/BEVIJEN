"""Specialist accounts: public sign-up requests that an admin approves.

A request is only a row (`SIGNUP#<email>` in the keys table); no Cognito user exists until an
admin approves it. Approving creates the Cognito user (Cognito emails a temporary password) and
puts it in one group. Users with no group are refused by `auth.current_user`, so removing every
group revokes access (their current token keeps its groups until it expires, max 1 h).

AUTH_MODE=dev has no Cognito: the user directory is `USER#<email>` rows in the same table.
"""

from __future__ import annotations

from .auth import ROLES
from .context import Context
from .store.commit import now_iso

SIGNUP = "SIGNUP#"
USER = "USER#"
MAX_PENDING = 200  # public endpoint: caps what an anonymous caller can pile up


class AccountError(Exception):
    def __init__(self, status: int, msg: str):
        super().__init__(msg)
        self.status = status


def _norm(email: str) -> str:
    return email.strip().lower()


def _rows(ctx: Context, prefix: str) -> list[dict]:
    return [r for r in ctx.stores.keys.scan() if r["PK"].startswith(prefix)]


def _public(row: dict) -> dict:
    return {k: v for k, v in row.items() if k != "PK"}


# --- sign-up requests ---------------------------------------------------------------------
def request_signup(ctx: Context, email: str, name: str, organization: str = "", reason: str = "") -> None:
    """Idempotent and silent about existing accounts (no account enumeration)."""
    email = _norm(email)
    row = ctx.stores.keys.get(SIGNUP + email)
    if row and row["status"] in ("pending", "approved"):
        return
    if sum(r["status"] == "pending" for r in _rows(ctx, SIGNUP)) >= MAX_PENDING:
        raise AccountError(429, "too many pending sign-ups, try again later")
    ctx.stores.keys.put({"PK": SIGNUP + email, "email": email, "name": name.strip(), "organization": organization.strip(),
                         "reason": reason.strip(), "status": "pending", "requested_at": now_iso()})


def list_signups(ctx: Context, status: str | None = None) -> list[dict]:
    rows = [_public(r) for r in _rows(ctx, SIGNUP) if not status or r["status"] == status]
    return sorted(rows, key=lambda r: r.get("requested_at", ""), reverse=True)


def _signup(ctx: Context, email: str) -> dict:
    row = ctx.stores.keys.get(SIGNUP + _norm(email))
    if not row:
        raise AccountError(404, "sign-up request not found")
    return row


def approve(ctx: Context, email: str, role: str, by: str) -> dict:
    row = _signup(ctx, email)
    if row["status"] != "pending":
        raise AccountError(409, f"request is already {row['status']}")
    create_user(ctx, row["email"], row.get("name", ""), role)
    row.update(status="approved", role=role, decided_by=by, decided_at=now_iso())
    ctx.stores.keys.put(row)
    return _public(row)


def reject(ctx: Context, email: str, by: str, reason: str = "") -> dict:
    row = _signup(ctx, email)
    if row["status"] != "pending":
        raise AccountError(409, f"request is already {row['status']}")
    row.update(status="rejected", decided_by=by, decided_at=now_iso(), decision_reason=reason)
    ctx.stores.keys.put(row)
    return _public(row)


# --- user directory (Cognito, or table rows in dev mode) --------------------------------------
def _dev(ctx: Context) -> bool:
    return ctx.settings.auth_mode == "dev"


def _idp(ctx: Context):
    from . import aws
    if not ctx.settings.cognito_user_pool_id:
        raise AccountError(500, "COGNITO_USER_POOL_ID is not configured")
    return aws.client("cognito-idp", ctx.settings), ctx.settings.cognito_user_pool_id


def create_user(ctx: Context, email: str, name: str, role: str) -> None:
    if role not in ROLES:
        raise AccountError(422, f"role must be one of {ROLES}")
    email = _norm(email)
    if _dev(ctx):
        if not ctx.stores.keys.get(USER + email):
            ctx.stores.keys.put({"PK": USER + email, "email": email, "name": name, "groups": [],
                                 "status": "CONFIRMED", "enabled": True, "created_at": now_iso()})
    else:
        idp, pool = _idp(ctx)
        attrs = [{"Name": "email", "Value": email}, {"Name": "email_verified", "Value": "true"}]
        if name:
            attrs.append({"Name": "name", "Value": name})
        try:  # Cognito emails the temporary password; the Hosted UI asks for a new one at first sign-in
            idp.admin_create_user(UserPoolId=pool, Username=email, UserAttributes=attrs, DesiredDeliveryMediums=["EMAIL"])
        except idp.exceptions.UsernameExistsException:
            pass
    set_role(ctx, email, role)


def set_role(ctx: Context, email: str, role: str | None, by: str = "") -> dict:
    """role=None removes every group (revokes access)."""
    if role is not None and role not in ROLES:
        raise AccountError(422, f"role must be one of {ROLES}")
    email = _norm(email)
    if by and _norm(by) == email and role != "admin":
        raise AccountError(409, "you can't remove your own admin role")
    if _dev(ctx):
        row = ctx.stores.keys.get(USER + email)
        if not row:
            raise AccountError(404, "user not found")
        row["groups"] = [role] if role else []
        ctx.stores.keys.put(row)
        return _public(row)
    idp, pool = _idp(ctx)
    try:
        current = [g["GroupName"] for g in idp.admin_list_groups_for_user(UserPoolId=pool, Username=email)["Groups"]]
    except idp.exceptions.UserNotFoundException:
        raise AccountError(404, "user not found") from None
    for g in current:
        if g in ROLES and g != role:
            idp.admin_remove_user_from_group(UserPoolId=pool, Username=email, GroupName=g)
    if role and role not in current:
        idp.admin_add_user_to_group(UserPoolId=pool, Username=email, GroupName=role)
    return {"email": email, "groups": [role] if role else []}


def list_users(ctx: Context) -> list[dict]:
    if _dev(ctx):
        return sorted((_public(r) for r in _rows(ctx, USER)), key=lambda r: r["email"])
    idp, pool = _idp(ctx)
    users: dict[str, dict] = {}
    for page in idp.get_paginator("list_users").paginate(UserPoolId=pool):
        for u in page["Users"]:
            a = {x["Name"]: x["Value"] for x in u.get("Attributes", [])}
            users[u["Username"]] = {"email": a.get("email", u["Username"]), "name": a.get("name", ""), "groups": [],
                                    "status": u.get("UserStatus", ""), "enabled": u.get("Enabled", True),
                                    "created_at": str(u.get("UserCreateDate", ""))}
    for g in ROLES:
        for page in idp.get_paginator("list_users_in_group").paginate(UserPoolId=pool, GroupName=g):
            for u in page["Users"]:
                if u["Username"] in users:
                    users[u["Username"]]["groups"].append(g)
    return sorted(users.values(), key=lambda r: r["email"])
