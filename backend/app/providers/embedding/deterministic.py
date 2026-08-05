"""Deterministic, offline embedding provider.

TEST AND CI USE ONLY. It never makes a network call, so the normal test suite
costs nothing, and it is refused outright in staging/production by the
validator in ``app.core.config``.

Vectors are hashed bag-of-words projections: identical text always yields an
identical vector, and texts sharing vocabulary land near each other, which is
enough for retrieval tests to be meaningful rather than merely deterministic.
"""

from __future__ import annotations

import hashlib
import math
import re

from app.providers.embedding.base import EmbeddingProvider

_WORD = re.compile(r"[a-z0-9']+")


class DeterministicEmbeddingProvider(EmbeddingProvider):
    @property
    def name(self) -> str:
        return "deterministic"

    def _vector(self, text: str) -> list[float]:
        vector = [0.0] * self.dimensions
        tokens = _WORD.findall(text.lower())
        if not tokens:
            tokens = ["\x00empty"]

        for token in tokens:
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            for offset in range(0, 16, 4):
                slot = int.from_bytes(digest[offset : offset + 2], "big") % self.dimensions
                sign = 1.0 if digest[offset + 2] & 1 else -1.0
                vector[slot] += sign

        norm = math.sqrt(sum(v * v for v in vector)) or 1.0
        return [v / norm for v in vector]

    async def _embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [self._vector(t) for t in texts]
