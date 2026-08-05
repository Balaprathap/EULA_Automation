"""Persistence for generated reports and their email deliveries.

Both tables are additive. Nothing here reads or writes analysis or findings
state, so report and email failures are structurally unable to change an
analysis's status.
"""

from __future__ import annotations

from typing import Any

from app.db.session import execute, fetch_all, fetch_one

REPORT_COLUMNS = """
    id, org_id, analysis_id, storage_path, version, generation_status,
    generated_at, file_size, checksum, error_code, error_message, created_at, updated_at
"""

DELIVERY_COLUMNS = """
    id, org_id, report_id, analysis_id, recipient_user_id, recipient_masked,
    status, attempt_count, provider, provider_message_id, error_code,
    error_message_safe, delivery_mode, sent_at, created_at, updated_at
"""


class ReportRepository:
    async def upsert_pending(self, org_id: str, analysis_id: str, version: int = 1) -> dict:
        """Create the report row, or return the existing one for this version.

        A worker retry hits the ON CONFLICT branch and re-uses the same row, so a
        second report is never produced for the same analysis version.
        """
        row = await fetch_one(
            f"""
            INSERT INTO analysis_reports (org_id, analysis_id, version, generation_status)
            VALUES ($1, $2, $3, 'generating')
            ON CONFLICT (analysis_id, version) DO UPDATE
                SET generation_status = CASE
                        WHEN analysis_reports.generation_status = 'ready' THEN 'ready'
                        ELSE 'generating'
                    END
            RETURNING {REPORT_COLUMNS}
            """,
            org_id,
            analysis_id,
            version,
        )
        if row is None:
            raise RuntimeError("The report INSERT returned no row.")
        return row

    async def mark_ready(
        self, report_id: str, *, storage_path: str, file_size: int, checksum: str
    ) -> dict | None:
        return await fetch_one(
            f"""
            UPDATE analysis_reports
            SET generation_status = 'ready', storage_path = $2, file_size = $3,
                checksum = $4, generated_at = NOW(), error_code = NULL, error_message = NULL
            WHERE id = $1
            RETURNING {REPORT_COLUMNS}
            """,
            report_id,
            storage_path,
            file_size,
            checksum,
        )

    async def mark_failed(self, report_id: str, code: str, message: str) -> None:
        await execute(
            "UPDATE analysis_reports SET generation_status = 'failed', error_code = $2, "
            "error_message = $3 WHERE id = $1",
            report_id,
            code,
            message[:1000],
        )

    async def latest_for_analysis(self, org_id: str, analysis_id: str) -> dict | None:
        return await fetch_one(
            f"SELECT {REPORT_COLUMNS} FROM analysis_reports "
            "WHERE analysis_id = $1 AND org_id = $2 ORDER BY version DESC LIMIT 1",
            analysis_id,
            org_id,
        )

    async def latest_unscoped(self, analysis_id: str) -> dict | None:
        """Worker-side lookup; the worker already owns the job's organization."""
        return await fetch_one(
            f"SELECT {REPORT_COLUMNS} FROM analysis_reports "
            "WHERE analysis_id = $1 ORDER BY version DESC LIMIT 1",
            analysis_id,
        )

    async def next_version(self, analysis_id: str) -> int:
        value = await fetch_one(
            "SELECT COALESCE(MAX(version), 0) + 1 AS next FROM analysis_reports "
            "WHERE analysis_id = $1",
            analysis_id,
        )
        return int((value or {}).get("next") or 1)


class DeliveryRepository:
    async def claim(
        self,
        *,
        org_id: str,
        report_id: str,
        analysis_id: str,
        recipient_user_id: str | None,
        recipient_email_hash: str,
        recipient_masked: str,
    ) -> dict | None:
        """Reserve a delivery slot.

        Returns None when a pending, sending, or sent row already exists for this
        (analysis, report, recipient) - the partial unique index makes duplicate
        sends impossible at the database level rather than by application logic.
        """
        return await fetch_one(
            f"""
            INSERT INTO report_deliveries
                (org_id, report_id, analysis_id, recipient_user_id,
                 recipient_email_hash, recipient_masked, status)
            VALUES ($1,$2,$3,$4,$5,$6,'pending')
            ON CONFLICT DO NOTHING
            RETURNING {DELIVERY_COLUMNS}
            """,
            org_id,
            report_id,
            analysis_id,
            recipient_user_id,
            recipient_email_hash,
            recipient_masked,
        )

    async def mark_sending(self, delivery_id: str) -> None:
        await execute(
            "UPDATE report_deliveries SET status = 'sending', "
            "attempt_count = attempt_count + 1 WHERE id = $1",
            delivery_id,
        )

    async def mark_sent(
        self, delivery_id: str, *, provider: str, message_id: str | None, mode: str
    ) -> None:
        await execute(
            "UPDATE report_deliveries SET status = 'sent', provider = $2, "
            "provider_message_id = $3, delivery_mode = $4, sent_at = NOW(), "
            "error_code = NULL, error_message_safe = NULL WHERE id = $1",
            delivery_id,
            provider,
            message_id,
            mode,
        )

    async def mark_failed(
        self, delivery_id: str, *, provider: str, code: str | None, message: str, permanent: bool
    ) -> None:
        await execute(
            "UPDATE report_deliveries SET status = $2, provider = $3, error_code = $4, "
            "error_message_safe = $5 WHERE id = $1",
            delivery_id,
            "permanently_failed" if permanent else "failed",
            provider,
            code,
            message[:500],
        )

    async def latest_for_analysis(self, org_id: str, analysis_id: str) -> dict | None:
        return await fetch_one(
            f"SELECT {DELIVERY_COLUMNS} FROM report_deliveries "
            "WHERE analysis_id = $1 AND org_id = $2 ORDER BY created_at DESC LIMIT 1",
            analysis_id,
            org_id,
        )

    async def list_for_analysis(self, org_id: str, analysis_id: str) -> list[dict[str, Any]]:
        return await fetch_all(
            f"SELECT {DELIVERY_COLUMNS} FROM report_deliveries "
            "WHERE analysis_id = $1 AND org_id = $2 ORDER BY created_at DESC",
            analysis_id,
            org_id,
        )

    async def reopen_for_resend(self, delivery_id: str) -> None:
        """Move a failed row back to pending so a manual resend can retry it."""
        await execute(
            "UPDATE report_deliveries SET status = 'pending', error_code = NULL, "
            "error_message_safe = NULL WHERE id = $1 AND status IN ('failed','permanently_failed')",
            delivery_id,
        )
