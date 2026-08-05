"""Report download, status, and manual resend.

Authorization is server-side on every route: the analysis must belong to the
caller's organization, which is resolved from the verified JWT, never from the
request. The email recipient is likewise resolved from the authenticated
profile - there is deliberately no request field for it.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response

from app.api.deps import client_ip, enforce_request_rate_limit
from app.core.config import Settings, get_settings
from app.core.errors import AppError, NotFound, RateLimited, Unprocessable
from app.core.logging import get_logger
from app.core.ratelimit import get_rate_limiter
from app.core.security import AuthenticatedUser
from app.db.repositories.analyses import AnalysisRepository, FindingRepository
from app.db.repositories.documents import DocumentRepository
from app.db.repositories.policies import PolicyRepository
from app.db.repositories.reports import DeliveryRepository, ReportRepository
from app.providers.email.base import mask_email
from app.providers.storage.factory import build_report_storage_provider
from app.schemas.api import ReportStatusResponse
from app.services.audit import AuditAction, record_audit
from app.services.report_storage import ProviderStorageAdapter, build_download_filename

logger = get_logger(__name__)
router = APIRouter(tags=["reports"])

analyses = AnalysisRepository()
documents = DocumentRepository()
policies = PolicyRepository()
findings = FindingRepository()
reports = ReportRepository()
deliveries = DeliveryRepository()

TERMINAL_STATUSES = {"complete", "partial"}


async def _require_analysis(user: AuthenticatedUser, analysis_id: str) -> dict:
    """Load the analysis, scoped to the caller's organization.

    A cross-tenant id yields the same 404 as a non-existent one, so the response
    cannot be used to probe for existence.
    """
    analysis = await analyses.get(user.org_id, analysis_id)
    if analysis is None:
        raise NotFound("That analysis does not exist, or you do not have access to it.")
    return analysis


def _storage(
    settings: Settings, *, analysis_id: str = "", org_id: str = "", version: int = 1
) -> ProviderStorageAdapter:
    """Storage for report downloads, selected by AWS_REPORT_STORAGE_ENABLED."""
    return ProviderStorageAdapter(
        build_report_storage_provider(settings),
        analysis_id=analysis_id,
        org_id=org_id,
        version=version,
    )


@router.get("/analyses/{analysis_id}/report/status", response_model=ReportStatusResponse)
async def report_status(
    analysis_id: str,
    user: AuthenticatedUser = Depends(enforce_request_rate_limit),
    settings: Settings = Depends(get_settings),
):
    """Generation and email-delivery state for the analysis's latest report."""
    analysis = await _require_analysis(user, analysis_id)
    report = await reports.latest_for_analysis(user.org_id, analysis_id)
    delivery = await deliveries.latest_for_analysis(user.org_id, analysis_id)

    return ReportStatusResponse(
        analysis_id=analysis_id,
        analysis_status=str(analysis["status"]),
        report_available=bool(report and report.get("generation_status") == "ready"),
        generation_status=str(report["generation_status"]) if report else "pending",
        version=int(report["version"]) if report else None,
        file_size=report.get("file_size") if report else None,
        generated_at=report.get("generated_at") if report else None,
        email_status=str(delivery["status"]) if delivery else None,
        email_attempts=int(delivery["attempt_count"]) if delivery else 0,
        email_masked_recipient=str(delivery["recipient_masked"]) if delivery else None,
        email_sent_at=delivery.get("sent_at") if delivery else None,
        email_error=str(delivery["error_message_safe"])
        if delivery and delivery.get("error_message_safe")
        else None,
        can_resend=bool(report and report.get("generation_status") == "ready"),
        storage_provider=("s3" if settings.aws_report_storage_enabled else "supabase"),
        download_url_ttl_seconds=(
            settings.aws_report_url_ttl_seconds
            if settings.aws_report_storage_enabled
            else settings.report_signed_url_ttl_seconds
        ),
    )


