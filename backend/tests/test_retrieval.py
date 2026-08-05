"""Hybrid retrieval, RRF, and the degradation chain."""

import pytest

from app.providers.embedding.deterministic import DeterministicEmbeddingProvider
from app.services.retrieval import (
    HybridRetriever,
    RetrievalMode,
    RetrievedChunk,
    build_category_query,
    reciprocal_rank_fusion,
)


def make_chunk(i: int, text: str = "clause text") -> RetrievedChunk:
    return RetrievedChunk(
        id=f"chunk-{i}",
        document_id="doc-1",
        ordinal=i,
        heading=None,
        text=text,
        start_offset=i * 100,
        end_offset=i * 100 + len(text),
    )


class TestReciprocalRankFusion:
    def test_single_ranking_preserves_order(self):
        fused = reciprocal_rank_fusion([["a", "b", "c"]])
        assert [item for item, _ in fused] == ["a", "b", "c"]

    def test_item_ranked_by_both_beats_item_ranked_by_one(self):
        fused = dict(reciprocal_rank_fusion([["a", "b"], ["b", "a"]]))
        solo = dict(reciprocal_rank_fusion([["a", "b"], ["c", "d"]]))
        assert fused["a"] > solo["a"]

    def test_consensus_wins_over_a_single_top_rank(self):
        # "x" is #1 in one list only; "y" is #2 in both.
        fused = dict(reciprocal_rank_fusion([["x", "y"], ["z", "y"]]))
        assert fused["y"] > fused["x"]

    def test_scores_follow_the_rrf_formula(self):
        fused = dict(reciprocal_rank_fusion([["a"]], k=60))
        assert fused["a"] == pytest.approx(1 / 61)

    def test_weights_are_applied(self):
        fused = dict(reciprocal_rank_fusion([["a"], ["b"]], weights=[2.0, 1.0]))
        assert fused["a"] == pytest.approx(2 * fused["b"])

    def test_mismatched_weights_raise(self):
        with pytest.raises(ValueError):
            reciprocal_rank_fusion([["a"], ["b"]], weights=[1.0])

    def test_empty_input(self):
        assert reciprocal_rank_fusion([]) == []
        assert reciprocal_rank_fusion([[], []]) == []

    def test_is_deterministic_across_runs(self):
        rankings = [["a", "b", "c"], ["c", "a", "d"]]
        assert reciprocal_rank_fusion(rankings) == reciprocal_rank_fusion(rankings)

    def test_ties_break_deterministically(self):
        # "a" and "b" are symmetric; first appearance must decide.
        assert [i for i, _ in reciprocal_rank_fusion([["a", "b"], ["a", "b"]])] == ["a", "b"]


class TestCategoryQuery:
    def test_combines_all_signal_sources(self):
        query = build_category_query(
            category="data_retention",
            display_name="Data Retention",
            description="How long the vendor keeps data.",
            retrieval_guidance="Look for retention periods.",
            keywords=["retain", "deletion"],
        )
        for token in ("Data Retention", "keeps data", "retention periods", "retain", "deletion"):
            assert token in query

    def test_keywords_are_repeated_for_fts_weighting(self):
        assert build_category_query(category="x", keywords=["indemnify"]).count("indemnify") == 2

    def test_bare_category_still_produces_a_query(self):
        assert build_category_query(category="class_action_waiver") == "class action waiver"


class FakeBackend:
    def __init__(self, dense=None, keyword=None, scan=None, fail_dense=False, fail_keyword=False):
        self._dense = dense if dense is not None else []
        self._keyword = keyword if keyword is not None else []
        self._scan = scan if scan is not None else []
        self.fail_dense = fail_dense
        self.fail_keyword = fail_keyword
        self.scan_calls = 0

    async def dense_search(self, document_id, embedding, limit):
        if self.fail_dense:
            raise ConnectionError("pgvector index unavailable")
        return list(self._dense[:limit])

    async def keyword_search(self, document_id, query, limit):
        if self.fail_keyword:
            raise ConnectionError("tsquery failed")
        return list(self._keyword[:limit])

    async def ordinal_scan(self, document_id, limit):
        self.scan_calls += 1
        return list(self._scan[:limit])

    async def get_by_ordinal_range(self, document_id, start, end):
        return [make_chunk(i) for i in range(start, end + 1)]


@pytest.fixture
def embeddings():
    return DeterministicEmbeddingProvider(model="test", dimensions=64)


