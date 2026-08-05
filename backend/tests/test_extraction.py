"""Structured-output validation, the repair path, and failure handling."""

import pytest

from app.core.errors import ProviderRateLimited, ProviderUnavailable
from app.providers.llm.base import LLMResponse, TokenUsage, ToolCall
from app.services.extraction import (
    CategoryExtractor,
    CategoryStatus,
    parse_model_json,
)
from app.services.tools import ToolExecutor

from .conftest import DOC_ID, ORG_ID

VALID_JSON = """
{"category": "data_retention", "abstain": false, "findings": [
  {"category": "data_retention", "chunk_id": "%s", "quote": "%s",
   "start_offset": 0, "end_offset": 40, "confidence": 0.9,
   "plain_summary": "Data is kept indefinitely.",
   "why_it_matters": "There is no guaranteed deletion."}]}
"""


@pytest.fixture
def extractor(fake_llm, retriever, chunk_lookup):
    return CategoryExtractor(fake_llm, retriever, ToolExecutor(retriever, chunk_lookup))


async def run(extractor, **overrides):
    kwargs = {
        "org_id": ORG_ID,
        "analysis_id": "a1",
        "document_id": DOC_ID,
        "category": "data_retention",
        "display_name": "Data Retention",
        "description": "How long the vendor retains customer data.",
        "keywords": ["retain", "retention", "delete"],
    }
    kwargs.update(overrides)
    return await extractor.extract(**kwargs)


class TestJsonParsing:
    def test_plain_object(self):
        assert parse_model_json('{"a": 1}') == {"a": 1}

    def test_markdown_fence_is_tolerated(self):
        assert parse_model_json('```json\n{"a": 1}\n```') == {"a": 1}

    def test_leading_prose_is_tolerated(self):
        assert parse_model_json('Here you go:\n{"a": 1}') == {"a": 1}

    def test_empty_response_raises(self):
        with pytest.raises(ValueError, match="empty"):
            parse_model_json("")

    def test_non_json_raises(self):
        with pytest.raises(ValueError):
            parse_model_json("I could not complete this task.")

    def test_json_array_is_rejected(self):
        with pytest.raises(ValueError, match="Expected a JSON object"):
            parse_model_json("[1, 2, 3]")


class TestHappyPath:
    @pytest.mark.asyncio
    async def test_valid_output_completes(self, extractor, fake_llm, stored_chunks):
        chunk = stored_chunks[0]
        fake_llm.queue_text(VALID_JSON % (chunk.id, chunk.text[:40].replace('"', "'")))
        result = await run(extractor)
        assert result.status is CategoryStatus.COMPLETED
        assert len(result.findings) == 1

    @pytest.mark.asyncio
    async def test_usage_and_cost_are_accumulated(self, extractor, fake_llm, stored_chunks):
        chunk = stored_chunks[0]
        fake_llm.queue(
            LLMResponse(
                text=VALID_JSON % (chunk.id, chunk.text[:40].replace('"', "'")),
                stop_reason="end_turn",
                usage=TokenUsage(input_tokens=1200, output_tokens=300, cache_read_input_tokens=800),
                model="fake",
                estimated_cost_usd=0.0071,
            )
        )
        result = await run(extractor)
        assert result.usage.input_tokens == 1200
        assert result.usage.cache_read_input_tokens == 800
        assert result.estimated_cost_usd == pytest.approx(0.0071)
        assert result.duration_ms > 0

    @pytest.mark.asyncio
    async def test_abstention_is_recorded_distinctly(self, extractor, fake_llm):
        fake_llm.queue_text('{"category": "data_retention", "abstain": true, "findings": []}')
        result = await run(extractor)
        assert result.status is CategoryStatus.ABSTAINED

    @pytest.mark.asyncio
    async def test_the_whole_document_is_not_sent(self, extractor, fake_llm, sample_text):
        fake_llm.queue_text('{"category": "data_retention", "abstain": true, "findings": []}')
        await run(extractor)
        sent = fake_llm.calls[0]["messages"][0]["content"]
        assert len(sent) < len(sample_text) + 6000
        assert sample_text not in sent


