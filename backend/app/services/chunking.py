"""Clause-aware chunking for legal agreements.

Legal documents are hierarchical, not prose. Splitting on a fixed character
window cuts obligations in half and destroys the evidence trail, so this module
segments on real clause boundaries - numbered sections, lettered subclauses,
ALL-CAPS headings, definition blocks, and bullet lists - and only falls back to
sentence splitting when a single clause is too large to send as one unit.

Every chunk carries exact ``start_offset``/``end_offset`` into the normalized
document. That invariant is what makes evidence verification and frontend
highlighting possible, and it is enforced by tests in
``tests/test_chunking.py``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.services.normalization import estimate_tokens

TARGET_TOKENS = 400
MIN_TOKENS = 60
MAX_TOKENS = 800
OVERLAP_TOKENS = 40

# Section headings: "1.", "1.1", "12.3.4", "Section 5", "Article IV", "(a)", "(iv)"
_HEADING_PATTERNS = [
    re.compile(
        r"^\s*(?:ARTICLE|Article|SECTION|Section|Clause|CLAUSE)\s+[0-9IVXLC]+[.:)]?\s*", re.M
    ),
    re.compile(r"^\s*\d+(?:\.\d+)*[.)]\s+", re.M),
    re.compile(r"^\s*\(?[a-z]\)\s+", re.M),
    re.compile(r"^\s*\(?(?:i{1,3}|iv|v|vi{1,3}|ix|x)\)\s+", re.M),
    # Standalone ALL-CAPS heading lines, e.g. "LIMITATION OF LIABILITY"
    re.compile(r"^\s*[A-Z][A-Z0-9 ,'&/\-]{6,80}\s*$", re.M),
]

_SENTENCE_END = re.compile(r"(?<=[.;:])\s+(?=[A-Z(\"'])")


@dataclass
class Chunk:
    """A clause-aligned span of the normalized document."""

    ordinal: int
    heading: str | None
    text: str
    start_offset: int
    end_offset: int
    token_count: int

    def __post_init__(self) -> None:
        if self.start_offset < 0 or self.end_offset < self.start_offset:
            raise ValueError(f"Invalid chunk span [{self.start_offset}, {self.end_offset})")


def _boundary_offsets(text: str) -> list[int]:
    """Collect every character offset at which a new clause plausibly starts."""
    offsets = {0, len(text)}

    for pattern in _HEADING_PATTERNS:
        for match in pattern.finditer(text):
            # Anchor to the first non-space character of the matched line.
            start = match.start()
            while start < len(text) and text[start] in " \t":
                start += 1
            offsets.add(start)

    # Blank lines are paragraph boundaries.
    for match in re.finditer(r"\n\n+", text):
        offsets.add(match.end())

    return sorted(o for o in offsets if 0 <= o <= len(text))


def _extract_heading(segment: str) -> str | None:
    """Return the segment's heading line, if the segment opens with one."""
    first_line = segment.lstrip().split("\n", 1)[0].strip()
    if not first_line or len(first_line) > 120:
        return None
    for pattern in _HEADING_PATTERNS:
        if pattern.match(first_line + "\n"):
            return first_line
    return None


def _split_oversized(text: str, start: int) -> list[tuple[int, int]]:
    """Split a clause that exceeds MAX_TOKENS at sentence boundaries.

    Falls back to a hard character split only if a single sentence is itself
    larger than the maximum, which keeps the function total.
    """
    spans: list[tuple[int, int]] = []
    cursor = 0
    piece_start = 0
    max_chars = MAX_TOKENS * 4

    boundaries = [m.start() for m in _SENTENCE_END.finditer(text)] + [len(text)]
    for boundary in boundaries:
        if boundary - piece_start >= TARGET_TOKENS * 4:
            spans.append((start + piece_start, start + boundary))
            piece_start = boundary
        cursor = boundary

    if piece_start < cursor:
        spans.append((start + piece_start, start + cursor))

    # Hard-split anything still oversized (a single enormous sentence).
    final: list[tuple[int, int]] = []
    for s, e in spans:
        while e - s > max_chars:
            final.append((s, s + max_chars))
            s += max_chars
        if e > s:
            final.append((s, e))
    return final or [(start, start + len(text))]


def chunk_document(text: str) -> list[Chunk]:
    """Split normalized document text into clause-aware chunks with exact offsets.

    Guarantees enforced by tests:
      * ``text[chunk.start_offset:chunk.end_offset].strip() == chunk.text``
      * chunks are ordered and non-overlapping
      * concatenating the spans covers all non-whitespace content
    """
    if not text or not text.strip():
        return []

    boundaries = _boundary_offsets(text)

    # Build raw segments between consecutive boundaries.
    raw_spans: list[tuple[int, int]] = []
    for i in range(len(boundaries) - 1):
        s, e = boundaries[i], boundaries[i + 1]
        if e > s and text[s:e].strip():
            raw_spans.append((s, e))
    if not raw_spans:
        raw_spans = [(0, len(text))]

    # Merge undersized neighbours forward, split oversized segments.
    merged: list[tuple[int, int]] = []
    pending: tuple[int, int] | None = None

    for s, e in raw_spans:
        current = (pending[0], e) if pending else (s, e)
        segment = text[current[0] : current[1]]
        tokens = estimate_tokens(segment.strip())

        if tokens < MIN_TOKENS:
            pending = current  # keep accumulating
            continue

        pending = None
        if tokens > MAX_TOKENS:
            merged.extend(_split_oversized(segment, current[0]))
        else:
            merged.append(current)

    if pending:
        # Tail fragment: append to the previous chunk if that keeps it in budget.
        if merged and estimate_tokens(text[merged[-1][0] : pending[1]]) <= MAX_TOKENS:
            merged[-1] = (merged[-1][0], pending[1])
        else:
            merged.append(pending)

    chunks: list[Chunk] = []
    for ordinal, (s, e) in enumerate(merged):
        segment = text[s:e]
        stripped = segment.strip()
        if not stripped:
            continue
        # Tighten the span onto the stripped content so offsets stay exact.
        lead = len(segment) - len(segment.lstrip())
        trail = len(segment) - len(segment.rstrip())
        chunks.append(
            Chunk(
                ordinal=len(chunks),
                heading=_extract_heading(segment),
                text=stripped,
                start_offset=s + lead,
                end_offset=e - trail,
                token_count=estimate_tokens(stripped),
            )
        )
        del ordinal
    return chunks


def verify_offsets(text: str, chunks: list[Chunk]) -> list[str]:
    """Return a list of offset-invariant violations. Empty list means valid."""
    problems: list[str] = []
    previous_end = -1
    for chunk in chunks:
        slice_ = text[chunk.start_offset : chunk.end_offset]
        if slice_ != chunk.text:
            problems.append(
                f"chunk {chunk.ordinal}: stored text does not match "
                f"text[{chunk.start_offset}:{chunk.end_offset}]"
            )
        if chunk.start_offset < previous_end:
            problems.append(f"chunk {chunk.ordinal}: overlaps the previous chunk")
        previous_end = chunk.end_offset
    return problems
