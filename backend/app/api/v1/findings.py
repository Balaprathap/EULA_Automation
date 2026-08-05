"""Evidence retrieval and the human review workflow."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, status

from app.api.deps import client_ip, enforce_request_rate_limit
from app.core.errors import NotFound, ValidationFailed
from app.core.security import AuthenticatedUser
from app.db.repositories.analyses import FindingRepository
from app.db.repositories.documents import DocumentRepository
from app.schemas.api import EvidenceResponse, ReviewRequest, ReviewResponse
from app.services.audit import AuditAction, record_audit

router = APIRouter(prefix="/findings", tags=["findings"])
findings = FindingRepository()
documents = DocumentRepository()

CONTEXT_CHARS = 600

ACTION_TO_AUDIT = {
    "accept": AuditAction.FINDING_ACCEPT,
    "dismiss": AuditAction.FINDING_DISMISS,
    "escalate": AuditAction.FINDING_ESCALATE,
    "override_severity": AuditAction.FINDING_OVERRIDE,
    "note": AuditAction.FINDING_NOTE,
}


@router.get("/{finding_id}/evidence", response_model=EvidenceResponse)
async def get_evidence(
    finding_id: str, user: AuthenticatedUser = Depends(enforce_request_rate_limit)
):
    """Return the verified quote, its absolute offsets, and surrounding context.

    A finding with no row here was never verified, so there is nothing to show.
    """
    finding = await findings.get(user.org_id, finding_id)
    if finding is None:
        raise NotFound("That finding does not exist, or you do not have access to it.")

    evidence = await findings.get_evidence(user.org_id, finding_id)
    if evidence is None:
        raise NotFound(
            "This finding has no verified evidence. It was quarantined because the "
            "quoted text could not be located in the source document.",
            code="EVIDENCE_NOT_VERIFIED",
        )

    surrounding = None
    surrounding_start = None
    text = await documents.get_normalized_text(user.org_id, str(evidence["document_id"]))
    if text:
        start = max(0, evidence["doc_start_offset"] - CONTEXT_CHARS)
        end = min(len(text), evidence["doc_end_offset"] + CONTEXT_CHARS)
        surrounding = text[start:end]
        surrounding_start = start

    return EvidenceResponse(
        finding_id=finding_id,
        quote=evidence["quote"],
        doc_start_offset=evidence["doc_start_offset"],
        doc_end_offset=evidence["doc_end_offset"],
        chunk_start_offset=evidence.get("chunk_start_offset"),
        chunk_end_offset=evidence.get("chunk_end_offset"),
        verification_method=evidence["verification_method"],
        verified_at=evidence["verified_at"],
        chunk_text=evidence.get("chunk_text"),
        chunk_ordinal=evidence.get("ordinal"),
        chunk_heading=evidence.get("heading"),
        surrounding_text=surrounding,
        surrounding_start_offset=surrounding_start,
    )


@router.post(
    "/{finding_id}/reviews", status_code=status.HTTP_201_CREATED, response_model=ReviewResponse
)
async def create_review(
    request: Request,
    finding_id: str,
    payload: ReviewRequest,
    user: AuthenticatedUser = Depends(enforce_request_rate_limit),
):
    """Record a reviewer action.

    The machine decision is preserved: an override writes to override_severity
    and appends an immutable review row. machine_severity is never rewritten,
    so the original automated judgement stays auditable forever.
    """
    if await findings.get(user.org_id, finding_id) is None:
        raise NotFound("That finding does not exist, or you do not have access to it.")

    if payload.action == "override_severity" and not payload.severity:
        raise ValidationFailed("A severity value is required when overriding severity.")
    if payload.action == "note" and not payload.note:
        raise ValidationFailed("A note is required for the 'note' action.")

    try:
        review = await findings.add_review(
            org_id=user.org_id,
            finding_id=finding_id,
            reviewer_id=user.user_id,
            action=payload.action,
            new_severity=payload.severity,
            note=payload.note,
            reason=payload.reason,
        )
    except LookupError as exc:
        raise NotFound("That finding does not exist, or you do not have access to it.") from exc

    await record_audit(
        org_id=user.org_id,
        actor_id=user.user_id,
        actor_email=user.email,
        action=ACTION_TO_AUDIT.get(payload.action, AuditAction.FINDING_NOTE),
        resource_type="finding",
        resource_id=finding_id,
        request_id=getattr(request.state, "request_id", None),
        ip_address=client_ip(request),
        metadata={
            "action": payload.action,
            "previous_severity": review.get("previous_severity"),
            "new_severity": review.get("new_severity"),
        },
    )

    return ReviewResponse(
        id=str(review["id"]),
        action=review["action"],
        previous_severity=review.get("previous_severity"),
        new_severity=review.get("new_severity"),
        previous_status=review.get("previous_status"),
        new_status=review.get("new_status"),
        note=review.get("note"),
        reason=review.get("reason"),
        reviewer_email=user.email,
        created_at=review["created_at"],
    )


@router.get("/{finding_id}/reviews", response_model=list[ReviewResponse])
async def list_reviews(
    finding_id: str, user: AuthenticatedUser = Depends(enforce_request_rate_limit)
):
    if await findings.get(user.org_id, finding_id) is None:
        raise NotFound("That finding does not exist, or you do not have access to it.")
    rows = await findings.list_reviews(user.org_id, finding_id)
    return [
        ReviewResponse(
            id=str(r["id"]),
            action=r["action"],
            previous_severity=r.get("previous_severity"),
            new_severity=r.get("new_severity"),
            previous_status=r.get("previous_status"),
            new_status=r.get("new_status"),
            note=r.get("note"),
            reason=r.get("reason"),
            reviewer_name=r.get("reviewer_name"),
            reviewer_email=r.get("reviewer_email"),
            created_at=r["created_at"],
        )
        for r in rows
    ]
