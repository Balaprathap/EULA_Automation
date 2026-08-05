"""Clause-aware chunking tests.

The offset invariant asserted here is what makes evidence verification and
frontend highlighting trustworthy, so these tests are load-bearing.
"""

from pathlib import Path

from app.services.chunking import MAX_TOKENS, Chunk, chunk_document, verify_offsets
from app.services.normalization import normalize_text

FIXTURES = Path(__file__).parent / "fixtures"
SAMPLE = normalize_text((FIXTURES / "sample_eula.txt").read_text(encoding="utf-8"))


class TestOffsetInvariant:
    """text[start:end] must equal the stored chunk text. Everything depends on this."""

    def test_sample_agreement_offsets_are_exact(self):
        chunks = chunk_document(SAMPLE)
        assert chunks, "sample agreement produced no chunks"
        for chunk in chunks:
            assert SAMPLE[chunk.start_offset : chunk.end_offset] == chunk.text

    def test_verify_offsets_reports_no_violations(self):
        assert verify_offsets(SAMPLE, chunk_document(SAMPLE)) == []

    def test_chunks_do_not_overlap_and_are_ordered(self):
        chunks = chunk_document(SAMPLE)
        for previous, current in zip(chunks, chunks[1:]):
            assert previous.end_offset <= current.start_offset
            assert previous.ordinal < current.ordinal

    def test_ordinals_are_dense_and_zero_based(self):
        chunks = chunk_document(SAMPLE)
        assert [c.ordinal for c in chunks] == list(range(len(chunks)))

    def test_no_content_is_lost(self):
        chunks = chunk_document(SAMPLE)
        covered = "".join(SAMPLE[c.start_offset : c.end_offset] for c in chunks)
        assert "".join(covered.split()) == "".join(SAMPLE.split())

    def test_verify_offsets_detects_corruption(self):
        chunks = chunk_document(SAMPLE)
        chunks[0].text = "this text is not in the document"
        assert verify_offsets(SAMPLE, chunks) != []


class TestClauseAwareness:
    def test_numbered_sections_start_new_chunks(self):
        text = normalize_text(
            "1. DEFINITIONS\nThe following terms apply throughout this agreement. "
            + "Filler sentence to reach a workable size. " * 12
            + "\n\n2. LICENSE GRANT\nVendor grants a limited license to the customer. "
            + "Filler sentence to reach a workable size. " * 12
        )
        chunks = chunk_document(text)
        assert len(chunks) >= 2
        assert verify_offsets(text, chunks) == []

    def test_headings_are_captured(self):
        chunks = chunk_document(SAMPLE)
        headings = [c.heading for c in chunks if c.heading]
        assert headings, "expected at least one heading to be detected"

    def test_all_caps_heading_is_recognised(self):
        text = normalize_text(
            "LIMITATION OF LIABILITY\n"
            + "In no event shall the vendor be liable for indirect damages. " * 20
        )
        chunks = chunk_document(text)
        assert any(c.heading == "LIMITATION OF LIABILITY" for c in chunks)


class TestSizeBounds:
    def test_no_chunk_greatly_exceeds_the_maximum(self):
        for chunk in chunk_document(SAMPLE):
            assert chunk.token_count <= MAX_TOKENS + 50

    def test_very_long_clause_is_split(self):
        long_clause = "The customer hereby agrees to the following obligation. " * 400
        text = normalize_text("1. OBLIGATIONS\n" + long_clause)
        chunks = chunk_document(text)
        assert len(chunks) > 1
        assert verify_offsets(text, chunks) == []
        for chunk in chunks:
            assert chunk.token_count <= MAX_TOKENS + 50

    def test_tiny_fragments_are_merged(self):
        text = normalize_text("\n\n".join(f"{i}. Short." for i in range(1, 16)))
        chunks = chunk_document(text)
        assert len(chunks) < 15
        assert verify_offsets(text, chunks) == []


class TestEdgeCases:
    def test_empty_document(self):
        assert chunk_document("") == []
        assert chunk_document("   \n\n  ") == []

    def test_single_short_line(self):
        text = normalize_text("This agreement is short.")
        chunks = chunk_document(text)
        assert len(chunks) == 1
        assert chunks[0].text == text
        assert chunks[0].start_offset == 0

    def test_duplicate_text_gets_distinct_offsets(self):
        clause = "Vendor may retain data indefinitely. " * 15
        text = normalize_text(f"1. FIRST\n{clause}\n\n2. SECOND\n{clause}")
        chunks = chunk_document(text)
        starts = [c.start_offset for c in chunks]
        assert len(starts) == len(set(starts))
        assert verify_offsets(text, chunks) == []

    def test_windows_line_endings_do_not_break_offsets(self):
        raw = "1. TERMS\r\nThe vendor retains all data. " * 30
        text = normalize_text(raw)
        assert verify_offsets(text, chunk_document(text)) == []

    def test_invalid_span_is_rejected(self):
        try:
            Chunk(ordinal=0, heading=None, text="x", start_offset=10, end_offset=5, token_count=1)
        except ValueError:
            return
        raise AssertionError("expected ValueError for an inverted span")
