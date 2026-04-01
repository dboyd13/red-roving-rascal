"""Unit tests for Pipeline orchestration."""
from __future__ import annotations

import pytest

from rascal.models import (
    AnalysisResult,
    InputOutputPair,
    ScoringConfig,
    ScoringResult,
    Verdict,
)
from rascal.pipeline import Pipeline
from rascal.registry import Registry
from rascal.scorers.pass_rate import PassRateScorer


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clear_registry():
    """Clear the Registry before and after each test."""
    Registry.clear()
    yield
    Registry.clear()


# ---------------------------------------------------------------------------
# Stub components
# ---------------------------------------------------------------------------


class StubAnalyzer:
    """Analyzer returning a deterministic AnalysisResult."""

    def __init__(self, name: str, score: float = 0.5) -> None:
        self._name = name
        self._score = score

    def analyze(self, input_text: str, output_text: str) -> AnalysisResult:
        return AnalysisResult(
            analyzer_name=self._name,
            raw_score=self._score,
        )


class StubJudge:
    """Judge returning a deterministic passing Verdict."""

    def __init__(self, name: str) -> None:
        self._name = name

    def judge(self, result: AnalysisResult) -> Verdict:
        return Verdict(
            passed=True,
            score=result.raw_score,
            threshold=0.5,
            analyzer_name=result.analyzer_name,
        )


class FailingAnalyzer:
    """Analyzer that always raises an exception."""

    def __init__(self, name: str) -> None:
        self._name = name

    def analyze(self, input_text: str, output_text: str) -> AnalysisResult:
        raise RuntimeError("boom")


class CapturingScorer:
    """Scorer that records the verdicts it receives."""

    def __init__(self) -> None:
        self.captured: list[Verdict] = []

    def score(self, verdicts: list[Verdict], config: ScoringConfig) -> ScoringResult:
        self.captured = list(verdicts)
        return ScoringResult(passed=True, per_analyzer={}, description="captured")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _pair(inp: str = "hi", out: str = "hello") -> InputOutputPair:
    return InputOutputPair(input_text=inp, output_text=out)


# ---------------------------------------------------------------------------
# Tests: correct orchestration flow
# ---------------------------------------------------------------------------


class TestOrchestrationFlow:
    """Verify the full Analyzer → Judge → Scorer flow with stubs."""

    def test_single_analyzer_single_pair(self):
        Registry.register("analyzer.a", StubAnalyzer("a"))
        Registry.register("judge.a", StubJudge("a"))
        scorer = CapturingScorer()
        Registry.register("scorer", scorer)

        result = Pipeline().run([_pair()], ScoringConfig())

        assert isinstance(result, ScoringResult)
        assert len(scorer.captured) == 1
        assert scorer.captured[0].analyzer_name == "a"

    def test_multiple_analyzers_multiple_pairs(self):
        for name in ("x", "y"):
            Registry.register(f"analyzer.{name}", StubAnalyzer(name))
            Registry.register(f"judge.{name}", StubJudge(name))
        scorer = CapturingScorer()
        Registry.register("scorer", scorer)

        pairs = [_pair("a", "b"), _pair("c", "d"), _pair("e", "f")]
        Pipeline().run(pairs, ScoringConfig())

        # 2 analyzers × 3 pairs = 6 verdicts
        assert len(scorer.captured) == 6
        names = {v.analyzer_name for v in scorer.captured}
        assert names == {"x", "y"}

    def test_default_scorer_registered_when_missing(self):
        """Pipeline registers PassRateScorer as default if no scorer present."""
        Registry.register("analyzer.a", StubAnalyzer("a"))
        Registry.register("judge.a", StubJudge("a"))

        result = Pipeline().run([_pair()], ScoringConfig())

        assert isinstance(result, ScoringResult)
        assert result.passed is True


# ---------------------------------------------------------------------------
# Tests: no analyzers registered
# ---------------------------------------------------------------------------


class TestNoAnalyzers:
    def test_returns_passing_result(self):
        result = Pipeline().run([_pair()], ScoringConfig())

        assert result.passed is True
        assert result.per_analyzer == {}

    def test_description_mentions_no_analyzers(self):
        result = Pipeline().run([_pair()], ScoringConfig())

        assert "No analyzers" in result.description


# ---------------------------------------------------------------------------
# Tests: empty pairs list
# ---------------------------------------------------------------------------


class TestEmptyPairs:
    def test_returns_passing_result(self):
        Registry.register("analyzer.a", StubAnalyzer("a"))
        Registry.register("judge.a", StubJudge("a"))

        result = Pipeline().run([], ScoringConfig())

        assert result.passed is True
        assert result.per_analyzer == {}

    def test_description_mentions_no_pairs(self):
        Registry.register("analyzer.a", StubAnalyzer("a"))

        result = Pipeline().run([], ScoringConfig())

        assert "No pairs" in result.description


# ---------------------------------------------------------------------------
# Tests: missing judge for an analyzer
# ---------------------------------------------------------------------------


class TestMissingJudge:
    def test_skips_analyzer_without_judge(self):
        Registry.register("analyzer.nojudge", StubAnalyzer("nojudge"))
        scorer = CapturingScorer()
        Registry.register("scorer", scorer)

        Pipeline().run([_pair()], ScoringConfig())

        # No verdicts because the judge is missing
        assert len(scorer.captured) == 0

    def test_other_analyzers_still_run(self):
        Registry.register("analyzer.good", StubAnalyzer("good"))
        Registry.register("judge.good", StubJudge("good"))
        Registry.register("analyzer.nojudge", StubAnalyzer("nojudge"))
        scorer = CapturingScorer()
        Registry.register("scorer", scorer)

        Pipeline().run([_pair()], ScoringConfig())

        assert len(scorer.captured) == 1
        assert scorer.captured[0].analyzer_name == "good"


# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------


class TestAnalyzerException:
    def test_skips_failing_analyzer(self):
        Registry.register("analyzer.bad", FailingAnalyzer("bad"))
        Registry.register("judge.bad", StubJudge("bad"))
        scorer = CapturingScorer()
        Registry.register("scorer", scorer)

        Pipeline().run([_pair()], ScoringConfig())

        assert len(scorer.captured) == 0

    def test_other_analyzers_continue(self):
        Registry.register("analyzer.bad", FailingAnalyzer("bad"))
        Registry.register("judge.bad", StubJudge("bad"))
        Registry.register("analyzer.ok", StubAnalyzer("ok"))
        Registry.register("judge.ok", StubJudge("ok"))
        scorer = CapturingScorer()
        Registry.register("scorer", scorer)

        Pipeline().run([_pair(), _pair()], ScoringConfig())

        # Only "ok" analyzer produces verdicts: 1 analyzer × 2 pairs
        assert len(scorer.captured) == 2
        assert all(v.analyzer_name == "ok" for v in scorer.captured)

    def test_returns_valid_scoring_result(self):
        Registry.register("analyzer.bad", FailingAnalyzer("bad"))
        Registry.register("judge.bad", StubJudge("bad"))
        Registry.register("scorer", PassRateScorer())

        result = Pipeline().run([_pair()], ScoringConfig())

        assert isinstance(result, ScoringResult)
        assert result.passed is True
