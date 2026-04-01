"""Plugin registry for swappable components."""
from __future__ import annotations

from typing import Protocol, runtime_checkable, Any

from rascal.models import AnalysisResult, Verdict, TestSuite


@runtime_checkable
class Analyzer(Protocol):
    """Inspects an input/output pair and produces a raw AnalysisResult."""
    def analyze(self, input_text: str, output_text: str) -> AnalysisResult: ...


@runtime_checkable
class Judge(Protocol):
    """Evaluates an AnalysisResult and produces a Verdict."""
    def judge(self, result: AnalysisResult) -> Verdict: ...


@runtime_checkable
class Scorer(Protocol):
    """Aggregates verdicts and applies pass/fail logic."""
    def score(self, verdicts: list[Any], config: Any) -> Any: ...


@runtime_checkable
class SuiteStore(Protocol):
    """Stores and retrieves test suites."""
    def get_suite(self, suite_id: str) -> TestSuite: ...
    def list_suites(self) -> list[str]: ...


class _NotFoundError(Exception):
    pass


ComponentNotFoundError = _NotFoundError


class Registry:
    """Singleton registry for pluggable components.

    Usage:
        Registry.register_default("analyzer.comprehend", ComprehendAnalyzer())
        Registry.register("analyzer.custom", MyAnalyzer())  # override
        analyzer = Registry.get("analyzer.comprehend")
    """

    _defaults: dict[str, object] = {}
    _custom: dict[str, object] = {}

    @classmethod
    def register_default(cls, key: str, component: object) -> None:
        cls._defaults[key] = component

    @classmethod
    def register(cls, key: str, component: object) -> None:
        cls._custom[key] = component

    @classmethod
    def get(cls, key: str) -> object:
        if key in cls._custom:
            return cls._custom[key]
        if key in cls._defaults:
            return cls._defaults[key]
        raise ComponentNotFoundError(f"No component registered for '{key}'")

    @classmethod
    def has(cls, key: str) -> bool:
        return key in cls._custom or key in cls._defaults

    @classmethod
    def clear(cls) -> None:
        cls._custom.clear()
        cls._defaults.clear()

    @classmethod
    def keys(cls) -> list[str]:
        return list(set(list(cls._defaults.keys()) + list(cls._custom.keys())))
