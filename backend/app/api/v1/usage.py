"""Usage, cost, dashboard summary, and administrator metrics."""

from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends, Query

from app.api.deps import enforce_request_rate_limit, require_admin
from app.core.security import AuthenticatedUser
from app.db.session import fetch_all, fetch_one, fetch_value, health_check
from app.schemas.api import AdminMetricsResponse, UsageResponse

router = APIRouter(tags=["usage"])


@router.get("/usage", response_model=UsageResponse)
async def get_usage(
    days: int = Query(default=30, ge=1, le=365),
    user: AuthenticatedUser = Depends(enforce_request_rate_limit),
):
    window = timedelta(days=days)

    totals = (
        await fetch_one(
            """
        SELECT COALESCE(SUM(input_tokens), 0)        AS input_tokens,
               COALESCE(SUM(cached_input_tokens), 0) AS cached_input_tokens,
               COALESCE(SUM(output_tokens), 0)       AS output_tokens,
               COALESCE(SUM(estimated_cost_usd), 0)  AS cost
        FROM usage_events
        WHERE org_id = $1 AND created_at > NOW() - $2::interval
        """,
            user.org_id,
            window,
        )
        or {}
    )

    by_type = await fetch_all(
        """
        SELECT event_type,
               COUNT(*)                              AS events,
               COALESCE(SUM(input_tokens), 0)        AS input_tokens,
               COALESCE(SUM(output_tokens), 0)       AS output_tokens,
               COALESCE(SUM(estimated_cost_usd), 0)  AS cost
        FROM usage_events
        WHERE org_id = $1 AND created_at > NOW() - $2::interval
        GROUP BY event_type ORDER BY cost DESC
        """,
        user.org_id,
        window,
    )

    daily = await fetch_all(
        """
        SELECT date_trunc('day', created_at)::date  AS day,
               COALESCE(SUM(input_tokens + output_tokens), 0) AS tokens,
               COALESCE(SUM(estimated_cost_usd), 0)  AS cost
        FROM usage_events
        WHERE org_id = $1 AND created_at > NOW() - $2::interval
        GROUP BY 1 ORDER BY 1
        """,
        user.org_id,
        window,
    )

    analyses_run = await fetch_value(
        "SELECT COUNT(*) FROM analyses WHERE org_id = $1 AND created_at > NOW() - $2::interval",
        user.org_id,
        window,
    )
    docs = await fetch_value(
        "SELECT COUNT(*) FROM documents WHERE org_id = $1 AND created_at > NOW() - $2::interval "
        "AND deleted_at IS NULL",
        user.org_id,
        window,
    )

    input_tokens = int(totals.get("input_tokens") or 0)
    cached = int(totals.get("cached_input_tokens") or 0)
    output_tokens = int(totals.get("output_tokens") or 0)

    return UsageResponse(
        period_days=days,
        analyses_run=int(analyses_run or 0),
        documents_uploaded=int(docs or 0),
        input_tokens=input_tokens,
        cached_input_tokens=cached,
        output_tokens=output_tokens,
        total_tokens=input_tokens + cached + output_tokens,
        estimated_cost_usd=round(float(totals.get("cost") or 0), 6),
        by_event_type=[
            {
                "event_type": r["event_type"],
                "events": int(r["events"]),
                "input_tokens": int(r["input_tokens"]),
                "output_tokens": int(r["output_tokens"]),
                "estimated_cost_usd": round(float(r["cost"]), 6),
            }
            for r in by_type
        ],
        daily=[
            {
                "day": r["day"].isoformat(),
                "tokens": int(r["tokens"]),
                "estimated_cost_usd": round(float(r["cost"]), 6),
            }
            for r in daily
        ],
    )


