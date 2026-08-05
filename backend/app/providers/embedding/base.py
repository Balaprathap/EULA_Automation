"""Embedding provider interface.

Deliberately independent of the LLM provider: Anthropic does not offer an
embeddings API, so vectors come from a separate vendor (OpenAI or Voyage by
default) and the two abstractions must not be coupled.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field

from app.services.normalization import content_hash


@dataclass
class EmbeddingResult:
    vectors: list[list[float]]
    model: str
    dimensions: int
    input_tokens: int = 0
    estimated_cost_usd: float = 0.0
    cache_hits: int = 0
    cache_misses: int = 0
    metadata: dict[str, object] = field(default_factory=dict)


class EmbeddingProvider(abc.ABC):
    """Batches, retries, caches, and validates dimensions on behalf of callers."""

    def __init__(self, model: str, dimensions: int, batch_size: int = 64) -> None:
        self.model = model
        self.dimensions = dimensions
        self.batch_size = batch_size
        self._cache: dict[str, list[float]] = {}

    @property
    @abc.abstractmethod
    def name(self) -> str: ...

    @abc.abstractmethod
    async def _embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Embed one batch. Implementations handle transport, retries, timeouts."""

    def _validate(self, vectors: list[list[float]], expected: int) -> None:
        if len(vectors) != expected:
            raise ValueError(f"{self.name} returned {len(vectors)} vectors for {expected} inputs.")
        for index, vector in enumerate(vectors):
            if len(vector) != self.dimensions:
                raise ValueError(
                    f"{self.name} returned a {len(vector)}-dimensional vector at index "
                    f"{index}, but EMBEDDING_DIMENSIONS is {self.dimensions}. "
                    "The configured model and dimension setting disagree."
                )

    async def embed(self, texts: list[str], *, use_cache: bool = True) -> EmbeddingResult:
        """Embed texts, reusing cached vectors for identical normalized content.

        Identical chunk text is never re-embedded while a valid cached vector
        exists, which keeps re-analysis of the same agreement close to free.
        """
        if not texts:
            return EmbeddingResult(vectors=[], model=self.model, dimensions=self.dimensions)

        keys = [content_hash(t) for t in texts]
        resolved: list[list[float] | None] = [None] * len(texts)
        pending: list[int] = []
        hits = 0

        for i, key in enumerate(keys):
            cached = self._cache.get(key) if use_cache else None
            if cached is not None:
                resolved[i] = cached
                hits += 1
            else:
                pending.append(i)

        total_tokens = 0
        for start in range(0, len(pending), self.batch_size):
            indices = pending[start : start + self.batch_size]
            batch = [texts[i] for i in indices]
            vectors = await self._embed_batch(batch)
            self._validate(vectors, len(batch))
            for i, vector in zip(indices, vectors):
                resolved[i] = vector
                if use_cache:
                    self._cache[keys[i]] = vector
            total_tokens += sum(max(1, len(t) // 4) for t in batch)

        return EmbeddingResult(
            vectors=[v for v in resolved if v is not None],
            model=self.model,
            dimensions=self.dimensions,
            input_tokens=total_tokens,
            estimated_cost_usd=self.estimate_cost(total_tokens),
            cache_hits=hits,
            cache_misses=len(pending),
        )

    async def embed_one(self, text: str) -> list[float]:
        result = await self.embed([text])
        return result.vectors[0]

    def estimate_cost(self, tokens: int) -> float:
        return 0.0

    def cache_size(self) -> int:
        return len(self._cache)

    def clear_cache(self) -> None:
        self._cache.clear()
