"""WeightedEntityJudge — scores based on weighted entity types."""
from __future__ import annotations

from rascal.models import AnalysisResult, Verdict


class WeightedEntityJudge:
    """Judge that scores based on weighted entity types."""

    def __init__(
        self,
        weights: dict[str, float] | None = None,
        threshold: float = 1.0,
    ) -> None:
        self.weights = weights or {}
        self.threshold = threshold

    def judge(self, result: AnalysisResult) -> Verdict:
        """Sum weights of detected entities, compare against threshold."""
        weighted_sum = sum(
            self.weights.get(entity, 0.0) for entity in result.entities
        )
        violations = {
            entity
            for entity in result.entities
            if self.weights.get(entity, 0.0) > 0
        }
        return Verdict(
            passed=weighted_sum <= self.threshold,
            score=weighted_sum,
            threshold=self.threshold,
            analyzer_name=result.analyzer_name,
            violations=violations,
        )