class TestValidationAndRepair:
    @pytest.mark.asyncio
    async def test_invalid_output_triggers_exactly_one_repair(
        self, extractor, fake_llm, stored_chunks
    ):
        chunk = stored_chunks[0]
        fake_llm.queue_text('{"category": "data_retention", "findings": "not an array"}')
        fake_llm.queue_text(VALID_JSON % (chunk.id, chunk.text[:40].replace('"', "'")))
        result = await run(extractor)
        assert result.status is CategoryStatus.COMPLETED
        assert len(fake_llm.calls) == 2

    @pytest.mark.asyncio
    async def test_repair_prompt_includes_the_validation_errors(self, extractor, fake_llm):
        fake_llm.queue_text('{"category": "x", "abstain": "maybe"}')
        fake_llm.queue_text('{"category": "data_retention", "abstain": true, "findings": []}')
        await run(extractor)
        repair_prompt = fake_llm.calls[1]["messages"][-1]["content"]
        assert "did not match the required schema" in repair_prompt
        assert "Do not invent findings" in repair_prompt

    @pytest.mark.asyncio
    async def test_twice_invalid_becomes_needs_review_not_a_guess(self, extractor, fake_llm):
        fake_llm.queue_text("this is not json")
        fake_llm.queue_text("still not json")
        result = await run(extractor)
        assert result.status is CategoryStatus.NEEDS_REVIEW
        assert result.error_code == "INVALID_MODEL_OUTPUT"
        assert result.findings == []

    @pytest.mark.asyncio
    async def test_out_of_range_confidence_is_rejected(self, extractor, fake_llm, stored_chunks):
        bad = VALID_JSON.replace('"confidence": 0.9', '"confidence": 7.5') % (
            stored_chunks[0].id,
            "quote text",
        )
        fake_llm.queue_text(bad)
        fake_llm.queue_text('{"category": "data_retention", "abstain": true, "findings": []}')
        result = await run(extractor)
        assert result.status is CategoryStatus.ABSTAINED  # repaired into an abstention

    @pytest.mark.asyncio
    async def test_inverted_offsets_are_rejected(self):
        from pydantic import ValidationError

        from app.schemas.extraction import ProposedFinding

        with pytest.raises(ValidationError, match="end_offset"):
            ProposedFinding(
                category="c",
                chunk_id="c1",
                quote="a sufficiently long quote",
                start_offset=100,
                end_offset=10,
                confidence=0.5,
                plain_summary="s",
                why_it_matters="w",
            )

    @pytest.mark.asyncio
    async def test_findings_must_be_empty_when_abstaining(self):
        from pydantic import ValidationError

        from app.schemas.extraction import CategoryExtraction

        with pytest.raises(ValidationError):
            CategoryExtraction(
                category="c",
                abstain=True,
                findings=[
                    {
                        "category": "c",
                        "chunk_id": "c1",
                        "quote": "a sufficiently long quote",
                        "start_offset": 0,
                        "end_offset": 20,
                        "confidence": 0.5,
                        "plain_summary": "s",
                        "why_it_matters": "w",
                    }
                ],
            )


class TestProviderFailureHandling:
    @pytest.mark.asyncio
    async def test_rate_limit_yields_needs_review_not_a_crash(self, extractor, fake_llm):
        fake_llm.queue(ProviderRateLimited())
        result = await run(extractor)
        assert result.status is CategoryStatus.NEEDS_REVIEW
        assert result.error_code == "PROVIDER_RATE_LIMITED"

    @pytest.mark.asyncio
    async def test_provider_overload_yields_needs_review(self, extractor, fake_llm):
        fake_llm.queue(ProviderUnavailable("overloaded"))
        result = await run(extractor)
        assert result.status is CategoryStatus.NEEDS_REVIEW
        assert result.error_code == "PROVIDER_UNAVAILABLE"

    @pytest.mark.asyncio
    async def test_unexpected_error_is_contained_to_the_category(self, extractor, fake_llm):
        fake_llm.queue(RuntimeError("something exploded"))
        result = await run(extractor)
        assert result.status is CategoryStatus.FAILED
        assert result.error_code == "EXTRACTION_ERROR"


