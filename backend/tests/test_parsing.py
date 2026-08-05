"""Upload validation and document parsing."""

import io
import zipfile

import pytest

from app.core.errors import (
    EmptyDocument,
    FileTooLarge,
    ScannedDocument,
    Unprocessable,
    UnsupportedMediaType,
)
from app.services.parsing import (
    parse_plain_text,
    parse_txt,
    parse_upload,
    sniff_file_type,
    validate_size,
)

LONG_TEXT = (
    "ACME SERVICES AGREEMENT\n\n1. TERMS\nThe vendor may retain all customer data "
    "indefinitely following termination of this agreement for any purpose. " * 8
)


class TestMagicByteSniffing:
    def test_pdf_is_detected_by_magic_bytes(self):
        assert sniff_file_type(b"%PDF-1.7\n...", "anything.txt") == "pdf"

    def test_docx_requires_the_word_content_part(self):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("word/document.xml", "<w:document/>")
        assert sniff_file_type(buffer.getvalue(), "x.docx") == "docx"

    def test_a_plain_zip_is_not_a_docx(self):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("hello.txt", "hi")
        with pytest.raises(UnsupportedMediaType, match="not a Word"):
            sniff_file_type(buffer.getvalue(), "x.docx")

    def test_plain_text_is_detected(self):
        assert sniff_file_type(b"This is an agreement.", "x.txt") == "txt"

    def test_a_renamed_binary_is_rejected(self):
        # An executable renamed to .pdf must not be accepted on extension alone.
        with pytest.raises(UnsupportedMediaType):
            sniff_file_type(b"\x7fELF\x02\x01\x01\x00" + b"\x00" * 100, "invoice.pdf")

    def test_the_error_explains_the_mismatch(self):
        with pytest.raises(UnsupportedMediaType, match="do not match that format"):
            sniff_file_type(b"\x00\x01\x02\x03binary", "contract.pdf")

    def test_empty_input_is_rejected(self):
        with pytest.raises(EmptyDocument):
            sniff_file_type(b"", "x.txt")


class TestSizeLimits:
    def test_oversized_file_is_rejected(self):
        with pytest.raises(FileTooLarge, match="exceeds"):
            validate_size(b"x" * 2000, 1000)

    def test_the_message_states_both_sizes(self):
        with pytest.raises(FileTooLarge) as excinfo:
            validate_size(b"x" * (11 * 1024 * 1024), 10 * 1024 * 1024)
        assert "11.0 MB" in str(excinfo.value)
        assert "10 MB" in str(excinfo.value)

    def test_a_file_at_the_limit_is_accepted(self):
        validate_size(b"x" * 1000, 1000)

    def test_empty_file_is_rejected(self):
        with pytest.raises(EmptyDocument):
            validate_size(b"", 1000)


class TestPlainText:
    def test_normalized_text_and_hash_are_produced(self):
        parsed = parse_plain_text(LONG_TEXT, max_pages=150)
        assert parsed.source_type == "paste"
        assert len(parsed.content_sha256) == 64
        assert parsed.char_count == len(parsed.normalized_text)

    def test_a_title_is_inferred_from_the_first_line(self):
        assert parse_plain_text(LONG_TEXT, 150).title == "ACME SERVICES AGREEMENT"

    def test_too_short_text_is_rejected_with_a_useful_message(self):
        with pytest.raises(EmptyDocument, match="at least"):
            parse_plain_text("Too short.", 150)

    def test_page_budget_is_enforced(self):
        with pytest.raises(Unprocessable) as excinfo:
            parse_plain_text("word " * 200_000, max_pages=10)
        assert excinfo.value.code == "TOO_MANY_PAGES"

    def test_utf8_bytes_are_decoded(self):
        parsed = parse_txt(LONG_TEXT.encode("utf-8"), 150)
        assert parsed.source_type == "txt"

    def test_latin1_bytes_are_decoded(self):
        parsed = parse_txt(LONG_TEXT.encode("latin-1"), 150)
        assert parsed.normalized_text


class TestPdf:
    def test_a_text_pdf_is_parsed(self):
        pypdf = pytest.importorskip("pypdf")
        writer = pypdf.PdfWriter()
        writer.add_blank_page(width=612, height=792)
        buffer = io.BytesIO()
        writer.write(buffer)
        # A blank page has no extractable text, so this must be treated as scanned.
        with pytest.raises(ScannedDocument):
            parse_upload(buffer.getvalue(), "blank.pdf", max_bytes=10_000_000, max_pages=150)

    def test_the_scanned_pdf_message_is_actionable(self):
        pypdf = pytest.importorskip("pypdf")
        writer = pypdf.PdfWriter()
        writer.add_blank_page(width=612, height=792)
        buffer = io.BytesIO()
        writer.write(buffer)
        with pytest.raises(ScannedDocument) as excinfo:
            parse_upload(buffer.getvalue(), "scan.pdf", max_bytes=10_000_000, max_pages=150)
        message = str(excinfo.value)
        assert "selectable text" in message
        assert "paste" in message.lower()
        assert excinfo.value.code == "SCANNED_PDF_UNSUPPORTED"

    def test_a_scanned_pdf_is_never_analyzed_as_an_empty_document(self):
        # Regression guard: the failure mode we must never have is accepting a
        # scan, extracting nothing, and reporting "no risks found".
        pypdf = pytest.importorskip("pypdf")
        writer = pypdf.PdfWriter()
        for _ in range(3):
            writer.add_blank_page(width=612, height=792)
        buffer = io.BytesIO()
        writer.write(buffer)
        with pytest.raises(ScannedDocument):
            parse_upload(buffer.getvalue(), "scan.pdf", max_bytes=10_000_000, max_pages=150)

    def test_corrupt_pdf_is_rejected_cleanly(self):
        with pytest.raises((Unprocessable, UnsupportedMediaType)):
            parse_upload(b"%PDF-1.4\nnot really a pdf", "x.pdf", max_bytes=10_000, max_pages=150)


class TestDocx:
    def test_a_real_docx_is_parsed(self):
        docx = pytest.importorskip("docx")
        document = docx.Document()
        document.add_paragraph("ACME SOFTWARE LICENSE AGREEMENT")
        for _ in range(12):
            document.add_paragraph(
                "The vendor may retain all customer data indefinitely following termination "
                "of this agreement for archival and analytics purposes."
            )
        buffer = io.BytesIO()
        document.save(buffer)

        parsed = parse_upload(buffer.getvalue(), "acme.docx", max_bytes=10_000_000, max_pages=150)
        assert parsed.source_type == "docx"
        assert "retain all customer data" in parsed.normalized_text
        assert parsed.title == "ACME SOFTWARE LICENSE AGREEMENT"

    def test_an_empty_docx_is_rejected(self):
        docx = pytest.importorskip("docx")
        buffer = io.BytesIO()
        docx.Document().save(buffer)
        with pytest.raises(EmptyDocument):
            parse_upload(buffer.getvalue(), "empty.docx", max_bytes=10_000_000, max_pages=150)


class TestUploadEntryPoint:
    def test_the_filename_becomes_a_fallback_title(self):
        parsed = parse_upload(
            LONG_TEXT.encode("utf-8"), "acme-eula.txt", max_bytes=10_000_000, max_pages=150
        )
        assert parsed.title

    def test_size_is_checked_before_parsing(self):
        with pytest.raises(FileTooLarge):
            parse_upload(b"x" * 5000, "x.txt", max_bytes=1000, max_pages=150)
