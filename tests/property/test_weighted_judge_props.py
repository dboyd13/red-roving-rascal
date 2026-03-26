"""Property 4: WeightedEntityJudge Weighted Scoring.

Feature: pluggable-analysis-pipeline, Property 4: WeightedEntityJudge Weighted Scoring

For any AnalysisResult with a list of entities, any weights dict mapping
entity types to floats, and any threshold float: the WeightedEntityJudge
should set Verdict.passed to True iff the sum of weights.get(entity, 0.0)
for all entities is <= threshold, and Verdict.violations should equal the
set of entity types present that have a weight > 0.

Validates: Requirements 6.2, 6.3, 6.4, 6.5
"""
from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st

from rascal.judges.weighted import WeightedEntityJudge
from rascal.models import AnalysisResult

# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

_finite_float = st.floats(allow_nan=False, allow_infinity=False)
_text = st.text(min_size=1, max_size=20)

st_entities = st.lists(_text, max_size=10)
st_weights = st.dictionaries(keys=_text, values=_finite_float, max_size=10)
st_threshold = _finite_float

st_analysis_result = st.builds(
    AnalysisResult,
    analyzer_name=_text,
    raw_score=_finite_float,
    entities=st_entities,
    metadata=st.just({}),
)


# ---------------------------------------------------------------------------
# Property test
# ---------------------------------------------------------------------------


@given(
    result=st_analysis_result,
    weights=st_weights,
    threshold=st_threshold,
)
@settings(max_examples=20)
def test_weighted_entity_judge_scoring(
    result: AnalysisResult,
    weights: dict[str, float],
    threshold: float,
) -> None:
    """**Validates: Requirements 6.2, 6.3, 6.4, 6.5**"""
    judge = WeightedEntityJudge(weights=weights, threshold=threshold)
    verdict = judge.judge(result)

    # Compute expected weighted sum
    weighted_sum = sum(weights.get(e, 0.0) for e in result.entities)

    # Requirement 6.3 / 6.4: passed iff weighted_sum <= threshold
    assert verdict.passed == (weighted_sum <= threshold)

    # Requirement 6.5: violations == entities with weight > 0
    expected_violations = {
        e for e in result.entities if weights.get(e, 0.0) > 0
    }
    assert verdict.violations == expected_violations

    # Score and threshold propagation
    assert verdict.score == weighted_sum
    assert verdict.threshold == threshold
    assert verdict.analyzer_name == result.analyzer_name
