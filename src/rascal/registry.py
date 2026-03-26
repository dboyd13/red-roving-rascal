"""Plugin registry for swappable components."""
from __future__ import annotations

from typing import Protocol, runtime_checkable, Any

from rascal.models import AnalysisResult, Verdict, TestSuite


@runtime_checkable
class Checker(Protocol):
    """Scores an input/output pair. Returns 1-5 (1=pass, 5=fail)."""
    def check(self, input_text: str, output_text: str) -> int: ...


@runtime_checkable
class Processor(Protocol):
    """Sends an input to a target and returns the response."""
    def process(self, input_text: str, target: str) -> str: ...


@runtime_checkable
class DataSource(Protocol):
    """Provides inputs for processing."""
    def load(self, tags: list[str] | None = None) -> list[str]: ...


@runtime_checkable
class Reporter(Protocol):
    """Formats results for output."""
    def report(self, summary: Any) -> str: ...


@runtime_checkable
class Scorer(Protocol):
    """Aggregates results and applies pass/fail logic."""
    def score(self, results: list[Any], threshold: float) -> Any: ...


@runtime_checkable
class AuthProvider(Protocol):
    """Signs outbound HTTP requests."""
    def sign(self, method: str, url: str, body: bytes = b"") -> dict[str, str]: ...


@runtime_checkable
class Analyzer(Protocol):
    """Inspects an input/output pair and produces a raw AnalysisResult."""
    def analyze(self, input_text: str, output_text: str) -> AnalysisResult: ...


@runtime_checkable
class Judge(Protocol):
    """Evaluates an AnalysisResult and produces a Verdict."""
    def judge(self, result: AnalysisResult) -> Verdict: ...


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
        # Register a default (shipped with the package)
        Registry.register_default("checker", StubChecker())

        # Override with a custom implementation (Amazon-internal)
        Registry.register("checker", ComprehendPiiChecker())

        # Retrieve (custom > default > error)
        checker = Registry.get("checker")
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
