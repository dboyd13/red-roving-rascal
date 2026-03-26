"""Property 3: ThresholdJudge Direction Consistency.

Feature: pluggable-analysis-pipeline, Property 3: ThresholdJudge Direction Consistency

For any AnalysisResult with a raw_score, any threshold float, and any
ThresholdDirection:
- ABOVE → passed == (raw_score >= threshold)
- BELOW → passed == (raw_score <= threshold)

Verdict fields score, threshold, and analyzer_name are propagated correctly.

Validates: Requirements 3.2, 3.3
"""
from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st

from rascal.judges.threshold import ThresholdJudge
from rascal.models import AnalysisResult, ThresholdDirection

# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

_finite_float = st.floats(allow_nan=False, allow_infinity=False)
_text = st.text(min_size=1, max_size=50)
_entity_list = st.lists(_text, max_size=5)
_metadata = st.fixed_dictionaries({}, optional={"a": _text, "b": _text})

st_analysis_result = st.builds(
    AnalysisResult,
    analyzer_name=_text,
    raw_score=_finite_float,
    entities=_entity_list,
    metadata=_metadata,
)

st_threshold = _finite_float
st_direction = st.sampled_from(ThresholdDirection)


# ---------------------------------------------------------------------------
# Property test
# ---------------------------------------------------------------------------


@given(result=st_analysis_result, threshold=st_threshold, direction=st_direction)
@settings(max_examples=20)
def test_threshold_judge_direction_consistency(
    result: AnalysisResult,
    threshold: float,
    direction: ThresholdDirection,
) -> None:
    """**Validates: Requirements 3.2, 3.3**"""
    judge = ThresholdJudge(threshold=threshold, direction=direction)
    verdict = judge.judge(result)

    # Direction consistency
    if direction is ThresholdDirection.ABOVE:
        assert verdict.passed == (result.raw_score >= threshold)
    else:
        assert verdict.passed == (result.raw_score <= threshold)

    # Field propagation
    assert verdict.score == result.raw_score
    assert verdict.threshold == threshold
    assert verdict.analyzer_name == result.analyzer_name
