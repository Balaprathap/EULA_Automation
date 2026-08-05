"""FastAPI dependencies: authentication, authorization, and rate limiting.

Layer two of the three-layer security model. RLS protects the database and
route guards shape the UI, but every request that reaches a handler is
authorized here as well - the org comes from the database via the verified
user id, never from anything the client can set.
"""

from __future__ import annotations

from fastapi import Depends, Header, Request

from app.core.config import Settings, get_settings
from app.core.errors import Forbidden, RateLimited, Unauthenticated
from app.core.logging import org_id_var
from app.core.ratelimit import get_rate_limiter
from app.core.security import AuthenticatedUser, decode_supabase_jwt, extract_bearer_token
from app.db.session import fetch_one


async def get_current_user(
    authorization: str | None = Header(default=None),
    settings: Settings = Depends(get_settings),
) -> AuthenticatedUser:
    token = extract_bearer_token(authorization)
    claims = decode_supabase_jwt(
        token,
        settings.supabase_jwt_secret,
        supabase_url=settings.supabase_url,
    )

    user_id = claims.get("sub")
    if not user_id:
        raise Unauthenticated("The access token is missing a subject claim.")

    # Tenancy is resolved server-side from the verified user id.
    profile = await fetch_one("SELECT id, org_id, email, role FROM profiles WHERE id = $1", user_id)
    if profile is None:
        raise Unauthenticated(
            "No profile exists for this account. Sign out and sign in again to provision one."
        )

    org_id_var.set(str(profile["org_id"]))
    return AuthenticatedUser(
        user_id=str(profile["id"]),
        email=profile["email"],
        org_id=str(profile["org_id"]),
        role=profile["role"],
    )


async def require_admin(
    user: AuthenticatedUser = Depends(get_current_user),
) -> AuthenticatedUser:
    if not user.is_admin:
        raise Forbidden(
            "This action requires an administrator or owner role.",
            code="ADMIN_REQUIRED",
        )
    return user


async def enforce_request_rate_limit(
    request: Request,
    user: AuthenticatedUser = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
) -> AuthenticatedUser:
    decision = await get_rate_limiter().check(
        key=f"requests:{user.org_id}",
        limit=settings.rate_limit_requests_per_minute,
        window_seconds=60,
    )
    if not decision.allowed:
        raise RateLimited(
            f"Your organization has exceeded {decision.limit} requests per minute.",
            retry_after=decision.retry_after_seconds,
        )
    request.state.rate_limit_remaining = decision.remaining
    return user


async def enforce_analysis_rate_limit(
    user: AuthenticatedUser = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
) -> AuthenticatedUser:
    decision = await get_rate_limiter().check(
        key=f"analyses:{user.org_id}",
        limit=settings.rate_limit_analyses_per_hour,
        window_seconds=3600,
    )
    if not decision.allowed:
        raise RateLimited(
            f"Your organization has reached the limit of {decision.limit} analyses per hour. "
            f"Try again in about {max(1, decision.retry_after_seconds // 60)} minute(s).",
            retry_after=decision.retry_after_seconds,
        )
    return user


def client_ip(request: Request) -> str | None:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else None
