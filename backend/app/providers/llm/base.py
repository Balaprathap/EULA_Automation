"""LLM provider interface and usage/cost accounting."""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import Any


@dataclass
class TokenUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return (
            self.input_tokens
            + self.output_tokens
            + self.cache_creation_input_tokens
            + self.cache_read_input_tokens
        )

    def __add__(self, other: TokenUsage) -> TokenUsage:
        return TokenUsage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            cache_creation_input_tokens=self.cache_creation_input_tokens
            + other.cache_creation_input_tokens,
            cache_read_input_tokens=self.cache_read_input_tokens + other.cache_read_input_tokens,
        )


@dataclass
class ToolCall:
    id: str
    name: str
    input: dict[str, Any]


@dataclass
class LLMResponse:
    text: str
    stop_reason: str | None
    usage: TokenUsage
    model: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    estimated_cost_usd: float = 0.0
    raw_content: list[dict[str, Any]] = field(default_factory=list)

    @property
    def wants_tools(self) -> bool:
        return bool(self.tool_calls)


class LLMProvider(abc.ABC):
    """Text generation only. Embeddings come from a separate provider."""

    @property
    @abc.abstractmethod
    def name(self) -> str: ...

    @property
    @abc.abstractmethod
    def model(self) -> str: ...

    @abc.abstractmethod
    async def complete(
        self,
        *,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int | None = None,
        temperature: float = 0.0,
    ) -> LLMResponse: ...


def calculate_cost(
    usage: TokenUsage,
    *,
    input_cost_per_mtok: float,
    output_cost_per_mtok: float,
    cached_input_cost_per_mtok: float = 0.0,
) -> float:
    """Estimate USD cost from a usage record.

    Cached-read input is billed at the discounted rate; cache *writes* are billed
    at the standard input rate. Rates come from configuration - verify them
    against current provider pricing before treating output as authoritative.
    """
    million = 1_000_000
    cost = (
        (usage.input_tokens / million) * input_cost_per_mtok
        + (usage.cache_creation_input_tokens / million) * input_cost_per_mtok
        + (usage.cache_read_input_tokens / million) * cached_input_cost_per_mtok
        + (usage.output_tokens / million) * output_cost_per_mtok
    )
    return round(max(0.0, cost), 8)