class TestFallbackChain:
    @pytest.mark.asyncio
    async def test_both_retrievers_available_uses_hybrid(self, embeddings):
        backend = FakeBackend(dense=[make_chunk(1), make_chunk(2)], keyword=[make_chunk(2)])
        result = await HybridRetriever(backend, embeddings, top_k=4).retrieve(
            document_id="doc-1", query="data retention"
        )
        assert result.mode is RetrievalMode.HYBRID
        assert result.degraded is False
        # chunk-2 appears in both rankings, so RRF should lift it to the top.
        assert result.chunks[0].id == "chunk-2"

    @pytest.mark.asyncio
    async def test_keyword_failure_degrades_to_dense(self, embeddings):
        backend = FakeBackend(dense=[make_chunk(1)], fail_keyword=True)
        result = await HybridRetriever(backend, embeddings).retrieve(document_id="doc-1", query="q")
        assert result.mode is RetrievalMode.DENSE
        assert result.degraded is True
        assert "tsquery" in result.degraded_reason

    @pytest.mark.asyncio
    async def test_dense_failure_degrades_to_keyword(self, embeddings):
        backend = FakeBackend(keyword=[make_chunk(3)], fail_dense=True)
        result = await HybridRetriever(backend, embeddings).retrieve(document_id="doc-1", query="q")
        assert result.mode is RetrievalMode.KEYWORD
        assert result.degraded is True
        assert "pgvector" in result.degraded_reason

    @pytest.mark.asyncio
    async def test_total_failure_degrades_to_bounded_ordinal_scan(self, embeddings):
        backend = FakeBackend(
            scan=[make_chunk(i) for i in range(6)], fail_dense=True, fail_keyword=True
        )
        result = await HybridRetriever(backend, embeddings, top_k=3).retrieve(
            document_id="doc-1", query="q"
        )
        assert result.mode is RetrievalMode.ORDINAL_SCAN
        assert result.degraded is True
        assert backend.scan_calls == 1
        assert len(result.chunks) == 3

    @pytest.mark.asyncio
    async def test_degradation_is_never_silent(self, embeddings):
        backend = FakeBackend(dense=[make_chunk(1)], fail_keyword=True)
        result = await HybridRetriever(backend, embeddings).retrieve(document_id="doc-1", query="q")
        assert result.degraded_reason, "a degraded retrieval must always explain itself"

    @pytest.mark.asyncio
    async def test_top_k_is_respected(self, embeddings):
        chunks = [make_chunk(i) for i in range(20)]
        backend = FakeBackend(dense=chunks, keyword=list(reversed(chunks)))
        result = await HybridRetriever(backend, embeddings, top_k=5).retrieve(
            document_id="doc-1", query="q"
        )
        assert len(result.chunks) == 5

    @pytest.mark.asyncio
    async def test_ranks_are_recorded_for_explainability(self, embeddings):
        backend = FakeBackend(dense=[make_chunk(1)], keyword=[make_chunk(1)])
        result = await HybridRetriever(backend, embeddings).retrieve(document_id="doc-1", query="q")
        assert result.chunks[0].dense_rank == 1
        assert result.chunks[0].keyword_rank == 1


class TestNeighbours:
    @pytest.mark.asyncio
    async def test_window_is_clamped(self, embeddings):
        retriever = HybridRetriever(FakeBackend(), embeddings)
        wide = await retriever.neighbours(document_id="doc-1", ordinal=10, window=99)
        assert len(wide) == 7  # window clamped to 3 -> ordinals 7..13

    @pytest.mark.asyncio
    async def test_does_not_go_below_zero(self, embeddings):
        retriever = HybridRetriever(FakeBackend(), embeddings)
        chunks = await retriever.neighbours(document_id="doc-1", ordinal=0, window=2)
        assert min(c.ordinal for c in chunks) == 0


class TestDeterministicEmbeddings:
    @pytest.mark.asyncio
    async def test_identical_text_yields_identical_vectors(self, embeddings):
        a = await embeddings.embed_one("data retention clause")
        b = await embeddings.embed_one("data retention clause")
        assert a == b

    @pytest.mark.asyncio
    async def test_vectors_are_unit_length(self, embeddings):
        vector = await embeddings.embed_one("indemnification")
        assert sum(v * v for v in vector) == pytest.approx(1.0, abs=1e-6)

    @pytest.mark.asyncio
    async def test_dimension_matches_configuration(self, embeddings):
        assert len(await embeddings.embed_one("x")) == 64

    @pytest.mark.asyncio
    async def test_related_text_is_closer_than_unrelated_text(self, embeddings):
        base = await embeddings.embed_one("data retention deletion period")
        near = await embeddings.embed_one("data retention deletion schedule")
        far = await embeddings.embed_one("governing law jurisdiction venue")
        dot = lambda x, y: sum(a * b for a, b in zip(x, y))  # noqa: E731
        assert dot(base, near) > dot(base, far)

    @pytest.mark.asyncio
    async def test_cache_avoids_recomputation(self, embeddings):
        await embeddings.embed(["a clause", "b clause"])
        result = await embeddings.embed(["a clause", "b clause"])
        assert result.cache_hits == 2
        assert result.cache_misses == 0

    @pytest.mark.asyncio
    async def test_empty_input_is_handled(self, embeddings):
        result = await embeddings.embed([])
        assert result.vectors == []

    @pytest.mark.asyncio
    async def test_batching_covers_all_inputs(self):
        provider = DeterministicEmbeddingProvider(model="t", dimensions=32, batch_size=3)
        result = await provider.embed([f"clause {i}" for i in range(10)])
        assert len(result.vectors) == 10

    @pytest.mark.asyncio
    async def test_dimension_mismatch_is_rejected(self, embeddings):
        class BadProvider(DeterministicEmbeddingProvider):
            async def _embed_batch(self, texts):
                return [[0.0] * 5 for _ in texts]

        provider = BadProvider(model="t", dimensions=64)
        with pytest.raises(ValueError, match="EMBEDDING_DIMENSIONS"):
            await provider.embed(["x"])
