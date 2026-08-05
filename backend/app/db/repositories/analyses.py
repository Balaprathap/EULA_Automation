"""Analysis, finding, evidence, and review persistence."""

from __future__ import annotations

import builtins
import json
from typing import Any

from app.db.session import execute, fetch_all, fetch_one, fetch_value, get_pool

ANALYSIS_COLUMNS = """
    id, org_id, document_id, policy_id, requested_by, status, stage, progress_message,
    categories_total, categories_completed, completed_categories, overall_score, risk_band,
    finding_count, review_count, quarantine_count, verification_pass_rate, executive_summary,
    degraded_retrieval, degraded_reason, stage_timings_ms, input_tokens, cached_input_tokens,
    output_tokens, estimated_cost_usd, model_used, error_code, error_message, attempt_count,
    worker_id, heartbeat_at, queued_at, started_at, completed_at, created_at, updated_at
"""

FINDING_COLUMNS = """
    id, org_id, analysis_id, document_id, policy_rule_id, chunk_id, category,
    plain_summary, why_it_matters, model_confidence, severity_weight, confidence_threshold,
    weighted_risk, machine_severity, severity_source, scoring_explanation, override_severity,
    review_status, reviewed_by, reviewed_at, verification_status, quarantine_reason,
    degraded_retrieval, created_at, updated_at
"""

# Same columns, table-qualified for the joined findings query.
FINDING_COLUMNS_F = ", ".join(
    f"f.{c.strip()}" for c in FINDING_COLUMNS.replace("\n", " ").split(",") if c.strip()
)


