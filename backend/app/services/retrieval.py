"""Hybrid retrieval: pgvector similarity + PostgreSQL full-text search, fused
with Reciprocal Rank Fusion, scoped to a single document.

Why hybrid: dense vectors find clauses that *mean* the right thing even when
they use unfamiliar wording, while full-text search reliably catches the exact
legal terms of art ("indemnify", "perpetual, irrevocable", "class action")
that embeddings sometimes smooth over. RRF combines the two rankings without
needing the two score scales to be comparable.

The model never receives the whole agreement by default - only the fused top-K
chunks for the one policy category being evaluated.

Fallback chain, in order:
    hybrid -> dense -> keyword -> bounded ordinal scan
Every degradation is recorded on the result, caps the confidence of anything
derived from it, and surfaces as a visible warning in the UI. Failures are
never hidden.
"""

from __future__ import annotations

import abc
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import Enum

from app.core.logging import get_logger

logger = get_logger(__name__)

RRF_K = 60  # standard RRF damping constant


class RetrievalMode(str, Enum):
    HYBRID = "hybrid"
    DENSE = "dense"
    KEYWORD = "keyword"
    ORDINAL_SCAN = "ordinal_scan"

    @property
    def is_degraded(self) -> bool:
        return self is not RetrievalMode.HYBRID


@dataclass
class RetrievedChunk:
    id: str
    document_id: str
    ordinal: int
    heading: str | None
    text: str
    start_offset: int
    end_offset: int
    score: float = 0.0
    dense_rank: int | None = None
    keyword_rank: int | None = None


@dataclass
class RetrievalResult:
    chunks: list[RetrievedChunk]
    mode: RetrievalMode
    query: str
    degraded_reason: str | None = None
    timings_ms: dict[str, float] = field(default_factory=dict)

    @property
    def degraded(self) -> bool:
        return self.mode.is_degraded


def reciprocal_rank_fusion(
    rankings: Sequence[Sequence[str]], *, k: int = RRF_K, weights: Sequence[float] | None = None
) -> list[tuple[str, float]]:
    """Fuse several ranked ID lists into one, highest score first.

    RRF score for an item is ``sum(weight / (k + rank))`` across the rankings it
    appears in, with ``rank`` starting at 1. Items ranked well by both retrievers
    beat items ranked superbly by only one, which is exactly the behaviour we
    want when neither signal is individually trustworthy.

    Ties break deterministically on first appearance, so identical inputs always
    produce identical output.
    """
    if weights is None:
        weights = [1.0] * len(rankings)
    if len(weights) != len(rankings):
        raise ValueError("weights must be the same length as rankings")

    scores: dict[str, float] = {}
    first_seen: dict[str, int] = {}
    counter = 0

    for ranking, weight in zip(rankings, weights):
        for position, item_id in enumerate(ranking, start=1):
            scores[item_id] = scores.get(item_id, 0.0) + weight / (k + position)
            if item_id not in first_seen:
                first_seen[item_id] = counter
                counter += 1

    return sorted(scores.items(), key=lambda pair: (-pair[1], first_seen[pair[0]]))


def build_category_query(
    *,
    category: str,
    display_name: str = "",
    description: str = "",
    retrieval_guidance: str = "",
    keywords: Sequence[str] | None = None,
) -> str:
    """Compose the retrieval query for one policy category.

    Keywords are repeated once because full-text ranking rewards term frequency,
    and legal terms of art are the highest-precision signal available.
    """
    parts = [display_name or category.replace("_", " "), description, retrieval_guidance]
    if keywords:
        parts.append(" ".join(keywords))
        parts.append(" ".join(keywords))
    return " ".join(p.strip() for p in parts if p and p.strip())


class ChunkSearchBackend(abc.ABC):
    """Storage-facing search operations, kept behind an interface so the fusion
    and fallback logic can be tested without a live database."""

    @abc.abstractmethod
    async def dense_search(
        self, document_id: str, embedding: list[float], limit: int
    ) -> list[RetrievedChunk]: ...

    @abc.abstractmethod
    async def keyword_search(
        self, document_id: str, query: str, limit: int
    ) -> list[RetrievedChunk]: ...

    @abc.abstractmethod
    async def ordinal_scan(self, document_id: str, limit: int) -> list[RetrievedChunk]: ...

    @abc.abstractmethod
    async def get_by_ordinal_range(
        self, document_id: str, start: int, end: int
    ) -> list[RetrievedChunk]: ...


