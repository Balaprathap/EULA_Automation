"""Shared fixtures. The whole suite runs offline against test doubles - no test
in the default run may make a paid provider call."""

import os
from dataclasses import dataclass
from pathlib import Path

import pytest

os.environ.setdefault("ENVIRONMENT", "test")

from app.providers.embedding.deterministic import DeterministicEmbeddingProvider  # noqa: E402
from app.providers.llm.fake import FakeLLMProvider  # noqa: E402
from app.services.chunking import chunk_document  # noqa: E402
from app.services.normalization import normalize_text  # noqa: E402
from app.services.retrieval import HybridRetriever, RetrievedChunk  # noqa: E402
from app.services.tools import ToolExecutor  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"
DOC_ID = "doc-under-analysis"
OTHER_DOC_ID = "some-other-tenants-document"
ORG_ID = "org-a"


@dataclass
class StoredChunk:
    id: str
    document_id: str
    ordinal: int
    heading: str | None
    text: str
    start_offset: int
    end_offset: int


def build_chunks(text: str, document_id: str = DOC_ID) -> list[StoredChunk]:
    return [
        StoredChunk(
            id=f"{document_id}-chunk-{c.ordinal}",
            document_id=document_id,
            ordinal=c.ordinal,
            heading=c.heading,
            text=c.text,
            start_offset=c.start_offset,
            end_offset=c.end_offset,
        )
        for c in chunk_document(text)
    ]


class InMemoryBackend:
    """Search backend over an in-memory chunk store.

    Dense search uses real cosine similarity over deterministic embeddings;
    keyword search uses token overlap. Crude compared with pgvector and
    tsvector, but faithful enough that fusion and fallback behaviour is
    exercised for real.
    """

    def __init__(self, chunks: list[StoredChunk], embeddings):
        self.chunks = chunks
        self.embeddings = embeddings
        self._vectors = {}

    async def _ensure_vectors(self):
        if self._vectors:
            return
        result = await self.embeddings.embed([c.text for c in self.chunks])
        self._vectors = {c.id: v for c, v in zip(self.chunks, result.vectors)}

    def _to_retrieved(self, chunk: StoredChunk) -> RetrievedChunk:
        return RetrievedChunk(
            id=chunk.id,
            document_id=chunk.document_id,
            ordinal=chunk.ordinal,
            heading=chunk.heading,
            text=chunk.text,
            start_offset=chunk.start_offset,
            end_offset=chunk.end_offset,
        )

    async def dense_search(self, document_id, embedding, limit):
        await self._ensure_vectors()
        scored = []
        for chunk in self.chunks:
            if chunk.document_id != document_id:
                continue
            vector = self._vectors[chunk.id]
            scored.append((sum(a * b for a, b in zip(vector, embedding)), chunk))
        scored.sort(key=lambda pair: -pair[0])
        return [self._to_retrieved(c) for _, c in scored[:limit]]

    async def keyword_search(self, document_id, query, limit):
        terms = {t for t in query.lower().split() if len(t) > 3}
        scored = []
        for chunk in self.chunks:
            if chunk.document_id != document_id:
                continue
            lowered = chunk.text.lower()
            overlap = sum(1 for t in terms if t in lowered)
            if overlap:
                scored.append((overlap, chunk))
        scored.sort(key=lambda pair: -pair[0])
        return [self._to_retrieved(c) for _, c in scored[:limit]]

    async def ordinal_scan(self, document_id, limit):
        return [self._to_retrieved(c) for c in self.chunks if c.document_id == document_id][:limit]

    async def get_by_ordinal_range(self, document_id, start, end):
        return [
            self._to_retrieved(c)
            for c in self.chunks
            if c.document_id == document_id and start <= c.ordinal <= end
        ]


@pytest.fixture
def sample_text() -> str:
    return normalize_text((FIXTURES / "sample_eula.txt").read_text(encoding="utf-8"))


@pytest.fixture
def injection_text() -> str:
    return normalize_text((FIXTURES / "prompt_injection_eula.txt").read_text(encoding="utf-8"))


@pytest.fixture
def embeddings():
    return DeterministicEmbeddingProvider(model="deterministic-v1", dimensions=128)


@pytest.fixture
def stored_chunks(sample_text):
    return build_chunks(sample_text)


@pytest.fixture
def backend(stored_chunks, embeddings):
    return InMemoryBackend(stored_chunks, embeddings)


@pytest.fixture
def retriever(backend, embeddings):
    return HybridRetriever(backend, embeddings, top_k=8)


@pytest.fixture
def chunk_lookup(stored_chunks):
    index = {c.id: c for c in stored_chunks}

    async def lookup(chunk_id):
        return index.get(chunk_id)

    return lookup


@pytest.fixture
def tool_executor(retriever, chunk_lookup):
    return ToolExecutor(retriever, chunk_lookup)


@pytest.fixture
def fake_llm():
    return FakeLLMProvider()
