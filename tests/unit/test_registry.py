"""Unit tests for protocols and registry."""
from __future__ import annotations

import pytest

from rascal.models import AnalysisResult, TestSuite, Verdict
from rascal.registry import (
    Analyzer,
    ComponentNotFoundError,
    Judge,
    Registry,
    SuiteStore,
)


# --- Fixtures ---


@pytest.fixture(autouse=True)
def _clear_registry():
    """Clear the Registry before each test."""
    Registry.clear()
    yield
    Registry.clear()


# --- Conforming stub classes ---


class StubAnalyzer:
    def analyze(self, input_text: str, output_text: str) -> AnalysisResult:
        return AnalysisResult(analyzer_name="stub", raw_score=0.0)


class StubJudge:
    def judge(self, result: AnalysisResult) -> Verdict:
        return Verdict(passed=True, score=0.0, threshold=0.5, analyzer_name="stub")


class StubSuiteStore:
    def get_suite(self, suite_id: str) -> TestSuite:
        return TestSuite(suite_id=suite_id, name="stub")

    def list_suites(self) -> list[str]:
        return []


# --- Non-conforming classes ---


class NotAnAnalyzer:
    """Missing the analyze method entirely."""
    def run(self) -> None: ...


class NotAJudge:
    def evaluate(self) -> None: ...


class NotASuiteStore:
    """Only has one of the two required methods."""
    def get_suite(self, suite_id: str) -> TestSuite:
        return TestSuite(suite_id=suite_id, name="x")


# --- Protocol isinstance checks (Req 1.2, 2.2, 8.3) ---


class TestAnalyzerProtocol:
    def test_conforming_class_is_instance(self):
        assert isinstance(StubAnalyzer(), Analyzer)

    def test_non_conforming_class_is_not_instance(self):
        assert not isinstance(NotAnAnalyzer(), Analyzer)

    def test_plain_object_is_not_instance(self):
        assert not isinstance(object(), Analyzer)


class TestJudgeProtocol:
    def test_conforming_class_is_instance(self):
        assert isinstance(StubJudge(), Judge)

    def test_non_conforming_class_is_not_instance(self):
        assert not isinstance(NotAJudge(), Judge)


class TestSuiteStoreProtocol:
    def test_conforming_class_is_instance(self):
        assert isinstance(StubSuiteStore(), SuiteStore)

    def test_partial_implementation_is_not_instance(self):
        assert not isinstance(NotASuiteStore(), SuiteStore)


# --- Registration and retrieval (Req 1.4, 2.4, 8.6) ---


class TestRegistryAnalyzerKeys:
    def test_register_and_get_analyzer(self):
        a = StubAnalyzer()
        Registry.register("analyzer.foo", a)
        assert Registry.get("analyzer.foo") is a

    def test_register_and_get_multiple_analyzers(self):
        a1 = StubAnalyzer()
        a2 = StubAnalyzer()
        Registry.register("analyzer.alpha", a1)
        Registry.register("analyzer.beta", a2)
        assert Registry.get("analyzer.alpha") is a1
        assert Registry.get("analyzer.beta") is a2


class TestRegistryJudgeKeys:
    def test_register_and_get_judge(self):
        j = StubJudge()
        Registry.register("judge.foo", j)
        assert Registry.get("judge.foo") is j

    def test_register_and_get_multiple_judges(self):
        j1 = StubJudge()
        j2 = StubJudge()
        Registry.register("judge.alpha", j1)
        Registry.register("judge.beta", j2)
        assert Registry.get("judge.alpha") is j1
        assert Registry.get("judge.beta") is j2


class TestRegistrySuiteStoreKey:
    def test_register_and_get_suite_store(self):
        s = StubSuiteStore()
        Registry.register("suite_store", s)
        assert Registry.get("suite_store") is s


# --- ComponentNotFoundError (Req 14.1) ---


class TestComponentNotFoundError:
    def test_get_missing_key_raises(self):
        with pytest.raises(ComponentNotFoundError):
            Registry.get("analyzer.nonexistent")

    def test_get_missing_judge_raises(self):
        with pytest.raises(ComponentNotFoundError):
            Registry.get("judge.nonexistent")

    def test_get_missing_suite_store_raises(self):
        with pytest.raises(ComponentNotFoundError):
            Registry.get("suite_store")


# --- keys() with legacy and new keys (Req 14.2, 14.3) ---


class TestRegistryKeys:
    def test_keys_includes_new_pipeline_keys(self):
        Registry.register("analyzer.x", StubAnalyzer())
        Registry.register("judge.x", StubJudge())
        Registry.register("suite_store", StubSuiteStore())
        k = set(Registry.keys())
        assert {"analyzer.x", "judge.x", "suite_store"} <= k

    def test_keys_includes_legacy_keys(self):
        Registry.register("processor", object())
        Registry.register("checker", object())
        Registry.register("data_source", object())
        Registry.register("reporter", object())
        Registry.register("scorer", object())
        k = set(Registry.keys())
        assert {"processor", "checker", "data_source", "reporter", "scorer"} <= k

    def test_keys_includes_both_legacy_and_new(self):
        Registry.register("processor", object())
        Registry.register("checker", object())
        Registry.register("analyzer.comp", StubAnalyzer())
        Registry.register("judge.comp", StubJudge())
        Registry.register("suite_store", StubSuiteStore())
        k = set(Registry.keys())
        assert {"processor", "checker", "analyzer.comp", "judge.comp", "suite_store"} <= k

    def test_keys_empty_after_clear(self):
        Registry.register("analyzer.x", StubAnalyzer())
        Registry.clear()
        assert Registry.keys() == []

    def test_default_and_custom_keys_both_appear(self):
        Registry.register_default("analyzer.default", StubAnalyzer())
        Registry.register("analyzer.custom", StubAnalyzer())
        k = set(Registry.keys())
        assert {"analyzer.default", "analyzer.custom"} <= k

    def test_custom_overrides_default_same_key(self):
        default_a = StubAnalyzer()
        custom_a = StubAnalyzer()
        Registry.register_default("analyzer.x", default_a)
        Registry.register("analyzer.x", custom_a)
        assert Registry.get("analyzer.x") is custom_a