class AnalysisRepository:
    async def create(
        self,
        *,
        org_id: str,
        document_id: str,
        policy_id: str,
        requested_by: str | None,
        categories_total: int,
        idempotency_key: str | None = None,
    ) -> dict | None:
        """Insert a queued analysis.

        Returns None when a live analysis for this document+policy already
        exists - the partial unique index makes duplicate submission a no-op
        rather than a second billed run.
        """
        return await fetch_one(
            f"""
            INSERT INTO analyses
                (org_id, document_id, policy_id, requested_by, categories_total, idempotency_key)
            VALUES ($1,$2,$3,$4,$5,$6)
            ON CONFLICT DO NOTHING
            RETURNING {ANALYSIS_COLUMNS}
            """,
            org_id,
            document_id,
            policy_id,
            requested_by,
            categories_total,
            idempotency_key,
        )

    async def get(self, org_id: str, analysis_id: str) -> dict | None:
        return await fetch_one(
            f"SELECT {ANALYSIS_COLUMNS} FROM analyses WHERE id = $1 AND org_id = $2",
            analysis_id,
            org_id,
        )

    async def get_unscoped(self, analysis_id: str) -> dict | None:
        """Worker-only lookup - the worker owns the job before a user context exists."""
        return await fetch_one(
            f"SELECT {ANALYSIS_COLUMNS} FROM analyses WHERE id = $1", analysis_id
        )

    async def active_for_document(self, org_id: str, document_id: str, policy_id: str):
        return await fetch_one(
            f"SELECT {ANALYSIS_COLUMNS} FROM analyses "
            "WHERE org_id = $1 AND document_id = $2 AND policy_id = $3 "
            "AND status IN ('queued','running')",
            org_id,
            document_id,
            policy_id,
        )

    async def list(self, org_id: str, *, limit: int = 20, offset: int = 0) -> dict[str, Any]:
        total = await fetch_value("SELECT COUNT(*) FROM analyses WHERE org_id = $1", org_id)
        items = await fetch_all(
            f"SELECT {ANALYSIS_COLUMNS} FROM analyses WHERE org_id = $1 "
            "ORDER BY created_at DESC LIMIT $2 OFFSET $3",
            org_id,
            limit,
            offset,
        )
        return {"items": items, "total": total or 0, "limit": limit, "offset": offset}

    async def claim(self, analysis_id: str, worker_id: str) -> dict | None:
        """Atomically take ownership of a queued or stalled analysis.

        The status guard makes claiming idempotent under concurrency: two
        workers racing on the same job means exactly one UPDATE matches.
        """
        return await fetch_one(
            f"""
            UPDATE analyses
            SET status = 'running',
                stage = CASE WHEN stage = 'queued' THEN 'parsing' ELSE stage END,
                worker_id = $2,
                heartbeat_at = NOW(),
                started_at = COALESCE(started_at, NOW()),
                attempt_count = attempt_count + 1
            WHERE id = $1
              AND (status = 'queued'
                   OR (status = 'running' AND heartbeat_at < NOW() - INTERVAL '5 minutes'))
            RETURNING {ANALYSIS_COLUMNS}
            """,
            analysis_id,
            worker_id,
        )

    async def heartbeat(self, analysis_id: str, worker_id: str) -> None:
        await execute(
            "UPDATE analyses SET heartbeat_at = NOW() WHERE id = $1 AND worker_id = $2",
            analysis_id,
            worker_id,
        )

    async def update_stage(
        self,
        analysis_id: str,
        stage: str,
        *,
        message: str | None = None,
        completed: int | None = None,
    ) -> None:
        await execute(
            """
            UPDATE analyses
            SET stage = $2,
                progress_message = COALESCE($3, progress_message),
                categories_completed = COALESCE($4, categories_completed),
                heartbeat_at = NOW()
            WHERE id = $1
            """,
            analysis_id,
            stage,
            message,
            completed,
        )

    async def mark_category_complete(self, analysis_id: str, category: str) -> None:
        """Record a finished category so a restarted worker can skip it."""
        await execute(
            """
            UPDATE analyses
            SET completed_categories = (
                    SELECT ARRAY(SELECT DISTINCT unnest(completed_categories || ARRAY[$2]))
                ),
                categories_completed = (
                    SELECT COUNT(DISTINCT c)
                    FROM unnest(completed_categories || ARRAY[$2]) AS c
                ),
                heartbeat_at = NOW()
            WHERE id = $1
            """,
            analysis_id,
            category,
        )

    async def record_usage(
        self,
        analysis_id: str,
        *,
        input_tokens: int,
        cached_tokens: int,
        output_tokens: int,
        cost: float,
    ) -> None:
        await execute(
            """
            UPDATE analyses
            SET input_tokens = input_tokens + $2,
                cached_input_tokens = cached_input_tokens + $3,
                output_tokens = output_tokens + $4,
                estimated_cost_usd = estimated_cost_usd + $5
            WHERE id = $1
            """,
            analysis_id,
            input_tokens,
            cached_tokens,
            output_tokens,
            cost,
        )

    async def complete(self, analysis_id: str, **fields) -> dict | None:
        return await fetch_one(
            f"""
            UPDATE analyses
            SET status = $2, stage = $3, overall_score = $4, risk_band = $5,
                finding_count = $6, review_count = $7, quarantine_count = $8,
                verification_pass_rate = $9, executive_summary = $10,
                degraded_retrieval = $11, degraded_reason = $12,
                stage_timings_ms = $13::jsonb, model_used = $14,
                progress_message = $15, completed_at = NOW()
            WHERE id = $1
            RETURNING {ANALYSIS_COLUMNS}
            """,
            analysis_id,
            fields["status"],
            fields.get("stage", "complete"),
            fields.get("overall_score"),
            fields.get("risk_band"),
            fields.get("finding_count", 0),
            fields.get("review_count", 0),
            fields.get("quarantine_count", 0),
            fields.get("verification_pass_rate"),
            fields.get("executive_summary"),
            fields.get("degraded_retrieval", False),
            fields.get("degraded_reason"),
            json.dumps(fields.get("stage_timings_ms") or {}),
            fields.get("model_used"),
            fields.get("progress_message"),
        )

    async def fail(self, analysis_id: str, code: str, message: str) -> None:
        await execute(
            "UPDATE analyses SET status = 'failed', stage = 'failed', "
            "error_code = $2, error_message = $3, completed_at = NOW() WHERE id = $1",
            analysis_id,
            code,
            message[:2000],
        )

    async def upsert_category(self, org_id: str, analysis_id: str, **fields) -> None:
        await execute(
            """
            INSERT INTO analysis_categories
                (org_id, analysis_id, category, status, needs_review_reason, error_code,
                 retrieval_mode, degraded_reason, tool_calls, duration_ms,
                 input_tokens, output_tokens)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12)
            ON CONFLICT (analysis_id, category) DO UPDATE SET
                status = EXCLUDED.status,
                needs_review_reason = EXCLUDED.needs_review_reason,
                error_code = EXCLUDED.error_code,
                retrieval_mode = EXCLUDED.retrieval_mode,
                degraded_reason = EXCLUDED.degraded_reason,
                tool_calls = EXCLUDED.tool_calls,
                duration_ms = EXCLUDED.duration_ms
            """,
            org_id,
            analysis_id,
            fields["category"],
            fields["status"],
            fields.get("needs_review_reason"),
            fields.get("error_code"),
            fields.get("retrieval_mode"),
            fields.get("degraded_reason"),
            fields.get("tool_calls", 0),
            fields.get("duration_ms"),
            fields.get("input_tokens", 0),
            fields.get("output_tokens", 0),
        )

    async def list_categories(self, analysis_id: str) -> builtins.list[dict]:
        return await fetch_all(
            "SELECT category, status, needs_review_reason, error_code, retrieval_mode, "
            "degraded_reason, tool_calls, duration_ms FROM analysis_categories "
            "WHERE analysis_id = $1 ORDER BY category",
            analysis_id,
        )

    async def recover_stalled(self, older_than_minutes: int = 5) -> builtins.list[dict]:
        """Requeue analyses whose worker stopped heartbeating."""
        return await fetch_all(
            """
            UPDATE analyses
            SET status = 'queued', worker_id = NULL,
                progress_message = 'Requeued after the previous worker stopped responding.'
            WHERE status = 'running'
              AND heartbeat_at < NOW() - ($1 || ' minutes')::interval
              AND attempt_count < 3
            RETURNING id, org_id, document_id, policy_id
            """,
            str(older_than_minutes),
        )


