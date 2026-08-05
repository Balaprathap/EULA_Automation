"""Action items derived from verified findings.

Organization scoping is server-side on every route, resolved from the verified
JWT. An item belonging to another tenant returns 404, identical to a missing
one, so the response cannot be used to probe for existence.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request

from app.api.deps import client_ip, enforce_request_rate_limit
from app.core.errors import NotFound, Unprocessable, ValidationFailed
from app.core.logging import get_logger
from app.core.security import AuthenticatedUser
from app.db.repositories.action_items import ActionItemRepository
from app.db.repositories.analyses import AnalysisRepository, FindingRepository
from app.schemas.api import (
    ActionItemListResponse,
    ActionItemResponse,
    ActionItemSummaryResponse,
    ActionItemUpdateRequest,
    GenerateActionItemsResponse,
)
from app.services.action_items import derive_action_items
from app.services.audit import AuditAction, record_audit

logger = get_logger(__name__)
router = APIRouter(tags=["action-items"])

items = ActionItemRepository()
analyses = AnalysisRepository()
findings = FindingRepository()

TERMINAL_STATUSES = {"complete", "partial"}


def to_response(row: dict) -> ActionItemResponse:
    return ActionItemResponse(
        id=str(row["id"]),
        analysis_id=str(row["analysis_id"]),
        document_id=str(row["document_id"]),
        finding_id=str(row["finding_id"]),
        document_title=row.get("document_title"),
        vendor_name=row.get("vendor_name"),
        title=row["title"],
        description=row["description"],
        category=row["category"],
        obligation_type=row["obligation_type"],
        evidence_quote=row["evidence_quote"],
        doc_start_offset=row.get("doc_start_offset"),
        doc_end_offset=row.get("doc_end_offset"),
        duration_days=row.get("duration_days"),
        duration_text=row.get("duration_text"),
        ai_priority=row["ai_priority"],
        due_date=row.get("due_date"),
        date_status=row["date_status"],
        assignee_id=str(row["assignee_id"]) if row.get("assignee_id") else None,
        priority=row["priority"],
        status=row["status"],
        reviewer_note=row.get("reviewer_note"),
        completed_at=row.get("completed_at"),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


@router.get("/action-items", response_model=ActionItemListResponse)
async def list_action_items(
    status: str | None = Query(default=None, pattern=r"^(open|in_progress|completed|dismissed)$"),
    category: str | None = Query(default=None, max_length=64),
    priority: str | None = Query(default=None, pattern=r"^(low|medium|high|urgent)$"),
    assignee_id: str | None = Query(default=None),
    document_id: str | None = Query(default=None),
    analysis_id: str | None = Query(default=None),
    due: str | None = Query(default=None, pattern=r"^(overdue|soon|unresolved)$"),
    sort: str = Query(default="due_date", pattern=r"^(due_date|priority|created_at)$"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    user: AuthenticatedUser = Depends(enforce_request_rate_limit),
):
    page = await items.list(
        user.org_id,
        status=status,
        category=category,
        priority=priority,
        assignee_id=assignee_id,
        document_id=document_id,
        analysis_id=analysis_id,
        due=due,
        sort=sort,
        limit=limit,
        offset=offset,
    )
    return ActionItemListResponse(
        items=[to_response(row) for row in page["items"]],
        total=page["total"],
        limit=limit,
        offset=offset,
    )


@router.get("/action-items/summary", response_model=ActionItemSummaryResponse)
async def action_item_summary(user: AuthenticatedUser = Depends(enforce_request_rate_limit)):
    """Counts for the dashboard widget."""
    return ActionItemSummaryResponse(**await items.summary(user.org_id))


@router.post(
    "/analyses/{analysis_id}/action-items/generate", response_model=GenerateActionItemsResponse
)
async def generate_action_items(
    request: Request,
    analysis_id: str,
    user: AuthenticatedUser = Depends(enforce_request_rate_limit),
):
    """Derive action items from this analysis's verified findings.

    Deterministic - no AI call, so no token cost. Idempotent: re-running adds
    only genuinely new items and never overwrites a human edit.
    """
    analysis = await analyses.get(user.org_id, analysis_id)
    if analysis is None:
        raise NotFound("That analysis does not exist, or you do not have access to it.")
    if str(analysis["status"]) not in TERMINAL_STATUSES:
        raise Unprocessable(
            f"This analysis is {analysis['status']}. Action items can only be generated once "
            "the analysis has finished.",
            code="ANALYSIS_NOT_FINISHED",
        )

    # include_quarantined=False is belt and braces; derive_action_items also
    # filters on verification_status.
    rows = await findings.list_for_analysis(user.org_id, analysis_id, include_quarantined=False)
    derived = derive_action_items([dict(r) for r in rows])

    inserted = await items.bulk_upsert(
        org_id=user.org_id,
        analysis_id=analysis_id,
        document_id=str(analysis["document_id"]),
        items=[vars(item) for item in derived],
    )

    await record_audit(
        org_id=user.org_id,
        actor_id=user.user_id,
        actor_email=user.email,
        action=AuditAction.ACTION_ITEMS_GENERATE,
        resource_type="analysis",
        resource_id=analysis_id,
        request_id=getattr(request.state, "request_id", None),
        ip_address=client_ip(request),
        metadata={"derived": len(derived), "inserted": inserted},
    )

    return GenerateActionItemsResponse(
        analysis_id=analysis_id, derived=len(derived), created=inserted
    )


@router.patch("/action-items/{item_id}", response_model=ActionItemResponse)
async def update_action_item(
    request: Request,
    item_id: str,
    payload: ActionItemUpdateRequest,
    user: AuthenticatedUser = Depends(enforce_request_rate_limit),
):
    """Apply a human edit.

    The machine's original extraction is preserved; each change appends an
    immutable row to action_item_reviews.
    """
    existing = await items.get(user.org_id, item_id)
    if existing is None:
        raise NotFound("That action item does not exist, or you do not have access to it.")

    changes = payload.model_dump(exclude_unset=True)
    if not changes:
        raise ValidationFailed("No changes were supplied.")

    # An assignee must be a member of the caller's organization, verified in the
    # database - never trusted from the request.
    if changes.get("assignee_id"):
        from app.db.session import fetch_one

        member = await fetch_one(
            "SELECT id FROM profiles WHERE id = $1 AND org_id = $2",
            changes["assignee_id"],
            user.org_id,
        )
        if member is None:
            raise ValidationFailed(
                "That assignee is not a member of your organization.", code="INVALID_ASSIGNEE"
            )

    updated = await items.update(
        org_id=user.org_id, item_id=item_id, reviewer_id=user.user_id, changes=changes
    )
    if updated is None:
        raise NotFound("That action item does not exist, or you do not have access to it.")

    await record_audit(
        org_id=user.org_id,
        actor_id=user.user_id,
        actor_email=user.email,
        action=AuditAction.ACTION_ITEM_UPDATE,
        resource_type="action_item",
        resource_id=item_id,
        request_id=getattr(request.state, "request_id", None),
        ip_address=client_ip(request),
        metadata={"fields": sorted(changes.keys())},
    )
    return to_response(updated)


@router.get("/action-items/{item_id}/history")
async def action_item_history(
    item_id: str, user: AuthenticatedUser = Depends(enforce_request_rate_limit)
):
    if await items.get(user.org_id, item_id) is None:
        raise NotFound("That action item does not exist, or you do not have access to it.")
    return {"item_id": item_id, "history": await items.list_reviews(user.org_id, item_id)}
