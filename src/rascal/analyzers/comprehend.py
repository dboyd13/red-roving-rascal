"""AWS Comprehend entity detection analyzer."""
from __future__ import annotations

import os

import boto3

from rascal.models import AnalysisResult


class ComprehendAnalyzer:
    """Analyzer that uses AWS Comprehend to detect entities."""

    def __init__(self, region: str | None = None) -> None:
        self._client = boto3.client(
            "comprehend",
            region_name=region or os.environ.get("AWS_REGION"),
        )

    def analyze(self, input_text: str, output_text: str) -> AnalysisResult:
        """Call Comprehend detect_entities on output_text."""
        try:
            response = self._client.detect_entities(
                Text=output_text, LanguageCode="en"
            )
            entities = response.get("Entities", [])
            entity_types = [e["Type"] for e in entities]
            raw_score = (
                max(e["Score"] for e in entities) if entities else 0.0
            )
            return AnalysisResult(
                analyzer_name="comprehend",
                raw_score=raw_score,
                entities=entity_types,
            )
        except Exception as e:
            return AnalysisResult(
                analyzer_name="comprehend",
                raw_score=0.0,
                entities=[],
                metadata={"error": str(e)},
            )
