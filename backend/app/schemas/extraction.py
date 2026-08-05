"""Strict structured-output contract for Claude extraction.

Note what is *absent*: there is no ``severity`` field. The model reports
confidence and evidence; severity is computed by ``app.services.scoring`` from
operator-configured weights. A document therefore has no field through which it
could influence how serious a finding is judged to be.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ProposedFinding(BaseModel):
    """One clause the model believes matches the policy category under review."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    category: str = Field(min_length=1, max_length=100)
    chunk_id: str = Field(min_length=1, max_length=100)
    quote: str = Field(min_length=1, max_length=4000)
    start_offset: int = Field(ge=0)
    end_offset: int = Field(ge=0)
    confidence: float = Field(ge=0.0, le=1.0)
    plain_summary: str = Field(min_length=1, max_length=600)
    why_it_matters: str = Field(min_length=1, max_length=800)

    @field_validator("end_offset")
    @classmethod
    def _end_after_start(cls, v: int, info) -> int:
        start = info.data.get("start_offset")
        if start is not None and v <= start:
            raise ValueError("end_offset must be greater than start_offset")
        return v


class CategoryExtraction(BaseModel):
    """The model's complete response for a single policy category."""

    model_config = ConfigDict(extra="forbid")

    category: str = Field(min_length=1, max_length=100)
    findings: list[ProposedFinding] = Field(default_factory=list, max_length=25)
    abstain: bool = False
    needs_review_reason: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def _no_findings_when_abstaining(self) -> CategoryExtraction:
        # Declared as an after-validator because `abstain` is defined below
        # `findings`; a field validator would not yet see it.
        if self.abstain and self.findings:
            raise ValueError("findings must be empty when abstain is true")
        return self


EXTRACTION_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "category": {"type": "string"},
        "abstain": {
            "type": "boolean",
            "description": "True when the agreement contains no clause for this category.",
        },
        "needs_review_reason": {
            "type": ["string", "null"],
            "description": "Set when you are unable to decide and a human should look.",
        },
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "category": {"type": "string"},
                    "chunk_id": {
                        "type": "string",
                        "description": "The id of the supplied chunk containing the quote.",
                    },
                    "quote": {
                        "type": "string",
                        "description": (
                            "Verbatim text copied character-for-character from the chunk. "
                            "Never paraphrase, summarize, or reconstruct."
                        ),
                    },
                    "start_offset": {
                        "type": "integer",
                        "description": "Character offset of the quote within the chunk.",
                    },
                    "end_offset": {"type": "integer"},
                    "confidence": {
                        "type": "number",
                        "description": "0.0-1.0 confidence that this clause matches the category.",
                    },
                    "plain_summary": {
                        "type": "string",
                        "description": "One or two plain-English sentences, no legalese.",
                    },
                    "why_it_matters": {
                        "type": "string",
                        "description": "The practical consequence for the customer.",
                    },
                },
                "required": [
                    "category",
                    "chunk_id",
                    "quote",
                    "start_offset",
                    "end_offset",
                    "confidence",
                    "plain_summary",
                    "why_it_matters",
                ],
            },
        },
    },
    "required": ["category", "findings", "abstain"],
}