class TestToolLoopSafety:
    @pytest.mark.asyncio
    async def test_a_tool_call_is_followed_by_a_final_answer(
        self, extractor, fake_llm, stored_chunks
    ):
        chunk = stored_chunks[0]
        fake_llm.queue(
            LLMResponse(
                text="",
                stop_reason="tool_use",
                usage=TokenUsage(input_tokens=100, output_tokens=20),
                model="fake",
                tool_calls=[
                    ToolCall(id="t1", name="search_document", input={"query": "retention"})
                ],
                raw_content=[
                    {
                        "type": "tool_use",
                        "id": "t1",
                        "name": "search_document",
                        "input": {"query": "retention"},
                    }
                ],
            )
        )
        fake_llm.queue_text(VALID_JSON % (chunk.id, chunk.text[:40].replace('"', "'")))
        result = await run(extractor)
        assert result.status is CategoryStatus.COMPLETED
        assert result.tool_calls == 1

    @pytest.mark.asyncio
    async def test_flag_for_review_ends_the_category(self, extractor, fake_llm):
        fake_llm.queue(
            LLMResponse(
                text="",
                stop_reason="tool_use",
                usage=TokenUsage(input_tokens=50, output_tokens=10),
                model="fake",
                tool_calls=[
                    ToolCall(id="t1", name="flag_for_review", input={"reason": "Clauses conflict."})
                ],
                raw_content=[
                    {
                        "type": "tool_use",
                        "id": "t1",
                        "name": "flag_for_review",
                        "input": {"reason": "Clauses conflict."},
                    }
                ],
            )
        )
        result = await run(extractor)
        assert result.status is CategoryStatus.NEEDS_REVIEW
        assert result.error_code == "FLAGGED_BY_MODEL"
        assert "Clauses conflict" in result.needs_review_reason

    @pytest.mark.asyncio
    async def test_an_infinite_tool_loop_terminates(self, extractor, fake_llm):
        def always_tool(**_kwargs):
            return LLMResponse(
                text="",
                stop_reason="tool_use",
                usage=TokenUsage(input_tokens=10, output_tokens=5),
                model="fake",
                tool_calls=[ToolCall(id="t", name="search_document", input={"query": "again"})],
                raw_content=[
                    {
                        "type": "tool_use",
                        "id": "t",
                        "name": "search_document",
                        "input": {"query": "again"},
                    }
                ],
            )

        for _ in range(30):
            fake_llm.queue(always_tool)

        result = await run(extractor)
        assert result.status is CategoryStatus.NEEDS_REVIEW
        assert result.error_code in ("TOOL_LOOP_EXHAUSTED", "FLAGGED_BY_MODEL")
        assert len(fake_llm.calls) <= 10, "the tool loop must be bounded"


class TestDegradedRetrieval:
    @pytest.mark.asyncio
    async def test_degradation_caps_confidence_and_is_reported(
        self, fake_llm, embeddings, stored_chunks, chunk_lookup
    ):
        from app.services.retrieval import HybridRetriever, RetrievalMode

        from .conftest import InMemoryBackend

        class KeywordOnlyBackend(InMemoryBackend):
            async def dense_search(self, *args, **kwargs):
                raise ConnectionError("pgvector down")

        backend = KeywordOnlyBackend(stored_chunks, embeddings)
        retriever = HybridRetriever(backend, embeddings, top_k=8)
        extractor = CategoryExtractor(fake_llm, retriever, ToolExecutor(retriever, chunk_lookup))

        chunk = stored_chunks[0]
        fake_llm.queue_text(VALID_JSON % (chunk.id, chunk.text[:40].replace('"', "'")))
        result = await extractor.extract(
            org_id=ORG_ID,
            analysis_id="a1",
            document_id=DOC_ID,
            category="data_retention",
            display_name="Data Retention",
            description="Retention of customer data and deletion obligations.",
            keywords=["retention", "delete", "data"],
        )

        assert result.retrieval_mode is RetrievalMode.KEYWORD
        assert result.degraded_reason
        if result.findings:
            assert result.findings[0].confidence <= 0.70

    @pytest.mark.asyncio
    async def test_degraded_note_reaches_the_prompt(
        self, fake_llm, embeddings, stored_chunks, chunk_lookup
    ):
        from app.services.retrieval import HybridRetriever

        from .conftest import InMemoryBackend

        class KeywordOnlyBackend(InMemoryBackend):
            async def dense_search(self, *args, **kwargs):
                raise ConnectionError("pgvector down")

        retriever = HybridRetriever(KeywordOnlyBackend(stored_chunks, embeddings), embeddings)
        extractor = CategoryExtractor(fake_llm, retriever, ToolExecutor(retriever, chunk_lookup))
        fake_llm.queue_text('{"category": "data_retention", "abstain": true, "findings": []}')
        await extractor.extract(
            org_id=ORG_ID,
            analysis_id="a1",
            document_id=DOC_ID,
            category="data_retention",
            display_name="Data Retention",
            description="Retention of customer data.",
            keywords=["retention", "data"],
        )
        assert (
            "retrieval for this category was degraded"
            in fake_llm.calls[0]["messages"][0]["content"]
        )
