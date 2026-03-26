"""Unit tests for ThresholdJudge."""
from __future__ import annotations

from rascal.judges.threshold import ThresholdJudge
from rascal.models import AnalysisResult, ThresholdDirection


def _result(score: float, name: str = "test") -> AnalysisResult:
    """Helper to build an AnalysisResult with a given raw_score."""
    return AnalysisResult(analyzer_name=name, raw_score=score)


# --- Default parameters (Req 3.4) ---


class TestDefaults:
    def test_default_threshold(self):
        judge = ThresholdJudge()
        assert judge.threshold == 0.5

    def test_default_direction(self):
        judge = ThresholdJudge()
        assert judge.direction is ThresholdDirection.BELOW


# --- ABOVE direction (Req 3.2) ---


class TestAboveDirection:
    def test_score_above_threshold_passes(self):
        judge = ThresholdJudge(threshold=0.5, direction=ThresholdDirection.ABOVE)
        verdict = judge.judge(_result(0.8))
        assert verdict.passed is True

    def test_score_exactly_at_threshold_passes(self):
        judge = ThresholdJudge(threshold=0.5, direction=ThresholdDirection.ABOVE)
        verdict = judge.judge(_result(0.5))
        assert verdict.passed is True

    def test_score_below_threshold_fails(self):
        judge = ThresholdJudge(threshold=0.5, direction=ThresholdDirection.ABOVE)
        verdict = judge.judge(_result(0.2))
        assert verdict.passed is False


# --- BELOW direction (Req 3.3) ---


class TestBelowDirection:
    def test_score_below_threshold_passes(self):
        judge = ThresholdJudge(threshold=0.5, direction=ThresholdDirection.BELOW)
        verdict = judge.judge(_result(0.2))
        assert verdict.passed is True

    def test_score_exactly_at_threshold_passes(self):
        judge = ThresholdJudge(threshold=0.5, direction=ThresholdDirection.BELOW)
        verdict = judge.judge(_result(0.5))
        assert verdict.passed is True

    def test_score_above_threshold_fails(self):
        judge = ThresholdJudge(threshold=0.5, direction=ThresholdDirection.BELOW)
        verdict = judge.judge(_result(0.8))
        assert verdict.passed is False


# --- Verdict field mapping ---


class TestVerdictFields:
    def test_score_matches_raw_score(self):
        judge = ThresholdJudge(threshold=0.7, direction=ThresholdDirection.ABOVE)
        verdict = judge.judge(_result(0.9))
        assert verdict.score == 0.9

    def test_threshold_matches_judge_threshold(self):
        judge = ThresholdJudge(threshold=0.7, direction=ThresholdDirection.ABOVE)
        verdict = judge.judge(_result(0.9))
        assert verdict.threshold == 0.7

    def test_analyzer_name_propagated(self):
        judge = ThresholdJudge(threshold=0.5, direction=ThresholdDirection.BELOW)
        verdict = judge.judge(_result(0.3, name="comprehend"))
        assert verdict.analyzer_name == "comprehend"

    def test_violations_default_empty(self):
        judge = ThresholdJudge()
        verdict = judge.judge(_result(0.1))
        assert verdict.violations == set()
