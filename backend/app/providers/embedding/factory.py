"""Embedding provider selection driven entirely by environment configuration."""

from __future__ import annotations

import functools

from app.core.config import Settings, get_settings
from app.providers.embedding.base import EmbeddingProvider
from app.providers.embedding.deterministic import DeterministicEmbeddingProvider
from app.providers.embedding.http_providers import (
    OpenAIEmbeddingProvider,
    VoyageEmbeddingProvider,
)


def build_embedding_provider(settings: Settings) -> EmbeddingProvider:
    if settings.embedding_provider == "deterministic":
        if settings.is_production:
            raise ValueError(
                "The deterministic embedding provider is test-only and cannot run in production."
            )
        return DeterministicEmbeddingProvider(
            model="deterministic-v1",
            dimensions=settings.embedding_dimensions,
            batch_size=settings.embedding_batch_size,
        )

    provider_cls = {
        "openai": OpenAIEmbeddingProvider,
        "voyage": VoyageEmbeddingProvider,
    }[settings.embedding_provider]

    return provider_cls(
        model=settings.embedding_model,
        dimensions=settings.embedding_dimensions,
        api_key=settings.embedding_api_key or "",
        batch_size=settings.embedding_batch_size,
        timeout=settings.embedding_timeout_seconds,
        cost_per_mtok=settings.embedding_cost_per_mtok,
    )


@functools.lru_cache
def get_embedding_provider() -> EmbeddingProvider:
    return build_embedding_provider(get_settings())
