"""Evidence verification tests.

``TestFabricatedEvidence`` is the project's critical regression test: it pins
the guarantee that a quote the model invented can never be shown to a user as a
confirmed finding.
"""

from dataclasses import dataclass

from app.services.chunking import chunk_document
from app.services.normalization import normalize_text
from app.services.verification import (
    FailureReason,
    VerificationMethod,
    VerificationStatus,
    verify_against_document,
    verify_evidence,
)

DOC_ID = "11111111-1111-1111-1111-111111111111"
OTHER_DOC_ID = "22222222-2222-2222-2222-222222222222"


@dataclass
class FakeChunk:
    id: str
    document_id: str
    ordinal: int
    text: str
    start_offset: int
    end_offset: int


REAL_TEXT = (
    "Acme may retain Customer Data indefinitely following termination of this "
    "Agreement for archival, security, and product improvement purposes."
)
CHUNK = FakeChunk(
    id="c1",
    document_id=DOC_ID,
    ordinal=3,
    text=REAL_TEXT,
    start_offset=1000,
    end_offset=1000 + len(REAL_TEXT),
)


class TestExactMatch:
    def test_exact_quote_verifies(self):
        result = verify_evidence(
            chunk=CHUNK,
            proposed_quote="Acme may retain Customer Data indefinitely",
            document_id=DOC_ID,
        )
        assert result.verified
        assert result.method is VerificationMethod.OFFSET_EXACT

    def test_absolute_offsets_are_recomputed_from_the_match(self):
        result = verify_evidence(
            chunk=CHUNK, proposed_quote="archival, security", document_id=DOC_ID
        )
        assert result.verified
        assert result.doc_start_offset == CHUNK.start_offset + REAL_TEXT.index("archival, security")
        assert result.doc_end_offset - result.doc_start_offset == len("archival, security")

    def test_offsets_address_the_original_text(self):
        result = verify_evidence(
            chunk=CHUNK, proposed_quote="following termination", document_id=DOC_ID
        )
        local = REAL_TEXT[result.chunk_start_offset : result.chunk_end_offset]
        assert local == "following termination"


class TestNormalizedMatch:
    def test_quote_reflowed_across_lines_still_verifies(self):
        result = verify_evidence(
            chunk=CHUNK,
            proposed_quote="Acme may retain\n   Customer Data\tindefinitely",
            document_id=DOC_ID,
        )
        assert result.verified
        assert result.method is VerificationMethod.OFFSET_NORMALIZED
        assert result.matched_quote == "Acme may retain Customer Data indefinitely"

    def test_smart_quotes_in_model_output_still_verify(self):
        text = 'The term "Customer Data" means any data submitted to the Service.'
        chunk = FakeChunk("c2", DOC_ID, 0, text, 0, len(text))
        result = verify_evidence(
            chunk=chunk, proposed_quote="The term “Customer Data” means", document_id=DOC_ID
        )
        assert result.verified

    def test_recovered_offsets_are_byte_exact_in_the_chunk(self):
        text = "Section 4.1\n\nAcme  may   retain   data\nindefinitely after termination."
        chunk = FakeChunk("c3", DOC_ID, 0, text, 500, 500 + len(text))
        result = verify_evidence(
            chunk=chunk, proposed_quote="Acme may retain data indefinitely", document_id=DOC_ID
        )
        assert result.verified
        assert text[result.chunk_start_offset : result.chunk_end_offset] == result.matched_quote


class TestFabricatedEvidence:
    """CRITICAL REGRESSION TEST - a fabricated quote must never reach the user.

    The model here proposes a plausible-sounding clause that does not exist in
    the document. The system must quarantine it, keep it out of the verified
    set, exclude it from the risk score, and still return a usable report.
    """

    FABRICATED = (
        "Acme guarantees that Customer Data will be permanently deleted within "
        "twenty-four hours of any termination request."
    )

    def test_fabricated_quote_is_rejected(self):
        result = verify_evidence(chunk=CHUNK, proposed_quote=self.FABRICATED, document_id=DOC_ID)
        assert not result.verified

    def test_fabricated_quote_is_quarantined(self):
        result = verify_evidence(chunk=CHUNK, proposed_quote=self.FABRICATED, document_id=DOC_ID)
        assert result.status is VerificationStatus.QUARANTINED
        assert result.failure_reason is FailureReason.QUOTE_NOT_FOUND

    def test_fabricated_quote_yields_no_offsets_to_highlight(self):
        result = verify_evidence(chunk=CHUNK, proposed_quote=self.FABRICATED, document_id=DOC_ID)
        assert result.doc_start_offset is None
        assert result.doc_end_offset is None
        assert result.matched_quote is None

    def test_counts_and_pass_rate_reflect_the_quarantine(self):
        from app.services.scoring import score_analysis

        proposed = [
            ("Acme may retain Customer Data indefinitely", True),
            (self.FABRICATED, False),
            ("archival, security, and product improvement purposes", True),
        ]
        verified_severities = []
        quarantined = 0
        for quote, _expected in proposed:
            result = verify_evidence(chunk=CHUNK, proposed_quote=quote, document_id=DOC_ID)
            if result.verified:
                verified_severities.append("high")
            else:
                quarantined += 1

        assert quarantined == 1
        score = score_analysis(
            verified_severities,
            quarantine_count=quarantined,
            verified_count=len(verified_severities),
            proposed_count=len(proposed),
        )
        # Report remains usable: two real findings still scored.
        assert score.finding_count == 2
        assert score.quarantine_count == 1
        assert score.verification_pass_rate == 66.7
        assert score.overall_score > 0

    def test_subtle_word_substitution_is_caught(self):
        # One word changed from "may" to "must" - still a fabrication.
        result = verify_evidence(
            chunk=CHUNK,
            proposed_quote="Acme must retain Customer Data indefinitely",
            document_id=DOC_ID,
        )
        assert result.status is VerificationStatus.QUARANTINED


