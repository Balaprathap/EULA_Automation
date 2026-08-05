"""ClauseGuard PDF report generation.

Builds a professional report from *real* analysis data only. Nothing here
invents content: every value comes from the analysis, findings, evidence and
policy rows the pipeline already persisted.

Two invariants are load-bearing and mirrored from the rest of the system:

  * Quarantined findings are rendered in a clearly separated section, labelled
    unsupported, and stated to be excluded from the score. They are never shown
    alongside confirmed findings.
  * Severity is presented as the deterministic output it is, with the inputs
    that produced it, so a reader can audit the number.

All user-controlled text is escaped before it reaches the PDF, because ReportLab
paragraphs accept a subset of HTML markup.
"""

from __future__ import annotations

import hashlib
import html
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    HRFlowable,
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from app.core.logging import get_logger

logger = get_logger(__name__)

SEVERITY_ORDER = ["critical", "high", "medium", "low", "info"]

SEVERITY_COLOR = {
    "critical": colors.HexColor("#dc2626"),
    "high": colors.HexColor("#ea580c"),
    "medium": colors.HexColor("#ca8a04"),
    "low": colors.HexColor("#0891b2"),
    "info": colors.HexColor("#64748b"),
}

DISCLAIMER = (
    "NOT LEGAL ADVICE. ClauseGuard highlights clauses that may be relevant to compliance "
    "review. It is an aid to human judgement, not a substitute for a qualified lawyer. "
    "Always confirm findings against the source agreement."
)

MAX_QUOTE_CHARS = 1200


def esc(value: Any) -> str:
    """Escape user-controlled text for ReportLab's mini-HTML paragraph parser."""
    if value is None:
        return ""
    return html.escape(str(value), quote=False)


@dataclass
class ReportContext:
    """Everything the report needs. Assembled by the caller from real rows."""

    analysis: dict[str, Any]
    document: dict[str, Any]
    policy: dict[str, Any] | None
    findings: list[dict[str, Any]] = field(default_factory=list)
    categories: list[dict[str, Any]] = field(default_factory=list)


