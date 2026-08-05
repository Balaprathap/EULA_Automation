"""Deterministic severity scoring tests.

These assert the property the whole trust model rests on: severity is a pure
function of confidence, operator-configured weights, and thresholds - never of
anything the model or the document says.
"""

import pytest

from app.services.scoring import (
    SEVERITY_LEVELS,
    AnalysisScore,
    SeveritySource,
    cap_confidence_for_degraded_retrieval,
    effective_severity,
    score_analysis,
    score_finding,
)


class TestSeverityMapping:
    @pytest.mark.parametrize(
        "confidence,weight,expected",
        [
            (0.95, 0.95, "critical"),
            (0.90, 0.75, "high"),
            (0.85, 0.55, "medium"),
            (0.80, 0.30, "low"),
            (0.90, 0.10, "info"),
        ],
    )
    def test_weighted_risk_maps_to_expected_band(self, confidence, weight, expected):
        assert (
            score_finding(confidence=confidence, severity_weight=weight).machine_severity
            == expected
        )

    def test_is_deterministic(self):
        a = score_finding(confidence=0.73, severity_weight=0.61)
        b = score_finding(confidence=0.73, severity_weight=0.61)
        assert a == b

    def test_weighted_risk_is_the_product(self):
        result = score_finding(confidence=0.8, severity_weight=0.5)
        assert result.weighted_risk == pytest.approx(0.40)

    def test_severity_is_monotonic_in_confidence(self):
        levels = [
            SEVERITY_LEVELS.index(
                score_finding(confidence=c / 10, severity_weight=0.9).machine_severity
            )
            for c in range(11)
        ]
        assert levels == sorted(levels)

    def test_severity_is_monotonic_in_weight(self):
        levels = [
            SEVERITY_LEVELS.index(
                score_finding(confidence=0.9, severity_weight=w / 10).machine_severity
            )
            for w in range(11)
        ]
        assert levels == sorted(levels)

    def test_inputs_are_clamped(self):
        result = score_finding(confidence=5.0, severity_weight=-2.0)
        assert result.confidence == 1.0
        assert result.severity_weight == 0.0
        assert result.machine_severity == "info"

    def test_explanation_is_always_populated(self):
        assert (
            "confidence" in score_finding(confidence=0.5, severity_weight=0.5).scoring_explanation
        )


class TestThresholdAndEscalation:
    def test_below_threshold_demotes_one_level(self):
        high = score_finding(confidence=0.85, severity_weight=0.75, threshold=0.10)
        low_conf = score_finding(confidence=0.20, severity_weight=1.0, threshold=0.50)
        assert high.machine_severity == "high"
        assert low_conf.below_threshold is True
        assert SEVERITY_LEVELS.index(low_conf.machine_severity) <= SEVERITY_LEVELS.index("low")

    def test_escalation_promotes_one_level(self):
        base = score_finding(confidence=0.85, severity_weight=0.55)
        escalated = score_finding(confidence=0.85, severity_weight=0.55, escalate=True)
        assert (
            SEVERITY_LEVELS.index(escalated.machine_severity)
            == SEVERITY_LEVELS.index(base.machine_severity) + 1
        )

    def test_escalation_cannot_exceed_critical(self):
        result = score_finding(confidence=1.0, severity_weight=1.0, escalate=True)
        assert result.machine_severity == "critical"


class TestDegradedRetrieval:
    def test_degraded_retrieval_caps_severity_at_high(self):
        result = score_finding(confidence=1.0, severity_weight=1.0, degraded_retrieval=True)
        assert result.machine_severity == "high"
        assert result.severity_source is SeveritySource.DEGRADED_CAP

    def test_normal_retrieval_is_not_capped(self):
        result = score_finding(confidence=1.0, severity_weight=1.0)
        assert result.machine_severity == "critical"
        assert result.severity_source is SeveritySource.DETERMINISTIC

    @pytest.mark.parametrize(
        "mode,ceiling",
        [("hybrid", 1.0), ("dense", 0.85), ("keyword", 0.70), ("ordinal_scan", 0.55)],
    )
    def test_confidence_ceiling_per_retrieval_mode(self, mode, ceiling):
        assert cap_confidence_for_degraded_retrieval(0.99, mode) == pytest.approx(
            min(0.99, ceiling)
        )

    def test_unknown_mode_gets_the_strictest_cap(self):
        assert cap_confidence_for_degraded_retrieval(0.99, "mystery") == pytest.approx(0.55)

    def test_capping_never_raises_confidence(self):
        assert cap_confidence_for_degraded_retrieval(0.20, "hybrid") == pytest.approx(0.20)


class TestPromptInjectionResistance:
    """A document cannot reach the severity calculation. This proves the boundary."""

    def test_severity_ignores_document_supplied_text(self):
        # Whatever a document claims, only these two numbers are inputs.
        result = score_finding(confidence=0.95, severity_weight=0.95)
        assert result.machine_severity == "critical"

    def test_score_finding_accepts_no_severity_argument(self):
        import inspect

        params = set(inspect.signature(score_finding).parameters)
        assert "severity" not in params
        assert "machine_severity" not in params
        assert params == {
            "confidence",
            "severity_weight",
            "threshold",
            "escalate",
            "degraded_retrieval",
        }


class TestAnalysisScore:
    def test_no_findings_scores_zero(self):
        result = score_analysis([])
        assert isinstance(result, AnalysisScore)
        assert result.overall_score == 0.0
        assert result.risk_band == "low"

    def test_score_stays_within_bounds(self):
        assert 0.0 <= score_analysis(["critical"] * 50).overall_score <= 100.0

    def test_one_critical_outranks_many_info(self):
        assert (
            score_analysis(["critical"]).overall_score > score_analysis(["info"] * 8).overall_score
        )

    def test_severity_counts_are_tallied(self):
        result = score_analysis(["high", "high", "low", "info"])
        assert result.severity_counts["high"] == 2
        assert result.severity_counts["low"] == 1
        assert result.finding_count == 4

    def test_unknown_severity_falls_back_to_info(self):
        assert score_analysis(["banana"]).severity_counts["info"] == 1

    def test_risk_band_rises_with_score(self):
        assert score_analysis([]).risk_band == "low"
        assert score_analysis(["critical"] * 10).risk_band == "high"

    def test_verification_pass_rate_is_computed(self):
        result = score_analysis(
            ["high"] * 9, verified_count=9, proposed_count=10, quarantine_count=1
        )
        assert result.verification_pass_rate == pytest.approx(90.0)

    def test_quarantined_findings_are_excluded_from_the_score(self):
        with_quarantine = score_analysis(["high"], quarantine_count=5)
        without = score_analysis(["high"])
        assert with_quarantine.overall_score == without.overall_score
        assert with_quarantine.quarantine_count == 5


class TestEffectiveSeverity:
    def test_override_wins(self):
        assert effective_severity("low", "critical") == "critical"

    def test_machine_value_used_when_no_override(self):
        assert effective_severity("medium", None) == "medium"

    def test_invalid_override_is_ignored(self):
        assert effective_severity("medium", "catastrophic") == "medium"

    def test_invalid_machine_value_falls_back_to_info(self):
        assert effective_severity("bogus", None) == "info"
