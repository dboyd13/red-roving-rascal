"""Unit tests for PassRateScorer."""
from __future__ import annotations

from rascal.models import PerAnalyzerResult, ScoringConfig, Verdict
from rascal.scorers.pass_rate import PassRateScorer


def _verdict(passed: bool, analyzer_name: str = "test") -> Verdict:
    """Helper to build a Verdict with minimal fields."""
    return Verdict(
        passed=passed,
        score=1.0 if passed else 0.0,
        threshold=0.5,
        analyzer_name=analyzer_name,
    )


# --- Known verdict sets and expected pass rates ---


class TestPassRateCalculation:
    def test_all_passed(self):
        scorer = PassRateScorer()
        verdicts = [_verdict(True, "a"), _verdict(True, "a")]
        result = scorer.score(verdicts, ScoringConfig(thresholds={"a": 1.0}))
        assert result.per_analyzer["a"].pass_rate == 1.0
        assert result.passed is True

    def test_all_failed(self):
        scorer = PassRateScorer()
        verdicts = [_verdict(False, "a"), _verdict(False, "a")]
        result = scorer.score(verdicts, ScoringConfig(thresholds={"a": 0.0}))
        assert result.per_analyzer["a"].pass_rate == 0.0
        assert result.passed is True  # 0.0 >= threshold 0.0

    def test_mixed_verdicts(self):
        scorer = PassRateScorer()
        verdicts = [_verdict(True, "a"), _verdict(False, "a")]
        result = scorer.score(verdicts, ScoringConfig(thresholds={"a": 0.5}))
        assert result.per_analyzer["a"].pass_rate == 0.5
        assert result.passed is True  # 0.5 >= 0.5

    def test_mixed_below_threshold(self):
        scorer = PassRateScorer()
        verdicts = [_verdict(True, "a"), _verdict(False, "a"), _verdict(False, "a")]
        result = scorer.score(verdicts, ScoringConfig(thresholds={"a": 0.5}))
        assert abs(result.per_analyzer["a"].pass_rate - 1 / 3) < 1e-9
        assert result.passed is False  # ~0.33 < 0.5


# --- Multiple analyzers ---


class TestMultipleAnalyzers:
    def test_all_analyzers_pass(self):
        scorer = PassRateScorer()
        verdicts = [
            _verdict(True, "a"),
            _verdict(True, "b"),
        ]
        config = ScoringConfig(thresholds={"a": 1.0, "b": 1.0})
        result = scorer.score(verdicts, config)
        assert result.passed is True
        assert result.per_analyzer["a"].pass_rate == 1.0
        assert result.per_analyzer["b"].pass_rate == 1.0

    def test_one_analyzer_fails(self):
        scorer = PassRateScorer()
        verdicts = [
            _verdict(True, "a"),
            _verdict(False, "b"),
        ]
        config = ScoringConfig(thresholds={"a": 1.0, "b": 1.0})
        result = scorer.score(verdicts, config)
        assert result.passed is False
        assert result.per_analyzer["a"].pass_rate == 1.0
        assert result.per_analyzer["b"].pass_rate == 0.0


# --- Empty verdicts list ---


class TestEmptyVerdicts:
    def test_empty_verdicts_passes(self):
        scorer = PassRateScorer()
        result = scorer.score([], ScoringConfig())
        assert result.passed is True
        assert result.per_analyzer == {}

    def test_empty_verdicts_description(self):
        scorer = PassRateScorer()
        result = scorer.score([], ScoringConfig())
        assert "No verdicts" in result.description


# --- Missing thresholds default to 1.0 ---


class TestDefaultThreshold:
    def test_missing_threshold_defaults_to_one(self):
        scorer = PassRateScorer()
        verdicts = [_verdict(True, "a")]
        result = scorer.score(verdicts, ScoringConfig(thresholds={}))
        assert result.per_analyzer["a"].threshold == 1.0

    def test_missing_threshold_all_pass_succeeds(self):
        scorer = PassRateScorer()
        verdicts = [_verdict(True, "a"), _verdict(True, "a")]
        result = scorer.score(verdicts, ScoringConfig(thresholds={}))
        assert result.passed is True

    def test_missing_threshold_partial_pass_fails(self):
        scorer = PassRateScorer()
        verdicts = [_verdict(True, "a"), _verdict(False, "a")]
        result = scorer.score(verdicts, ScoringConfig(thresholds={}))
        assert result.passed is False  # 0.5 < default 1.0


# --- Description content ---


class TestDescription:
    def test_description_contains_analyzer_name(self):
        scorer = PassRateScorer()
        verdicts = [_verdict(True, "comprehend")]
        result = scorer.score(verdicts, ScoringConfig(thresholds={"comprehend": 0.8}))
        assert "comprehend" in result.description

    def test_description_contains_pass_rate(self):
        scorer = PassRateScorer()
        verdicts = [_verdict(True, "a"), _verdict(False, "a")]
        result = scorer.score(verdicts, ScoringConfig(thresholds={"a": 0.5}))
        assert "50.0%" in result.description

    def test_description_contains_overall_result(self):
        scorer = PassRateScorer()
        verdicts = [_verdict(True, "a")]
        result = scorer.score(verdicts, ScoringConfig(thresholds={"a": 1.0}))
        assert "PASSED" in result.description

    def test_description_contains_fail_when_failing(self):
        scorer = PassRateScorer()
        verdicts = [_verdict(False, "a")]
        result = scorer.score(verdicts, ScoringConfig(thresholds={"a": 1.0}))
        assert "FAIL" in result.description
