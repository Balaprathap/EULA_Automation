"""Structured JSON logging with request correlation and secret redaction.

Hard rules enforced here:
  * Never log full document text.
  * Never log complete evidence quotes.
  * Never log passwords, API keys, JWTs, refresh tokens, or auth headers.
"""

from __future__ import annotations

import contextvars
import json
import logging
import re
import sys
from typing import Any

request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")
analysis_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("analysis_id", default="-")
org_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("org_id", default="-")

REDACTED = "[REDACTED]"

# Keys whose values must never reach a log sink, at any nesting depth.
SENSITIVE_KEYS = frozenset(
    {
        "password",
        "passwd",
        "secret",
        "token",
        "access_token",
        "refresh_token",
        "id_token",
        "jwt",
        "authorization",
        "api_key",
        "apikey",
        "anthropic_api_key",
        "groq_api_key",
        "embedding_api_key",
        "supabase_service_role_key",
        "supabase_jwt_secret",
        "service_role_key",
        "cookie",
        "set-cookie",
        "x-api-key",
        # Content fields: text bodies and verbatim quotes are never logged.
        "text",
        "raw_text",
        "normalized_text",
        "document_text",
        "chunk_text",
        "quote",
        "evidence",
        "content",
    }
)

_TOKEN_PATTERNS = [
    re.compile(r"\bBearer\s+[A-Za-z0-9._\-]+", re.IGNORECASE),
    re.compile(r"\bey[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{5,}"),
    re.compile(r"\bsk-[A-Za-z0-9_\-]{16,}"),
    re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{16,}"),
]

MAX_LOGGED_STRING = 200


def scrub_string(value: str) -> str:
    for pattern in _TOKEN_PATTERNS:
        value = pattern.sub(REDACTED, value)
    if len(value) > MAX_LOGGED_STRING:
        value = value[:MAX_LOGGED_STRING] + f"...[truncated {len(value) - MAX_LOGGED_STRING} chars]"
    return value


def redact(value: Any, _depth: int = 0) -> Any:
    """Recursively redact sensitive keys and token-shaped strings."""
    if _depth > 6:
        return "[MAX_DEPTH]"
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for k, v in value.items():
            if str(k).lower() in SENSITIVE_KEYS:
                out[k] = REDACTED
            else:
                out[k] = redact(v, _depth + 1)
        return out
    if isinstance(value, (list, tuple)):
        return [redact(v, _depth + 1) for v in value][:50]
    if isinstance(value, str):
        return scrub_string(value)
    return value


class JsonFormatter(logging.Formatter):
    RESERVED = frozenset(vars(logging.LogRecord("", 0, "", 0, "", (), None)).keys()) | {
        "message",
        "asctime",
        "taskName",
    }

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": scrub_string(record.getMessage()),
            "request_id": request_id_var.get(),
        }
        if analysis_id_var.get() != "-":
            payload["analysis_id"] = analysis_id_var.get()
        if org_id_var.get() != "-":
            payload["org_id"] = org_id_var.get()

        extras = {k: v for k, v in record.__dict__.items() if k not in self.RESERVED}
        if extras:
            payload.update(redact(extras))

        if record.exc_info:
            payload["exception"] = scrub_string(self.formatException(record.exc_info))

        return json.dumps(payload, default=str)


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level.upper())
    for noisy in ("uvicorn.access", "httpx", "httpcore", "anthropic"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


def bind_request(request_id: str, org_id: str | None = None) -> None:
    request_id_var.set(request_id)
    if org_id:
        org_id_var.set(org_id)
