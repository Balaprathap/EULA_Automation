"""Anthropic Messages API provider.

Uses the official ``anthropic`` Python SDK. The model identifier is read once
from ``ANTHROPIC_MODEL`` and never hard-coded anywhere else in the codebase.

Retry policy: bounded attempts with exponential backoff plus jitter for rate
limits, overload, and timeouts. Authentication and request-shape errors are not
retried, because retrying a misconfiguration only burns time and quota.
"""

from __future__ import annotations

import asyncio
import random
from typing import Any

from app.core.errors import ProviderRateLimited, ProviderUnavailable
from app.core.logging import get_logger
from app.providers.llm.base import LLMProvider, LLMResponse, TokenUsage, ToolCall, calculate_cost

logger = get_logger(__name__)

BASE_BACKOFF = 1.0
MAX_BACKOFF = 30.0


class AnthropicProvider(LLMProvider):
    def __init__(
        self,
        api_key: str,
        model: str,
        *,
        max_tokens: int = 4096,
        timeout: float = 120.0,
        max_retries: int = 4,
        input_cost_per_mtok: float = 3.0,
        cached_input_cost_per_mtok: float = 0.3,
        output_cost_per_mtok: float = 15.0,
    ) -> None:
        if not api_key:
            raise ValueError("ANTHROPIC_API_KEY is required to use the Anthropic provider.")
        self._api_key = api_key
        self._model = model
        self._max_tokens = max_tokens
        self._timeout = timeout
        self._max_retries = max_retries
        self._input_cost = input_cost_per_mtok
        self._cached_input_cost = cached_input_cost_per_mtok
        self._output_cost = output_cost_per_mtok
        self._client: Any = None

    @property
    def name(self) -> str:
        return "anthropic"

    @property
    def model(self) -> str:
        return self._model

    def _get_client(self) -> Any:
        if self._client is None:
            from anthropic import AsyncAnthropic

            # SDK-level retries are disabled: backoff is handled here so that
            # rate-limit and overload behaviour is observable and testable.
            self._client = AsyncAnthropic(
                api_key=self._api_key, timeout=self._timeout, max_retries=0
            )
        return self._client

    async def complete(
        self,
        *,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int | None = None,
        temperature: float = 0.0,
    ) -> LLMResponse:
        import anthropic

        client = self._get_client()
        request: dict[str, Any] = {
            "model": self._model,
            "max_tokens": max_tokens or self._max_tokens,
            "temperature": temperature,
            "system": system,
            "messages": messages,
        }
        if tools:
            request["tools"] = tools

        last_error: Exception | None = None

        for attempt in range(1, self._max_retries + 1):
            try:
                response = await client.messages.create(**request)
                return self._to_response(response)

            except anthropic.RateLimitError as exc:
                last_error = exc
                retryable, label = True, "rate_limit"
            except anthropic.APIStatusError as exc:
                status = getattr(exc, "status_code", 500)
                last_error = exc
                # 429/5xx are transient; 4xx are configuration errors.
                retryable = status == 429 or status >= 500
                label = f"status_{status}"
                if not retryable:
                    raise ProviderUnavailable(
                        f"The Anthropic API rejected the request ({status}). "
                        "Verify ANTHROPIC_MODEL and ANTHROPIC_API_KEY.",
                        code="PROVIDER_REQUEST_REJECTED",
                    ) from exc
            except (anthropic.APIConnectionError, anthropic.APITimeoutError) as exc:
                last_error = exc
                retryable, label = True, "connection"

            if attempt >= self._max_retries:
                break

            delay = min(MAX_BACKOFF, BASE_BACKOFF * (2 ** (attempt - 1)))
            delay += random.uniform(0, delay * 0.25)  # noqa: S311 - jitter, not crypto
            logger.warning(
                "anthropic retry",
                extra={
                    "attempt": attempt,
                    "max_attempts": self._max_retries,
                    "reason": label,
                    "delay_s": round(delay, 2),
                    "model": self._model,
                },
            )
            await asyncio.sleep(delay)

        if isinstance(last_error, Exception) and "RateLimit" in type(last_error).__name__:
            raise ProviderRateLimited(
                "The Anthropic rate limit was reached after "
                f"{self._max_retries} attempts. Please retry shortly."
            ) from last_error
        raise ProviderUnavailable(
            f"The Anthropic API was unavailable after {self._max_retries} attempts."
        ) from last_error

    def _to_response(self, response: Any) -> LLMResponse:
        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        raw: list[dict[str, Any]] = []

        for block in response.content:
            block_type = getattr(block, "type", None)
            if block_type == "text":
                text_parts.append(block.text)
                raw.append({"type": "text", "text": block.text})
            elif block_type == "tool_use":
                tool_calls.append(
                    ToolCall(id=block.id, name=block.name, input=dict(block.input or {}))
                )
                raw.append(
                    {
                        "type": "tool_use",
                        "id": block.id,
                        "name": block.name,
                        "input": dict(block.input or {}),
                    }
                )

        raw_usage = getattr(response, "usage", None)
        usage = TokenUsage(
            input_tokens=getattr(raw_usage, "input_tokens", 0) or 0,
            output_tokens=getattr(raw_usage, "output_tokens", 0) or 0,
            cache_creation_input_tokens=getattr(raw_usage, "cache_creation_input_tokens", 0) or 0,
            cache_read_input_tokens=getattr(raw_usage, "cache_read_input_tokens", 0) or 0,
        )

        return LLMResponse(
            text="".join(text_parts),
            stop_reason=getattr(response, "stop_reason", None),
            usage=usage,
            model=getattr(response, "model", self._model),
            tool_calls=tool_calls,
            estimated_cost_usd=calculate_cost(
                usage,
                input_cost_per_mtok=self._input_cost,
                output_cost_per_mtok=self._output_cost,
                cached_input_cost_per_mtok=self._cached_input_cost,
            ),
            raw_content=raw,
        )