@router.get("/dashboard")
async def dashboard(user: AuthenticatedUser = Depends(enforce_request_rate_limit)):
    """Everything the dashboard needs, from real data, in one round trip."""
    recent_documents = await fetch_all(
        "SELECT id, title, vendor_name, source_type, status, page_count, created_at "
        "FROM documents WHERE org_id = $1 AND deleted_at IS NULL "
        "ORDER BY created_at DESC LIMIT 5",
        user.org_id,
    )
    recent_analyses = await fetch_all(
        """
        SELECT a.id, a.status, a.stage, a.overall_score, a.risk_band, a.finding_count,
               a.created_at, a.completed_at, d.title AS document_title, d.id AS document_id
        FROM analyses a JOIN documents d ON d.id = a.document_id
        WHERE a.org_id = $1 ORDER BY a.created_at DESC LIMIT 5
        """,
        user.org_id,
    )
    status_counts = await fetch_all(
        "SELECT status, COUNT(*) AS count FROM analyses WHERE org_id = $1 GROUP BY status",
        user.org_id,
    )
    risk_distribution = await fetch_all(
        """
        SELECT COALESCE(f.override_severity, f.machine_severity) AS severity, COUNT(*) AS count
        FROM findings f
        WHERE f.org_id = $1 AND f.verification_status = 'verified'
        GROUP BY 1
        """,
        user.org_id,
    )
    pending_reviews = await fetch_value(
        "SELECT COUNT(*) FROM findings WHERE org_id = $1 AND review_status = 'pending' "
        "AND verification_status = 'verified'",
        user.org_id,
    )
    cost_30d = await fetch_value(
        "SELECT COALESCE(SUM(estimated_cost_usd), 0) FROM usage_events "
        "WHERE org_id = $1 AND created_at > NOW() - INTERVAL '30 days'",
        user.org_id,
    )

    return {
        "recent_documents": [
            {**r, "id": str(r["id"]), "created_at": r["created_at"].isoformat()}
            for r in recent_documents
        ],
        "recent_analyses": [
            {
                **r,
                "id": str(r["id"]),
                "document_id": str(r["document_id"]),
                "overall_score": float(r["overall_score"])
                if r["overall_score"] is not None
                else None,
                "created_at": r["created_at"].isoformat(),
                "completed_at": r["completed_at"].isoformat() if r["completed_at"] else None,
            }
            for r in recent_analyses
        ],
        "analysis_status_counts": {r["status"]: int(r["count"]) for r in status_counts},
        "risk_distribution": {r["severity"]: int(r["count"]) for r in risk_distribution},
        "pending_reviews": int(pending_reviews or 0),
        "estimated_cost_usd_30d": round(float(cost_30d or 0), 4),
    }


@router.get("/admin/metrics", response_model=AdminMetricsResponse)
async def admin_metrics(user: AuthenticatedUser = Depends(require_admin)):
    """Operational metrics. Admin and owner roles only."""
    from app.main import get_queue

    counts = (
        await fetch_one(
            """
        SELECT COUNT(*) AS total,
               COUNT(*) FILTER (WHERE status = 'complete') AS succeeded,
               COUNT(*) FILTER (WHERE status = 'partial')  AS partial,
               COUNT(*) FILTER (WHERE status = 'failed')   AS failed,
               AVG(verification_pass_rate)                 AS pass_rate,
               COALESCE(SUM(input_tokens), 0)              AS input_tokens,
               COALESCE(SUM(output_tokens), 0)             AS output_tokens,
               COALESCE(SUM(estimated_cost_usd), 0)        AS cost,
               PERCENTILE_CONT(0.95) WITHIN GROUP (
                   ORDER BY EXTRACT(EPOCH FROM (completed_at - started_at))
               ) FILTER (WHERE completed_at IS NOT NULL)   AS p95_seconds
        FROM analyses WHERE org_id = $1
        """,
            user.org_id,
        )
        or {}
    )

    stage_rows = await fetch_all(
        """
        SELECT key AS stage, AVG((value)::text::numeric) AS avg_ms
        FROM analyses, jsonb_each(stage_timings_ms)
        WHERE org_id = $1 AND stage_timings_ms <> '{}'::jsonb
        GROUP BY key
        """,
        user.org_id,
    )

    total = int(counts.get("total") or 0)
    failed = int(counts.get("failed") or 0)
    succeeded = int(counts.get("succeeded") or 0)

    queue = get_queue()
    queue_health = await queue.health() if queue else {"connected": False}

    return AdminMetricsResponse(
        analyses_total=total,
        analyses_succeeded=succeeded,
        analyses_partial=int(counts.get("partial") or 0),
        analyses_failed=failed,
        success_rate=round(100.0 * succeeded / total, 2) if total else 0.0,
        error_rate=round(100.0 * failed / total, 2) if total else 0.0,
        verification_pass_rate=(
            round(float(counts["pass_rate"]), 2) if counts.get("pass_rate") is not None else None
        ),
        average_stage_latency_ms={r["stage"]: round(float(r["avg_ms"]), 2) for r in stage_rows},
        p95_analysis_seconds=(
            round(float(counts["p95_seconds"]), 2)
            if counts.get("p95_seconds") is not None
            else None
        ),
        input_tokens=int(counts.get("input_tokens") or 0),
        output_tokens=int(counts.get("output_tokens") or 0),
        estimated_cost_usd=round(float(counts.get("cost") or 0), 6),
        queue_depth=queue_health.get("queue_depth", -1),
        live_workers=queue_health.get("live_workers", -1),
        redis_connected=queue_health.get("connected", False),
        database_connected=await health_check(),
    )
