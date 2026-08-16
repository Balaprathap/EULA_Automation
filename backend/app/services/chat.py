"""Grounded ClauseGuard document chat using Groq.

The chatbot receives only retrieved contract text and verified ClauseGuard
findings. It never receives unrestricted database access or another tenant's
documents.
"""

from __future__ import annotations

import re
from typing import Any

import httpx

from app.core.config import Settings


class ChatProviderError(RuntimeError):
    """Raised when the external chatbot provider cannot answer safely."""


def extract_citation_refs(text: str) -> list[str]:
    """Return unique C#/F# references in first-appearance order."""
    refs: list[str] = []

    for ref in re.findall(r"\[(C\d+|F\d+)\]", text):
        if ref not in refs:
            refs.append(ref)

    return refs


class GroqChatService:
    def __init__(self, settings: Settings) -> None:
        if not settings.groq_api_key:
            raise ValueError("GROQ_API_KEY is required for ClauseGuard chat.")

        self.api_key = settings.groq_api_key
        self.model = settings.groq_model
        self.max_tokens = settings.groq_max_completion_tokens
        self.reasoning_effort = settings.groq_reasoning_effort
        self.timeout = settings.groq_timeout_seconds

    async def answer(
        self,
        *,
        question: str,
        sources: list[dict[str, Any]],
        history: list[dict[str, str]] | None = None,
    ) -> tuple[str, list[str], dict[str, Any]]:
        rendered_sources: list[str] = []

        for source in sources:
            ref = source["ref"]

            if source["type"] == "chunk":
                rendered_sources.append(
                    "\n".join(
                        [
                            f"[{ref}] CONTRACT SOURCE",
                            f"Heading: {source.get('heading') or 'Not provided'}",
                            f"Chunk: {source.get('ordinal')}",
                            source.get("text", "")[:2400],
                        ]
                    )
                )
            else:
                rendered_sources.append(
                    "\n".join(
                        [
                            f"[{ref}] VERIFIED CLAUSEGUARD FINDING",
                            f"Category: {source.get('category')}",
                            f"Severity: {source.get('severity')}",
                            f"Summary: {source.get('summary') or ''}",
                            f"Why it matters: {source.get('why_it_matters') or ''}",
                            f"Verified quote: {source.get('quote') or 'No quote available'}",
                        ]
                    )
                )

        context = "\n\n---\n\n".join(rendered_sources)

        system = """You are ClauseGuard Assistant, a document-grounded compliance assistant.

Rules:
1. Answer ONLY from the supplied contract sources and verified ClauseGuard findings.
2. Never invent, reconstruct, or guess contract language.
3. Every contract-specific factual claim must contain a citation such as [C1] or [F2].
4. Use only citation IDs that appear in the supplied context.
5. Clearly distinguish what the agreement says from ClauseGuard's interpretation.
6. If the supplied evidence is insufficient, say so.
7. Do not claim to provide legal advice.
8. Be concise, practical, and easy to understand.
"""

        messages: list[dict[str, str]] = [{"role": "system", "content": system}]

        for item in (history or [])[-6:]:
            role = item.get("role")
            content = item.get("content", "").strip()

            if role in {"user", "assistant"} and content:
                messages.append(
                    {
                        "role": role,
                        "content": content[:1800],
                    }
                )

        messages.append(
            {
                "role": "user",
                "content": (
                    f"""Question:
{question}

DOCUMENT CONTEXT:
{context}

Answer the question using the evidence above. Include citations in the answer."""
                ),
            }
        )

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(
                    "https://api.groq.com/openai/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": self.model,
                        "messages": messages,
                        "reasoning_effort": self.reasoning_effort,
                        "max_completion_tokens": self.max_tokens,
                    },
                )
        except httpx.HTTPError as exc:
            raise ChatProviderError("The chatbot provider could not be reached.") from exc

        if response.status_code == 429:
            raise ChatProviderError(
                "The chatbot free-tier rate limit has been reached. Please try again shortly."
            )

        if not response.is_success:
            raise ChatProviderError(
                f"The chatbot provider returned HTTP {response.status_code}."
            )

        payload = response.json()

        try:
            answer = payload["choices"][0]["message"]["content"].strip()
        except (KeyError, IndexError, TypeError, AttributeError) as exc:
            raise ChatProviderError("The chatbot provider returned an invalid response.") from exc

        if not answer:
            raise ChatProviderError("The chatbot returned an empty answer.")

        refs = extract_citation_refs(answer)
        usage = payload.get("usage") or {}

        return answer, refs, usage
