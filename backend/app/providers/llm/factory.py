"""LLM provider selection. Test doubles are never constructed here."""

from __future__ import annotations

import functools

from app.core.config import Settings, get_settings
from app.providers.llm.anthropic_provider import AnthropicProvider
from app.providers.llm.base import LLMProvider


def build_llm_provider(settings: Settings) -> LLMProvider:
    return AnthropicProvider(
        api_key=settings.anthropic_api_key or "",
        model=settings.anthropic_model,
        max_tokens=settings.anthropic_max_tokens,
        timeout=settings.anthropic_timeout_seconds,
        max_retries=settings.anthropic_max_retries,
        input_cost_per_mtok=settings.anthropic_input_cost_per_mtok,
        cached_input_cost_per_mtok=settings.anthropic_cached_input_cost_per_mtok,
        output_cost_per_mtok=settings.anthropic_output_cost_per_mtok,
    )


@functools.lru_cache
def get_llm_provider() -> LLMProvider:
    return build_llm_provider(get_settings())
