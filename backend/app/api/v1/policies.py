"""Compliance policy management. Writes require an admin or owner role."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, status

from app.api.deps import enforce_request_rate_limit, require_admin
from app.core.errors import NotFound, ValidationFailed
from app.core.security import AuthenticatedUser
from app.db.repositories.policies import PolicyRepository
from app.schemas.api import (
    PolicyCreateRequest,
    PolicyResponse,
    PolicyRuleResponse,
    PolicyRulesReplaceRequest,
    PolicyUpdateRequest,
)
from app.services.audit import AuditAction, record_audit

router = APIRouter(prefix="/policies", tags=["policies"])
policies = PolicyRepository()


def to_response(row: dict, rule_count: int = 0) -> PolicyResponse:
    return PolicyResponse(
        id=str(row["id"]),
        name=row["name"],
        description=row.get("description"),
        version=row["version"],
        is_default=row["is_default"],
        is_active=row["is_active"],
        rule_count=rule_count,
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def rule_to_response(row: dict) -> PolicyRuleResponse:
    return PolicyRuleResponse(
        id=str(row["id"]),
        policy_id=str(row["policy_id"]),
        category=row["category"],
        display_name=row["display_name"],
        description=row["description"],
        retrieval_guidance=row.get("retrieval_guidance"),
        keywords=list(row.get("keywords") or []),
        severity_weight=float(row["severity_weight"]),
        confidence_threshold=float(row["confidence_threshold"]),
        escalate=row["escalate"],
        is_enabled=row["is_enabled"],
        sort_order=row["sort_order"],
    )


@router.get("", response_model=list[PolicyResponse])
async def list_policies(user: AuthenticatedUser = Depends(enforce_request_rate_limit)):
    rows = await policies.list(user.org_id)
    result = []
    for row in rows:
        rules = await policies.list_rules(user.org_id, str(row["id"]))
        result.append(to_response(row, len(rules)))
    return result


@router.post("", status_code=status.HTTP_201_CREATED, response_model=PolicyResponse)
async def create_policy(
    request: Request,
    payload: PolicyCreateRequest,
    user: AuthenticatedUser = Depends(require_admin),
):
    categories = [r.category for r in payload.rules]
    if len(categories) != len(set(categories)):
        raise ValidationFailed("Each category may appear only once in a policy.")

    row = await policies.create(
        user.org_id,
        name=payload.name,
        description=payload.description,
        created_by=user.user_id,
        rules=[r.model_dump() for r in payload.rules],
    )
    await record_audit(
        org_id=user.org_id,
        actor_id=user.user_id,
        actor_email=user.email,
        action=AuditAction.POLICY_CREATE,
        resource_type="policy",
        resource_id=str(row["id"]),
        request_id=getattr(request.state, "request_id", None),
        metadata={"name": payload.name, "rules": len(payload.rules)},
    )
    return to_response(row, len(payload.rules))


@router.get("/{policy_id}", response_model=PolicyResponse)
async def get_policy(policy_id: str, user: AuthenticatedUser = Depends(enforce_request_rate_limit)):
    row = await policies.get(user.org_id, policy_id)
    if row is None:
        raise NotFound("That policy does not exist, or you do not have access to it.")
    rules = await policies.list_rules(user.org_id, policy_id)
    return to_response(row, len(rules))


@router.patch("/{policy_id}", response_model=PolicyResponse)
async def update_policy(
    request: Request,
    policy_id: str,
    payload: PolicyUpdateRequest,
    user: AuthenticatedUser = Depends(require_admin),
):
    row = await policies.update(user.org_id, policy_id, **payload.model_dump(exclude_none=True))
    if row is None:
        raise NotFound("That policy does not exist, or you do not have access to it.")
    await record_audit(
        org_id=user.org_id,
        actor_id=user.user_id,
        actor_email=user.email,
        action=AuditAction.POLICY_UPDATE,
        resource_type="policy",
        resource_id=policy_id,
        request_id=getattr(request.state, "request_id", None),
        metadata=payload.model_dump(exclude_none=True),
    )
    rules = await policies.list_rules(user.org_id, policy_id)
    return to_response(row, len(rules))


@router.post(
    "/{policy_id}/versions", status_code=status.HTTP_201_CREATED, response_model=PolicyResponse
)
async def create_policy_version(
    request: Request, policy_id: str, user: AuthenticatedUser = Depends(require_admin)
):
    """Clone a policy at version+1 so historical analyses keep their exact rules."""
    row = await policies.create_version(user.org_id, policy_id, user.user_id)
    if row is None:
        raise NotFound("That policy does not exist, or you do not have access to it.")
    await record_audit(
        org_id=user.org_id,
        actor_id=user.user_id,
        actor_email=user.email,
        action=AuditAction.POLICY_VERSION_CREATE,
        resource_type="policy",
        resource_id=str(row["id"]),
        request_id=getattr(request.state, "request_id", None),
        metadata={"source_policy_id": policy_id, "version": row["version"]},
    )
    rules = await policies.list_rules(user.org_id, str(row["id"]))
    return to_response(row, len(rules))


@router.get("/{policy_id}/rules", response_model=list[PolicyRuleResponse])
async def list_rules(policy_id: str, user: AuthenticatedUser = Depends(enforce_request_rate_limit)):
    if await policies.get(user.org_id, policy_id) is None:
        raise NotFound("That policy does not exist, or you do not have access to it.")
    return [rule_to_response(r) for r in await policies.list_rules(user.org_id, policy_id)]


@router.put("/{policy_id}/rules", response_model=list[PolicyRuleResponse])
async def replace_rules(
    request: Request,
    policy_id: str,
    payload: PolicyRulesReplaceRequest,
    user: AuthenticatedUser = Depends(require_admin),
):
    if await policies.get(user.org_id, policy_id) is None:
        raise NotFound("That policy does not exist, or you do not have access to it.")

    categories = [r.category for r in payload.rules]
    if len(categories) != len(set(categories)):
        raise ValidationFailed("Each category may appear only once in a policy.")

    rows = await policies.replace_rules(
        user.org_id, policy_id, [r.model_dump() for r in payload.rules]
    )
    await record_audit(
        org_id=user.org_id,
        actor_id=user.user_id,
        actor_email=user.email,
        action=AuditAction.POLICY_RULES_REPLACE,
        resource_type="policy",
        resource_id=policy_id,
        request_id=getattr(request.state, "request_id", None),
        metadata={"rules": len(payload.rules)},
    )
    return [rule_to_response(r) for r in rows]
