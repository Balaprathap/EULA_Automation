"""Request and response models for the REST API."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ErrorDetail(BaseModel):
    code: str
    message: str
    request_id: str
    details: dict[str, Any] | None = None


class ErrorResponse(BaseModel):
    error: ErrorDetail


class Page(BaseModel):
    total: int
    limit: int
    offset: int


# --- documents ---------------------------------------------------------------
class PasteDocumentRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    title: str = Field(min_length=1, max_length=500)
    text: str = Field(min_length=200, max_length=2_000_000)
    vendor_name: str | None = Field(default=None, max_length=200)


class DocumentUpdateRequest(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=500)
    vendor_name: str | None = Field(default=None, max_length=200)


class DocumentResponse(BaseModel):
    id: str
    title: str
    vendor_name: str | None = None
    source_type: str
    original_filename: str | None = None
    file_size_bytes: int | None = None
    page_count: int | None = None
    char_count: int | None = None
    status: str
    error_code: str | None = None
    error_message: str | None = None
    created_at: datetime
    updated_at: datetime


class DocumentListResponse(Page):
    items: list[DocumentResponse]


# --- policies ----------------------------------------------------------------
class PolicyRuleInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    category: str = Field(min_length=2, max_length=64, pattern=r"^[a-z][a-z0-9_]*$")
    display_name: str = Field(min_length=1, max_length=120)
    description: str = Field(min_length=1, max_length=2000)
    retrieval_guidance: str | None = Field(default=None, max_length=2000)
    keywords: list[str] = Field(default_factory=list, max_length=40)
    severity_weight: float = Field(default=0.5, ge=0.0, le=1.0)
    confidence_threshold: float = Field(default=0.35, ge=0.0, le=1.0)
    escalate: bool = False
    is_enabled: bool = True
    sort_order: int = 0


class PolicyRuleResponse(PolicyRuleInput):
    id: str
    policy_id: str


class PolicyCreateRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    rules: list[PolicyRuleInput] = Field(default_factory=list, max_length=60)


class PolicyAIDraftRequest(BaseModel):
    """Generate an unsaved policy proposal for administrator review."""

    model_config = ConfigDict(str_strip_whitespace=True)

    prompt: str = Field(min_length=10, max_length=4000)
    agreement_type: str | None = Field(default=None, max_length=120)
    name_hint: str | None = Field(default=None, max_length=200)
    rule_count: int = Field(default=8, ge=3, le=12)


class PolicyAIDraftResponse(BaseModel):
    """AI proposal only. Nothing represented here has been persisted."""

    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    rules: list[PolicyRuleInput] = Field(min_length=3, max_length=12)
    model: str
    usage: dict[str, Any] = Field(default_factory=dict)


class PolicyUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    is_active: bool | None = None
    is_default: bool | None = None


class PolicyRulesReplaceRequest(BaseModel):
    rules: list[PolicyRuleInput] = Field(min_length=1, max_length=60)


class PolicyResponse(BaseModel):
    id: str
    name: str
    description: str | None = None
    version: int
    is_default: bool
    is_active: bool
    rule_count: int = 0
    created_at: datetime
    updated_at: datetime


# --- analyses ----------------------------------------------------------------
class AnalysisCreateRequest(BaseModel):
    policy_id: str | None = None
    idempotency_key: str | None = Field(default=None, max_length=200)


class CategoryProgress(BaseModel):
    category: str
    status: str
    needs_review_reason: str | None = None
    error_code: str | None = None
    retrieval_mode: str | None = None
    degraded_reason: str | None = None
    tool_calls: int = 0
    duration_ms: float | None = None


class AnalysisResponse(BaseModel):
    id: str
    document_id: str
    policy_id: str
    status: str
    stage: str
    progress_message: str | None = None
    categories_total: int
    categories_completed: int
    completed_categories: list[str] = Field(default_factory=list)
    overall_score: float | None = None
    risk_band: str | None = None
    finding_count: int = 0
    review_count: int = 0
    quarantine_count: int = 0
    verification_pass_rate: float | None = None
    executive_summary: str | None = None
    degraded_retrieval: bool = False
    degraded_reason: str | None = None
    stage_timings_ms: dict[str, Any] = Field(default_factory=dict)
    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0
    estimated_cost_usd: float = 0.0
    model_used: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    categories: list[CategoryProgress] = Field(default_factory=list)
    queued_at: datetime | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    created_at: datetime


# --- findings ----------------------------------------------------------------
class FindingResponse(BaseModel):
    id: str
    analysis_id: str
    document_id: str
    category: str
    plain_summary: str
    why_it_matters: str
    model_confidence: float
    severity_weight: float
    confidence_threshold: float
    weighted_risk: float
    machine_severity: str
    override_severity: str | None = None
    effective_severity: str
    severity_source: str
    scoring_explanation: str
    review_status: str
    verification_status: str
    quarantine_reason: str | None = None
    degraded_retrieval: bool = False
    quote: str | None = None
    doc_start_offset: int | None = None
    doc_end_offset: int | None = None
    verification_method: str | None = None
    chunk_ordinal: int | None = None
    chunk_heading: str | None = None
    reviewed_at: datetime | None = None
    created_at: datetime


class EvidenceResponse(BaseModel):
    finding_id: str
    quote: str
    doc_start_offset: int
    doc_end_offset: int
    chunk_start_offset: int | None = None
    chunk_end_offset: int | None = None
    verification_method: str
    verified_at: datetime
    chunk_text: str | None = None
    chunk_ordinal: int | None = None
    chunk_heading: str | None = None
    surrounding_text: str | None = None
    surrounding_start_offset: int | None = None


class ReviewRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    action: str = Field(pattern=r"^(accept|dismiss|escalate|override_severity|note)$")
    severity: str | None = Field(default=None, pattern=r"^(info|low|medium|high|critical)$")
    note: str | None = Field(default=None, max_length=4000)
    reason: str | None = Field(default=None, max_length=1000)


class ReviewResponse(BaseModel):
    id: str
    action: str
    previous_severity: str | None = None
    new_severity: str | None = None
    previous_status: str | None = None
    new_status: str | None = None
    note: str | None = None
    reason: str | None = None
    reviewer_name: str | None = None
    reviewer_email: str | None = None
    created_at: datetime


# --- usage / admin -----------------------------------------------------------
class UsageResponse(BaseModel):
    period_days: int
    analyses_run: int
    documents_uploaded: int
    input_tokens: int
    cached_input_tokens: int
    output_tokens: int
    total_tokens: int
    estimated_cost_usd: float
    by_event_type: list[dict[str, Any]] = Field(default_factory=list)
    daily: list[dict[str, Any]] = Field(default_factory=list)


class AdminMetricsResponse(BaseModel):
    analyses_total: int
    analyses_succeeded: int
    analyses_partial: int
    analyses_failed: int
    success_rate: float
    error_rate: float
    verification_pass_rate: float | None = None
    average_stage_latency_ms: dict[str, float] = Field(default_factory=dict)
    p95_analysis_seconds: float | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    estimated_cost_usd: float = 0.0
    queue_depth: int = -1
    live_workers: int = -1
    redis_connected: bool = False
    database_connected: bool = False


# --- reports -----------------------------------------------------------------
class ReportStatusResponse(BaseModel):
    """Generation and email-delivery state for an analysis's latest report."""

    analysis_id: str
    analysis_status: str
    report_available: bool
    generation_status: str
    version: int | None = None
    file_size: int | None = None
    generated_at: datetime | None = None
    email_status: str | None = None
    email_attempts: int = 0
    # Masked for display, e.g. a***@example.com. The full address is never
    # returned to the browser.
    email_masked_recipient: str | None = None
    email_sent_at: datetime | None = None
    email_error: str | None = None
    can_resend: bool = False
    # Which cloud served this report. Never the bucket name or object key.
    storage_provider: str | None = None
    download_url_ttl_seconds: int | None = None


