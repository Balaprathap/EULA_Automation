"""Ask ClauseGuard about one uploaded agreement."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.api.deps import enforce_request_rate_limit
from app.core.config import get_settings
from app.core.errors import NotFound
from app.core.security import AuthenticatedUser
from app.db.session import fetch_all, fetch_one
from app.services.audit import record_usage
from app.services.chat import ChatProviderError, GroqChatService

router = APIRouter(prefix="/chat", tags=["chat"])


class ChatHistoryMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=4000)


class ChatRequest(BaseModel):
    document_id: str
    message: str = Field(min_length=2, max_length=2000)
    history: list[ChatHistoryMessage] = Field(default_factory=list, max_length=8)


class ChatCitation(BaseModel):
    ref: str
    type: Literal["chunk", "finding"]
    chunk_id: str | None = None
    finding_id: str | None = None
    ordinal: int | None = None
    heading: str | None = None
    category: str | None = None
    severity: str | None = None
    quote: str | None = None
    doc_start_offset: int | None = None
    doc_end_offset: int | None = None


class ChatResponse(BaseModel):
    answer: str
    citations: list[ChatCitation]
    model: str
    usage: dict[str, Any] = Field(default_factory=dict)


@router.post("", response_model=ChatResponse)
async def ask_clauseguard(
    payload: ChatRequest,
    user: AuthenticatedUser = Depends(enforce_request_rate_limit),
):
    document = await fetch_one(
        """
        SELECT id, title, vendor_name
        FROM documents
        WHERE id = $1
          AND org_id = $2
          AND deleted_at IS NULL
        """,
        payload.document_id,
        user.org_id,
    )

    if document is None:
        raise NotFound("That document does not exist, or you do not have access to it.")

    chunks = await fetch_all(
        """
        SELECT id, ordinal, heading, chunk_text, start_offset, end_offset
        FROM document_chunks
        WHERE org_id = $1
          AND document_id = $2
          AND fts @@ plainto_tsquery('english', $3)
        ORDER BY ts_rank_cd(fts, plainto_tsquery('english', $3)) DESC
        LIMIT 6
        """,
        user.org_id,
        payload.document_id,
        payload.message,
    )

    # Broad questions such as "What are the biggest risks?" may not share exact
    # terms with the agreement. Use a bounded beginning-of-document fallback.
    if not chunks:
        chunks = await fetch_all(
            """
            SELECT id, ordinal, heading, chunk_text, start_offset, end_offset
            FROM document_chunks
            WHERE org_id = $1
              AND document_id = $2
            ORDER BY ordinal
            LIMIT 6
            """,
            user.org_id,
            payload.document_id,
        )

    analysis = await fetch_one(
        """
        SELECT id
        FROM analyses
        WHERE org_id = $1
          AND document_id = $2
          AND status IN ('complete', 'partial')
        ORDER BY created_at DESC
        LIMIT 1
        """,
        user.org_id,
        payload.document_id,
    )

    finding_rows: list[dict] = []

    if analysis:
        finding_rows = await fetch_all(
            """
            SELECT
                f.id,
                f.category,
                f.plain_summary,
                f.why_it_matters,
                COALESCE(f.override_severity, f.machine_severity) AS severity,
                f.model_confidence,
                e.quote,
                e.doc_start_offset,
                e.doc_end_offset,
                c.id AS chunk_id,
                c.ordinal,
                c.heading
            FROM findings f
            LEFT JOIN finding_evidence e ON e.finding_id = f.id
            LEFT JOIN document_chunks c ON c.id = f.chunk_id
            WHERE f.org_id = $1
              AND f.analysis_id = $2
              AND f.verification_status = 'verified'
            ORDER BY
                CASE COALESCE(f.override_severity, f.machine_severity)
                    WHEN 'critical' THEN 0
                    WHEN 'high' THEN 1
                    WHEN 'medium' THEN 2
                    WHEN 'low' THEN 3
                    ELSE 4
                END,
                f.model_confidence DESC
            LIMIT 6
            """,
            user.org_id,
            str(analysis["id"]),
        )

    sources: list[dict[str, Any]] = []

    for index, chunk in enumerate(chunks, start=1):
        sources.append(
            {
                "ref": f"C{index}",
                "type": "chunk",
                "chunk_id": str(chunk["id"]),
                "ordinal": int(chunk["ordinal"]),
                "heading": chunk.get("heading"),
                "text": chunk["chunk_text"],
                "quote": chunk["chunk_text"][:900],
                "doc_start_offset": chunk.get("start_offset"),
                "doc_end_offset": chunk.get("end_offset"),
            }
        )

    for index, finding in enumerate(finding_rows, start=1):
        sources.append(
            {
                "ref": f"F{index}",
                "type": "finding",
                "finding_id": str(finding["id"]),
                "chunk_id": str(finding["chunk_id"]) if finding.get("chunk_id") else None,
                "ordinal": finding.get("ordinal"),
                "heading": finding.get("heading"),
                "category": finding["category"],
                "severity": finding["severity"],
                "summary": finding["plain_summary"],
                "why_it_matters": finding["why_it_matters"],
                "quote": finding.get("quote"),
                "doc_start_offset": finding.get("doc_start_offset"),
                "doc_end_offset": finding.get("doc_end_offset"),
            }
        )

    if not sources:
        raise HTTPException(
            status_code=422,
            detail="This document does not have searchable text yet.",
        )

    settings = get_settings()
    service = GroqChatService(settings)

    history = [item.model_dump() for item in payload.history]

    try:
        answer, cited_refs, usage = await service.answer(
            question=payload.message,
            sources=sources,
            history=history,
        )
    except ChatProviderError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    source_by_ref = {source["ref"]: source for source in sources}

    citations: list[ChatCitation] = []

    for ref in cited_refs:
        source = source_by_ref.get(ref)

        # Never trust a model-generated citation identifier unless the server
        # actually supplied that identifier in this request.
        if not source:
            continue

        citations.append(
            ChatCitation(
                ref=ref,
                type=source["type"],
                chunk_id=source.get("chunk_id"),
                finding_id=source.get("finding_id"),
                ordinal=source.get("ordinal"),
                heading=source.get("heading"),
                category=source.get("category"),
                severity=source.get("severity"),
                quote=source.get("quote"),
                doc_start_offset=source.get("doc_start_offset"),
                doc_end_offset=source.get("doc_end_offset"),
            )
        )

    await record_usage(
        org_id=user.org_id,
        analysis_id=str(analysis["id"]) if analysis else None,
        actor_id=user.user_id,
        event_type="chat",
        provider="groq",
        model=settings.groq_model,
        input_tokens=int(usage.get("prompt_tokens") or 0),
        output_tokens=int(usage.get("completion_tokens") or 0),
        estimated_cost_usd=0.0,
    )

    return ChatResponse(
        answer=answer,
        citations=citations,
        model=settings.groq_model,
        usage={
            "input_tokens": int(usage.get("prompt_tokens") or 0),
            "output_tokens": int(usage.get("completion_tokens") or 0),
            "total_tokens": int(usage.get("total_tokens") or 0),
        },
    )