class FindingRepository:
    async def create_with_evidence(
        self, *, finding: dict[str, Any], evidence: dict[str, Any] | None
    ) -> dict[str, Any]:
        """Persist a finding and, when verification succeeded, its evidence.

        Written in one transaction so a verified finding can never exist without
        the evidence row that justifies it.
        """
        async with get_pool().acquire() as connection, connection.transaction():
            row = await connection.fetchrow(
                f"""
                INSERT INTO findings
                    (org_id, analysis_id, document_id, policy_rule_id, chunk_id, category,
                     plain_summary, why_it_matters, model_confidence, severity_weight,
                     confidence_threshold, weighted_risk, machine_severity, severity_source,
                     scoring_explanation, verification_status, quarantine_reason,
                     degraded_retrieval)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18)
                RETURNING {FINDING_COLUMNS}
                """,
                finding["org_id"],
                finding["analysis_id"],
                finding["document_id"],
                finding.get("policy_rule_id"),
                finding.get("chunk_id"),
                finding["category"],
                finding["plain_summary"],
                finding["why_it_matters"],
                finding["model_confidence"],
                finding["severity_weight"],
                finding["confidence_threshold"],
                finding["weighted_risk"],
                finding["machine_severity"],
                finding["severity_source"],
                finding["scoring_explanation"],
                finding["verification_status"],
                finding.get("quarantine_reason"),
                finding.get("degraded_retrieval", False),
            )
            if evidence:
                await connection.execute(
                    """
                    INSERT INTO finding_evidence
                        (org_id, finding_id, chunk_id, document_id, quote,
                         doc_start_offset, doc_end_offset, chunk_start_offset,
                         chunk_end_offset, verification_method)
                    VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)
                    """,
                    finding["org_id"],
                    row["id"],
                    finding.get("chunk_id"),
                    finding["document_id"],
                    evidence["quote"],
                    evidence["doc_start_offset"],
                    evidence["doc_end_offset"],
                    evidence.get("chunk_start_offset"),
                    evidence.get("chunk_end_offset"),
                    evidence["verification_method"],
                )
        return dict(row)

    async def list_for_analysis(
        self,
        org_id: str,
        analysis_id: str,
        *,
        category: str | None = None,
        severity: str | None = None,
        review_status: str | None = None,
        include_quarantined: bool = False,
    ) -> list[dict]:
        conditions = ["f.org_id = $1", "f.analysis_id = $2"]
        params: list[Any] = [org_id, analysis_id]
        if not include_quarantined:
            conditions.append("f.verification_status <> 'quarantined'")
        if category:
            params.append(category)
            conditions.append(f"f.category = ${len(params)}")
        if severity:
            params.append(severity)
            conditions.append(f"COALESCE(f.override_severity, f.machine_severity) = ${len(params)}")
        if review_status:
            params.append(review_status)
            conditions.append(f"f.review_status = ${len(params)}")

        return await fetch_all(
            f"""
            SELECT {FINDING_COLUMNS_F},
                   e.quote, e.doc_start_offset, e.doc_end_offset,
                   e.verification_method, c.ordinal AS chunk_ordinal, c.heading AS chunk_heading
            FROM findings f
            LEFT JOIN finding_evidence e ON e.finding_id = f.id
            LEFT JOIN document_chunks c ON c.id = f.chunk_id
            WHERE {" AND ".join(conditions)}
            ORDER BY
                CASE COALESCE(f.override_severity, f.machine_severity)
                    WHEN 'critical' THEN 0 WHEN 'high' THEN 1 WHEN 'medium' THEN 2
                    WHEN 'low' THEN 3 ELSE 4 END,
                f.model_confidence DESC
            """,
            *params,
        )

    async def get(self, org_id: str, finding_id: str) -> dict | None:
        return await fetch_one(
            f"SELECT {FINDING_COLUMNS} FROM findings WHERE id = $1 AND org_id = $2",
            finding_id,
            org_id,
        )

    async def get_evidence(self, org_id: str, finding_id: str) -> dict | None:
        return await fetch_one(
            """
            SELECT e.id, e.quote, e.doc_start_offset, e.doc_end_offset,
                   e.chunk_start_offset, e.chunk_end_offset, e.verification_method,
                   e.verified_at, c.chunk_text, c.ordinal, c.heading, c.start_offset,
                   c.end_offset, e.document_id
            FROM finding_evidence e
            LEFT JOIN document_chunks c ON c.id = e.chunk_id
            WHERE e.finding_id = $1 AND e.org_id = $2
            """,
            finding_id,
            org_id,
        )

    async def add_review(
        self,
        *,
        org_id: str,
        finding_id: str,
        reviewer_id: str | None,
        action: str,
        new_severity: str | None = None,
        note: str | None = None,
        reason: str | None = None,
    ) -> dict[str, Any]:
        """Apply a reviewer action.

        The machine columns (machine_severity, severity_source, scoring_explanation)
        are never touched. The override and status columns record the current
        effective state, and an immutable review row preserves the history.
        """
        status_map = {
            "accept": "accepted",
            "dismiss": "dismissed",
            "escalate": "escalated",
        }

        async with get_pool().acquire() as connection, connection.transaction():
            current = await connection.fetchrow(
                "SELECT machine_severity, override_severity, review_status "
                "FROM findings WHERE id = $1 AND org_id = $2 FOR UPDATE",
                finding_id,
                org_id,
            )
            if current is None:
                raise LookupError("finding not found")

            previous_severity = current["override_severity"] or current["machine_severity"]
            new_status = status_map.get(action, current["review_status"])

            await connection.execute(
                """
                UPDATE findings
                SET review_status = $3,
                    override_severity = COALESCE($4, override_severity),
                    severity_source = CASE WHEN $4 IS NOT NULL
                                           THEN 'human_override' ELSE severity_source END,
                    reviewed_by = $5,
                    reviewed_at = NOW()
                WHERE id = $1 AND org_id = $2
                """,
                finding_id,
                org_id,
                new_status,
                new_severity,
                reviewer_id,
            )

            review = await connection.fetchrow(
                """
                INSERT INTO finding_reviews
                    (org_id, finding_id, reviewer_id, action, previous_severity,
                     new_severity, previous_status, new_status, note, reason)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)
                RETURNING id, action, previous_severity, new_severity, previous_status,
                          new_status, note, reason, created_at, reviewer_id
                """,
                org_id,
                finding_id,
                reviewer_id,
                action,
                previous_severity,
                new_severity,
                current["review_status"],
                new_status,
                note,
                reason,
            )
        return dict(review)

    async def list_reviews(self, org_id: str, finding_id: str) -> list[dict]:
        return await fetch_all(
            """
            SELECT r.id, r.action, r.previous_severity, r.new_severity, r.previous_status,
                   r.new_status, r.note, r.reason, r.created_at,
                   p.full_name AS reviewer_name, p.email AS reviewer_email
            FROM finding_reviews r
            LEFT JOIN profiles p ON p.id = r.reviewer_id
            WHERE r.finding_id = $1 AND r.org_id = $2
            ORDER BY r.created_at DESC
            """,
            finding_id,
            org_id,
        )

    async def pending_review_count(self, org_id: str) -> int:
        return (
            await fetch_value(
                "SELECT COUNT(*) FROM findings WHERE org_id = $1 AND review_status = 'pending' "
                "AND verification_status = 'verified'",
                org_id,
            )
            or 0
        )
