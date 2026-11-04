"""Authentication (design §9 legend).

C  consumer      Bearer API key -> sha256(key + pepper) looked up in `re-api-keys` (R3.4)
S  specialist    Cognito user-pool JWT (groups: specialist, lead, admin)
T  tool caller   AgentCore Gateway, via the AgentCore Identity API-key provider
                 (header `x-api-key`, value from Secrets Manager `re/tools-api-key`)

AUTH_MODE=dev (local only) accepts `X-Dev-User` / `X-Dev-Groups` headers instead
of a JWT and lets consumer endpoints through without a key.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass, field
from functools import lru_cache

from fastapi import Depends, Header, HTTPException, Request

from .config import Settings
from .context import Context, get_context
from .store.commit import now_iso

ROLES = ("specialist", "lead", "admin")


@dataclass
class User:
    email: str
    groups: list[str] = field(default_factory=list)

    def has(self, role: str) -> bool:
        rank = {r: i for i, r in enumerate(ROLES)}
        return any(rank.get(g, -1) >= rank[role] for g in self.groups)


@dataclass
class Consumer:
    owner: str
    scopes: list[str]
    source_filter: str | None = None


def ctx_dep() -> Context:
    return get_context()


@lru_cache(maxsize=4)
def _jwks(region: str, pool: str):
    import jwt
    return jwt.PyJWKClient(f"https://cognito-idp.{region}.amazonaws.com/{pool}/.well-known/jwks.json")


def verify_cognito(token: str, s: Settings) -> User:
    import jwt
    if not s.cognito_user_pool_id:
        raise HTTPException(500, "COGNITO_USER_POOL_ID is not configured")
    issuer = f"https://cognito-idp.{s.aws_region}.amazonaws.com/{s.cognito_user_pool_id}"
    try:
        key = _jwks(s.aws_region, s.cognito_user_pool_id).get_signing_key_from_jwt(token).key
        claims = jwt.decode(token, key, algorithms=["RS256"], issuer=issuer, options={"verify_aud": False})
    except Exception:
        raise HTTPException(401, "invalid token") from None
    client = claims.get("aud") if claims.get("token_use") == "id" else claims.get("client_id")
    if s.cognito_client_id and client != s.cognito_client_id:
        raise HTTPException(401, "token is for another client")
    email = claims.get("email") or claims.get("username") or claims.get("cognito:username") or claims["sub"]
    return User(email=email, groups=list(claims.get("cognito:groups") or []))


def current_user(request: Request, ctx: Context = Depends(ctx_dep)) -> User:
    s = ctx.settings
    if s.auth_mode == "dev":
        return User(email=request.headers.get("x-dev-user", "dev@local"),
                    groups=[g for g in request.headers.get("x-dev-groups", "admin").split(",") if g])
    auth = request.headers.get("authorization", "")
    if not auth.lower().startswith("bearer "):
        raise HTTPException(401, "missing bearer token")
    return verify_cognito(auth.split(" ", 1)[1], s)


def require(role: str):
    def dep(user: User = Depends(current_user)) -> User:
        if not user.has(role):
            raise HTTPException(403, f"requires group {role}")
        return user
    return dep


def _pepper(ctx: Context) -> str:
    if ctx.settings.is_memory:
        return "dev-pepper"
    from . import aws
    return aws.secret(ctx.settings.api_key_pepper_secret)


def hash_key(ctx: Context, key: str) -> str:
    return hashlib.sha256((key + _pepper(ctx)).encode()).hexdigest()


def issue_api_key(ctx: Context, owner: str, scopes: list[str] | None = None, source_filter: str | None = None) -> str:
    key = "re_" + secrets.token_urlsafe(32)
    ctx.stores.keys.put({"PK": f"KEY#{hash_key(ctx, key)}", "owner": owner, "scopes": scopes or ["read"],
                         "source_filter": source_filter, "created_at": now_iso()})
    return key  # shown once; only the hash is stored


def consumer(request: Request, ctx: Context = Depends(ctx_dep)) -> Consumer:
    auth = request.headers.get("authorization", "")
    if not auth.lower().startswith("bearer "):
        if ctx.settings.auth_mode == "dev":
            return Consumer(owner="dev", scopes=["read"])
        raise HTTPException(401, "missing API key")
    item = ctx.stores.keys.get(f"KEY#{hash_key(ctx, auth.split(' ', 1)[1].strip())}")
    if not item or item.get("revoked"):
        raise HTTPException(401, "invalid API key")
    return Consumer(owner=item["owner"], scopes=item.get("scopes", []), source_filter=item.get("source_filter"))


@lru_cache(maxsize=1)
def _tools_key(secret_id: str) -> str:
    from . import aws
    return aws.secret(secret_id)


def tool_caller(x_api_key: str = Header(default=""), x_re_agent: str = Header(default=""),
                ctx: Context = Depends(ctx_dep)) -> str:
    """Returns the calling agent name (verifier-agent | discovery-agent)."""
    expected = ctx.settings.tools_api_key or ("" if ctx.settings.is_memory else _tools_key(ctx.settings.tools_api_key_secret))
    if not expected or not hmac.compare_digest(x_api_key, expected):
        raise HTTPException(401, "invalid tool key")
    return x_re_agent or "unknown-agent"
