"""ThresholdJudge — passes/fails based on a score threshold and direction."""
from __future__ import annotations

from rascal.models import AnalysisResult, ThresholdDirection, Verdict


class ThresholdJudge:
    """Judge that passes/fails based on a score threshold and direction."""

    def __init__(
        self,
        threshold: float = 0.5,
        direction: ThresholdDirection = ThresholdDirection.BELOW,
    ) -> None:
        self.threshold = threshold
        self.direction = direction

    def judge(self, result: AnalysisResult) -> Verdict:
        """Compare raw_score against threshold using configured direction."""
        if self.direction is ThresholdDirection.ABOVE:
            passed = result.raw_score >= self.threshold
        else:
            passed = result.raw_score <= self.threshold

        return Verdict(
            passed=passed,
            score=result.raw_score,
            threshold=self.threshold,
            analyzer_name=result.analyzer_name,
        )
