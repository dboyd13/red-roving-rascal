"""Unit tests for pipeline data models."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from rascal.models import (
    AnalysisResult,
    EvaluateRequest,
    EvaluateResponse,
    EvaluationStatus,
    InputOutputPair,
    PerAnalyzerResult,
    ScoringConfig,
    ScoringResult,
    TestCase,
    TestSuite,
    ThresholdDirection,
    Verdict,
)


# --- ThresholdDirection enum ---


class TestThresholdDirection:
    def test_above_value(self):
        assert ThresholdDirection.ABOVE.value == "above"

    def test_below_value(self):
        assert ThresholdDirection.BELOW.value == "below"

    def test_is_str_enum(self):
        assert isinstance(ThresholdDirection.ABOVE, str)
        assert isinstance(ThresholdDirection.BELOW, str)

    def test_only_two_members(self):
        assert set(ThresholdDirection) == {ThresholdDirection.ABOVE, ThresholdDirection.BELOW}


# --- AnalysisResult ---


class TestAnalysisResult:
    def test_required_fields(self):
        r = AnalysisResult(analyzer_name="test", raw_score=0.75)
        assert r.analyzer_name == "test"
        assert r.raw_score == 0.75

    def test_defaults(self):
        r = AnalysisResult(analyzer_name="a", raw_score=0.0)
        assert r.entities == []
        assert r.metadata == {}

    def test_entities_and_metadata(self):
        r = AnalysisResult(
            analyzer_name="a",
            raw_score=1.0,
            entities=["PERSON", "DATE"],
            metadata={"source": "test"},
        )
        assert r.entities == ["PERSON", "DATE"]
        assert r.metadata == {"source": "test"}

    def test_raw_score_accepts_negative(self):
        r = AnalysisResult(analyzer_name="a", raw_score=-1.5)
        assert r.raw_score == -1.5

    def test_raw_score_accepts_int_coerced_to_float(self):
        r = AnalysisResult(analyzer_name="a", raw_score=1)
        assert isinstance(r.raw_score, float)


# --- Verdict ---


class TestVerdict:
    def test_required_fields(self):
        v = Verdict(passed=True, score=0.5, threshold=0.5, analyzer_name="a")
        assert v.passed is True
        assert v.score == 0.5
        assert v.threshold == 0.5
        assert v.analyzer_name == "a"

    def test_violations_default_empty(self):
        v = Verdict(passed=False, score=1.0, threshold=0.5, analyzer_name="a")
        assert v.violations == set()

    def test_violations_set(self):
        v = Verdict(
            passed=False,
            score=2.0,
            threshold=1.0,
            analyzer_name="a",
            violations={"PERSON", "ORG"},
        )
        assert v.violations == {"PERSON", "ORG"}

    def test_violations_deduplicates(self):
        v = Verdict(
            passed=False,
            score=1.0,
            threshold=0.5,
            analyzer_name="a",
            violations={"X", "X", "Y"},
        )
        assert v.violations == {"X", "Y"}


# --- PerAnalyzerResult ---


class TestPerAnalyzerResult:
    def test_valid_bounds(self):
        p = PerAnalyzerResult(pass_rate=0.0, threshold=0.0)
        assert p.pass_rate == 0.0
        assert p.threshold == 0.0

        p = PerAnalyzerResult(pass_rate=1.0, threshold=1.0)
        assert p.pass_rate == 1.0
        assert p.threshold == 1.0

    def test_mid_values(self):
        p = PerAnalyzerResult(pass_rate=0.75, threshold=0.5)
        assert p.pass_rate == 0.75
        assert p.threshold == 0.5

    def test_pass_rate_below_zero_rejected(self):
        with pytest.raises(ValidationError):
            PerAnalyzerResult(pass_rate=-0.1, threshold=0.5)

    def test_pass_rate_above_one_rejected(self):
        with pytest.raises(ValidationError):
            PerAnalyzerResult(pass_rate=1.1, threshold=0.5)

    def test_threshold_below_zero_rejected(self):
        with pytest.raises(ValidationError):
            PerAnalyzerResult(pass_rate=0.5, threshold=-0.01)

    def test_threshold_above_one_rejected(self):
        with pytest.raises(ValidationError):
            PerAnalyzerResult(pass_rate=0.5, threshold=1.01)


# --- ScoringConfig ---


class TestScoringConfig:
    def test_default_empty_thresholds(self):
        c = ScoringConfig()
        assert c.thresholds == {}

    def test_with_thresholds(self):
        c = ScoringConfig(thresholds={"comprehend": 0.8, "custom": 0.5})
        assert c.thresholds["comprehend"] == 0.8
        assert c.thresholds["custom"] == 0.5


# --- ScoringResult ---


class TestScoringResult:
    def test_defaults(self):
        r = ScoringResult(passed=True)
        assert r.per_analyzer == {}
        assert r.description == ""

    def test_with_per_analyzer(self):
        r = ScoringResult(
            passed=False,
            per_analyzer={"a": PerAnalyzerResult(pass_rate=0.5, threshold=0.8)},
            description="failed",
        )
        assert r.per_analyzer["a"].pass_rate == 0.5
        assert r.description == "failed"


# --- InputOutputPair ---


class TestInputOutputPair:
    def test_required_fields(self):
        p = InputOutputPair(input_text="hello", output_text="world")
        assert p.input_text == "hello"
        assert p.output_text == "world"

    def test_missing_input_text_rejected(self):
        with pytest.raises(ValidationError):
            InputOutputPair(output_text="world")  # type: ignore[call-arg]

    def test_missing_output_text_rejected(self):
        with pytest.raises(ValidationError):
            InputOutputPair(input_text="hello")  # type: ignore[call-arg]


# --- TestCase ---


class TestTestCaseModel:
    def test_required_fields(self):
        tc = TestCase(input_text="prompt")
        assert tc.input_text == "prompt"

    def test_defaults(self):
        tc = TestCase(input_text="x")
        assert tc.category == "default"
        assert tc.metadata == {}

    def test_custom_category(self):
        tc = TestCase(input_text="x", category="edge")
        assert tc.category == "edge"


# --- TestSuite ---


class TestTestSuiteModel:
    def test_required_fields(self):
        ts = TestSuite(suite_id="s1", name="Suite 1")
        assert ts.suite_id == "s1"
        assert ts.name == "Suite 1"

    def test_defaults(self):
        ts = TestSuite(suite_id="s1", name="n")
        assert ts.test_cases == []
        assert ts.metadata == {}

    def test_with_test_cases(self):
        ts = TestSuite(
            suite_id="s1",
            name="n",
            test_cases=[TestCase(input_text="a"), TestCase(input_text="b")],
        )
        assert len(ts.test_cases) == 2
        assert ts.test_cases[0].input_text == "a"


# --- EvaluateRequest ---


class TestEvaluateRequest:
    def test_with_pairs(self):
        req = EvaluateRequest(
            pairs=[InputOutputPair(input_text="i", output_text="o")]
        )
        assert len(req.pairs) == 1
        assert req.config.thresholds == {}

    def test_with_config(self):
        req = EvaluateRequest(
            pairs=[InputOutputPair(input_text="i", output_text="o")],
            config=ScoringConfig(thresholds={"a": 0.9}),
        )
        assert req.config.thresholds["a"] == 0.9

    def test_missing_pairs_rejected(self):
        with pytest.raises(ValidationError):
            EvaluateRequest()  # type: ignore[call-arg]


# --- EvaluationStatus enum ---


class TestEvaluationStatus:
    def test_pending_value(self):
        assert EvaluationStatus.PENDING.value == "pending"

    def test_running_value(self):
        assert EvaluationStatus.RUNNING.value == "running"

    def test_complete_value(self):
        assert EvaluationStatus.COMPLETE.value == "complete"

    def test_failed_value(self):
        assert EvaluationStatus.FAILED.value == "failed"

    def test_is_str_enum(self):
        for member in EvaluationStatus:
            assert isinstance(member, str)

    def test_exactly_four_members(self):
        assert set(EvaluationStatus) == {
            EvaluationStatus.PENDING,
            EvaluationStatus.RUNNING,
            EvaluationStatus.COMPLETE,
            EvaluationStatus.FAILED,
        }


# --- EvaluateResponse ---


class TestEvaluateResponse:
    def test_required_fields(self):
        resp = EvaluateResponse(
            evaluation_id="eval-1",
            status=EvaluationStatus.PENDING,
            created_at=1000.0,
        )
        assert resp.evaluation_id == "eval-1"
        assert resp.status == EvaluationStatus.PENDING
        assert resp.created_at == 1000.0

    def test_defaults_none(self):
        resp = EvaluateResponse(
            evaluation_id="eval-2",
            status=EvaluationStatus.RUNNING,
            created_at=2000.0,
        )
        assert resp.result is None
        assert resp.error is None

    def test_complete_with_result(self):
        result = ScoringResult(passed=True, description="ok")
        resp = EvaluateResponse(
            evaluation_id="eval-3",
            status=EvaluationStatus.COMPLETE,
            result=result,
            created_at=3000.0,
        )
        assert resp.result is not None
        assert resp.result.passed is True

    def test_failed_with_error(self):
        resp = EvaluateResponse(
            evaluation_id="eval-4",
            status=EvaluationStatus.FAILED,
            error="pipeline crashed",
            created_at=4000.0,
        )
        assert resp.error == "pipeline crashed"
        assert resp.result is None

    def test_missing_evaluation_id_rejected(self):
        with pytest.raises(ValidationError):
            EvaluateResponse(
                status=EvaluationStatus.PENDING,
                created_at=1000.0,
            )  # type: ignore[call-arg]

    def test_missing_status_rejected(self):
        with pytest.raises(ValidationError):
            EvaluateResponse(
                evaluation_id="eval-5",
                created_at=1000.0,
            )  # type: ignore[call-arg]

    def test_missing_created_at_rejected(self):
        with pytest.raises(ValidationError):
            EvaluateResponse(
                evaluation_id="eval-6",
                status=EvaluationStatus.PENDING,
            )  # type: ignore[call-arg]
