"""ComprehendAnalyzer Max Confidence Score."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from hypothesis import given, settings
from hypothesis import strategies as st

from rascal.analyzers.comprehend import ComprehendAnalyzer

# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

_entity_types = st.sampled_from(
    ["PERSON", "LOCATION", "ORGANIZATION", "DATE", "QUANTITY"]
)
_confidence = st.floats(min_value=0.0, max_value=1.0, allow_nan=False)

st_entity = st.tuples(_entity_types, _confidence)
st_entity_list = st.lists(st_entity, min_size=1, max_size=20)
st_empty_entity_list = st.just([])


# ---------------------------------------------------------------------------
# Property tests
# ---------------------------------------------------------------------------


@given(entities=st_entity_list)
@settings(max_examples=20)
def test_raw_score_equals_max_confidence_when_entities_exist(
    entities: list[tuple[str, float]],
) -> None:
    
    mock_response = {
        "Entities": [
            {"Type": etype, "Score": score} for etype, score in entities
        ]
    }

    with patch("rascal.analyzers.comprehend.boto3") as mock_boto3:
        mock_client = MagicMock()
        mock_boto3.client.return_value = mock_client
        mock_client.detect_entities.return_value = mock_response

        analyzer = ComprehendAnalyzer(region="us-east-1")
        result = analyzer.analyze(input_text="hello", output_text="some text")

    expected_max = max(score for _, score in entities)
    assert result.raw_score == expected_max
    assert result.analyzer_name == "comprehend"


@given(entities=st_empty_entity_list)
@settings(max_examples=20)
def test_raw_score_is_zero_when_no_entities(
    entities: list[tuple[str, float]],
) -> None:
    
    mock_response: dict = {"Entities": []}

    with patch("rascal.analyzers.comprehend.boto3") as mock_boto3:
        mock_client = MagicMock()
        mock_boto3.client.return_value = mock_client
        mock_client.detect_entities.return_value = mock_response

        analyzer = ComprehendAnalyzer(region="us-east-1")
        result = analyzer.analyze(input_text="hello", output_text="some text")

    assert result.raw_score == 0.0
    assert result.analyzer_name == "comprehend"
    assert result.entities == []
