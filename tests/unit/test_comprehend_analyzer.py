"""Unit tests for ComprehendAnalyzer.

"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from rascal.analyzers.comprehend import ComprehendAnalyzer
from rascal.models import AnalysisResult


# ---------------------------------------------------------------------------
# Tests: entity detection with known input
# ---------------------------------------------------------------------------


class TestEntityDetection:
    """Verify ComprehendAnalyzer maps Comprehend response correctly."""

    @patch("rascal.analyzers.comprehend.boto3")
    def test_returns_entities_and_max_score(self, mock_boto3: MagicMock) -> None:
        mock_client = MagicMock()
        mock_boto3.client.return_value = mock_client
        mock_client.detect_entities.return_value = {
            "Entities": [
                {"Type": "PERSON", "Score": 0.85},
                {"Type": "LOCATION", "Score": 0.92},
                {"Type": "DATE", "Score": 0.70},
            ]
        }

        analyzer = ComprehendAnalyzer(region="us-east-1")
        result = analyzer.analyze(input_text="prompt", output_text="Alice visited Paris on Monday")

        assert isinstance(result, AnalysisResult)
        assert result.analyzer_name == "comprehend"
        assert result.raw_score == 0.92
        assert result.entities == ["PERSON", "LOCATION", "DATE"]

    @patch("rascal.analyzers.comprehend.boto3")
    def test_calls_detect_entities_with_output_text(self, mock_boto3: MagicMock) -> None:
        mock_client = MagicMock()
        mock_boto3.client.return_value = mock_client
        mock_client.detect_entities.return_value = {"Entities": []}

        analyzer = ComprehendAnalyzer(region="us-east-1")
        analyzer.analyze(input_text="ignored", output_text="the actual text")

        mock_client.detect_entities.assert_called_once_with(
            Text="the actual text", LanguageCode="en"
        )


# ---------------------------------------------------------------------------
# Tests: empty entity response
# ---------------------------------------------------------------------------


class TestEmptyEntities:
    """Verify behavior when Comprehend returns no entities."""

    @patch("rascal.analyzers.comprehend.boto3")
    def test_raw_score_zero_and_empty_entities(self, mock_boto3: MagicMock) -> None:
        mock_client = MagicMock()
        mock_boto3.client.return_value = mock_client
        mock_client.detect_entities.return_value = {"Entities": []}

        analyzer = ComprehendAnalyzer(region="us-east-1")
        result = analyzer.analyze(input_text="hi", output_text="nothing special")

        assert result.raw_score == 0.0
        assert result.entities == []
        assert result.analyzer_name == "comprehend"


# ---------------------------------------------------------------------------
# Tests: API failure handling
# ---------------------------------------------------------------------------


class TestApiFailure:
    """Verify graceful error handling when Comprehend API fails."""

    @patch("rascal.analyzers.comprehend.boto3")
    def test_returns_error_in_metadata(self, mock_boto3: MagicMock) -> None:
        mock_client = MagicMock()
        mock_boto3.client.return_value = mock_client
        mock_client.detect_entities.side_effect = RuntimeError("Service unavailable")

        analyzer = ComprehendAnalyzer(region="us-east-1")
        result = analyzer.analyze(input_text="hi", output_text="text")

        assert isinstance(result, AnalysisResult)
        assert result.analyzer_name == "comprehend"
        assert result.raw_score == 0.0
        assert result.entities == []
        assert "error" in result.metadata
        assert "Service unavailable" in result.metadata["error"]