class ReportGenerator:
    """Renders a ReportContext into PDF bytes."""

    def __init__(self, page_size=LETTER) -> None:
        self.page_size = page_size
        self._styles = self._build_styles()

    @staticmethod
    def _build_styles() -> dict[str, ParagraphStyle]:
        base = getSampleStyleSheet()
        return {
            "title": ParagraphStyle(
                "cgTitle", parent=base["Title"], fontSize=22, spaceAfter=4, alignment=TA_LEFT
            ),
            "subtitle": ParagraphStyle(
                "cgSubtitle",
                parent=base["Normal"],
                fontSize=10,
                textColor=colors.HexColor("#64748b"),
                spaceAfter=12,
            ),
            "h2": ParagraphStyle(
                "cgH2", parent=base["Heading2"], fontSize=13, spaceBefore=14, spaceAfter=6
            ),
            "h3": ParagraphStyle(
                "cgH3", parent=base["Heading3"], fontSize=11, spaceBefore=10, spaceAfter=4
            ),
            "body": ParagraphStyle("cgBody", parent=base["Normal"], fontSize=9.5, leading=13),
            "small": ParagraphStyle(
                "cgSmall",
                parent=base["Normal"],
                fontSize=8,
                leading=11,
                textColor=colors.HexColor("#475569"),
            ),
            "quote": ParagraphStyle(
                "cgQuote",
                parent=base["Normal"],
                fontName="Courier",
                fontSize=8,
                leading=11,
                leftIndent=10,
                borderPadding=4,
                backColor=colors.HexColor("#f1f5f9"),
            ),
            "warn": ParagraphStyle(
                "cgWarn",
                parent=base["Normal"],
                fontSize=9,
                leading=12,
                textColor=colors.HexColor("#92400e"),
                backColor=colors.HexColor("#fef3c7"),
                borderPadding=6,
                spaceBefore=6,
                spaceAfter=6,
            ),
            "disclaimer": ParagraphStyle(
                "cgDisclaimer",
                parent=base["Normal"],
                fontSize=8,
                leading=11,
                textColor=colors.HexColor("#334155"),
                backColor=colors.HexColor("#f8fafc"),
                borderPadding=6,
            ),
        }

    # -- helpers ----------------------------------------------------------
    def _kv_table(self, rows: list[tuple[str, Any]]) -> Table:
        data = [
            [
                Paragraph(f"<b>{esc(k)}</b>", self._styles["small"]),
                Paragraph(esc(v), self._styles["small"]),
            ]
            for k, v in rows
        ]
        table = Table(data, colWidths=[1.7 * inch, 4.6 * inch])
        table.setStyle(
            TableStyle(
                [
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                    ("TOPPADDING", (0, 0), (-1, -1), 4),
                    ("LINEBELOW", (0, 0), (-1, -2), 0.25, colors.HexColor("#e2e8f0")),
                ]
            )
        )
        return table

    def _finding_block(self, finding: dict[str, Any], *, quarantined: bool = False) -> KeepTogether:
        effective = str(
            finding.get("effective_severity") or finding.get("machine_severity") or "info"
        )
        machine = str(finding.get("machine_severity") or "info")
        colour = SEVERITY_COLOR.get(effective, SEVERITY_COLOR["info"])

        heading = Paragraph(
            f'<font color="{colour.hexval()}"><b>[{esc(effective.upper())}]</b></font> '
            f"<b>{esc(str(finding.get('category', '')).replace('_', ' ').title())}</b>",
            self._styles["h3"],
        )

        confidence = float(finding.get("model_confidence") or 0)
        weight = float(finding.get("severity_weight") or 0)

        meta_rows: list[tuple[str, str]] = [
            ("Machine severity", machine),
            ("Effective severity", effective),
            ("Confidence", f"{confidence:.2f}"),
            ("Policy weight", f"{weight:.2f}"),
            ("Evidence", str(finding.get("verification_status") or "unknown")),
        ]
        if finding.get("override_severity"):
            meta_rows.append(("Human override", str(finding["override_severity"])))
        if finding.get("review_status") and finding["review_status"] != "pending":
            meta_rows.append(("Review status", str(finding["review_status"])))

        parts: list[Any] = [heading, self._kv_table(meta_rows), Spacer(1, 4)]

        if finding.get("plain_summary"):
            parts.append(Paragraph(esc(finding["plain_summary"]), self._styles["body"]))
        if finding.get("why_it_matters"):
            parts.append(
                Paragraph(
                    f"<b>Why it matters:</b> {esc(finding['why_it_matters'])}",
                    self._styles["body"],
                )
            )

        if quarantined:
            parts.append(
                Paragraph(
                    "<b>Unsupported evidence.</b> The quoted text could not be located in the "
                    "source document, so this item is NOT a confirmed finding and is excluded "
                    "from the risk score. " + esc(finding.get("quarantine_reason") or ""),
                    self._styles["warn"],
                )
            )
        elif finding.get("quote"):
            quote = str(finding["quote"])[:MAX_QUOTE_CHARS]
            parts.append(Spacer(1, 3))
            parts.append(Paragraph(esc(quote), self._styles["quote"]))
            if finding.get("verification_method"):
                parts.append(
                    Paragraph(
                        f"Verified in the source document ({esc(finding['verification_method'])}), "
                        f"characters {esc(finding.get('doc_start_offset'))}"
                        f"-{esc(finding.get('doc_end_offset'))}.",
                        self._styles["small"],
                    )
                )

        if finding.get("scoring_explanation"):
            parts.append(
                Paragraph(
                    f"<b>Severity calculation:</b> {esc(finding['scoring_explanation'])}",
                    self._styles["small"],
                )
            )

        parts.append(Spacer(1, 10))
        return KeepTogether(parts)

    # -- main entry point --------------------------------------------------
    def generate(self, context: ReportContext) -> bytes:
        import io

        analysis = context.analysis
        document = context.document
        policy = context.policy or {}
        generated_at = datetime.now(timezone.utc)

        buffer = io.BytesIO()
        doc = SimpleDocTemplate(
            buffer,
            pagesize=self.page_size,
            leftMargin=0.9 * inch,
            rightMargin=0.9 * inch,
            topMargin=0.8 * inch,
            bottomMargin=0.8 * inch,
            title=f"ClauseGuard report - {document.get('title', 'Agreement')}",
            author="ClauseGuard",
        )

        story: list[Any] = []
        S = self._styles

        # --- header -------------------------------------------------------
        story.append(Paragraph("ClauseGuard", S["title"]))
        story.append(
            Paragraph(
                f"Automated EULA Compliance Extraction &middot; report generated "
                f"{esc(generated_at.strftime('%d %B %Y, %H:%M UTC'))}",
                S["subtitle"],
            )
        )
        story.append(HRFlowable(width="100%", color=colors.HexColor("#cbd5e1")))
        story.append(Spacer(1, 10))

        status = str(analysis.get("status") or "unknown")
        score = analysis.get("overall_score")
        band = analysis.get("risk_band")

        story.append(Paragraph("Analysis summary", S["h2"]))
        story.append(
            self._kv_table(
                [
                    ("Document", document.get("title") or "Untitled"),
                    ("Vendor", document.get("vendor_name") or "Not specified"),
                    ("Analysis ID", analysis.get("id")),
                    (
                        "Policy",
                        f"{policy.get('name', 'Unknown policy')} (version {policy.get('version', '-')})",
                    ),
                    ("Status", status),
                    ("Overall risk score", f"{score} / 100" if score is not None else "Not scored"),
                    ("Risk band", band or "Not scored"),
                    ("Verified findings", analysis.get("finding_count", 0)),
                    ("Needs review", analysis.get("review_count", 0)),
                    ("Quarantined", analysis.get("quarantine_count", 0)),
                    (
                        "Verification pass rate",
                        f"{analysis.get('verification_pass_rate')}%"
                        if analysis.get("verification_pass_rate") is not None
                        else "Not measured",
                    ),
                ]
            )
        )

        # --- warnings -----------------------------------------------------
        if status == "partial":
            story.append(
                Paragraph(
                    "<b>Partial analysis.</b> Some policy categories could not be completed and "
                    "are marked as needing review. The findings below are real and verified, but "
                    "this is not a complete pass over the agreement.",
                    S["warn"],
                )
            )
        if analysis.get("degraded_retrieval"):
            story.append(
                Paragraph(
                    "<b>Degraded retrieval.</b> "
                    + esc(
                        analysis.get("degraded_reason")
                        or "A retrieval fallback was used, so some clauses may have been missed."
                    )
                    + " Confidence was capped for anything derived from degraded retrieval.",
                    S["warn"],
                )
            )

        if analysis.get("executive_summary"):
            story.append(Paragraph("Executive summary", S["h2"]))
            story.append(Paragraph(esc(analysis["executive_summary"]), S["body"]))
            story.append(
                Paragraph(
                    "Written only from findings that were already verified and scored.", S["small"]
                )
            )

        # --- split the findings -------------------------------------------
        verified = [f for f in context.findings if f.get("verification_status") == "verified"]
        needs_review = [
            f for f in context.findings if f.get("verification_status") == "needs_review"
        ]
        quarantined = [f for f in context.findings if f.get("verification_status") == "quarantined"]

        story.append(PageBreak())
        story.append(Paragraph("Verified findings", S["h2"]))
        if not verified:
            story.append(
                Paragraph(
                    "No verified findings were produced for this agreement under this policy. "
                    "That is a real result, not an error.",
                    S["body"],
                )
            )
        else:
            by_severity: dict[str, list[dict[str, Any]]] = {s: [] for s in SEVERITY_ORDER}
            for finding in verified:
                key = str(finding.get("effective_severity") or "info")
                by_severity.setdefault(key, []).append(finding)

            for severity in SEVERITY_ORDER:
                group = by_severity.get(severity) or []
                if not group:
                    continue
                colour = SEVERITY_COLOR[severity]
                story.append(
                    Paragraph(
                        f'<font color="{colour.hexval()}"><b>{severity.upper()}</b></font> '
                        f"&mdash; {len(group)} finding(s)",
                        S["h3"],
                    )
                )
                for finding in group:
                    story.append(self._finding_block(finding))

        # --- needs review ---------------------------------------------------
        if needs_review or context.categories:
            story.append(Paragraph("Needs human review", S["h2"]))
            incomplete = [
                c for c in context.categories if c.get("status") in ("needs_review", "failed")
            ]
            if incomplete:
                story.append(
                    Paragraph(
                        "These policy categories could not be completed automatically:", S["body"]
                    )
                )
                story.append(
                    self._kv_table(
                        [
                            (
                                str(c.get("category")),
                                str(c.get("needs_review_reason") or c.get("status")),
                            )
                            for c in incomplete
                        ]
                    )
                )
            for finding in needs_review:
                story.append(self._finding_block(finding))
            if not incomplete and not needs_review:
                story.append(Paragraph("Nothing requires human review.", S["body"]))

        # --- quarantined ----------------------------------------------------
        story.append(Paragraph("Quarantined - unsupported evidence", S["h2"]))
        if not quarantined:
            story.append(
                Paragraph(
                    "No proposed findings were quarantined. Every quote was located in the "
                    "source document.",
                    S["body"],
                )
            )
        else:
            story.append(
                Paragraph(
                    "The items below were proposed but their quoted text could NOT be located in "
                    "the source document. They are <b>not confirmed findings</b>, are excluded "
                    "from the risk score, and are listed only for transparency.",
                    S["warn"],
                )
            )
            for finding in quarantined:
                story.append(self._finding_block(finding, quarantined=True))

        # --- usage ----------------------------------------------------------
        input_tokens = analysis.get("input_tokens")
        if input_tokens is not None:
            story.append(Paragraph("Processing detail", S["h2"]))
            story.append(
                self._kv_table(
                    [
                        ("Model", analysis.get("model_used") or "Not recorded"),
                        ("Input tokens", f"{int(input_tokens):,}"),
                        (
                            "Cached input tokens",
                            f"{int(analysis.get('cached_input_tokens') or 0):,}",
                        ),
                        ("Output tokens", f"{int(analysis.get('output_tokens') or 0):,}"),
                        (
                            "Estimated cost",
                            f"${float(analysis.get('estimated_cost_usd') or 0):.4f}",
                        ),
                    ]
                )
            )
            story.append(
                Paragraph(
                    "Cost is estimated from provider-reported token counts and the rates in this "
                    "deployment's configuration.",
                    S["small"],
                )
            )

        # --- footer ---------------------------------------------------------
        story.append(Spacer(1, 14))
        story.append(HRFlowable(width="100%", color=colors.HexColor("#cbd5e1")))
        story.append(Spacer(1, 8))
        story.append(Paragraph(DISCLAIMER, S["disclaimer"]))
        story.append(Spacer(1, 6))
        story.append(
            Paragraph(
                f"Generated {esc(generated_at.isoformat(timespec='seconds'))} &middot; "
                f"ClauseGuard analysis {esc(analysis.get('id'))}",
                S["small"],
            )
        )

        doc.build(story)
        return buffer.getvalue()


def checksum_of(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
