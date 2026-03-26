"""Property 5: PassRateScorer Aggregation Correctness.

Feature: pluggable-analysis-pipeline, Property 5: PassRateScorer Aggregation Correctness

For any list of Verdict objects and any ScoringConfig, the PassRateScorer
should group verdicts by analyzer_name, compute each group's pass rate as
passed_count / total_count, use the threshold from config.thresholds or
default to 1.0 if absent, set ScoringResult.passed to True iff every
per-analyzer pass rate meets or exceeds its threshold, and produce a
non-empty description string.

Validates: Requirements 5.1, 5.2, 5.3, 5.4, 5.5, 5.6
"""
from __future__ import annotations

from collections import defaultdict

from hypothesis import given, settings
from hypothesis import strategies as st

from rascal.models import ScoringConfig, Verdict
from rascal.scorers.pass_rate import PassRateScorer

# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

_finite_float = st.floats(allow_nan=False, allow_infinity=False)
_analyzer_names = st.sampled_from(["alpha", "beta", "gamma", "delta"])

st_verdict = st.builds(
    Verdict,
    passed=st.booleans(),
    score=_finite_float,
    threshold=_finite_float,
    analyzer_name=_analyzer_names,
    violations=st.just(set()),
)

st_verdicts = st.lists(st_verdict, min_size=1, max_size=30)

st_scoring_config = st.builds(
    ScoringConfig,
    thresholds=st.dictionaries(
        keys=_analyzer_names,
        values=st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False),
        max_size=4,
    ),
)


# ---------------------------------------------------------------------------
# Property test
# ---------------------------------------------------------------------------


@given(verdicts=st_verdicts, config=st_scoring_config)
@settings(max_examples=20)
def test_pass_rate_scorer_aggregation(
    verdicts: list[Verdict],
    config: ScoringConfig,
) -> None:
    """**Validates: Requirements 5.1, 5.2, 5.3, 5.4, 5.5, 5.6**"""
    scorer = PassRateScorer()
    result = scorer.score(verdicts, config)

    # Group verdicts by analyzer_name and compute expected pass rates
    groups: dict[str, list[Verdict]] = defaultdict(list)
    for v in verdicts:
        groups[v.analyzer_name].append(v)

    # Requirement 5.1: per-analyzer pass rate == passed_count / total_count
    for analyzer_name, group in groups.items():
        passed_count = sum(1 for v in group if v.passed)
        total_count = len(group)
        expected_pass_rate = passed_count / total_count

        assert analyzer_name in result.per_analyzer
        assert result.per_analyzer[analyzer_name].pass_rate == expected_pass_rate

    # Requirement 5.5: missing thresholds default to 1.0
    for analyzer_name in groups:
        expected_threshold = config.thresholds.get(analyzer_name, 1.0)
        assert result.per_analyzer[analyzer_name].threshold == expected_threshold

    # Requirement 5.2 / 5.3 / 5.4: overall passed iff all pass rates >= thresholds
    expected_passed = all(
        result.per_analyzer[name].pass_rate >= result.per_analyzer[name].threshold
        for name in groups
    )
    assert result.passed == expected_passed

    # Requirement 5.6: description is non-empty
    assert len(result.description) > 0
