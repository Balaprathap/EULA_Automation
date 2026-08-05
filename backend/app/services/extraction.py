"""Per-category extraction: retrieve, prompt, run the bounded tool loop,
validate the structured output, repair once, or fall back to needs_review.

Failure policy: a partial report with explicit ``needs_review`` categories is
always preferable to failing the whole run, dropping categories silently, or
inventing results. Every terminal state here is one of those three explicit
outcomes.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from pydantic import ValidationError

from app.core.errors import ProviderUnavailable
from app.core.logging import get_logger
from app.providers.llm.base import TokenUsage
from app.schemas.extraction import EXTRACTION_JSON_SCHEMA, CategoryExtraction
from app.services.prompts import (
    EXTRACTION_SYSTEM_PROMPT,
    build_extraction_message,
    build_repair_message,
)
from app.services.retrieval import RetrievalMode
from app.services.scoring import cap_confidence_for_degraded_retrieval
from app.services.tools import TOOL_DEFINITIONS, ToolContext, ToolExecutor

logger = get_logger(__name__)

MAX_TOOL_ITERATIONS = 8  # hard stop; the per-category cap normally bites first


class CategoryStatus(str, Enum):
    COMPLETED = "completed"
    ABSTAINED = "abstained"
    NEEDS_REVIEW = "needs_review"
    FAILED = "failed"


@dataclass
class CategoryResult:
    category: str
    status: CategoryStatus
    extraction: CategoryExtraction | None = None
    retrieval_mode: RetrievalMode = RetrievalMode.HYBRID
    degraded_reason: str | None = None
    needs_review_reason: str | None = None
    usage: TokenUsage = field(default_factory=TokenUsage)
    estimated_cost_usd: float = 0.0
    duration_ms: float = 0.0
    tool_calls: int = 0
    chunk_ids: list[str] = field(default_factory=list)
    error_code: str | None = None

    @property
    def findings(self):
        return self.extraction.findings if self.extraction else []


_FENCE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.MULTILINE)


def parse_model_json(text: str) -> dict[str, Any]:
    """Extract a JSON object from the model's reply.

    Tolerates markdown fences and leading prose, because tolerating formatting
    noise is cheap while accepting *malformed* output is not - anything that is
    not a well-formed object still raises.
    """
    if not text or not text.strip():
        raise ValueError("The model returned an empty response.")

    cleaned = _FENCE.sub("", text).strip()
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("No JSON object was found in the model response.") from None
        parsed = json.loads(cleaned[start : end + 1])

    if not isinstance(parsed, dict):
        raise ValueError(f"Expected a JSON object, got {type(parsed).__name__}.")
    return parsed


def format_validation_errors(error: ValidationError) -> str:
    lines = []
    for item in error.errors()[:10]:
        location = ".".join(str(p) for p in item["loc"]) or "(root)"
        lines.append(f"  - {location}: {item['msg']}")
    return "\n".join(lines)


class CategoryExtractor:
    def __init__(
        self,
        llm,
        retriever,
        tool_executor: ToolExecutor,
        *,
        max_tool_calls: int = 5,
        top_k: int = 8,
    ) -> None:
        self.llm = llm
        self.retriever = retriever
        self.tools = tool_executor
        self.max_tool_calls = max_tool_calls
        self.top_k = top_k

    async def extract(
        self,
        *,
        org_id: str,
        analysis_id: str,
        document_id: str,
        document_title: str = "",
        vendor_name: str = "",
        category: str,
        display_name: str,
        description: str,
        retrieval_guidance: str = "",
        keywords: list[str] | None = None,
        definitions: str = "",
    ) -> CategoryResult:
        started = time.perf_counter()
        usage = TokenUsage()
        cost = 0.0

        from app.services.retrieval import build_category_query

        query = build_category_query(
            category=category,
            display_name=display_name,
            description=description,
            retrieval_guidance=retrieval_guidance,
            keywords=keywords,
        )

        try:
            retrieval = await self.retriever.retrieve(
                document_id=document_id, query=query, top_k=self.top_k
            )
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "retrieval failed", extra={"category": category, "error_type": type(exc).__name__}
            )
            return CategoryResult(
                category=category,
                status=CategoryStatus.NEEDS_REVIEW,
                needs_review_reason=f"Retrieval failed for this category ({type(exc).__name__}).",
                error_code="RETRIEVAL_FAILED",
                duration_ms=(time.perf_counter() - started) * 1000,
            )

        if not retrieval.chunks:
            return CategoryResult(
                category=category,
                status=CategoryStatus.NEEDS_REVIEW,
                retrieval_mode=retrieval.mode,
                needs_review_reason="No document text could be retrieved for this category.",
                error_code="NO_CHUNKS",
                duration_ms=(time.perf_counter() - started) * 1000,
            )

        context = ToolContext(
            org_id=org_id,
            document_id=document_id,
            analysis_id=analysis_id,
            category=category,
            max_calls=self.max_tool_calls,
            chunk_ids={c.id for c in retrieval.chunks},
        )

        messages: list[dict[str, Any]] = [
            {
                "role": "user",
                "content": build_extraction_message(
                    category=category,
                    display_name=display_name,
                    description=description,
                    definitions=definitions,
                    vendor_name=vendor_name,
                    chunks=retrieval.chunks,
                    degraded_retrieval=retrieval.degraded,
                )
                + f"\n\nRequired JSON schema:\n{json.dumps(EXTRACTION_JSON_SCHEMA)}",
            }
        ]

        def finish(status: CategoryStatus, **kwargs) -> CategoryResult:
            return CategoryResult(
                category=category,
                status=status,
                retrieval_mode=retrieval.mode,
                degraded_reason=retrieval.degraded_reason,
                usage=usage,
                estimated_cost_usd=round(cost, 8),
                duration_ms=(time.perf_counter() - started) * 1000,
                tool_calls=context.call_count,
                chunk_ids=sorted(context.chunk_ids),
                **kwargs,
            )

        # --- bounded tool-use loop -------------------------------------------
        raw_text = ""
        for _iteration in range(MAX_TOOL_ITERATIONS):
            try:
                response = await self.llm.complete(
                    system=EXTRACTION_SYSTEM_PROMPT, messages=messages, tools=TOOL_DEFINITIONS
                )
            except ProviderUnavailable as exc:
                logger.warning(
                    "provider unavailable", extra={"category": category, "code": exc.code}
                )
                return finish(
                    CategoryStatus.NEEDS_REVIEW,
                    needs_review_reason=f"The AI provider was unavailable: {exc.message}",
                    error_code=exc.code,
                )
            except Exception as exc:  # noqa: BLE001
                logger.error("extraction call failed", extra={"error_type": type(exc).__name__})
                return finish(
                    CategoryStatus.FAILED,
                    needs_review_reason=f"Extraction failed ({type(exc).__name__}).",
                    error_code="EXTRACTION_ERROR",
                )

            usage = usage + response.usage
            cost += response.estimated_cost_usd

            if not response.wants_tools:
                raw_text = response.text
                break

            messages.append({"role": "assistant", "content": response.raw_content})
            blocks: list[dict[str, Any]] = []
            stop = False

            for call in response.tool_calls:
                result = await self.tools.execute(
                    tool_use_id=call.id, name=call.name, arguments=call.input, context=context
                )
                blocks.append(result.to_message_block())
                if result.stop_category:
                    stop = True
                    if result.flag_reason:
                        context.flagged_reason = result.flag_reason

            messages.append({"role": "user", "content": blocks})

            if stop and context.flagged_reason:
                return finish(
                    CategoryStatus.NEEDS_REVIEW,
                    needs_review_reason=f"The model flagged this category: {context.flagged_reason}",
                    error_code="FLAGGED_BY_MODEL",
                )
        else:
            # Loop exhausted without a final answer - never spin forever.
            return finish(
                CategoryStatus.NEEDS_REVIEW,
                needs_review_reason=(
                    "The model did not produce a final answer within the tool-use budget."
                ),
                error_code="TOOL_LOOP_EXHAUSTED",
            )

        # --- validate, repair once, then give up honestly ---------------------
        extraction, error_detail = self._validate(raw_text, category)

        if extraction is None:
            logger.info(
                "structured output invalid, attempting repair", extra={"category": category}
            )
            messages.append({"role": "assistant", "content": raw_text[:4000] or "(empty)"})
            messages.append({"role": "user", "content": build_repair_message(error_detail)})
            try:
                repaired = await self.llm.complete(
                    system=EXTRACTION_SYSTEM_PROMPT, messages=messages, tools=None
                )
                usage = usage + repaired.usage
                cost += repaired.estimated_cost_usd
                extraction, error_detail = self._validate(repaired.text, category)
            except Exception as exc:  # noqa: BLE001
                error_detail = f"{error_detail}\nRepair attempt failed: {type(exc).__name__}"

        if extraction is None:
            return finish(
                CategoryStatus.NEEDS_REVIEW,
                needs_review_reason=(
                    "The model returned output that failed schema validation twice. "
                    "This category requires human review."
                ),
                error_code="INVALID_MODEL_OUTPUT",
            )

        # Degraded retrieval caps the confidence of everything derived from it.
        if retrieval.degraded:
            for finding in extraction.findings:
                finding.confidence = cap_confidence_for_degraded_retrieval(
                    finding.confidence, retrieval.mode.value
                )

        if extraction.abstain or not extraction.findings:
            return finish(
                CategoryStatus.ABSTAINED,
                extraction=extraction,
                needs_review_reason=extraction.needs_review_reason,
            )

        if extraction.needs_review_reason:
            return finish(
                CategoryStatus.NEEDS_REVIEW,
                extraction=extraction,
                needs_review_reason=extraction.needs_review_reason,
            )

        return finish(CategoryStatus.COMPLETED, extraction=extraction)

    def _validate(self, raw: str, category: str):
        try:
            payload = parse_model_json(raw)
        except ValueError as exc:
            return None, f"  - (root): {exc}"

        payload.setdefault("category", category)
        for finding in payload.get("findings") or []:
            if isinstance(finding, dict):
                finding.setdefault("category", category)

        try:
            return CategoryExtraction.model_validate(payload), ""
        except ValidationError as exc:
            return None, format_validation_errors(exc)
