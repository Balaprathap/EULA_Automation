"""Evidence verification.

No finding is ever displayed as confirmed on the model's word alone. Every
proposed quote is checked back against the stored chunk before it is persisted:

  1. the cited chunk exists,
  2. the chunk belongs to the document under analysis,
  3. the document belongs to the requesting organization,
  4. the quote - after identical normalization - actually occurs in that chunk,
  5. the absolute document offsets are recomputed from the match, not trusted
     from the model.

Anything that fails is quarantined and excluded from the score. This is what
makes a fabricated quote structurally unable to reach the user; the regression
test in ``tests/test_verification.py`` pins that behaviour permanently.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Protocol

from app.services.normalization import normalize_quote

MIN_QUOTE_CHARS = 12


class VerificationMethod(str, Enum):
    OFFSET_EXACT = "offset_exact"
    OFFSET_NORMALIZED = "offset_normalized"


class VerificationStatus(str, Enum):
    VERIFIED = "verified"
    QUARANTINED = "quarantined"
    NEEDS_REVIEW = "needs_review"


class FailureReason(str, Enum):
    CHUNK_NOT_FOUND = "chunk_not_found"
    CHUNK_WRONG_DOCUMENT = "chunk_wrong_document"
    DOCUMENT_WRONG_ORG = "document_wrong_org"
    QUOTE_EMPTY = "quote_empty"
    QUOTE_TOO_SHORT = "quote_too_short"
    QUOTE_NOT_FOUND = "quote_not_found"


class ChunkLike(Protocol):
    id: str
    document_id: str
    ordinal: int
    text: str
    start_offset: int
    end_offset: int


@dataclass(frozen=True)
class VerificationResult:
    status: VerificationStatus
    method: VerificationMethod | None = None
    matched_quote: str | None = None
    doc_start_offset: int | None = None
    doc_end_offset: int | None = None
    chunk_start_offset: int | None = None
    chunk_end_offset: int | None = None
    failure_reason: FailureReason | None = None
    detail: str = ""

    @property
    def verified(self) -> bool:
        return self.status is VerificationStatus.VERIFIED


def _normalized_index_map(text: str) -> tuple[str, list[int]]:
    """Build the normalized form of ``text`` alongside a map from each
    normalized character position back to its index in the original string.

    This is what lets a whitespace-insensitive match still yield byte-exact
    offsets into the stored document.
    """
    normalized_chars: list[str] = []
    index_map: list[int] = []
    previous_was_space = True  # suppresses leading whitespace

    folded = normalize_quote(text)
    if folded == text:
        return text, list(range(len(text)))

    for i, char in enumerate(text):
        is_space = char.isspace()
        if is_space:
            if previous_was_space:
                continue
            normalized_chars.append(" ")
            index_map.append(i)
            previous_was_space = True
        else:
            normalized_chars.append(char)
            index_map.append(i)
            previous_was_space = False

    while normalized_chars and normalized_chars[-1] == " ":
        normalized_chars.pop()
        index_map.pop()

    return "".join(normalized_chars), index_map


def verify_evidence(
    *,
    chunk: ChunkLike | None,
    proposed_quote: str,
    document_id: str,
    org_document_ids: set | None = None,
) -> VerificationResult:
    """Verify one proposed quote against its cited chunk.

    ``org_document_ids`` is the set of document IDs the requesting organization
    owns; when supplied, a cited chunk pointing outside that set is rejected as
    a cross-tenant access attempt.
    """
    if chunk is None:
        return VerificationResult(
            status=VerificationStatus.QUARANTINED,
            failure_reason=FailureReason.CHUNK_NOT_FOUND,
            detail="The cited chunk does not exist.",
        )

    if str(chunk.document_id) != str(document_id):
        return VerificationResult(
            status=VerificationStatus.QUARANTINED,
            failure_reason=FailureReason.CHUNK_WRONG_DOCUMENT,
            detail="The cited chunk belongs to a different document.",
        )

    if org_document_ids is not None and str(document_id) not in {str(d) for d in org_document_ids}:
        return VerificationResult(
            status=VerificationStatus.QUARANTINED,
            failure_reason=FailureReason.DOCUMENT_WRONG_ORG,
            detail="The document is not owned by this organization.",
        )

    if not proposed_quote or not proposed_quote.strip():
        return VerificationResult(
            status=VerificationStatus.QUARANTINED,
            failure_reason=FailureReason.QUOTE_EMPTY,
            detail="The model proposed an empty quote.",
        )

    # --- Path 1: byte-exact substring of the stored chunk --------------------
    exact_index = chunk.text.find(proposed_quote)
    if exact_index >= 0:
        if len(proposed_quote.strip()) < MIN_QUOTE_CHARS:
            return VerificationResult(
                status=VerificationStatus.NEEDS_REVIEW,
                failure_reason=FailureReason.QUOTE_TOO_SHORT,
                detail=f"The quote is shorter than {MIN_QUOTE_CHARS} characters.",
            )
        return VerificationResult(
            status=VerificationStatus.VERIFIED,
            method=VerificationMethod.OFFSET_EXACT,
            matched_quote=proposed_quote,
            chunk_start_offset=exact_index,
            chunk_end_offset=exact_index + len(proposed_quote),
            doc_start_offset=chunk.start_offset + exact_index,
            doc_end_offset=chunk.start_offset + exact_index + len(proposed_quote),
            detail="Exact substring match in the cited chunk.",
        )

    # --- Path 2: whitespace-insensitive match, offsets recovered via index map
    normalized_chunk, index_map = _normalized_index_map(chunk.text)
    normalized_target = normalize_quote(proposed_quote)

    if not normalized_target:
        return VerificationResult(
            status=VerificationStatus.QUARANTINED,
            failure_reason=FailureReason.QUOTE_EMPTY,
            detail="The quote normalized to an empty string.",
        )

    found = normalized_chunk.find(normalized_target)
    if found < 0:
        return VerificationResult(
            status=VerificationStatus.QUARANTINED,
            failure_reason=FailureReason.QUOTE_NOT_FOUND,
            detail=(
                "The proposed quote does not appear in the cited chunk. "
                "The finding was quarantined and excluded from the risk score."
            ),
        )

    if len(normalized_target) < MIN_QUOTE_CHARS:
        return VerificationResult(
            status=VerificationStatus.NEEDS_REVIEW,
            failure_reason=FailureReason.QUOTE_TOO_SHORT,
            detail=f"The quote is shorter than {MIN_QUOTE_CHARS} characters.",
        )

    chunk_start = index_map[found]
    last = found + len(normalized_target) - 1
    chunk_end = index_map[last] + 1

    return VerificationResult(
        status=VerificationStatus.VERIFIED,
        method=VerificationMethod.OFFSET_NORMALIZED,
        matched_quote=chunk.text[chunk_start:chunk_end],
        chunk_start_offset=chunk_start,
        chunk_end_offset=chunk_end,
        doc_start_offset=chunk.start_offset + chunk_start,
        doc_end_offset=chunk.start_offset + chunk_end,
        detail="Whitespace-insensitive match; offsets recovered from the index map.",
    )


def verify_against_document(
    document_text: str, doc_start: int, doc_end: int, expected_quote: str
) -> bool:
    """Final belt-and-braces check that absolute offsets address the right text."""
    if doc_start < 0 or doc_end > len(document_text) or doc_end <= doc_start:
        return False
    return normalize_quote(document_text[doc_start:doc_end]) == normalize_quote(expected_quote)
