"""Text normalization tests - the coordinate space everything else depends on."""

from app.services.normalization import (
    content_hash,
    estimate_tokens,
    normalize_quote,
    normalize_text,
    text_density,
)


class TestUnicodeNormalization:
    def test_nfkc_folds_compatibility_forms(self):
        assert normalize_text("ﬁle") == "file"
        assert normalize_text("①") == "1"

    def test_non_breaking_space_becomes_ordinary_space(self):
        assert normalize_text("Section 1") == "Section 1"

    def test_zero_width_characters_are_removed(self):
        assert normalize_text("Lia​bility") == "Liability"
        assert normalize_text("﻿AGREEMENT") == "AGREEMENT"

    def test_smart_quotes_fold_to_ascii(self):
        assert normalize_text("“Customer Data”") == '"Customer Data"'
        assert normalize_text("Acme’s data") == "Acme's data"

    def test_dashes_fold_to_hyphen(self):
        assert normalize_text("ninety—day") == "ninety-day"


class TestLineEndings:
    def test_windows_line_endings(self):
        assert normalize_text("a\r\nb") == "a\nb"

    def test_classic_mac_line_endings(self):
        assert normalize_text("a\rb") == "a\nb"

    def test_mixed_line_endings(self):
        assert normalize_text("a\r\nb\rc\nd") == "a\nb\nc\nd"


class TestWhitespace:
    def test_repeated_spaces_collapse(self):
        assert normalize_text("a     b") == "a b"

    def test_tabs_collapse(self):
        assert normalize_text("a\t\t\tb") == "a b"

    def test_line_indentation_is_stripped(self):
        assert normalize_text("a\n    b") == "a\nb"

    def test_trailing_line_whitespace_is_stripped(self):
        assert normalize_text("a   \nb") == "a\nb"

    def test_three_or_more_newlines_collapse_to_two(self):
        assert normalize_text("a\n\n\n\n\nb") == "a\n\nb"

    def test_paragraph_break_is_preserved(self):
        assert normalize_text("a\n\nb") == "a\n\nb"

    def test_document_edges_are_stripped(self):
        assert normalize_text("\n\n  AGREEMENT  \n\n") == "AGREEMENT"


class TestIdempotence:
    def test_normalizing_twice_changes_nothing(self):
        raw = "  “A”\r\n\n\n\tB  C  \n "
        once = normalize_text(raw)
        assert normalize_text(once) == once

    def test_empty_input(self):
        assert normalize_text("") == ""
        assert normalize_text("   \n\n  ") == ""


class TestQuoteNormalization:
    def test_newlines_in_quote_flatten_to_spaces(self):
        assert normalize_quote("Acme may\nretain data") == "Acme may retain data"

    def test_quote_matches_reflowed_source(self):
        source = normalize_text("Acme may retain\nCustomer Data indefinitely")
        quote = normalize_quote("Acme may retain Customer Data indefinitely")
        assert quote in normalize_quote(source)

    def test_empty_quote(self):
        assert normalize_quote("") == ""


class TestHashingAndEstimates:
    def test_hash_is_stable_and_distinct(self):
        assert content_hash("abc") == content_hash("abc")
        assert content_hash("abc") != content_hash("abd")
        assert len(content_hash("abc")) == 64

    def test_token_estimate_scales_with_length(self):
        assert estimate_tokens("") == 0
        assert estimate_tokens("abcd") == 1
        assert estimate_tokens("a" * 400) == 100

    def test_text_density_detects_scanned_documents(self):
        assert text_density("x" * 3000, 10) == 300.0
        assert text_density("", 10) == 0.0
        assert text_density("abc", 0) == 3.0
