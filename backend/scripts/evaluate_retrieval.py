"""Measure retrieval recall@k against the labeled evaluation set.

Run with the deterministic provider (offline, free) to track regressions, or
with the real provider to measure production behaviour:

    python -m scripts.evaluate_retrieval                 # offline, deterministic
    EMBEDDING_PROVIDER=openai python -m scripts.evaluate_retrieval

The printed numbers are measurements from this run. Nothing here fabricates a
result: if the target is not met, the script says so and exits non-zero.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.core.config import get_settings  # noqa: E402
from app.providers.embedding.deterministic import DeterministicEmbeddingProvider  # noqa: E402
from app.providers.embedding.factory import build_embedding_provider  # noqa: E402
from app.services.chunking import chunk_document  # noqa: E402
from app.services.normalization import normalize_text  # noqa: E402
from app.services.retrieval import (  # noqa: E402
    HybridRetriever,
    RetrievedChunk,
    build_category_query,
)

LABELS_PATH = ROOT / "evaluation" / "retrieval_labels.json"
TARGET_RECALL = 0.90


class InMemoryBackend:
    """Stands in for Postgres so the evaluation needs no database.

    Dense search is real cosine similarity over the configured embedding
    provider; keyword search approximates ``ts_rank_cd`` with term-overlap
    scoring. Absolute numbers will differ slightly from production, but
    regressions show up the same way.
    """

    def __init__(self, chunks: list[RetrievedChunk], embeddings):
        self.chunks = chunks
        self.embeddings = embeddings
        self.vectors: dict[str, list[float]] = {}

    async def prepare(self):
        result = await self.embeddings.embed([c.text for c in self.chunks])
        self.vectors = {c.id: v for c, v in zip(self.chunks, result.vectors)}

    async def dense_search(self, document_id, embedding, limit):
        scored = [
            (sum(a * b for a, b in zip(self.vectors[c.id], embedding)), c) for c in self.chunks
        ]
        scored.sort(key=lambda pair: -pair[0])
        return [c for _, c in scored[:limit]]

    async def keyword_search(self, document_id, query, limit):
        terms = {t.strip(".,;:()").lower() for t in query.split() if len(t) > 3}
        scored = []
        for chunk in self.chunks:
            lowered = chunk.text.lower()
            score = sum(1 for t in terms if t in lowered)
            if score:
                scored.append((score, chunk))
        scored.sort(key=lambda pair: -pair[0])
        return [c for _, c in scored[:limit]]

    async def ordinal_scan(self, document_id, limit):
        return self.chunks[:limit]

    async def get_by_ordinal_range(self, document_id, start, end):
        return [c for c in self.chunks if start <= c.ordinal <= end]


async def evaluate(k: int) -> int:
    labels = json.loads(LABELS_PATH.read_text(encoding="utf-8"))
    document_path = ROOT / labels["document"]
    text = normalize_text(document_path.read_text(encoding="utf-8"))

    chunks = [
        RetrievedChunk(
            id=f"chunk-{c.ordinal}",
            document_id="eval-doc",
            ordinal=c.ordinal,
            heading=c.heading,
            text=c.text,
            start_offset=c.start_offset,
            end_offset=c.end_offset,
        )
        for c in chunk_document(text)
    ]

    settings = get_settings()
    if settings.embedding_provider == "deterministic":
        embeddings = DeterministicEmbeddingProvider(model="deterministic-v1", dimensions=256)
    else:
        embeddings = build_embedding_provider(settings)

    backend = InMemoryBackend(chunks, embeddings)
    await backend.prepare()
    retriever = HybridRetriever(backend, embeddings, top_k=k)

    # Import the seeded category definitions so the evaluation uses the same
    # queries production does.
    from scripts.seed import CATEGORIES

    definitions = {c["category"]: c for c in CATEGORIES}

    print(f"Retrieval evaluation - document: {labels['document']}")
    print(f"Provider: {embeddings.name} | chunks: {len(chunks)} | k = {k}\n")
    print(f"{'category':<28} {'hit':<5} {'rank':<6} mode")
    print("-" * 60)

    hits = 0
    total = 0
    for entry in labels["categories"]:
        category = entry["category"]
        definition = definitions.get(category, {})
        query = build_category_query(
            category=category,
            display_name=definition.get("display_name", ""),
            description=definition.get("description", ""),
            retrieval_guidance=definition.get("retrieval_guidance", ""),
            keywords=definition.get("keywords", []),
        )
        result = await retriever.retrieve(document_id="eval-doc", query=query, top_k=k)

        rank = None
        for position, chunk in enumerate(result.chunks, start=1):
            if any(marker in chunk.text for marker in entry["relevant_markers"]):
                rank = position
                break

        total += 1
        if rank is not None:
            hits += 1
        print(
            f"{category:<28} {'yes' if rank else 'NO':<5} "
            f"{str(rank) if rank else '-':<6} {result.mode.value}"
        )

    recall = hits / total if total else 0.0
    print("-" * 60)
    print(f"\nrecall@{k} = {recall:.1%}  ({hits}/{total} categories)")
    print(f"target     = {TARGET_RECALL:.0%}")

    if recall >= TARGET_RECALL:
        print("\nRESULT: target met.")
        return 0
    print(
        "\nRESULT: target NOT met. Do not report the target as achieved. "
        "Review the category keywords and retrieval guidance in scripts/seed.py."
    )
    return 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("-k", type=int, default=8, help="Number of chunks retrieved per category.")
    args = parser.parse_args()
    sys.exit(asyncio.run(evaluate(args.k)))