class HybridRetriever:
    def __init__(
        self,
        backend: ChunkSearchBackend,
        embedding_provider,
        *,
        top_k: int = 8,
        candidate_multiplier: int = 3,
    ) -> None:
        self.backend = backend
        self.embeddings = embedding_provider
        self.top_k = top_k
        self.candidate_multiplier = candidate_multiplier

    async def retrieve(self, *, document_id: str, query: str, top_k: int | None = None):
        """Retrieve the most relevant chunks, degrading gracefully and visibly."""
        k = top_k or self.top_k
        candidates = max(k * self.candidate_multiplier, k + 4)

        dense: list[RetrievedChunk] = []
        keyword: list[RetrievedChunk] = []
        dense_error: str | None = None
        keyword_error: str | None = None

        try:
            embedding = await self.embeddings.embed_one(query)
            dense = await self.backend.dense_search(document_id, embedding, candidates)
        except Exception as exc:  # noqa: BLE001 - degrade, never fail the analysis
            dense_error = f"{type(exc).__name__}: {exc}"
            logger.warning("dense retrieval failed", extra={"error_type": type(exc).__name__})

        try:
            keyword = await self.backend.keyword_search(document_id, query, candidates)
        except Exception as exc:  # noqa: BLE001
            keyword_error = f"{type(exc).__name__}: {exc}"
            logger.warning("keyword retrieval failed", extra={"error_type": type(exc).__name__})

        # --- Tier 1: hybrid ---------------------------------------------------
        if dense and keyword:
            by_id: dict[str, RetrievedChunk] = {}
            for rank, chunk in enumerate(dense, start=1):
                chunk.dense_rank = rank
                by_id[chunk.id] = chunk
            for rank, chunk in enumerate(keyword, start=1):
                existing = by_id.get(chunk.id)
                if existing:
                    existing.keyword_rank = rank
                else:
                    chunk.keyword_rank = rank
                    by_id[chunk.id] = chunk

            fused = reciprocal_rank_fusion([[c.id for c in dense], [c.id for c in keyword]])
            ordered: list[RetrievedChunk] = []
            for chunk_id, score in fused[:k]:
                chunk = by_id[chunk_id]
                chunk.score = round(score, 6)
                ordered.append(chunk)
            return RetrievalResult(chunks=ordered, mode=RetrievalMode.HYBRID, query=query)

        # --- Tier 2: dense only ----------------------------------------------
        if dense:
            for rank, chunk in enumerate(dense[:k], start=1):
                chunk.dense_rank = rank
                chunk.score = 1.0 / (RRF_K + rank)
            return RetrievalResult(
                chunks=dense[:k],
                mode=RetrievalMode.DENSE,
                query=query,
                degraded_reason=keyword_error or "Keyword search returned no results.",
            )

        # --- Tier 3: keyword only --------------------------------------------
        if keyword:
            for rank, chunk in enumerate(keyword[:k], start=1):
                chunk.keyword_rank = rank
                chunk.score = 1.0 / (RRF_K + rank)
            return RetrievalResult(
                chunks=keyword[:k],
                mode=RetrievalMode.KEYWORD,
                query=query,
                degraded_reason=dense_error or "Vector search returned no results.",
            )

        # --- Tier 4: bounded ordinal scan ------------------------------------
        scanned = await self.backend.ordinal_scan(document_id, k)
        return RetrievalResult(
            chunks=scanned,
            mode=RetrievalMode.ORDINAL_SCAN,
            query=query,
            degraded_reason=(
                "Both vector and keyword retrieval were unavailable; "
                "a bounded ordinal scan was used instead. "
                f"dense={dense_error or 'empty'}; keyword={keyword_error or 'empty'}"
            ),
        )

    async def neighbours(
        self, *, document_id: str, ordinal: int, window: int = 1
    ) -> list[RetrievedChunk]:
        """Fetch surrounding clauses - obligations often span adjacent sections."""
        window = max(0, min(window, 3))
        return await self.backend.get_by_ordinal_range(
            document_id, max(0, ordinal - window), ordinal + window
        )
