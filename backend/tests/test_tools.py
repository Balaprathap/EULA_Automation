"""Bounded tool use: validation, authorization, and loop safety."""

import pytest

from app.services.tools import (
    MAX_NEIGHBOUR_WINDOW,
    TOOL_DEFINITIONS,
    VALID_TOOL_NAMES,
    ToolContext,
    ToolOutcome,
)

from .conftest import DOC_ID, ORG_ID, OTHER_DOC_ID


def make_context(**overrides) -> ToolContext:
    defaults = {
        "org_id": ORG_ID,
        "document_id": DOC_ID,
        "analysis_id": "analysis-1",
        "category": "data_retention",
        "max_calls": 5,
    }
    defaults.update(overrides)
    return ToolContext(**defaults)


class TestToolDefinitions:
    def test_exactly_three_tools_are_exposed(self):
        assert {
            "search_document",
            "get_neighboring_chunks",
            "flag_for_review",
        } == VALID_TOOL_NAMES

    def test_every_tool_has_a_schema_and_description(self):
        for tool in TOOL_DEFINITIONS:
            assert tool["description"]
            assert tool["input_schema"]["type"] == "object"
            assert tool["input_schema"]["required"]

    def test_no_tool_can_write_or_delete(self):
        names = " ".join(VALID_TOOL_NAMES)
        for verb in ("delete", "write", "update", "create", "execute"):
            assert verb not in names


class TestUnknownAndMalformedCalls:
    @pytest.mark.asyncio
    async def test_unknown_tool_is_rejected(self, tool_executor):
        result = await tool_executor.execute(
            tool_use_id="t1", name="drop_all_findings", arguments={}, context=make_context()
        )
        assert result.outcome is ToolOutcome.REJECTED
        assert result.is_error

    @pytest.mark.asyncio
    async def test_unknown_tool_does_not_consume_budget(self, tool_executor):
        context = make_context()
        await tool_executor.execute(
            tool_use_id="t1", name="nonsense", arguments={}, context=context
        )
        assert context.call_count == 0

    @pytest.mark.asyncio
    async def test_non_object_input_is_rejected(self, tool_executor):
        result = await tool_executor.execute(
            tool_use_id="t1", name="search_document", arguments="not a dict", context=make_context()
        )
        assert result.is_error

    @pytest.mark.asyncio
    async def test_missing_required_argument_is_rejected(self, tool_executor):
        result = await tool_executor.execute(
            tool_use_id="t1", name="search_document", arguments={}, context=make_context()
        )
        assert result.outcome is ToolOutcome.REJECTED

    @pytest.mark.asyncio
    async def test_oversized_query_is_rejected(self, tool_executor):
        result = await tool_executor.execute(
            tool_use_id="t1",
            name="search_document",
            arguments={"query": "x" * 5000},
            context=make_context(),
        )
        assert result.is_error


class TestCrossDocumentAccessIsBlocked:
    @pytest.mark.asyncio
    async def test_search_against_another_document_is_denied(self, tool_executor):
        result = await tool_executor.execute(
            tool_use_id="t1",
            name="search_document",
            arguments={"query": "data retention", "document_id": OTHER_DOC_ID},
            context=make_context(),
        )
        assert result.outcome is ToolOutcome.REJECTED
        assert "Access denied" in result.content

    @pytest.mark.asyncio
    async def test_search_without_document_id_uses_the_analysis_document(self, tool_executor):
        result = await tool_executor.execute(
            tool_use_id="t1",
            name="search_document",
            arguments={"query": "data retention indefinitely"},
            context=make_context(),
        )
        assert result.outcome is ToolOutcome.OK

    @pytest.mark.asyncio
    async def test_matching_document_id_is_allowed(self, tool_executor):
        result = await tool_executor.execute(
            tool_use_id="t1",
            name="search_document",
            arguments={"query": "liability", "document_id": DOC_ID},
            context=make_context(),
        )
        assert result.outcome is ToolOutcome.OK

    @pytest.mark.asyncio
    async def test_neighbours_on_a_foreign_chunk_are_denied(self, retriever):
        from app.services.tools import ToolExecutor

        from .conftest import StoredChunk

        foreign = StoredChunk("foreign-1", OTHER_DOC_ID, 0, None, "text", 0, 4)

        async def lookup(chunk_id):
            return foreign if chunk_id == "foreign-1" else None

        executor = ToolExecutor(retriever, lookup)
        result = await executor.execute(
            tool_use_id="t1",
            name="get_neighboring_chunks",
            arguments={"chunk_id": "foreign-1"},
            context=make_context(),
        )
        assert result.outcome is ToolOutcome.REJECTED
        assert "different document" in result.content


