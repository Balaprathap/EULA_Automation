"""Audit logging and usage accounting.

Both are append-only and deliberately store only *safe* metadata - never
document text, quotes, tokens, or keys. The redaction helper is a second line
of defence in case a caller passes something it should not.
"""

from __future__ import annotations

import json
from typing import Any

from app.core.logging import SENSITIVE_KEYS, get_logger
from app.db.session import execute

logger = get_logger(__name__)


class AuditAction:
    DOCUMENT_UPLOAD = "document.upload"
    DOCUMENT_UPDATE = "document.update"
    DOCUMENT_DELETE = "document.delete"
    ANALYSIS_CREATE = "analysis.create"
    ANALYSIS_COMPLETE = "analysis.complete"
    POLICY_CREATE = "policy.create"
    POLICY_UPDATE = "policy.update"
    POLICY_RULES_REPLACE = "policy.rules.replace"
    POLICY_VERSION_CREATE = "policy.version.create"
    POLICY_AI_DRAFT = "policy.ai_draft"
    FINDING_ACCEPT = "finding.accept"
    FINDING_DISMISS = "finding.dismiss"
    FINDING_ESCALATE = "finding.escalate"
    FINDING_OVERRIDE = "finding.override_severity"
    FINDING_NOTE = "finding.note"
    REPORT_GENERATE = "report.generate"
    REPORT_DOWNLOAD = "report.download"
    REPORT_EMAIL_SEND = "report.email.send"
    REPORT_EMAIL_RESEND = "report.email.resend"
    ACTION_ITEMS_GENERATE = "action_items.generate"
    ACTION_ITEM_UPDATE = "action_item.update"


def safe_metadata(metadata: dict[str, Any] | None) -> dict[str, Any]:
    """Strip anything sensitive and cap value sizes before persisting."""
    if not metadata:
        return {}
    clean: dict[str, Any] = {}
    for key, value in metadata.items():
        if str(key).lower() in SENSITIVE_KEYS:
            continue
        if isinstance(value, str):
            clean[key] = value[:200]
        elif isinstance(value, (int, float, bool)) or value is None:
            clean[key] = value
        else:
            clean[key] = str(value)[:200]
    return clean


async def record_audit(
    *,
    org_id: str,
    action: str,
    resource_type: str,
    resource_id: str | None = None,
    actor_id: str | None = None,
    actor_email: str | None = None,
    request_id: str | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    try:
        await execute(
            """
            INSERT INTO audit_logs
                (org_id, actor_id, actor_email, action, resource_type, resource_id,
                 request_id, ip_address, user_agent, metadata)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10::jsonb)
            """,
            org_id,
            actor_id,
            actor_email,
            action,
            resource_type,
            resource_id,
            request_id,
            ip_address,
            (user_agent or "")[:500] or None,
            json.dumps(safe_metadata(metadata)),
        )
    except Exception as exc:  # noqa: BLE001 - auditing must not break the request
        logger.error(
            "audit write failed", extra={"action": action, "error_type": type(exc).__name__}
        )


async def record_usage(
    *,
    org_id: str,
    event_type: str,
    analysis_id: str | None = None,
    actor_id: str | None = None,
    provider: str | None = None,
    model: str | None = None,
    input_tokens: int = 0,
    cached_input_tokens: int = 0,
    output_tokens: int = 0,
    estimated_cost_usd: float = 0.0,
    duration_ms: float | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    try:
        await execute(
            """
            INSERT INTO usage_events
                (org_id, analysis_id, actor_id, event_type, provider, model,
                 input_tokens, cached_input_tokens, output_tokens,
                 estimated_cost_usd, duration_ms, metadata)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12::jsonb)
            """,
            org_id,
            analysis_id,
            actor_id,
            event_type,
            provider,
            model,
            input_tokens,
            cached_input_tokens,
            output_tokens,
            estimated_cost_usd,
            duration_ms,
            json.dumps(safe_metadata(metadata)),
        )
    except Exception as exc:  # noqa: BLE001
        logger.error("usage write failed", extra={"error_type": type(exc).__name__})
