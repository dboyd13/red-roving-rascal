"""Pipeline orchestration: Analyzer → Judge → Scorer."""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from rascal.models import InputOutputPair, ScoringConfig, ScoringResult
from rascal.registry import Registry
from rascal.scorers.pass_rate import PassRateScorer

if TYPE_CHECKING:
    from rascal.models import Verdict

logger = logging.getLogger(__name__)


class Pipeline:
    """Orchestrates the Analyzer → Judge → Scorer pipeline."""

    def run(self, pairs: list[InputOutputPair], config: ScoringConfig) -> ScoringResult:
        """Run all registered analyzers/judges against pairs, then score."""
        analyzer_names = self._get_analyzer_names()

        if not analyzer_names:
            return ScoringResult(
                passed=True,
                per_analyzer={},
                description="No analyzers configured",
            )

        if not pairs:
            return ScoringResult(
                passed=True,
                per_analyzer={},
                description="No pairs to evaluate",
            )

        # Register default scorer if not already present
        if not Registry.has("scorer"):
            Registry.register_default("scorer", PassRateScorer())

        verdicts: list[Verdict] = []

        for pair in pairs:
            for name in analyzer_names:
                analyzer = Registry.get(f"analyzer.{name}")
                try:
                    result = analyzer.analyze(pair.input_text, pair.output_text)  # type: ignore[union-attr]
                except Exception:
                    logger.warning(
                        "Analyzer '%s' raised an exception, skipping for this pair",
                        name,
                    )
                    continue

                judge_key = f"judge.{name}"
                if not Registry.has(judge_key):
                    logger.warning(
                        "No judge registered for analyzer '%s', skipping",
                        name,
                    )
                    continue

                judge = Registry.get(judge_key)
                verdict = judge.judge(result)  # type: ignore[union-attr]
                verdicts.append(verdict)

        scorer = Registry.get("scorer")
        return scorer.score(verdicts, config)  # type: ignore[union-attr]

    def _get_analyzer_names(self) -> list[str]:
        """Return analyzer names from registry keys matching 'analyzer.*'."""
        prefix = "analyzer."
        return [
            key[len(prefix):]
            for key in Registry.keys()
            if key.startswith(prefix)
        ]