@router.get("/analyses/{analysis_id}/report")
async def download_report(
    request: Request,
    analysis_id: str,
    user: AuthenticatedUser = Depends(enforce_request_rate_limit),
    settings: Settings = Depends(get_settings),
):
    """Stream the PDF. Never returns a permanent public URL."""
    analysis = await _require_analysis(user, analysis_id)
    report = await reports.latest_for_analysis(user.org_id, analysis_id)

    if report is None or report.get("generation_status") != "ready":
        raise Unprocessable(
            "The report is not ready yet. It is generated automatically once the analysis "
            "finishes; refresh in a moment.",
            code="REPORT_NOT_READY",
        )
    if not report.get("storage_path"):
        raise Unprocessable("The report has no stored file.", code="REPORT_MISSING_FILE")

    try:
        pdf = await _storage(settings).download(str(report["storage_path"]))
    except AppError:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "report download failed",
            extra={"analysis_id": analysis_id, "error_type": type(exc).__name__},
        )
        raise AppError(
            "The report could not be retrieved from storage.",
            code="REPORT_DOWNLOAD_FAILED",
            status_code=502,
        ) from exc

    document = await documents.get(user.org_id, str(analysis["document_id"])) or {}
    filename = build_download_filename(str(document.get("title") or "agreement"), analysis_id)

    await record_audit(
        org_id=user.org_id,
        actor_id=user.user_id,
        actor_email=user.email,
        action=AuditAction.REPORT_DOWNLOAD,
        resource_type="analysis_report",
        resource_id=str(report["id"]),
        request_id=getattr(request.state, "request_id", None),
        ip_address=client_ip(request),
        metadata={"analysis_id": analysis_id, "bytes": len(pdf)},
    )

    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Content-Length": str(len(pdf)),
            "Cache-Control": "private, no-store",
        },
    )


@router.post("/analyses/{analysis_id}/report/email", response_model=ReportStatusResponse)
async def resend_report_email(
    request: Request,
    analysis_id: str,
    user: AuthenticatedUser = Depends(enforce_request_rate_limit),
    settings: Settings = Depends(get_settings),
):
    """Re-send the report to the authenticated user's own verified address.

    There is no request body, and no recipient parameter. The address comes from
    the caller's profile, so one user can never mail a report to someone else.
    """
    analysis = await _require_analysis(user, analysis_id)

    if str(analysis["status"]) not in TERMINAL_STATUSES:
        raise Unprocessable(
            f"This analysis is {analysis['status']}. A report can only be emailed once the "
            "analysis has finished.",
            code="ANALYSIS_NOT_FINISHED",
        )

    decision = await get_rate_limiter().check(
        key=f"report-email:{user.org_id}",
        limit=settings.rate_limit_report_emails_per_hour,
        window_seconds=3600,
    )
    if not decision.allowed:
        raise RateLimited(
            f"Your organization has reached the limit of {decision.limit} report emails per hour.",
            retry_after=decision.retry_after_seconds,
        )

    report = await reports.latest_for_analysis(user.org_id, analysis_id)
    if report is None or report.get("generation_status") != "ready":
        raise Unprocessable(
            "The report is not ready yet, so it cannot be emailed.", code="REPORT_NOT_READY"
        )

    from app.providers.email.providers import build_email_provider
    from app.services.report_delivery import (
        ReportDeliveryService,
        build_report_context,
        severity_counts_from,
    )

    context = await build_report_context(
        analyses_repo=analyses,
        documents_repo=documents,
        policies_repo=policies,
        findings_repo=findings,
        org_id=user.org_id,
        analysis=analysis,
    )

    service = ReportDeliveryService(
        reports=reports,
        deliveries=deliveries,
        storage=_storage(settings),
        email_provider=build_email_provider(settings),
        max_attachment_bytes=int(settings.email_max_attachment_mb * 1024 * 1024),
        max_attempts=settings.email_max_attempts,
        signed_url_ttl=settings.report_signed_url_ttl_seconds,
    )

    outcome = await service.send_report_email(
        org_id=user.org_id,
        analysis_id=analysis_id,
        report=report,
        recipient_email=user.email,  # server-side identity, not request input
        recipient_user_id=user.user_id,
        document_title=str(context.document.get("title") or "Agreement"),
        analysis=dict(analysis),
        severity_counts=severity_counts_from(context.findings),
        allow_resend=True,
    )

    await record_audit(
        org_id=user.org_id,
        actor_id=user.user_id,
        actor_email=user.email,
        action=AuditAction.REPORT_EMAIL_RESEND,
        resource_type="analysis_report",
        resource_id=str(report["id"]),
        request_id=getattr(request.state, "request_id", None),
        ip_address=client_ip(request),
        metadata={
            "analysis_id": analysis_id,
            "sent": outcome.email_sent,
            "recipient_masked": mask_email(user.email),
        },
    )

    return await report_status(analysis_id, user, settings)  # type: ignore[arg-type]
