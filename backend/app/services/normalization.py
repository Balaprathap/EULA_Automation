"""Text normalization.

The normalized text is the single authoritative coordinate space for the whole
system. Chunk offsets, evidence offsets, and frontend highlight ranges all index
into the *normalized* string, which is what we persist as
``documents.normalized_text``. Nothing downstream ever indexes into raw text.

Because the same function normalizes both the stored document and any quote the
model proposes, evidence verification is a pure substring problem.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata

# Unicode space characters that should be treated as an ordinary space.
_UNICODE_SPACES = dict.fromkeys(
    [
        0x00A0,  # no-break space
        0x1680,
        0x2000,
        0x2001,
        0x2002,
        0x2003,
        0x2004,
        0x2005,
        0x2006,
        0x2007,
        0x2008,
        0x2009,
        0x200A,
        0x202F,
        0x205F,
        0x3000,
    ],
    " ",
)

# Zero-width and formatting characters that carry no textual meaning.
_INVISIBLE = dict.fromkeys([0x200B, 0x200C, 0x200D, 0x2060, 0xFEFF, 0x00AD], None)

# Typographic punctuation folded to ASCII so quotes match regardless of source.
_PUNCTUATION = {
    0x2018: "'",
    0x2019: "'",
    0x201A: "'",
    0x201B: "'",
    0x201C: '"',
    0x201D: '"',
    0x201E: '"',
    0x2032: "'",
    0x2033: '"',
    0x2010: "-",
    0x2011: "-",
    0x2012: "-",
    0x2013: "-",
    0x2014: "-",
    0x2015: "-",
    0x2212: "-",
}

_TRANSLATION = {**_UNICODE_SPACES, **_INVISIBLE, **_PUNCTUATION}

_HORIZONTAL_RUN = re.compile(r"[ \t\f\v]+")
_TRAILING_WS = re.compile(r"[ \t]+(?=\n)")
_LEADING_WS = re.compile(r"(?<=\n)[ \t]+")
_BLANK_RUN = re.compile(r"\n{3,}")


def normalize_text(raw: str) -> str:
    """Normalize document text into the authoritative coordinate space.

    Steps, in order:
      1. Unicode NFKC normalization.
      2. Fold unicode spaces, strip invisible characters, fold smart punctuation.
      3. Normalize CRLF and CR line endings to LF.
      4. Collapse runs of spaces/tabs to a single space.
      5. Strip leading/trailing horizontal whitespace on each line.
      6. Collapse three or more newlines to exactly two.
      7. Strip the document's leading and trailing whitespace.

    The function is idempotent: ``normalize_text(normalize_text(x)) == normalize_text(x)``.
    """
    if not raw:
        return ""

    text = unicodedata.normalize("NFKC", raw)
    text = text.translate(_TRANSLATION)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _HORIZONTAL_RUN.sub(" ", text)
    text = _TRAILING_WS.sub("", text)
    text = _LEADING_WS.sub("", text)
    text = _BLANK_RUN.sub("\n\n", text)
    return text.strip()


def normalize_quote(quote: str) -> str:
    """Normalize a model-proposed quote for comparison against stored text.

    Applies the same character-level folding as :func:`normalize_text` and then
    flattens *all* whitespace (including newlines) to single spaces, so a quote
    the model reflowed across lines still matches the stored clause.
    """
    if not quote:
        return ""
    text = unicodedata.normalize("NFKC", quote)
    text = text.translate(_TRANSLATION)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def content_hash(text: str) -> str:
    """SHA-256 of normalized text - used for dedupe and embedding cache keys."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def estimate_tokens(text: str) -> int:
    """Cheap, dependency-free token estimate (~4 characters per token).

    Used only for chunk sizing and pre-flight budget checks. Authoritative token
    counts always come from the provider's usage response, never from this.
    """
    if not text:
        return 0
    return max(1, (len(text) + 3) // 4)


def text_density(text: str, page_count: int) -> float:
    """Average characters of extracted text per page - drives scanned-PDF detection."""
    if page_count <= 0:
        return float(len(text))
    return len(text) / page_count
