"""Production embedding providers (OpenAI and Voyage) over HTTP.

Both speak an OpenAI-compatible JSON shape, so the transport, retry, and
rate-limit handling is shared. Verify the current model identifiers and
dimensions against the vendor's live documentation before deploying.
"""

from __future__ import annotations

import asyncio
import random
from typing import Any

import httpx

from app.core.errors import ProviderRateLimited, ProviderUnavailable
from app.core.logging import get_logger
from app.providers.embedding.base import EmbeddingProvider

logger = get_logger(__name__)

MAX_ATTEMPTS = 4
BASE_BACKOFF = 1.0


class _HttpEmbeddingProvider(EmbeddingProvider):
    endpoint: str = ""
    cost_per_mtok: float = 0.0

    def __init__(
        self,
        model: str,
        dimensions: int,
        api_key: str,
        *,
        batch_size: int = 64,
        timeout: float = 60.0,
        cost_per_mtok: float = 0.0,
    ) -> None:
        super().__init__(model=model, dimensions=dimensions, batch_size=batch_size)
        if not api_key:
            raise ValueError(f"{self.name} embedding provider requires EMBEDDING_API_KEY.")
        self._api_key = api_key
        self._timeout = timeout
        self.cost_per_mtok = cost_per_mtok

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

    def _payload(self, texts: list[str]) -> dict[str, Any]:
        return {"model": self.model, "input": texts}

    def _parse(self, body: dict[str, Any]) -> list[list[float]]:
        items = sorted(body.get("data", []), key=lambda d: d.get("index", 0))
        return [item["embedding"] for item in items]

    async def _embed_batch(self, texts: list[str]) -> list[list[float]]:
        last_error: Exception | None = None

        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                async with httpx.AsyncClient(timeout=self._timeout) as client:
                    response = await client.post(
                        self.endpoint, headers=self._headers(), json=self._payload(texts)
                    )

                if response.status_code == 429:
                    raise ProviderRateLimited(
                        f"{self.name} embedding rate limit reached.",
                    )
                if response.status_code >= 500:
                    raise ProviderUnavailable(
                        f"{self.name} embedding service returned {response.status_code}."
                    )
                response.raise_for_status()
                return self._parse(response.json())

            except (ProviderRateLimited, ProviderUnavailable, httpx.TimeoutException) as exc:
                last_error = exc
                if attempt == MAX_ATTEMPTS:
                    break
                delay = BASE_BACKOFF * (2 ** (attempt - 1)) + random.uniform(0, 0.5)  # noqa: S311
                logger.warning(
                    "embedding retry",
                    extra={"provider": self.name, "attempt": attempt, "delay_s": round(delay, 2)},
                )
                await asyncio.sleep(delay)
            except httpx.HTTPStatusError as exc:
                # 4xx other than 429 is a configuration error - do not retry.
                raise ProviderUnavailable(
                    f"{self.name} embedding request was rejected "
                    f"({exc.response.status_code}). Check EMBEDDING_MODEL and EMBEDDING_API_KEY."
                ) from exc

        raise ProviderUnavailable(
            f"{self.name} embedding provider failed after {MAX_ATTEMPTS} attempts."
        ) from last_error

    def estimate_cost(self, tokens: int) -> float:
        return round(tokens / 1_000_000 * self.cost_per_mtok, 8)


class OpenAIEmbeddingProvider(_HttpEmbeddingProvider):
    endpoint = "https://api.openai.com/v1/embeddings"

    @property
    def name(self) -> str:
        return "openai"

    def _payload(self, texts: list[str]) -> dict[str, Any]:
        payload = super()._payload(texts)
        # text-embedding-3-* support explicit output dimensions.
        if self.model.startswith("text-embedding-3"):
            payload["dimensions"] = self.dimensions
        return payload


class VoyageEmbeddingProvider(_HttpEmbeddingProvider):
    endpoint = "https://api.voyageai.com/v1/embeddings"

    @property
    def name(self) -> str:
        return "voyage"

    def _payload(self, texts: list[str]) -> dict[str, Any]:
        return {"model": self.model, "input": texts, "input_type": "document"}
