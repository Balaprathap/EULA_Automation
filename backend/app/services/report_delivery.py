"""Orchestrates report generation, private storage, and email delivery.

Sequence, matching the requested design:

    analysis complete/partial
      -> generate report
      -> store private PDF
      -> record report status
      -> send email
      -> record delivery status

Two guarantees:

  * **Email failure never changes analysis status.** Nothing in this module
    writes to `analyses`. The caller invokes it after the analysis has already
    been finalised, inside its own try/except.
  * **A worker retry cannot send a duplicate.** The delivery row is claimed via
    a partial unique index on (analysis_id, report_id, recipient_email_hash);
    a second attempt gets None back and stops.

The recipient is always resolved server-side from the profile that requested the
analysis. No caller can supply an address.
"""

from __future__ import annotations

import asyncio
import html
import random
from dataclasses import dataclass
from typing import Protocol

from app.core.logging import analysis_id_var, get_logger
from app.db.repositories.reports import DeliveryRepository, ReportRepository
from app.db.session import fetch_one
from app.providers.email.base import Attachment, EmailMessage, EmailProvider, hash_email, mask_email
from app.services.report_generator import ReportContext, ReportGenerator, checksum_of
from app.services.report_storage import build_download_filename, build_report_key

logger = get_logger(__name__)

RETRY_BASE_SECONDS = 2.0


class ReportStore(Protocol):
    """Whatever backs report bytes - Supabase or S3, via ProviderStorageAdapter."""

    async def upload(self, key: str, pdf_bytes: bytes) -> str: ...

    async def signed_url(self, key: str, ttl: int) -> str: ...

    async def download(self, key: str) -> bytes: ...


@dataclass
class DeliveryOutcome:
    report_id: str | None = None
    storage_path: str | None = None
    generated: bool = False
    email_attempted: bool = False
    email_sent: bool = False
    skipped_reason: str | None = None


async def resolve_recipient(user_id: str | None) -> tuple[str | None, str | None]:
    """Resolve the verified email from the authenticated profile, server-side.

    Returns (email, user_id). Never accepts an address from a request body.
    """
    if not user_id:
        return None, None
    row = await fetch_one("SELECT id, email FROM profiles WHERE id = $1", user_id)
    if row is None or not row.get("email"):
        return None, None
    return str(row["email"]), str(row["id"])


def build_email_bodies(
    *,
    document_title: str,
    analysis: dict,
    severity_counts: dict[str, int],
    download_url: str | None,
    attached: bool,
) -> tuple[str, str]:
    """Plain-text and HTML bodies. All interpolated values are HTML-escaped."""
    status = str(analysis.get("status") or "complete")
    score = analysis.get("overall_score")
    band = analysis.get("risk_band") or "not scored"

    status_line = (
        "The analysis completed in full."
        if status == "complete"
        else "The analysis completed partially - some categories need human review."
    )
    counts_line = ", ".join(
        f"{severity}: {severity_counts.get(severity, 0)}"
        for severity in ("critical", "high", "medium", "low", "info")
    )
    access_line = (
        "Your report is attached to this email."
        if attached
        else "Your report is available through the secure link below. The link expires shortly."
    )

    disclaimer = (
        "Not legal advice. ClauseGuard highlights clauses that may be relevant to compliance "
        "review. It is an aid to human judgement, not a substitute for a qualified lawyer."
    )

    text = f"""ClauseGuard analysis complete

Document: {document_title}
{status_line}

Overall risk score: {score if score is not None else "not scored"} / 100 ({band})
Findings by severity: {counts_line}

{access_line}
{download_url or ""}

{disclaimer}
"""

    # Everything interpolated below is escaped; there is no raw user content.
    e = html.escape
    link_block = (
        f'<p><a href="{e(download_url or "", quote=True)}">Download your report</a> '
        f"(link expires shortly)</p>"
        if download_url and not attached
        else "<p>Your report is attached to this email.</p>"
    )
    html_body = f"""<!doctype html>
<html><body style="font-family:system-ui,-apple-system,Segoe UI,sans-serif;color:#0f172a;">
  <h2 style="margin-bottom:4px;">ClauseGuard analysis complete</h2>
  <p style="color:#475569;margin-top:0;">{e(document_title)}</p>
  <p>{e(status_line)}</p>
  <p><strong>Overall risk score:</strong>
     {e(str(score) if score is not None else "not scored")} / 100 ({e(str(band))})</p>
  <p><strong>Findings by severity:</strong> {e(counts_line)}</p>
  {link_block}
  <hr style="border:none;border-top:1px solid #e2e8f0;margin:20px 0;">
  <p style="font-size:12px;color:#475569;">{e(disclaimer)}</p>
</body></html>"""
    return text, html_body


