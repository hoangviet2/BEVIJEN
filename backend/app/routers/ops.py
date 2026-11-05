"""Ops (design §9.7): health, readiness, metrics, Cognito hosted-UI login."""

from __future__ import annotations

import secrets
import urllib.parse
from collections import Counter

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse

from ..auth import User, ctx_dep, require
from ..context import Context

router = APIRouter(tags=["Ops"])


@router.get("/")
def root(ctx: Context = Depends(ctx_dep)):
    return {"service": "resource-engine", "hsds_version": ctx.settings.hsds_version,
            "links": {"docs": "/docs", "services": "/services", "organizations": "/organizations",
                      "taxonomies": "/taxonomies", "search": "/search", "health": "/health"}}


@router.get("/health")
def health():
    return {"ok": True}


@router.get("/ready")
def ready(ctx: Context = Depends(ctx_dep)):
    checks = {}
    for name in ("index", "hsds", "findings", "tasks"):
        try:
            getattr(ctx.stores, name).get("__ready__", "CURRENT") if getattr(ctx.stores, name).spec.range_key \
                else getattr(ctx.stores, name).get("__ready__")
            checks[f"dynamodb:{name}"] = "ok"
        except Exception as exc:
            checks[f"dynamodb:{name}"] = type(exc).__name__
    try:
        ctx.stores.blobs.get_bytes("__ready__")
        checks["s3"] = "ok"
    except Exception as exc:
        checks["s3"] = type(exc).__name__
    try:
        if hasattr(ctx.stores.verify_q, "url"):
            from .. import aws
            aws.client("sqs", ctx.settings).get_queue_attributes(QueueUrl=ctx.stores.verify_q.url,
                                                                 AttributeNames=["ApproximateNumberOfMessages"])
        checks["sqs"] = "ok"
    except Exception as exc:
        checks["sqs"] = type(exc).__name__
    ok = all(v == "ok" for v in checks.values())
    if not ok:
        raise HTTPException(503, checks)
    return checks


@router.get("/metrics")
def metrics(ctx: Context = Depends(ctx_dep), user: User = Depends(require("admin"))):
    idx = list(ctx.repo.index_items())
    return {
        "services": len(idx),
        "by_source": Counter(i.get("source") for i in idx),
        "by_status": Counter(i.get("verification_status") for i in idx),
        "by_reason": Counter(i.get("unverified_reason") for i in idx if i.get("unverified_reason")),
        "by_publish_state": Counter(i.get("publish_state", "published") for i in idx),
        "open_findings": Counter(f["category"] for f in ctx.findings.list(status="open")),
        "open_tasks": sum(1 for t in ctx.stores.tasks.scan() if t["PK"].startswith("TASK#") and t["status"] == "open"),
        "recent_ingest_runs": [{k: r.get(k) for k in ("run_id", "status", "counts", "started_at")}
                               for r in ctx.runs.list("ingest", 10)],
    }


def _cognito_base(ctx: Context) -> str:
    if not ctx.settings.cognito_domain:
        raise HTTPException(500, "COGNITO_DOMAIN not configured")
    d = ctx.settings.cognito_domain
    return d if d.startswith("https://") else f"https://{d}"


@router.get("/auth/login")
def login(request: Request, ctx: Context = Depends(ctx_dep)):
    state = secrets.token_urlsafe(16)
    q = urllib.parse.urlencode({"response_type": "code", "client_id": ctx.settings.cognito_client_id,
                                "redirect_uri": str(request.url_for("auth_callback")), "scope": "openid email",
                                "state": state})
    resp = RedirectResponse(f"{_cognito_base(ctx)}/oauth2/authorize?{q}")
    resp.set_cookie("re_state", state, httponly=True, secure=True, samesite="lax", max_age=600)
    return resp


@router.get("/auth/callback", name="auth_callback")
def callback(request: Request, code: str, state: str, ctx: Context = Depends(ctx_dep)):
    """Exchanges the code for tokens (public client, no secret) and sends the browser to UI_URL with them
    in the URL fragment (#id_token=…&access_token=…&expires_in=…): fragments never reach servers or logs."""
    import json
    import urllib.request
    if request.cookies.get("re_state") != state:
        raise HTTPException(400, "state mismatch")
    data = urllib.parse.urlencode({"grant_type": "authorization_code", "client_id": ctx.settings.cognito_client_id,
                                   "code": code, "redirect_uri": str(request.url_for("auth_callback"))}).encode()
    req = urllib.request.Request(f"{_cognito_base(ctx)}/oauth2/token", data=data,
                                 headers={"Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(req, timeout=15) as r:
        tokens = json.loads(r.read())
    frag = urllib.parse.urlencode({k: tokens[k] for k in ("id_token", "access_token", "expires_in") if tokens.get(k)})
    resp = RedirectResponse(f"{ctx.settings.ui_url}#{frag}", status_code=303)
    resp.delete_cookie("re_state")
    return resp
