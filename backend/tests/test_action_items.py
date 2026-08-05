"""Action items derived from verified findings.

The load-bearing guarantees: only verified findings produce items, quarantined
findings produce none, and no calendar date is ever invented.
"""

import pytest

from app.services.action_items import (
    CATEGORY_OBLIGATIONS,
    DerivedActionItem,
    derive_action_items,
    extract_duration,
)


def finding(**overrides):
    base = {
        "id": "f-1",
        "category": "automatic_renewal",
        "verification_status": "verified",
        "quote": "shall automatically renew for successive twelve (12) month periods unless "
        "Customer provides written notice at least ninety (90) days prior to the end of the term",
        "plain_summary": "The subscription renews automatically.",
        "machine_severity": "high",
        "effective_severity": "high",
        "doc_start_offset": 100,
        "doc_end_offset": 260,
    }
    base.update(overrides)
    return base


class TestVerifiedFindingsOnly:
    def test_verified_finding_produces_an_action_item(self):
        items = derive_action_items([finding()])
        assert len(items) == 1
        assert items[0].finding_id == "f-1"
        assert items[0].obligation_type == "automatic_renewal"

    def test_quarantined_finding_produces_nothing(self):
        items = derive_action_items([finding(verification_status="quarantined")])
        assert items == [], "a quarantined finding must never become an action item"

    def test_needs_review_finding_produces_nothing(self):
        assert derive_action_items([finding(verification_status="needs_review")]) == []

    def test_pending_finding_produces_nothing(self):
        assert derive_action_items([finding(verification_status="pending")]) == []

    def test_mixed_set_only_yields_the_verified_one(self):
        items = derive_action_items(
            [
                finding(id="ok", verification_status="verified"),
                finding(id="bad", verification_status="quarantined", category="cancellation"),
            ]
        )
        assert [i.finding_id for i in items] == ["ok"]

    def test_every_item_carries_its_evidence_quote(self):
        item = derive_action_items([finding()])[0]
        assert item.evidence_quote
        assert item.doc_start_offset == 100
        assert item.doc_end_offset == 260

    def test_verified_finding_without_a_quote_is_skipped(self):
        assert derive_action_items([finding(quote="")]) == []

    def test_unmapped_category_produces_nothing(self):
        """Categories without a trackable obligation must not create vague items."""
        assert derive_action_items([finding(category="some_unmapped_category")]) == []


class TestDatesAreNeverInvented:
    def test_calendar_date_stays_unresolved(self):
        """The schema has no contract start date, so no due date can be computed."""
        item = derive_action_items([finding()])[0]
        assert item.date_status == "unresolved"

    def test_duration_is_captured_verbatim_not_converted_to_a_date(self):
        item = derive_action_items([finding()])[0]
        assert item.duration_days == 360  # twelve (12) months, matched first
        assert item.duration_text is not None
        assert not hasattr(item, "due_date") or getattr(item, "due_date", None) is None

    def test_description_explains_why_the_date_is_unresolved(self):
        item = derive_action_items([finding()])[0]
        assert "cannot be determined automatically" in item.description
        assert "set the due date manually" in item.description

    def test_missing_duration_is_recorded_as_absent(self):
        item = derive_action_items(
            [
                finding(
                    quote="This agreement is governed by the laws of Delaware.",
                    category="governing_law",
                )
            ]
        )[0]
        assert item.duration_days is None
        assert item.duration_text is None
        assert "No specific period was stated" in item.description

    @pytest.mark.parametrize(
        "quote,expected_days",
        [
            ("at least ninety (90) days prior", 90),
            ("within thirty (30) days", 30),
            ("one hundred eighty (180) days", 180),
            ("successive twelve (12) month periods", 360),
            ("delete within 45 days", 45),
            ("no duration at all here", None),
        ],
    )
    def test_duration_extraction(self, quote, expected_days):
        days, _ = extract_duration(quote)
        assert days == expected_days

    def test_absurd_durations_are_ignored(self):
        assert extract_duration("(99999) days")[0] is None

    def test_extraction_reads_the_quote_not_the_summary(self):
        """A period mentioned only in the AI summary must not become a duration."""
        item = derive_action_items(
            [
                finding(
                    quote="The Customer may cancel this agreement.",
                    plain_summary="Cancellation requires 90 days notice.",
                    category="cancellation",
                )
            ]
        )[0]
        assert item.duration_days is None


class TestPriorityAndDedupe:
    def test_severity_can_raise_priority(self):
        item = derive_action_items(
            [finding(category="governing_law", effective_severity="critical")]
        )[0]
        assert item.priority == "urgent"

    def test_severity_never_lowers_the_category_default(self):
        item = derive_action_items(
            [finding(category="automatic_renewal", effective_severity="info")]
        )[0]
        assert item.priority == "urgent", "auto-renewal stays urgent regardless of severity"

    def test_dedupe_key_is_stable_across_runs(self):
        first = derive_action_items([finding()])[0]
        second = derive_action_items([finding()])[0]
        assert first.dedupe_key == second.dedupe_key

    def test_dedupe_key_differs_per_finding(self):
        a = derive_action_items([finding(id="f-1")])[0]
        b = derive_action_items([finding(id="f-2")])[0]
        assert a.dedupe_key != b.dedupe_key

    def test_duplicate_findings_yield_one_item(self):
        items = derive_action_items([finding(), finding()])
        assert len(items) == 1

    def test_all_mapped_categories_produce_valid_obligation_types(self):
        valid = {
            "cancellation_deadline",
            "automatic_renewal",
            "notice_period",
            "termination_requirement",
            "payment_obligation",
            "data_deletion",
            "audit_requirement",
            "data_retention",
            "renewal_frequency",
            "governing_law_followup",
            "legal_escalation",
        }
        for category, (obligation_type, title, priority) in CATEGORY_OBLIGATIONS.items():
            assert obligation_type in valid, category
            assert title and len(title) <= 300
            assert priority in ("low", "medium", "high", "urgent")


class TestNoAiCall:
    def test_derivation_is_deterministic_and_offline(self):
        """No LLM, so no token cost and no possibility of invention."""
        import inspect

        import app.services.action_items as module

        source = inspect.getsource(module)
        for forbidden in ("anthropic", "LLMProvider", "llm.complete", "openai"):
            assert forbidden not in source, f"action item derivation must not use {forbidden}"

    def test_repeated_derivation_is_identical(self):
        findings = [finding(), finding(id="f-2", category="cancellation")]
        first = derive_action_items(findings)
        second = derive_action_items(findings)
        assert [vars(i) for i in first] == [vars(i) for i in second]


class TestDataclass:
    def test_defaults_are_safe(self):
        item = DerivedActionItem(
            title="t",
            description="d",
            category="c",
            obligation_type="notice_period",
            finding_id="f",
            evidence_quote="q",
        )
        assert item.date_status == "unresolved"
        assert item.priority == "medium"
        assert item.duration_days is None