# --- action items ------------------------------------------------------------
class ActionItemResponse(BaseModel):
    id: str
    analysis_id: str
    document_id: str
    finding_id: str
    document_title: str | None = None
    vendor_name: str | None = None
    title: str
    description: str
    category: str
    obligation_type: str
    evidence_quote: str
    doc_start_offset: int | None = None
    doc_end_offset: int | None = None
    duration_days: int | None = None
    duration_text: str | None = None
    ai_priority: str
    due_date: date | None = None
    date_status: str
    assignee_id: str | None = None
    priority: str
    status: str
    reviewer_note: str | None = None
    completed_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class ActionItemListResponse(Page):
    items: list[ActionItemResponse]


class ActionItemSummaryResponse(BaseModel):
    open_count: int = 0
    completed_count: int = 0
    overdue_count: int = 0
    due_soon_count: int = 0
    urgent_count: int = 0
    unresolved_date_count: int = 0


class ActionItemUpdateRequest(BaseModel):
    """Only human-editable fields. The machine extraction is never accepted here."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    due_date: date | None = None
    assignee_id: str | None = None
    priority: str | None = Field(default=None, pattern=r"^(low|medium|high|urgent)$")
    status: str | None = Field(default=None, pattern=r"^(open|in_progress|completed|dismissed)$")
    reviewer_note: str | None = Field(default=None, max_length=4000)
    date_status: str | None = Field(
        default=None, pattern=r"^(unresolved|ai_extracted|human_set|not_applicable)$"
    )


class GenerateActionItemsResponse(BaseModel):
    analysis_id: str
    derived: int
    created: int
