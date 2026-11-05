"""Sign-up requests (public) and the admin's user management (see app/accounts.py)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from .. import accounts
from ..auth import User, ctx_dep, require
from ..context import Context
from ..models import ApproveSignup, RejectSignup, SetRole, SignupRequest

router = APIRouter(tags=["Accounts"])


def _acc(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except accounts.AccountError as exc:
        raise HTTPException(exc.status, str(exc)) from None


@router.post("/auth/signup", status_code=202)
def signup(body: SignupRequest, ctx: Context = Depends(ctx_dep)):
    """Public. Stores a request for an admin to approve; the same answer whether or not the email is known."""
    _acc(accounts.request_signup, ctx, body.email, body.name, body.organization, body.reason)
    return {"status": "received"}


@router.get("/admin/signups")
def signups(status: str | None = None, ctx: Context = Depends(ctx_dep), user: User = Depends(require("admin"))):
    return accounts.list_signups(ctx, status)


@router.post("/admin/signups/{email}/approve")
def approve(email: str, body: ApproveSignup, ctx: Context = Depends(ctx_dep), user: User = Depends(require("admin"))):
    return _acc(accounts.approve, ctx, email, body.role, user.email)


@router.post("/admin/signups/{email}/reject")
def reject(email: str, body: RejectSignup, ctx: Context = Depends(ctx_dep), user: User = Depends(require("admin"))):
    return _acc(accounts.reject, ctx, email, user.email, body.reason)


@router.get("/admin/users")
def users(ctx: Context = Depends(ctx_dep), user: User = Depends(require("admin"))):
    return _acc(accounts.list_users, ctx)


@router.post("/admin/users/{email}/role")
def set_role(email: str, body: SetRole, ctx: Context = Depends(ctx_dep), user: User = Depends(require("admin"))):
    return _acc(accounts.set_role, ctx, email, body.role, user.email)
