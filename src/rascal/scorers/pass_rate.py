"""Default scorer that calculates per-analyzer pass rates."""
from __future__ import annotations

from collections import defaultdict

from rascal.models import PerAnalyzerResult, ScoringConfig, ScoringResult, Verdict


class PassRateScorer:
    """Scorer that calculates per-analyzer pass rates."""

    def score(self, verdicts: list[Verdict], config: ScoringConfig) -> ScoringResult:
        """Group verdicts by analyzer, compute pass rates, compare to thresholds."""
        if not verdicts:
            return ScoringResult(
                passed=True,
                per_analyzer={},
                description="No verdicts to score",
            )

        groups: dict[str, list[Verdict]] = defaultdict(list)
        for v in verdicts:
            groups[v.analyzer_name].append(v)

        per_analyzer: dict[str, PerAnalyzerResult] = {}
        all_passed = True
        lines: list[str] = []

        for analyzer_name, group in sorted(groups.items()):
            passed_count = sum(1 for v in group if v.passed)
            total_count = len(group)
            pass_rate = passed_count / total_count
            threshold = config.thresholds.get(analyzer_name, 1.0)
            met = pass_rate >= threshold

            per_analyzer[analyzer_name] = PerAnalyzerResult(
                pass_rate=pass_rate,
                threshold=threshold,
            )

            if not met:
                all_passed = False

            status = "PASS" if met else "FAIL"
            lines.append(
                f"{analyzer_name}: {pass_rate:.1%} pass rate "
                f"(threshold {threshold:.1%}) — {status}"
            )

        overall = "PASSED" if all_passed else "FAILED"
        lines.append(f"Overall: {overall}")
        description = "; ".join(lines)

        return ScoringResult(
            passed=all_passed,
            per_analyzer=per_analyzer,
            description=description,
        )
