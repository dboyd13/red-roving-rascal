"""Unit tests for WeightedEntityJudge."""
from __future__ import annotations

from rascal.judges.weighted import WeightedEntityJudge
from rascal.models import AnalysisResult


def _result(entities: list[str], name: str = "test") -> AnalysisResult:
    """Helper to build an AnalysisResult with given entities."""
    return AnalysisResult(analyzer_name=name, raw_score=0.0, entities=entities)


# --- Constructor defaults (Req 6.1) ---


class TestDefaults:
    def test_default_weights_empty(self):
        judge = WeightedEntityJudge()
        assert judge.weights == {}

    def test_default_threshold(self):
        judge = WeightedEntityJudge()
        assert judge.threshold == 1.0

    def test_custom_weights_and_threshold(self):
        weights = {"PERSON": 0.5, "DATE": 0.3}
        judge = WeightedEntityJudge(weights=weights, threshold=2.0)
        assert judge.weights == weights
        assert judge.threshold == 2.0


# --- Weighted sum and pass/fail (Req 6.2, 6.3, 6.4) ---


class TestWeightedScoring:
    def test_sum_below_threshold_passes(self):
        judge = WeightedEntityJudge(weights={"PERSON": 0.3, "DATE": 0.2}, threshold=1.0)
        verdict = judge.judge(_result(["PERSON", "DATE"]))
        assert verdict.passed is True
        assert verdict.score == 0.5

    def test_sum_above_threshold_fails(self):
        judge = WeightedEntityJudge(weights={"PERSON": 0.8, "DATE": 0.5}, threshold=1.0)
        verdict = judge.judge(_result(["PERSON", "DATE"]))
        assert verdict.passed is False
        assert verdict.score == 1.3

    def test_sum_exactly_at_threshold_passes(self):
        judge = WeightedEntityJudge(weights={"PERSON": 0.5, "DATE": 0.5}, threshold=1.0)
        verdict = judge.judge(_result(["PERSON", "DATE"]))
        assert verdict.passed is True
        assert verdict.score == 1.0


# --- Empty entities (Req 6.2) ---


class TestEmptyEntities:
    def test_empty_entities_passes(self):
        judge = WeightedEntityJudge(weights={"PERSON": 0.5}, threshold=1.0)
        verdict = judge.judge(_result([]))
        assert verdict.passed is True
        assert verdict.score == 0.0

    def test_empty_entities_no_violations(self):
        judge = WeightedEntityJudge(weights={"PERSON": 0.5}, threshold=1.0)
        verdict = judge.judge(_result([]))
        assert verdict.violations == set()


# --- Unknown entity types default to 0.0 (Req 6.6) ---


class TestUnknownEntities:
    def test_unknown_entity_weight_defaults_to_zero(self):
        judge = WeightedEntityJudge(weights={"PERSON": 0.5}, threshold=1.0)
        verdict = judge.judge(_result(["UNKNOWN_TYPE"]))
        assert verdict.score == 0.0
        assert verdict.passed is True

    def test_mix_of_known_and_unknown_entities(self):
        judge = WeightedEntityJudge(weights={"PERSON": 0.8}, threshold=1.0)
        verdict = judge.judge(_result(["PERSON", "UNKNOWN_TYPE"]))
        assert verdict.score == 0.8
        assert verdict.passed is True


# --- Violations (Req 6.5) ---


class TestViolations:
    def test_violations_include_positive_weight_entities(self):
        judge = WeightedEntityJudge(weights={"PERSON": 0.5, "DATE": 0.3}, threshold=1.0)
        verdict = judge.judge(_result(["PERSON", "DATE"]))
        assert verdict.violations == {"PERSON", "DATE"}

    def test_violations_exclude_zero_weight_entities(self):
        judge = WeightedEntityJudge(weights={"PERSON": 0.5, "DATE": 0.0}, threshold=1.0)
        verdict = judge.judge(_result(["PERSON", "DATE"]))
        assert verdict.violations == {"PERSON"}

    def test_violations_exclude_unknown_entities(self):
        judge = WeightedEntityJudge(weights={"PERSON": 0.5}, threshold=1.0)
        verdict = judge.judge(_result(["PERSON", "UNKNOWN"]))
        assert verdict.violations == {"PERSON"}


# --- Verdict field mapping ---


class TestVerdictFields:
    def test_score_is_weighted_sum(self):
        judge = WeightedEntityJudge(weights={"A": 0.3, "B": 0.7}, threshold=2.0)
        verdict = judge.judge(_result(["A", "B"]))
        assert verdict.score == 1.0

    def test_threshold_matches_judge_threshold(self):
        judge = WeightedEntityJudge(weights={"A": 0.1}, threshold=0.5)
        verdict = judge.judge(_result(["A"]))
        assert verdict.threshold == 0.5

    def test_analyzer_name_propagated(self):
        judge = WeightedEntityJudge(weights={}, threshold=1.0)
        verdict = judge.judge(_result([], name="comprehend"))
        assert verdict.analyzer_name == "comprehend"
