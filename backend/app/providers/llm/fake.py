"""Scripted LLM provider for automated tests.

Test doubles live here and are never wired into the production factory, so no
CI run can accidentally bill a real Anthropic call.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from app.core.errors import ProviderRateLimited, ProviderUnavailable
from app.providers.llm.base import LLMProvider, LLMResponse, TokenUsage, ToolCall

Script = LLMResponse | Exception | Callable[..., LLMResponse]


class FakeLLMProvider(LLMProvider):
    """Replays a scripted sequence of responses and records every request."""

    def __init__(self, script: list[Script] | None = None, model: str = "fake-model") -> None:
        self.script: list[Script] = list(script or [])
        self._model = model
        self.calls: list[dict[str, Any]] = []

    @property
    def name(self) -> str:
        return "fake"

    @property
    def model(self) -> str:
        return self._model

    def queue(self, item: Script) -> FakeLLMProvider:
        self.script.append(item)
        return self

    def queue_text(self, text: str, *, stop_reason: str = "end_turn") -> FakeLLMProvider:
        return self.queue(
            LLMResponse(
                text=text,
                stop_reason=stop_reason,
                usage=TokenUsage(input_tokens=100, output_tokens=50),
                model=self._model,
            )
        )

    def queue_tool_use(self, name: str, tool_input: dict[str, Any], call_id: str = "toolu_1"):
        return self.queue(
            LLMResponse(
                text="",
                stop_reason="tool_use",
                usage=TokenUsage(input_tokens=100, output_tokens=20),
                model=self._model,
                tool_calls=[ToolCall(id=call_id, name=name, input=tool_input)],
            )
        )

    async def complete(
        self,
        *,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int | None = None,
        temperature: float = 0.0,
    ) -> LLMResponse:
        self.calls.append(
            {
                "system": system,
                "messages": messages,
                "tools": [t.get("name") for t in (tools or [])],
                "max_tokens": max_tokens,
                "temperature": temperature,
            }
        )
        if not self.script:
            return LLMResponse(
                text="{}",
                stop_reason="end_turn",
                usage=TokenUsage(input_tokens=10, output_tokens=5),
                model=self._model,
            )
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        if callable(item):
            return item(system=system, messages=messages, tools=tools)
        return item

    # --- Convenience constructors for failure-mode tests ---------------------
    @staticmethod
    def rate_limited() -> Exception:
        return ProviderRateLimited()

    @staticmethod
    def overloaded() -> Exception:
        return ProviderUnavailable("The provider is overloaded.")
