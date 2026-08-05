"""The analysis pipeline.

Stages, in order, each persisted so a restarted worker resumes rather than
restarts:

    parsing -> chunking -> retrieving -> extracting -> verifying -> scoring

Design commitments:
  * One category failing never fails the run. The analysis completes as
    ``partial`` with that category explicitly marked ``needs_review``.
  * Nothing is displayed as a confirmed finding until its quote has been
    verified against the stored chunk.
  * Severity is computed here, in Python, from policy configuration - the model
    has no input into it.
  * The executive summary is generated only from findings that are already
    verified, persisted, and scored.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from app.core.logging import analysis_id_var, get_logger
from app.services.audit import record_usage
from app.services.chunking import chunk_document
from app.services.extraction import CategoryExtractor, CategoryStatus
from app.services.normalization import content_hash
from app.services.prompts import SUMMARY_SYSTEM_PROMPT, build_summary_message
from app.services.scoring import score_analysis, score_finding
from app.services.verification import VerificationStatus, verify_evidence

logger = get_logger(__name__)


@dataclass
class StoredChunkRow:
    """Adapter giving verification the attribute names it expects."""

    id: str
    document_id: str
    ordinal: int
    text: str
    start_offset: int
    end_offset: int


@dataclass
class PipelineResult:
    status: str
    overall_score: float = 0.0
    risk_band: str = "low"
    finding_count: int = 0
    review_count: int = 0
    quarantine_count: int = 0
    verification_pass_rate: float = 100.0
    executive_summary: str | None = None
    degraded_retrieval: bool = False
    degraded_reason: str | None = None
    stage_timings_ms: dict[str, float] = field(default_factory=dict)
    categories: list[dict[str, Any]] = field(default_factory=list)
    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0
    estimated_cost_usd: float = 0.0
    model_used: str | None = None


class AnalysisPipeline:
    def __init__(
        self,
        *,
        documents,
        chunks,
        policies,
        analyses,
        findings,
        retriever,
        embeddings,
        llm,
        tool_executor,
        max_tool_calls: int = 5,
        top_k: int = 8,
    ) -> None:
        self.documents = documents
        self.chunks = chunks
        self.policies = policies
        self.analyses = analyses
        self.findings = findings
        self.retriever = retriever
        self.embeddings = embeddings
        self.llm = llm
        self.extractor = CategoryExtractor(
            llm, retriever, tool_executor, max_tool_calls=max_tool_calls, top_k=top_k
        )

    async def run(self, analysis: dict[str, Any], worker_id: str) -> PipelineResult:
        analysis_id = str(analysis["id"])
        org_id = str(analysis["org_id"])
        document_id = str(analysis["document_id"])
        policy_id = str(analysis["policy_id"])
        analysis_id_var.set(analysis_id)

        timings: dict[str, float] = {}
        already_done = set(analysis.get("completed_categories") or [])

        # --- parsing / chunking ------------------------------------------------
        started = time.perf_counter()
        await self.analyses.update_stage(analysis_id, "parsing", message="Reading the document")
        document = await self.documents.get(org_id, document_id, with_text=True)
        if document is None:
            raise LookupError("The document no longer exists.")

        text = document.get("normalized_text") or ""
        if not text.strip():
            raise ValueError("The document has no normalized text to analyze.")
        timings["parsing"] = (time.perf_counter() - started) * 1000

        started = time.perf_counter()
        await self.analyses.update_stage(analysis_id, "chunking", message="Splitting into clauses")
        stored = await self.chunks.list_for_document(document_id)
        if not stored:
            produced = chunk_document(text)
            await self.chunks.bulk_insert(
                org_id,
                document_id,
                [
                    {
                        "ordinal": c.ordinal,
                        "heading": c.heading,
                        "chunk_text": c.text,
                        "start_offset": c.start_offset,
                        "end_offset": c.end_offset,
                        "token_count": c.token_count,
                        "content_sha256": content_hash(c.text),
                    }
                    for c in produced
                ],
            )
            stored = await self.chunks.list_for_document(document_id)
        timings["chunking"] = (time.perf_counter() - started) * 1000

        # --- embeddings --------------------------------------------------------
        started = time.perf_counter()
        await self.analyses.update_stage(
            analysis_id, "retrieving", message="Indexing clauses for retrieval"
        )
        pending = await self.chunks.missing_embeddings(document_id)
        if pending:
            result = await self.embeddings.embed([row["chunk_text"] for row in pending])
            for row, vector in zip(pending, result.vectors):
                await self.chunks.set_embedding(str(row["id"]), vector, result.model)
            await record_usage(
                org_id=org_id,
                analysis_id=analysis_id,
                event_type="embedding",
                provider=self.embeddings.name,
                model=result.model,
                input_tokens=result.input_tokens,
                estimated_cost_usd=result.estimated_cost_usd,
                metadata={"chunks": len(pending), "cache_hits": result.cache_hits},
            )
        timings["embedding"] = (time.perf_counter() - started) * 1000

        chunk_index = {
            str(row["id"]): StoredChunkRow(
                id=str(row["id"]),
                document_id=str(row["document_id"]),
                ordinal=row["ordinal"],
                text=row["chunk_text"],
                start_offset=row["start_offset"],
                end_offset=row["end_offset"],
            )
            for row in stored
        }

        # --- extraction --------------------------------------------------------
        rules = await self.policies.list_rules(org_id, policy_id, enabled_only=True)
        if not rules:
            raise ValueError("The selected policy has no enabled rules.")

        await self.analyses.update_stage(
            analysis_id, "extracting", message=f"Reviewing {len(rules)} compliance categories"
        )

        extraction_start = time.perf_counter()
        category_results = []
        totals: dict[str, int] = {"input": 0, "cached": 0, "output": 0}
        total_cost = 0.0
        degraded_any = False
        degraded_reason = None

        for index, rule in enumerate(rules, start=1):
            category = rule["category"]
            if category in already_done:
                logger.info("skipping already-completed category", extra={"category": category})
                continue

            await self.analyses.update_stage(
                analysis_id,
                "extracting",
                message=f"Reviewing {rule['display_name']} ({index} of {len(rules)})",
                completed=index - 1,
            )

            result = await self.extractor.extract(
                org_id=org_id,
                analysis_id=analysis_id,
                document_id=document_id,
                document_title=document.get("title") or "",
                vendor_name=document.get("vendor_name") or "",
                category=category,
                display_name=rule["display_name"],
                description=rule["description"],
                retrieval_guidance=rule.get("retrieval_guidance") or "",
                keywords=list(rule.get("keywords") or []),
            )
            category_results.append((rule, result))

            totals["input"] += result.usage.input_tokens
            totals["cached"] += result.usage.cache_read_input_tokens
            totals["output"] += result.usage.output_tokens
            total_cost += result.estimated_cost_usd

            if result.retrieval_mode.is_degraded:
                degraded_any = True
                degraded_reason = degraded_reason or result.degraded_reason

            await self.analyses.upsert_category(
                org_id,
                analysis_id,
                category=category,
                status=result.status.value,
                needs_review_reason=result.needs_review_reason,
                error_code=result.error_code,
                retrieval_mode=result.retrieval_mode.value,
                degraded_reason=result.degraded_reason,
                tool_calls=result.tool_calls,
                duration_ms=round(result.duration_ms, 2),
                input_tokens=result.usage.input_tokens,
                output_tokens=result.usage.output_tokens,
            )
            await self.analyses.mark_category_complete(analysis_id, category)
            await self.analyses.record_usage(
                analysis_id,
                input_tokens=result.usage.input_tokens,
                cached_tokens=result.usage.cache_read_input_tokens,
                output_tokens=result.usage.output_tokens,
                cost=result.estimated_cost_usd,
            )
            await record_usage(
                org_id=org_id,
                analysis_id=analysis_id,
                event_type="llm_extraction",
                provider=getattr(self.llm, "name", "unknown"),
                model=getattr(self.llm, "model", None),
                input_tokens=result.usage.input_tokens,
                cached_input_tokens=result.usage.cache_read_input_tokens,
                output_tokens=result.usage.output_tokens,
                estimated_cost_usd=result.estimated_cost_usd,
                duration_ms=round(result.duration_ms, 2),
                metadata={"category": category, "status": result.status.value},
            )

        timings["extraction"] = (time.perf_counter() - extraction_start) * 1000

        # --- verification and deterministic scoring ----------------------------
        started = time.perf_counter()
        await self.analyses.update_stage(
            analysis_id, "verifying", message="Verifying every quote against the source document"
        )

        proposed = 0
        verified_severities: list[str] = []
        quarantined = 0
        needs_review = 0
        summary_lines: list[str] = []

        for rule, result in category_results:
            if result.status in (CategoryStatus.NEEDS_REVIEW, CategoryStatus.FAILED):
                needs_review += 1
            for finding in result.findings:
                proposed += 1
                chunk = chunk_index.get(str(finding.chunk_id))
                verification = verify_evidence(
                    chunk=chunk,
                    proposed_quote=finding.quote,
                    document_id=document_id,
                )

                scored = score_finding(
                    confidence=finding.confidence,
                    severity_weight=float(rule["severity_weight"]),
                    threshold=float(rule["confidence_threshold"]),
                    escalate=bool(rule["escalate"]),
                    degraded_retrieval=result.retrieval_mode.is_degraded,
                )

                if verification.status is VerificationStatus.QUARANTINED:
                    quarantined += 1
                elif verification.status is VerificationStatus.NEEDS_REVIEW:
                    needs_review += 1
                else:
                    verified_severities.append(scored.machine_severity)
                    summary_lines.append(
                        f"- [{scored.machine_severity}] {rule['display_name']}: "
                        f"{finding.plain_summary}"
                    )

                await self.findings.create_with_evidence(
                    finding={
                        "org_id": org_id,
                        "analysis_id": analysis_id,
                        "document_id": document_id,
                        "policy_rule_id": rule["id"],
                        "chunk_id": chunk.id if chunk else None,
                        "category": rule["category"],
                        "plain_summary": finding.plain_summary,
                        "why_it_matters": finding.why_it_matters,
                        "model_confidence": round(finding.confidence, 3),
                        "severity_weight": float(rule["severity_weight"]),
                        "confidence_threshold": float(rule["confidence_threshold"]),
                        "weighted_risk": scored.weighted_risk,
                        "machine_severity": scored.machine_severity,
                        "severity_source": scored.severity_source.value,
                        "scoring_explanation": scored.scoring_explanation,
                        "verification_status": verification.status.value,
                        "quarantine_reason": verification.detail
                        if not verification.verified
                        else None,
                        "degraded_retrieval": result.retrieval_mode.is_degraded,
                    },
                    evidence=(
                        {
                            "quote": verification.matched_quote,
                            "doc_start_offset": verification.doc_start_offset,
                            "doc_end_offset": verification.doc_end_offset,
                            "chunk_start_offset": verification.chunk_start_offset,
                            "chunk_end_offset": verification.chunk_end_offset,
                            "verification_method": (
                                verification.method.value if verification.method else "offset_exact"
                            ),
                        }
                        if verification.verified
                        else None
                    ),
                )

        timings["verification"] = (time.perf_counter() - started) * 1000

        started = time.perf_counter()
        await self.analyses.update_stage(analysis_id, "scoring", message="Calculating risk score")
        score = score_analysis(
            verified_severities,
            review_count=needs_review,
            quarantine_count=quarantined,
            verified_count=len(verified_severities),
            proposed_count=proposed,
        )
        timings["scoring"] = (time.perf_counter() - started) * 1000

        # --- executive summary (verified findings only) ------------------------
        summary = None
        if summary_lines:
            try:
                response = await self.llm.complete(
                    system=SUMMARY_SYSTEM_PROMPT,
                    messages=[
                        {
                            "role": "user",
                            "content": build_summary_message(
                                "\n".join(summary_lines[:40]),
                                document_title=document.get("title") or "this agreement",
                                risk_band=score.risk_band,
                            ),
                        }
                    ],
                    max_tokens=600,
                )
                summary = response.text.strip() or None
                totals["input"] += response.usage.input_tokens
                totals["output"] += response.usage.output_tokens
                total_cost += response.estimated_cost_usd
                await record_usage(
                    org_id=org_id,
                    analysis_id=analysis_id,
                    event_type="llm_summary",
                    provider=getattr(self.llm, "name", "unknown"),
                    model=getattr(self.llm, "model", None),
                    input_tokens=response.usage.input_tokens,
                    output_tokens=response.usage.output_tokens,
                    estimated_cost_usd=response.estimated_cost_usd,
                )
            except Exception as exc:  # noqa: BLE001 - a missing summary is not a failure
                logger.warning(
                    "summary generation failed", extra={"error_type": type(exc).__name__}
                )

        failed_categories = [
            r.category
            for _rule, r in category_results
            if r.status in (CategoryStatus.NEEDS_REVIEW, CategoryStatus.FAILED)
        ]
        status = "partial" if failed_categories else "complete"

        return PipelineResult(
            status=status,
            overall_score=score.overall_score,
            risk_band=score.risk_band,
            finding_count=score.finding_count,
            review_count=needs_review,
            quarantine_count=quarantined,
            verification_pass_rate=score.verification_pass_rate,
            executive_summary=summary,
            degraded_retrieval=degraded_any,
            degraded_reason=degraded_reason,
            stage_timings_ms={k: round(v, 2) for k, v in timings.items()},
            categories=[
                {"category": r.category, "status": r.status.value} for _rule, r in category_results
            ],
            input_tokens=totals["input"],
            cached_input_tokens=totals["cached"],
            output_tokens=totals["output"],
            estimated_cost_usd=round(total_cost, 6),
            model_used=getattr(self.llm, "model", None),
        )
