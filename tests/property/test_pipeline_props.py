"""Pipeline End-to-End Orchestration."""
from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st

from rascal.models import (
    AnalysisResult,
    InputOutputPair,
    PerAnalyzerResult,
    ScoringConfig,
    ScoringResult,
    Verdict,
)
from rascal.pipeline import Pipeline
from rascal.registry import Registry


# ---------------------------------------------------------------------------
# Deterministic stubs
# ---------------------------------------------------------------------------


class StubAnalyzer:
    """Analyzer that returns a deterministic AnalysisResult."""

    def __init__(self, name: str) -> None:
        self._name = name

    def analyze(self, input_text: str, output_text: str) -> AnalysisResult:
        return AnalysisResult(
            analyzer_name=self._name,
            raw_score=0.5,
            entities=[],
            metadata={},
        )


class StubJudge:
    """Judge that returns a deterministic passing Verdict."""

    def __init__(self, name: str) -> None:
        self._name = name

    def judge(self, result: AnalysisResult) -> Verdict:
        return Verdict(
            passed=True,
            score=result.raw_score,
            threshold=0.5,
            analyzer_name=result.analyzer_name,
            violations=set(),
        )


class CapturingScorer:
    """Scorer that captures the verdicts it receives for assertion."""

    def __init__(self) -> None:
        self.captured_verdicts: list[Verdict] = []

    def score(self, verdicts: list[Verdict], config: ScoringConfig) -> ScoringResult:
        self.captured_verdicts = list(verdicts)
        return ScoringResult(
            passed=True,
            per_analyzer={},
            description="captured",
        )


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

_analyzer_name_pool = ["alpha", "beta", "gamma"]

st_analyzer_names = st.lists(
    st.sampled_from(_analyzer_name_pool),
    min_size=1,
    max_size=3,
    unique=True,
)

st_input_output_pair = st.builds(
    InputOutputPair,
    input_text=st.text(min_size=1, max_size=20),
    output_text=st.text(min_size=1, max_size=20),
)

st_pairs = st.lists(st_input_output_pair, min_size=1, max_size=5)

st_scoring_config = st.builds(
    ScoringConfig,
    thresholds=st.dictionaries(
        keys=st.sampled_from(_analyzer_name_pool),
        values=st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False),
        max_size=3,
    ),
)


# ---------------------------------------------------------------------------
# Property test
# ---------------------------------------------------------------------------


@given(
    analyzer_names=st_analyzer_names,
    pairs=st_pairs,
    config=st_scoring_config,
)
@settings(max_examples=20)
def test_pipeline_end_to_end_orchestration(
    analyzer_names: list[str],
    pairs: list[InputOutputPair],
    config: ScoringConfig,
) -> None:
    
    # Clear registry for isolation
    Registry.clear()

    # Register stub analyzers and paired judges
    for name in analyzer_names:
        Registry.register(f"analyzer.{name}", StubAnalyzer(name))
        Registry.register(f"judge.{name}", StubJudge(name))

    # Register capturing scorer
    capturing_scorer = CapturingScorer()
    Registry.register("scorer", capturing_scorer)

    # Run pipeline
    pipeline = Pipeline()
    result = pipeline.run(pairs, config)

    # Requirement 9.1 / 1.5: every analyzer runs on every pair
    assert len(capturing_scorer.captured_verdicts) == len(pairs) * len(analyzer_names)

    # Requirement 9.2 / 9.3: each verdict's analyzer_name matches a registered name
    registered_names = set(analyzer_names)
    for verdict in capturing_scorer.captured_verdicts:
        assert verdict.analyzer_name in registered_names

    # Requirement 9.4: Pipeline returns a ScoringResult
    assert isinstance(result, ScoringResult)

    # Clean up
    Registry.clear()
