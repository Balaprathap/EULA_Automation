"""Liveness and readiness probes. Unauthenticated by design."""

from __future__ import annotations

from fastapi import APIRouter, Response, status

from app import __version__
from app.core.config import get_settings
from app.db.session import health_check

router = APIRouter(tags=["health"])


@router.get("/health")
async def health():
    """Liveness: the process is up. Never touches dependencies."""
    settings = get_settings()
    return {
        "status": "ok",
        "version": __version__,
        "environment": settings.environment,
    }


@router.get("/health/ready")
async def readiness(response: Response):
    """Readiness: every dependency this instance needs is reachable."""
    from app.main import get_redis

    database_ok = await health_check()

    redis_ok = False
    try:
        redis = get_redis()
        if redis is not None:
            await redis.ping()
            redis_ok = True
    except Exception:  # noqa: BLE001
        redis_ok = False

    checks = {"database": database_ok, "redis": redis_ok}
    ready = all(checks.values())
    if not ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return {"status": "ready" if ready else "not_ready", "checks": checks}
