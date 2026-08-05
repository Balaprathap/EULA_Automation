"""Analysis creation and progress. Work is always performed in the worker."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request, Response, status

from app.api.deps import client_ip, enforce_analysis_rate_limit, enforce_request_rate_limit
from app.core.errors import Conflict, NotFound, Unprocessable, ValidationFailed
from app.core.logging import get_logger
from app.core.security import AuthenticatedUser
from app.db.repositories.analyses import AnalysisRepository, FindingRepository
from app.db.repositories.documents import DocumentRepository
from app.db.repositories.policies import PolicyRepository
from app.schemas.api import (
    AnalysisCreateRequest,
    AnalysisResponse,
    CategoryProgress,
    FindingResponse,
)
from app.services.audit import AuditAction, record_audit
from app.services.scoring import effective_severity

logger = get_logger(__name__)
router = APIRouter(tags=["analyses"])

analyses = AnalysisRepository()
documents = DocumentRepository()
policies = PolicyRepository()
findings = FindingRepository()


def to_response(row: dict, categories: list[dict] | None = None) -> AnalysisResponse:
    return AnalysisResponse(
        id=str(row["id"]),
        document_id=str(row["document_id"]),
        policy_id=str(row["policy_id"]),
        status=row["status"],
        stage=row["stage"],
        progress_message=row.get("progress_message"),
        categories_total=row["categories_total"],
        categories_completed=row["categories_completed"],
        completed_categories=list(row.get("completed_categories") or []),
        overall_score=float(row["overall_score"]) if row.get("overall_score") is not None else None,
        risk_band=row.get("risk_band"),
        finding_count=row["finding_count"],
        review_count=row["review_count"],
        quarantine_count=row["quarantine_count"],
        verification_pass_rate=(
            float(row["verification_pass_rate"])
            if row.get("verification_pass_rate") is not None
            else None
        ),
        executive_summary=row.get("executive_summary"),
        degraded_retrieval=row.get("degraded_retrieval", False),
        degraded_reason=row.get("degraded_reason"),
        stage_timings_ms=row.get("stage_timings_ms") or {},
        input_tokens=row.get("input_tokens", 0),
        cached_input_tokens=row.get("cached_input_tokens", 0),
        output_tokens=row.get("output_tokens", 0),
        estimated_cost_usd=float(row.get("estimated_cost_usd") or 0),
        model_used=row.get("model_used"),
        error_code=row.get("error_code"),
        error_message=row.get("error_message"),
        categories=[
            CategoryProgress(
                category=c["category"],
                status=c["status"],
                needs_review_reason=c.get("needs_review_reason"),
                error_code=c.get("error_code"),
                retrieval_mode=c.get("retrieval_mode"),
                degraded_reason=c.get("degraded_reason"),
                tool_calls=c.get("tool_calls", 0),
                duration_ms=float(c["duration_ms"]) if c.get("duration_ms") is not None else None,
            )
            for c in (categories or [])
        ],
        queued_at=row.get("queued_at"),
        started_at=row.get("started_at"),
        completed_at=row.get("completed_at"),
        created_at=row["created_at"],
    )


@router.post(
    "/documents/{document_id}/analyses",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=AnalysisResponse,
)
async def create_analysis(
    request: Request,
    document_id: str,
    payload: AnalysisCreateRequest,
    response: Response,
    user: AuthenticatedUser = Depends(enforce_analysis_rate_limit),
):
    """Queue an analysis and return 202 immediately.

    Validation order: authentication, organization ownership, document
    readiness, then enqueue. No AI work happens on the request path.
    """
    document = await documents.get(user.org_id, document_id)
    if document is None:
        raise NotFound("That document does not exist, or you do not have access to it.")
    if document["status"] != "ready":
        raise Unprocessable(
            f"This document is not ready to analyze (status: {document['status']}).",
            code="DOCUMENT_NOT_READY",
        )

    policy = (
        await policies.get(user.org_id, payload.policy_id)
        if payload.policy_id
        else await policies.get_default(user.org_id)
    )
    if policy is None:
        raise ValidationFailed(
            "No compliance policy was found. Select a policy, or seed the default policy.",
            code="POLICY_NOT_FOUND",
        )

    rules = await policies.list_rules(user.org_id, str(policy["id"]), enabled_only=True)
    if not rules:
        raise ValidationFailed(
            "The selected policy has no enabled categories.", code="POLICY_HAS_NO_RULES"
        )

    existing = await analyses.active_for_document(user.org_id, document_id, str(policy["id"]))
    if existing:
        response.status_code = status.HTTP_202_ACCEPTED
        return to_response(existing, await analyses.list_categories(str(existing["id"])))

    row = await analyses.create(
        org_id=user.org_id,
        document_id=document_id,
        policy_id=str(policy["id"]),
        requested_by=user.user_id,
        categories_total=len(rules),
        idempotency_key=payload.idempotency_key,
    )
    if row is None:
        existing = await analyses.active_for_document(user.org_id, document_id, str(policy["id"]))
        if existing:
            return to_response(existing)
        raise Conflict("An analysis for this document and policy is already in progress.")

    analysis_id = str(row["id"])

    from app.main import get_queue

    queue = get_queue()
    if queue is not None:
        await queue.enqueue(
            analysis_id=analysis_id,
            org_id=user.org_id,
            document_id=document_id,
            policy_id=str(policy["id"]),
        )
    else:
        logger.error("queue unavailable; analysis will be picked up by recovery sweep")

    await record_audit(
        org_id=user.org_id,
        actor_id=user.user_id,
        actor_email=user.email,
        action=AuditAction.ANALYSIS_CREATE,
        resource_type="analysis",
        resource_id=analysis_id,
        request_id=getattr(request.state, "request_id", None),
        ip_address=client_ip(request),
        metadata={
            "document_id": document_id,
            "policy_id": str(policy["id"]),
            "categories": len(rules),
        },
    )
    return to_response(row)


@router.get("/analyses", response_model=dict)
async def list_analyses(
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    user: AuthenticatedUser = Depends(enforce_request_rate_limit),
):
    page = await analyses.list(user.org_id, limit=limit, offset=offset)
    return {
        "items": [to_response(row).model_dump(mode="json") for row in page["items"]],
        "total": page["total"],
        "limit": limit,
        "offset": offset,
    }


@router.get("/analyses/{analysis_id}", response_model=AnalysisResponse)
async def get_analysis(
    analysis_id: str, user: AuthenticatedUser = Depends(enforce_request_rate_limit)
):
    row = await analyses.get(user.org_id, analysis_id)
    if row is None:
        raise NotFound("That analysis does not exist, or you do not have access to it.")
    return to_response(row, await analyses.list_categories(analysis_id))


@router.get("/analyses/{analysis_id}/findings", response_model=list[FindingResponse])
async def list_findings(
    analysis_id: str,
    category: str | None = Query(default=None),
    severity: str | None = Query(default=None),
    review_status: str | None = Query(default=None),
    include_quarantined: bool = Query(default=False),
    user: AuthenticatedUser = Depends(enforce_request_rate_limit),
):
    """List findings for an analysis.

    Quarantined findings are excluded by default: unverified evidence must never
    look like a confirmed result. They remain retrievable for transparency.
    """
    if await analyses.get(user.org_id, analysis_id) is None:
        raise NotFound("That analysis does not exist, or you do not have access to it.")

    rows = await findings.list_for_analysis(
        user.org_id,
        analysis_id,
        category=category,
        severity=severity,
        review_status=review_status,
        include_quarantined=include_quarantined,
    )
    return [
        FindingResponse(
            id=str(r["id"]),
            analysis_id=str(r["analysis_id"]),
            document_id=str(r["document_id"]),
            category=r["category"],
            plain_summary=r["plain_summary"],
            why_it_matters=r["why_it_matters"],
            model_confidence=float(r["model_confidence"]),
            severity_weight=float(r["severity_weight"]),
            confidence_threshold=float(r["confidence_threshold"]),
            weighted_risk=float(r["weighted_risk"]),
            machine_severity=r["machine_severity"],
            override_severity=r.get("override_severity"),
            effective_severity=effective_severity(
                r["machine_severity"], r.get("override_severity")
            ),
            severity_source=r["severity_source"],
            scoring_explanation=r["scoring_explanation"],
            review_status=r["review_status"],
            verification_status=r["verification_status"],
            quarantine_reason=r.get("quarantine_reason"),
            degraded_retrieval=r.get("degraded_retrieval", False),
            quote=r.get("quote"),
            doc_start_offset=r.get("doc_start_offset"),
            doc_end_offset=r.get("doc_end_offset"),
            verification_method=r.get("verification_method"),
            chunk_ordinal=r.get("chunk_ordinal"),
            chunk_heading=r.get("chunk_heading"),
            reviewed_at=r.get("reviewed_at"),
            created_at=r["created_at"],
        )
        for r in rows
    ]