class TestOwnershipEnforcement:
    def test_chunk_from_another_document_is_rejected(self):
        foreign = FakeChunk("cX", OTHER_DOC_ID, 0, REAL_TEXT, 0, len(REAL_TEXT))
        result = verify_evidence(
            chunk=foreign, proposed_quote="Acme may retain Customer Data", document_id=DOC_ID
        )
        assert result.status is VerificationStatus.QUARANTINED
        assert result.failure_reason is FailureReason.CHUNK_WRONG_DOCUMENT

    def test_document_outside_the_organization_is_rejected(self):
        result = verify_evidence(
            chunk=CHUNK,
            proposed_quote="Acme may retain Customer Data",
            document_id=DOC_ID,
            org_document_ids={OTHER_DOC_ID},
        )
        assert result.status is VerificationStatus.QUARANTINED
        assert result.failure_reason is FailureReason.DOCUMENT_WRONG_ORG

    def test_document_inside_the_organization_is_allowed(self):
        result = verify_evidence(
            chunk=CHUNK,
            proposed_quote="Acme may retain Customer Data",
            document_id=DOC_ID,
            org_document_ids={DOC_ID, OTHER_DOC_ID},
        )
        assert result.verified

    def test_missing_chunk_is_quarantined(self):
        result = verify_evidence(chunk=None, proposed_quote="anything", document_id=DOC_ID)
        assert result.failure_reason is FailureReason.CHUNK_NOT_FOUND


class TestDegenerateQuotes:
    def test_empty_quote_is_quarantined(self):
        result = verify_evidence(chunk=CHUNK, proposed_quote="", document_id=DOC_ID)
        assert result.failure_reason is FailureReason.QUOTE_EMPTY

    def test_whitespace_only_quote_is_quarantined(self):
        result = verify_evidence(chunk=CHUNK, proposed_quote="   \n\t ", document_id=DOC_ID)
        assert result.failure_reason is FailureReason.QUOTE_EMPTY

    def test_trivially_short_quote_needs_review(self):
        result = verify_evidence(chunk=CHUNK, proposed_quote="Acme", document_id=DOC_ID)
        assert result.status is VerificationStatus.NEEDS_REVIEW
        assert result.failure_reason is FailureReason.QUOTE_TOO_SHORT


class TestEndToEndAgainstRealChunks:
    def test_quotes_taken_from_real_chunks_verify_and_map_back(self):
        from pathlib import Path

        raw = (Path(__file__).parent / "fixtures" / "sample_eula.txt").read_text(encoding="utf-8")
        document = normalize_text(raw)
        chunks = chunk_document(document)

        checked = 0
        for index, chunk in enumerate(chunks):
            if len(chunk.text) < 80:
                continue
            quote = chunk.text[20:80]
            result = verify_evidence(
                chunk=FakeChunk(
                    id=f"c{index}",
                    document_id=DOC_ID,
                    ordinal=chunk.ordinal,
                    text=chunk.text,
                    start_offset=chunk.start_offset,
                    end_offset=chunk.end_offset,
                ),
                proposed_quote=quote,
                document_id=DOC_ID,
            )
            assert result.verified, f"chunk {index} failed verification"
            # Absolute offsets must address exactly this text in the full document.
            assert verify_against_document(
                document, result.doc_start_offset, result.doc_end_offset, quote
            )
            checked += 1
        assert checked > 3, "expected several chunks to be checked"

    def test_out_of_range_offsets_are_rejected(self):
        assert verify_against_document("short text", 0, 999, "short text") is False
        assert verify_against_document("short text", 5, 2, "x") is False
