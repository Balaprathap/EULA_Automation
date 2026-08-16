from app.services.chat import extract_citation_refs


def test_extract_citation_refs_deduplicates_and_preserves_order():
    text = "Risk [F2], supported by [C1]. Again [F2]. Another source [C4]."

    assert extract_citation_refs(text) == ["F2", "C1", "C4"]


def test_extract_citation_refs_ignores_unknown_formats():
    text = "Ignore [X1] and [CABC], keep [C2]."

    assert extract_citation_refs(text) == ["C2"]