class ReportDeliveryService:
    def __init__(
        self,
        *,
        reports: ReportRepository,
        deliveries: DeliveryRepository,
        storage: ReportStore,
        email_provider: EmailProvider,
        generator: ReportGenerator | None = None,
        max_attachment_bytes: int = 8 * 1024 * 1024,
        max_attempts: int = 3,
        signed_url_ttl: int = 900,
    ) -> None:
        self.reports = reports
        self.deliveries = deliveries
        self.storage = storage
        self.email = email_provider
        self.generator = generator or ReportGenerator()
        self.max_attachment_bytes = max_attachment_bytes
        self.max_attempts = max_attempts
        self.signed_url_ttl = signed_url_ttl

    # -- generation -------------------------------------------------------
    async def generate_and_store(
        self, *, org_id: str, analysis_id: str, context: ReportContext, version: int = 1
    ) -> dict:
        report = await self.reports.upsert_pending(org_id, analysis_id, version)

        # Already produced by an earlier attempt - do not regenerate.
        if report.get("generation_status") == "ready" and report.get("storage_path"):
            logger.info("report already generated", extra={"analysis_id": analysis_id})
            return report

        try:
            pdf = self.generator.generate(context)
            key = build_report_key(org_id, analysis_id, version)
            await self.storage.upload(key, pdf)
            updated = await self.reports.mark_ready(
                str(report["id"]),
                storage_path=key,
                file_size=len(pdf),
                checksum=checksum_of(pdf),
            )
            logger.info(
                "report generated",
                extra={"analysis_id": analysis_id, "bytes": len(pdf), "version": version},
            )
            return updated or report
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "report generation failed",
                extra={"analysis_id": analysis_id, "error_type": type(exc).__name__},
            )
            await self.reports.mark_failed(
                str(report["id"]), "REPORT_GENERATION_FAILED", f"{type(exc).__name__}"
            )
            raise

    # -- email ------------------------------------------------------------
    async def send_report_email(
        self,
        *,
        org_id: str,
        analysis_id: str,
        report: dict,
        recipient_email: str,
        recipient_user_id: str | None,
        document_title: str,
        analysis: dict,
        severity_counts: dict[str, int],
        allow_resend: bool = False,
    ) -> DeliveryOutcome:
        outcome = DeliveryOutcome(
            report_id=str(report.get("id")), storage_path=report.get("storage_path")
        )
        email_hash = hash_email(recipient_email)
        masked = mask_email(recipient_email)

        delivery = await self.deliveries.claim(
            org_id=org_id,
            report_id=str(report["id"]),
            analysis_id=analysis_id,
            recipient_user_id=recipient_user_id,
            recipient_email_hash=email_hash,
            recipient_masked=masked,
        )

        if delivery is None:
            if not allow_resend:
                # A pending/sending/sent row already exists: this is a retry.
                logger.info(
                    "duplicate email suppressed",
                    extra={"analysis_id": analysis_id, "recipient_masked": masked},
                )
                outcome.skipped_reason = "already_delivered_or_in_flight"
                return outcome
            existing = await self.deliveries.latest_for_analysis(org_id, analysis_id)
            if existing is None:
                outcome.skipped_reason = "no_delivery_row"
                return outcome
            await self.deliveries.reopen_for_resend(str(existing["id"]))
            delivery = existing

        outcome.email_attempted = True
        delivery_id = str(delivery["id"])

        pdf_bytes: bytes | None = None
        download_url: str | None = None
        try:
            pdf_bytes = await self.storage.download(str(report["storage_path"]))
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "could not attach report; falling back to a link",
                extra={"analysis_id": analysis_id, "error_type": type(exc).__name__},
            )

        attach = pdf_bytes is not None and len(pdf_bytes) <= self.max_attachment_bytes
        if not attach:
            try:
                download_url = await self.storage.signed_url(
                    str(report["storage_path"]), self.signed_url_ttl
                )
            except Exception as exc:  # noqa: BLE001
                logger.error(
                    "could not mint a download link",
                    extra={"analysis_id": analysis_id, "error_type": type(exc).__name__},
                )

        text_body, html_body = build_email_bodies(
            document_title=document_title,
            analysis=analysis,
            severity_counts=severity_counts,
            download_url=download_url,
            attached=attach,
        )

        message = EmailMessage(
            to=recipient_email,
            subject=f"ClauseGuard analysis complete - {document_title}",
            text_body=text_body,
            html_body=html_body,
            attachments=(
                [
                    Attachment(
                        filename=build_download_filename(document_title, analysis_id),
                        content=pdf_bytes,
                    )
                ]
                if attach and pdf_bytes is not None
                else None
            ),
        )

        last_error = ("UNKNOWN", "The email could not be sent.")
        for attempt in range(1, self.max_attempts + 1):
            await self.deliveries.mark_sending(delivery_id)
            result = await self.email.send(message)
            if result.ok:
                await self.deliveries.mark_sent(
                    delivery_id,
                    provider=result.provider,
                    message_id=result.message_id,
                    mode="attachment" if attach else "link",
                )
                logger.info(
                    "report email sent",
                    extra={
                        "analysis_id": analysis_id,
                        "recipient_masked": masked,
                        "provider": result.provider,
                        "attempt": attempt,
                        "mode": "attachment" if attach else "link",
                    },
                )
                outcome.email_sent = True
                return outcome

            last_error = (
                result.error_code or "SEND_FAILED",
                result.error_message_safe or "The email could not be sent.",
            )
            if attempt < self.max_attempts:
                delay = RETRY_BASE_SECONDS * (2 ** (attempt - 1))
                delay += random.uniform(0, delay * 0.25)  # noqa: S311 - jitter, not crypto
                logger.warning(
                    "email retry",
                    extra={
                        "analysis_id": analysis_id,
                        "attempt": attempt,
                        "retry_in_s": round(delay, 2),
                    },
                )
                await asyncio.sleep(delay)

        await self.deliveries.mark_failed(
            delivery_id,
            provider=self.email.name,
            code=last_error[0],
            message=last_error[1],
            permanent=True,
        )
        logger.error(
            "report email permanently failed",
            extra={
                "analysis_id": analysis_id,
                "recipient_masked": masked,
                "attempts": self.max_attempts,
            },
        )
        return outcome


async def build_report_context(
    *, analyses_repo, documents_repo, policies_repo, findings_repo, org_id: str, analysis: dict
) -> ReportContext:
    """Assemble the report from rows the pipeline already persisted."""
    analysis_id = str(analysis["id"])
    analysis_id_var.set(analysis_id)

    document = await documents_repo.get(org_id, str(analysis["document_id"])) or {}
    policy = await policies_repo.get(org_id, str(analysis["policy_id"]))
    findings = await findings_repo.list_for_analysis(org_id, analysis_id, include_quarantined=True)
    categories = await analyses_repo.list_categories(analysis_id)

    return ReportContext(
        analysis=dict(analysis),
        document=dict(document),
        policy=dict(policy) if policy else None,
        findings=[dict(f) for f in findings],
        categories=[dict(c) for c in categories],
    )


def severity_counts_from(findings: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for finding in findings:
        if finding.get("verification_status") != "verified":
            continue
        key = str(finding.get("effective_severity") or finding.get("machine_severity") or "info")
        counts[key] = counts.get(key, 0) + 1
    return counts
