"""FastAPI application: middleware, error handling, and lifespan wiring."""

from __future__ import annotations

import time
import uuid
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app import __version__
from app.api.v1 import (
    action_items,
    analyses,
    chat,
    documents,
    findings,
    health,
    policies,
    reports,
    usage,
)
from app.core.config import get_settings
from app.core.errors import AppError, RateLimited
from app.core.logging import bind_request, configure_logging, get_logger, request_id_var
from app.core.ratelimit import RateLimiter, set_rate_limiter
from app.core.security import security_headers
from app.db.session import close_pool, init_pool

logger = get_logger(__name__)

_redis: Any = None
_queue: Any = None


def get_redis() -> Any:
    return _redis


def get_queue() -> Any:
    return _queue


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _redis, _queue

    settings = get_settings()
    configure_logging(settings.log_level)
    logger.info(
        "api starting",
        extra={
            "version": __version__,
            "environment": settings.environment,
            "model": settings.anthropic_model,
            "embedding_provider": settings.embedding_provider,
        },
    )

    if settings.sentry_dsn:
        import sentry_sdk

        sentry_sdk.init(
            dsn=settings.sentry_dsn,
            environment=settings.environment,
            traces_sample_rate=0.1,
            send_default_pii=False,  # never ship user content to the error tracker
        )

    if settings.is_test:
        # The automated suite runs entirely on in-memory fakes; connecting to
        # real infrastructure here would make the tests environment-dependent.
        logger.info("test environment: skipping database and redis initialisation")
        yield
        return

    await init_pool(settings.database_url)

    try:
        from app.jobs.queue import AnalysisQueue, build_api_redis

        # The API issues only short, non-blocking commands, so it keeps a
        # bounded read timeout. Only the worker's blocking consumer needs
        # socket_timeout=None.
        _redis = build_api_redis(settings.redis_url)
        await _redis.ping()
        _queue = AnalysisQueue(_redis)
        set_rate_limiter(RateLimiter(_redis))
        logger.info("redis connected")
    except Exception as exc:  # noqa: BLE001
        # The API stays up; readiness reports the degradation honestly.
        logger.error("redis unavailable", extra={"error_type": type(exc).__name__})
        _redis = None
        _queue = None

    yield

    logger.info("api shutting down")
    await close_pool()
    if _redis is not None:
        await _redis.aclose()


settings = get_settings()

app = FastAPI(
    title="ClauseGuard API",
    description=(
        "Automated EULA Compliance Extraction. Analyzes EULAs, terms of service, "
        "SaaS agreements, and vendor contracts for compliance-relevant clauses, with "
        "verified source evidence for every finding.\n\n"
        "**This tool provides information, not legal advice.**"
    ),
    version=__version__,
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,  # explicit allowlist; never "*" in production
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
    expose_headers=["X-Request-ID", "Retry-After"],
    max_age=600,
)


@app.middleware("http")
async def request_context(request: Request, call_next):
    """Assign a correlation id, time the request, and attach security headers."""
    request_id = request.headers.get("x-request-id") or str(uuid.uuid4())
    request.state.request_id = request_id
    bind_request(request_id)

    started = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        # Build the 500 here rather than re-raising. Starlette's
        # ServerErrorMiddleware sits OUTSIDE user middleware, so a re-raised
        # exception would produce a response that never passes back through
        # this function - and would therefore ship with no security headers at
        # all. Handling it here keeps every response, including failures,
        # covered by the headers applied below.
        logger.exception(
            "unhandled error",
            extra={"path": request.url.path, "method": request.method},
        )
        response = JSONResponse(
            status_code=500,
            content={
                "error": {
                    "code": "INTERNAL_ERROR",
                    "message": (
                        "An unexpected error occurred. Quote the request id if you report this."
                    ),
                    "request_id": request_id,
                }
            },
        )
    duration_ms = (time.perf_counter() - started) * 1000

    response.headers["X-Request-ID"] = request_id
    # The documentation routes need a CSP that permits the Swagger/ReDoc CDN
    # bundles; every other route keeps the strict application policy. See
    # app/core/security.py for the exact difference and why it is dev-only.
    for header, value in security_headers(
        settings.app_base_url,
        settings.api_base_url,
        production=settings.is_production,
        path=request.url.path,
    ).items():
        response.headers.setdefault(header, value)

    if not request.url.path.startswith("/health"):
        logger.info(
            "request",
            extra={
                "method": request.method,
                "path": request.url.path,
                "status": response.status_code,
                "duration_ms": round(duration_ms, 2),
            },
        )
    return response


# --- error handlers: one consistent envelope for every failure ---------------
@app.exception_handler(AppError)
async def handle_app_error(request: Request, exc: AppError):
    headers = {}
    if isinstance(exc, RateLimited):
        headers["Retry-After"] = str(exc.retry_after)
    return JSONResponse(
        status_code=exc.status_code,
        content=exc.envelope(request_id_var.get()),
        headers=headers,
    )


@app.exception_handler(RequestValidationError)
async def handle_validation_error(request: Request, exc: RequestValidationError):
    problems = [
        {"field": ".".join(str(p) for p in e["loc"][1:]) or "body", "message": e["msg"]}
        for e in exc.errors()[:10]
    ]
    return JSONResponse(
        status_code=400,
        content={
            "error": {
                "code": "INVALID_REQUEST",
                "message": "The request did not pass validation.",
                "request_id": request_id_var.get(),
                "details": {"fields": problems},
            }
        },
    )


@app.exception_handler(StarletteHTTPException)
async def handle_http_exception(request: Request, exc: StarletteHTTPException):
    codes = {
        401: "AUTHENTICATION_REQUIRED",
        403: "ACCESS_DENIED",
        404: "NOT_FOUND",
        405: "METHOD_NOT_ALLOWED",
        413: "FILE_TOO_LARGE",
        415: "UNSUPPORTED_FILE_TYPE",
    }
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": {
                "code": codes.get(exc.status_code, "HTTP_ERROR"),
                "message": str(exc.detail),
                "request_id": request_id_var.get(),
            }
        },
    )


@app.exception_handler(Exception)
async def handle_unexpected(request: Request, exc: Exception):
    # Never leak internal detail to the client; the request id ties it to the log.
    return JSONResponse(
        status_code=500,
        content={
            "error": {
                "code": "INTERNAL_ERROR",
                "message": "An unexpected error occurred. Quote the request id if you report this.",
                "request_id": request_id_var.get(),
            }
        },
    )


app.include_router(health.router)
app.include_router(chat.router, prefix="/api/v1")
app.include_router(documents.router, prefix="/api/v1")
app.include_router(policies.router, prefix="/api/v1")
app.include_router(analyses.router, prefix="/api/v1")
app.include_router(findings.router, prefix="/api/v1")
app.include_router(action_items.router, prefix="/api/v1")
app.include_router(reports.router, prefix="/api/v1")
app.include_router(usage.router, prefix="/api/v1")


@app.get("/", include_in_schema=False)
async def root():
    return {
        "name": "ClauseGuard",
        "topic": "Automated EULA Compliance Extraction",
        "version": __version__,
        "docs": "/docs",
        "disclaimer": "This tool provides information, not legal advice.",
    }
