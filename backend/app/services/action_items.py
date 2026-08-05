"""Deterministic derivation of action items from verified findings.

No LLM is involved. Every action item is produced by pattern-matching the
*already verified* evidence quote, so nothing here can invent an obligation that
is not in the agreement.

The date rule, stated plainly: the schema holds no contract start, effective, or
renewal date, so a clause like "ninety (90) days prior to the end of the term"
cannot be turned into a calendar date without inventing an anchor. We therefore
record the obligation and the duration, and leave `date_status = 'unresolved'`
for a human to complete. That is a deliberate limitation, not an oversight.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)

# Only these finding categories imply a trackable obligation. Everything else
# produces no action item rather than a vague one.
CATEGORY_OBLIGATIONS: dict[str, tuple[str, str, str]] = {
    # category -> (obligation_type, title, default priority)
    "cancellation": (
        "cancellation_deadline",
        "Confirm the cancellation procedure and deadline",
        "high",
    ),
    "automatic_renewal": (
        "automatic_renewal",
        "Diarise the auto-renewal and non-renewal notice window",
        "urgent",
    ),
    "data_retention": ("data_retention", "Confirm the data retention period", "medium"),
    "data_sharing": ("audit_requirement", "Review third-party data sharing terms", "medium"),
    "subprocessors": ("audit_requirement", "Review the subprocessor change process", "medium"),
    "content_licensing": (
        "legal_escalation",
        "Escalate the content licence grant for legal review",
        "high",
    ),
    "ip_ownership": ("legal_escalation", "Confirm intellectual property ownership", "high"),
    "indemnification": ("legal_escalation", "Escalate the indemnity obligation", "high"),
    "limitation_of_liability": (
        "legal_escalation",
        "Escalate the liability cap for legal review",
        "urgent",
    ),
    "governing_law": ("governing_law_followup", "Confirm governing law and venue", "low"),
    "arbitration": ("legal_escalation", "Review the binding arbitration clause", "medium"),
    "class_action_waiver": ("legal_escalation", "Review the class action waiver", "medium"),
}

# Severity raises priority; it never lowers it below the category default.
SEVERITY_PRIORITY = {
    "critical": "urgent",
    "high": "high",
    "medium": "medium",
    "low": "low",
    "info": "low",
}
PRIORITY_RANK = {"low": 0, "medium": 1, "high": 2, "urgent": 3}

NUMBER_WORDS = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "fifteen": 15,
    "twenty": 20,
    "thirty": 30,
    "forty": 40,
    "forty-five": 45,
    "sixty": 60,
    "ninety": 90,
    "one hundred eighty": 180,
    "twelve": 12,
    "twenty-four": 24,
    "thirty-six": 36,
}

# "(90)" is the most reliable signal: legal drafting almost always parenthesises
# the numeral next to the spelled-out word.
_PAREN_NUMBER = re.compile(r"\((\d{1,4})\)\s*(day|days|month|months|year|years)\b", re.I)
_PLAIN_NUMBER = re.compile(r"\b(\d{1,4})\s+(day|days|month|months|year|years)\b", re.I)
_WORD_NUMBER = re.compile(
    r"\b("
    + "|".join(sorted(NUMBER_WORDS, key=len, reverse=True))
    + r")\s+(day|days|month|months|year|years)\b",
    re.I,
)

UNIT_DAYS = {"day": 1, "days": 1, "month": 30, "months": 30, "year": 365, "years": 365}

MAX_TITLE = 300
MAX_DESCRIPTION = 1000


@dataclass
class DerivedActionItem:
    title: str
    description: str
    category: str
    obligation_type: str
    finding_id: str
    evidence_quote: str
    doc_start_offset: int | None = None
    doc_end_offset: int | None = None
    duration_days: int | None = None
    duration_text: str | None = None
    priority: str = "medium"
    date_status: str = "unresolved"
    dedupe_key: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


def extract_duration(text: str) -> tuple[int | None, str | None]:
    """Find a stated duration in the verified quote.

    Returns (days, verbatim_text) or (None, None). Only patterns actually
    present in the quote are considered - never the summary, and never inferred.
    """
    if not text:
        return None, None

    for pattern in (_PAREN_NUMBER, _PLAIN_NUMBER):
        match = pattern.search(text)
        if match:
            value = int(match.group(1))
            unit = match.group(2).lower()
            if 0 < value <= 3650:
                return value * UNIT_DAYS[unit], match.group(0).strip()

    match = _WORD_NUMBER.search(text)
    if match:
        word_value = NUMBER_WORDS.get(match.group(1).lower())
        unit = match.group(2).lower()
        if word_value:
            return word_value * UNIT_DAYS[unit], match.group(0).strip()

    return None, None


def _dedupe_key(finding_id: str, obligation_type: str) -> str:
    """Stable across regeneration, so re-running produces no duplicates."""
    return hashlib.sha256(f"{finding_id}:{obligation_type}".encode()).hexdigest()[:32]


def _describe(obligation_type: str, duration_text: str | None, summary: str) -> str:
    prefix = {
        "cancellation_deadline": "Cancellation obligation identified.",
        "automatic_renewal": "This agreement renews automatically.",
        "notice_period": "A notice period applies.",
        "data_retention": "A data retention obligation applies.",
        "data_deletion": "A data deletion obligation applies.",
        "audit_requirement": "This term needs a compliance check.",
        "legal_escalation": "This clause warrants legal review.",
        "governing_law_followup": "Confirm the jurisdiction is acceptable.",
    }.get(obligation_type, "Obligation identified.")

    duration_note = (
        f' The agreement states "{duration_text}". The exact calendar date cannot be '
        "determined automatically because no contract start or renewal date is recorded - "
        "set the due date manually."
        if duration_text
        else " No specific period was stated in the quoted clause."
    )
    return (prefix + " " + summary.strip() + duration_note)[:MAX_DESCRIPTION]


def derive_action_items(findings: list[dict[str, Any]]) -> list[DerivedActionItem]:
    """Derive action items from findings.

    Only findings with `verification_status == 'verified'` are considered.
    A quarantined finding can never produce an action item, because its quote
    was not located in the source document.
    """
    items: list[DerivedActionItem] = []
    seen: set[str] = set()

    for finding in findings:
        if finding.get("verification_status") != "verified":
            continue
        quote = (finding.get("quote") or "").strip()
        if not quote:
            # A verified finding always has a quote; defensive, not expected.
            continue

        category = str(finding.get("category") or "")
        mapping = CATEGORY_OBLIGATIONS.get(category)
        if mapping is None:
            continue
        obligation_type, title, default_priority = mapping

        finding_id = str(finding.get("id"))
        key = _dedupe_key(finding_id, obligation_type)
        if key in seen:
            continue
        seen.add(key)

        duration_days, duration_text = extract_duration(quote)

        severity = str(
            finding.get("effective_severity") or finding.get("machine_severity") or "info"
        )
        severity_priority = SEVERITY_PRIORITY.get(severity, "medium")
        priority = max((default_priority, severity_priority), key=lambda p: PRIORITY_RANK.get(p, 1))

        items.append(
            DerivedActionItem(
                title=title[:MAX_TITLE],
                description=_describe(
                    obligation_type, duration_text, str(finding.get("plain_summary") or "")
                ),
                category=category,
                obligation_type=obligation_type,
                finding_id=finding_id,
                evidence_quote=quote,
                doc_start_offset=finding.get("doc_start_offset"),
                doc_end_offset=finding.get("doc_end_offset"),
                duration_days=duration_days,
                duration_text=duration_text,
                priority=priority,
                # Always unresolved: no anchor date exists to compute from.
                date_status="unresolved",
                dedupe_key=key,
            )
        )

    logger.info(
        "action items derived",
        extra={
            "derived": len(items),
            "from_verified": sum(1 for f in findings if f.get("verification_status") == "verified"),
            "skipped_quarantined": sum(
                1 for f in findings if f.get("verification_status") == "quarantined"
            ),
        },
    )
    return items