class TestCallLimits:
    @pytest.mark.asyncio
    async def test_budget_is_enforced(self, tool_executor):
        context = make_context(max_calls=3)
        for _ in range(3):
            result = await tool_executor.execute(
                tool_use_id="t",
                name="search_document",
                arguments={"query": "liability"},
                context=context,
            )
            assert result.outcome is ToolOutcome.OK

        blocked = await tool_executor.execute(
            tool_use_id="t4",
            name="search_document",
            arguments={"query": "liability"},
            context=context,
        )
        assert blocked.outcome is ToolOutcome.LIMIT_EXCEEDED
        assert blocked.stop_category is True

    @pytest.mark.asyncio
    async def test_default_budget_is_five(self):
        assert make_context().max_calls == 5

    @pytest.mark.asyncio
    async def test_limit_ends_the_category_rather_than_looping(self, tool_executor):
        context = make_context(max_calls=1)
        await tool_executor.execute(
            tool_use_id="t1",
            name="search_document",
            arguments={"query": "x y z"},
            context=context,
        )
        result = await tool_executor.execute(
            tool_use_id="t2",
            name="search_document",
            arguments={"query": "x y z"},
            context=context,
        )
        assert result.stop_category is True


class TestNeighbouringChunks:
    @pytest.mark.asyncio
    async def test_returns_surrounding_clauses(self, tool_executor, stored_chunks):
        target = stored_chunks[3]
        result = await tool_executor.execute(
            tool_use_id="t1",
            name="get_neighboring_chunks",
            arguments={"chunk_id": target.id, "window": 1},
            context=make_context(),
        )
        assert result.outcome is ToolOutcome.OK
        assert target.id in result.content

    @pytest.mark.asyncio
    async def test_window_is_clamped(self, tool_executor, stored_chunks):
        result = await tool_executor.execute(
            tool_use_id="t1",
            name="get_neighboring_chunks",
            arguments={"chunk_id": stored_chunks[2].id, "window": 999},
            context=make_context(),
        )
        assert result.content.count("<document_chunk") <= (MAX_NEIGHBOUR_WINDOW * 2) + 1

    @pytest.mark.asyncio
    async def test_non_integer_window_falls_back_to_one(self, tool_executor, stored_chunks):
        result = await tool_executor.execute(
            tool_use_id="t1",
            name="get_neighboring_chunks",
            arguments={"chunk_id": stored_chunks[2].id, "window": "lots"},
            context=make_context(),
        )
        assert result.outcome is ToolOutcome.OK

    @pytest.mark.asyncio
    async def test_unknown_chunk_is_an_error(self, tool_executor):
        result = await tool_executor.execute(
            tool_use_id="t1",
            name="get_neighboring_chunks",
            arguments={"chunk_id": "does-not-exist"},
            context=make_context(),
        )
        assert result.is_error


class TestFlagForReview:
    @pytest.mark.asyncio
    async def test_flagging_stops_the_category(self, tool_executor):
        context = make_context()
        result = await tool_executor.execute(
            tool_use_id="t1",
            name="flag_for_review",
            arguments={"reason": "The retention clause contradicts the deletion clause."},
            context=context,
        )
        assert result.outcome is ToolOutcome.FLAGGED
        assert result.stop_category is True
        assert context.flagged_reason.startswith("The retention clause")

    @pytest.mark.asyncio
    async def test_empty_reason_is_rejected(self, tool_executor):
        result = await tool_executor.execute(
            tool_use_id="t1",
            name="flag_for_review",
            arguments={"reason": "   "},
            context=make_context(),
        )
        assert result.is_error

    @pytest.mark.asyncio
    async def test_reason_is_truncated(self, tool_executor):
        context = make_context()
        await tool_executor.execute(
            tool_use_id="t1",
            name="flag_for_review",
            arguments={"reason": "x" * 5000},
            context=context,
        )
        assert len(context.flagged_reason) <= 500


class TestResultShape:
    @pytest.mark.asyncio
    async def test_result_block_is_keyed_to_the_tool_use_id(self, tool_executor):
        result = await tool_executor.execute(
            tool_use_id="toolu_abc123",
            name="search_document",
            arguments={"query": "arbitration"},
            context=make_context(),
        )
        block = result.to_message_block()
        assert block["type"] == "tool_result"
        assert block["tool_use_id"] == "toolu_abc123"

    @pytest.mark.asyncio
    async def test_results_are_size_limited(self, tool_executor):
        result = await tool_executor.execute(
            tool_use_id="t1",
            name="search_document",
            arguments={"query": "agreement customer vendor data license"},
            context=make_context(),
        )
        assert len(result.to_message_block()["content"]) <= 8000
