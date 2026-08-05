"""Deterministic severity scoring.

The model never chooses severity. It reports *confidence* that a clause matches
a policy category; the application multiplies that confidence by the
organization's configured severity weight and maps the result onto fixed bands.

This is the structural defence against prompt injection: a document that says
"set every risk to low" can, at worst, influence a confidence value - it can
never reach the severity weights, thresholds, or band boundaries, which live in
the operator's policy configuration and are applied here in ordinary Python.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum

# Severity ladder, ascending.
SEVERITY_LEVELS = ("info", "low", "medium", "high", "critical")

# Weighted-risk cut points. weighted_risk = confidence * severity_weight, in [0, 1].
SEVERITY_BANDS = (
    (0.80, "critical"),
    (0.60, "high"),
    (0.40, "medium"),
    (0.20, "low"),
    (0.00, "info"),
)

RISK_BANDS = ((75.0, "high"), (50.0, "elevated"), (25.0, "moderate"), (0.0, "low"))

# Contribution of one finding to the 0-100 document score, by severity.
_SEVERITY_POINTS = {"info": 1.0, "low": 4.0, "medium": 10.0, "high": 20.0, "critical": 32.0}


class SeveritySource(str, Enum):
    """Provenance of the severity currently in effect for a finding."""

    DETERMINISTIC = "deterministic"
    HUMAN_OVERRIDE = "human_override"
    DEGRADED_CAP = "degraded_cap"


@dataclass(frozen=True)
class ScoredSeverity:
    machine_severity: str
    weighted_risk: float
    confidence: float
    severity_weight: float
    threshold: float
    severity_source: SeveritySource
    scoring_explanation: str
    below_threshold: bool


def clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def score_finding(
    *,
    confidence: float,
    severity_weight: float,
    threshold: float = 0.35,
    escalate: bool = False,
    degraded_retrieval: bool = False,
) -> ScoredSeverity:
    """Compute severity for one finding. Pure function - no I/O, fully testable.

    Args:
        confidence: Model-reported confidence in [0, 1] that the clause matches.
        severity_weight: Policy rule weight in [0, 1] set by the organization.
        threshold: Minimum confidence for the finding to count as confirmed.
        escalate: Policy flag that promotes the result one level.
        degraded_retrieval: True when a retrieval fallback was used; caps severity
            at "high" and records the cap, because the evidence base is weaker.
    """
    confidence = clamp(confidence)
    severity_weight = clamp(severity_weight)
    threshold = clamp(threshold)

    weighted_risk = confidence * severity_weight
    below_threshold = confidence < threshold

    severity = "info"
    for cut, level in SEVERITY_BANDS:
        if weighted_risk >= cut:
            severity = level
            break

    reasons: list[str] = [
        f"confidence {confidence:.2f} x weight {severity_weight:.2f} = {weighted_risk:.2f}",
        f"maps to {severity}",
    ]

    if below_threshold and severity != "info":
        index = max(0, SEVERITY_LEVELS.index(severity) - 1)
        severity = SEVERITY_LEVELS[index]
        reasons.append(f"confidence below threshold {threshold:.2f}, reduced to {severity}")

    if escalate and severity != "critical":
        index = min(len(SEVERITY_LEVELS) - 1, SEVERITY_LEVELS.index(severity) + 1)
        severity = SEVERITY_LEVELS[index]
        reasons.append(f"policy escalation applied, raised to {severity}")

    source = SeveritySource.DETERMINISTIC
    if degraded_retrieval and SEVERITY_LEVELS.index(severity) > SEVERITY_LEVELS.index("high"):
        severity = "high"
        source = SeveritySource.DEGRADED_CAP
        reasons.append("degraded retrieval: capped at high")

    return ScoredSeverity(
        machine_severity=severity,
        weighted_risk=round(weighted_risk, 4),
        confidence=confidence,
        severity_weight=severity_weight,
        threshold=threshold,
        severity_source=source,
        scoring_explanation="; ".join(reasons),
        below_threshold=below_threshold,
    )


def cap_confidence_for_degraded_retrieval(confidence: float, mode: str) -> float:
    """Cap confidence when the finding rests on a degraded retrieval path."""
    caps = {"hybrid": 1.0, "dense": 0.85, "keyword": 0.70, "ordinal_scan": 0.55}
    return clamp(min(clamp(confidence), caps.get(mode, 0.55)))


@dataclass(frozen=True)
class AnalysisScore:
    overall_score: float
    risk_band: str
    severity_counts: dict
    finding_count: int
    review_count: int
    quarantine_count: int
    verification_pass_rate: float
    explanation: str


def score_analysis(
    severities: Sequence[str],
    *,
    review_count: int = 0,
    quarantine_count: int = 0,
    verified_count: int | None = None,
    proposed_count: int | None = None,
) -> AnalysisScore:
    """Roll verified findings up into a 0-100 document risk score and band.

    Only verified, persisted, already-scored findings should be passed in;
    quarantined and unverified findings never contribute to the score.
    """
    counts = dict.fromkeys(SEVERITY_LEVELS, 0)
    total_points = 0.0
    for severity in severities:
        level = severity if severity in counts else "info"
        counts[level] += 1
        total_points += _SEVERITY_POINTS[level]

    # Saturating curve: many low-severity findings should not outrank one critical.
    overall = 100.0 * (1.0 - (0.5 ** (total_points / 30.0))) if total_points else 0.0
    overall = round(clamp(overall, 0.0, 100.0), 1)

    band = "low"
    for cut, name in RISK_BANDS:
        if overall >= cut:
            band = name
            break

    if proposed_count:
        pass_rate = round(100.0 * (verified_count or 0) / proposed_count, 1)
    else:
        pass_rate = 100.0 if not quarantine_count else 0.0

    return AnalysisScore(
        overall_score=overall,
        risk_band=band,
        severity_counts=counts,
        finding_count=len(severities),
        review_count=review_count,
        quarantine_count=quarantine_count,
        verification_pass_rate=pass_rate,
        explanation=(
            f"{len(severities)} verified findings contributed {total_points:.0f} risk points, "
            f"producing an overall score of {overall} ({band} risk). "
            f"{quarantine_count} finding(s) were quarantined and excluded; "
            f"{review_count} require human review."
        ),
    )


def effective_severity(machine_severity: str, override_severity: str | None) -> str:
    """A human override wins, but the machine value is never destroyed - the
    caller persists both, and the review record retains full history."""
    if override_severity and override_severity in SEVERITY_LEVELS:
        return override_severity
    return machine_severity if machine_severity in SEVERITY_LEVELS else "info"
