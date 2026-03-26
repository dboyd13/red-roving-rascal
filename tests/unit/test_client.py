"""Unit tests for RascalClient new methods: get_suites, get_suite, evaluate,
get_evaluation, evaluate_and_wait.

Tests use a real HTTPServer with AppHandler and stub components registered
in the Registry, matching the approach used in test_app.py.

Requirements: 12.1, 12.2, 12.3, 12.4, 6.1, 6.2, 6.3, 6.4
"""
from __future__ import annotations

import os
import threading
import time
from http.server import HTTPServer
from unittest.mock import patch

import boto3
import httpx
import moto
import pytest

from rascal.app import AppHandler
from rascal.client import RascalClient
from rascal.models import (
    AnalysisResult,
    EvaluateResponse,
    EvaluationStatus,
    InputOutputPair,
    ScoringConfig,
    ScoringResult,
    TestCase,
    TestSuite,
    Verdict,
)
from rascal.registry import ComponentNotFoundError, Registry


# ---------------------------------------------------------------------------
# Stub components
# ---------------------------------------------------------------------------


class StubAnalyzer:
    def __init__(self, name: str, score: float = 0.5) -> None:
        self._name = name
        self._score = score

    def analyze(self, input_text: str, output_text: str) -> AnalysisResult:
        return AnalysisResult(analyzer_name=self._name, raw_score=self._score)


class StubJudge:
    def __init__(self, name: str) -> None:
        self._name = name

    def judge(self, result: AnalysisResult) -> Verdict:
        return Verdict(
            passed=True,
            score=result.raw_score,
            threshold=0.5,
            analyzer_name=result.analyzer_name,
        )


class StubScorer:
    def score(self, verdicts: list[Verdict], config: ScoringConfig) -> ScoringResult:
        return ScoringResult(passed=True, per_analyzer={}, description="ok")


class StubSuiteStore:
    def __init__(self, suites: dict[str, TestSuite] | None = None) -> None:
        self._suites = suites or {}

    def list_suites(self) -> list[str]:
        return list(self._suites.keys())

    def get_suite(self, suite_id: str) -> TestSuite:
        if suite_id not in self._suites:
            raise ComponentNotFoundError(f"Suite '{suite_id}' not found")
        return self._suites[suite_id]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_SAMPLE_SUITE = TestSuite(
    suite_id="s1",
    name="Sample Suite",
    test_cases=[TestCase(input_text="hi")],
)


@pytest.fixture(scope="module")
def _dynamo():
    """Create a mocked DynamoDB Jobs table for the module."""
    with moto.mock_aws():
        os.environ["AWS_DEFAULT_REGION"] = "us-east-1"
        os.environ["AWS_ACCESS_KEY_ID"] = "testing"
        os.environ["AWS_SECRET_ACCESS_KEY"] = "testing"
        os.environ["JOBS_TABLE"] = "rascal-jobs"
        ddb = boto3.resource("dynamodb", region_name="us-east-1")
        ddb.create_table(
            TableName="rascal-jobs",
            KeySchema=[{"AttributeName": "jobId", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "jobId", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
        yield


@pytest.fixture(scope="module")
def server_url(_dynamo):
    """Start a real HTTPServer on a random port, reuse across all tests."""
    server = HTTPServer(("127.0.0.1", 0), AppHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{port}"
    server.shutdown()


@pytest.fixture(autouse=True)
def _clear_registry():
    """Clear the Registry before each test for isolation."""
    Registry.clear()
    yield
    Registry.clear()


# ---------------------------------------------------------------------------
# get_suites — Req 12.1
# ---------------------------------------------------------------------------


class TestGetSuites:
    def test_returns_list_of_strings(self, server_url: str):
        store = StubSuiteStore({"s1": _SAMPLE_SUITE})
        Registry.register("suite_store", store)

        client = RascalClient(server_url)
        result = client.get_suites()

        assert isinstance(result, list)
        assert result == ["s1"]

    def test_empty_store_returns_empty_list(self, server_url: str):
        Registry.register("suite_store", StubSuiteStore())

        client = RascalClient(server_url)
        result = client.get_suites()

        assert result == []

    def test_no_store_raises_http_error(self, server_url: str):
        client = RascalClient(server_url)

        with pytest.raises(httpx.HTTPStatusError) as exc_info:
            client.get_suites()
        assert exc_info.value.response.status_code == 501


# ---------------------------------------------------------------------------
# get_suite — Req 12.2
# ---------------------------------------------------------------------------


class TestGetSuite:
    def test_returns_test_suite_object(self, server_url: str):
        store = StubSuiteStore({"s1": _SAMPLE_SUITE})
        Registry.register("suite_store", store)

        client = RascalClient(server_url)
        suite = client.get_suite("s1")

        assert isinstance(suite, TestSuite)
        assert suite.suite_id == "s1"
        assert suite.name == "Sample Suite"
        assert len(suite.test_cases) == 1

    def test_unknown_id_raises_http_error(self, server_url: str):
        store = StubSuiteStore({"s1": _SAMPLE_SUITE})
        Registry.register("suite_store", store)

        client = RascalClient(server_url)

        with pytest.raises(httpx.HTTPStatusError) as exc_info:
            client.get_suite("nonexistent")
        assert exc_info.value.response.status_code == 404

    def test_no_store_raises_http_error(self, server_url: str):
        client = RascalClient(server_url)

        with pytest.raises(httpx.HTTPStatusError) as exc_info:
            client.get_suite("s1")
        assert exc_info.value.response.status_code == 501


# ---------------------------------------------------------------------------
# evaluate — Req 6.1
# ---------------------------------------------------------------------------


class TestEvaluate:
    def test_returns_evaluate_response_with_pending_status(self, server_url: str):
        Registry.register("analyzer.stub", StubAnalyzer("stub"))
        Registry.register("judge.stub", StubJudge("stub"))
        Registry.register("scorer", StubScorer())

        client = RascalClient(server_url)
        pairs = [InputOutputPair(input_text="hello", output_text="world")]
        config = ScoringConfig(thresholds={})

        result = client.evaluate(pairs, config)

        assert isinstance(result, EvaluateResponse)
        assert result.status == EvaluationStatus.PENDING
        assert result.evaluation_id
        assert result.result is None
        assert result.error is None

    def test_evaluate_with_multiple_pairs(self, server_url: str):
        Registry.register("analyzer.stub", StubAnalyzer("stub"))
        Registry.register("judge.stub", StubJudge("stub"))
        Registry.register("scorer", StubScorer())

        client = RascalClient(server_url)
        pairs = [
            InputOutputPair(input_text="a", output_text="b"),
            InputOutputPair(input_text="c", output_text="d"),
        ]
        config = ScoringConfig(thresholds={"stub": 0.5})

        result = client.evaluate(pairs, config)

        assert isinstance(result, EvaluateResponse)
        assert result.status == EvaluationStatus.PENDING
        assert result.evaluation_id


# ---------------------------------------------------------------------------
# Error handling — Req 12.4
# ---------------------------------------------------------------------------


class TestErrorHandling:
    def test_evaluate_storage_failure_raises_http_error(self, server_url: str):
        """Storage failure during save_evaluation → 500."""
        Registry.register("analyzer.stub", StubAnalyzer("stub"))
        Registry.register("judge.stub", StubJudge("stub"))
        Registry.register("scorer", StubScorer())

        client = RascalClient(server_url)
        pairs = [InputOutputPair(input_text="x", output_text="y")]
        config = ScoringConfig(thresholds={})

        with patch("rascal.app.Storage") as MockStorage:
            MockStorage.return_value.save_evaluation.side_effect = RuntimeError("db down")
            with pytest.raises(httpx.HTTPStatusError) as exc_info:
                client.evaluate(pairs, config)
            assert exc_info.value.response.status_code == 500


# ---------------------------------------------------------------------------
# get_evaluation — Req 6.2
# ---------------------------------------------------------------------------


class TestGetEvaluation:
    def test_returns_evaluation_state(self, server_url: str):
        """get_evaluation returns the current EvaluateResponse for a known ID."""
        Registry.register("analyzer.stub", StubAnalyzer("stub"))
        Registry.register("judge.stub", StubJudge("stub"))
        Registry.register("scorer", StubScorer())

        client = RascalClient(server_url)
        pairs = [InputOutputPair(input_text="hello", output_text="world")]
        config = ScoringConfig(thresholds={})

        submitted = client.evaluate(pairs, config)
        result = client.get_evaluation(submitted.evaluation_id)

        assert isinstance(result, EvaluateResponse)
        assert result.evaluation_id == submitted.evaluation_id
        assert result.status in (
            EvaluationStatus.PENDING,
            EvaluationStatus.RUNNING,
            EvaluationStatus.COMPLETE,
        )

    def test_unknown_id_raises_http_error(self, server_url: str):
        """get_evaluation with a non-existent ID raises HTTPStatusError (404)."""
        client = RascalClient(server_url)

        with pytest.raises(httpx.HTTPStatusError) as exc_info:
            client.get_evaluation("nonexistent-id")
        assert exc_info.value.response.status_code == 404


# ---------------------------------------------------------------------------
# evaluate_and_wait — Req 6.3, 6.4
# ---------------------------------------------------------------------------


class TestEvaluateAndWait:
    def test_polls_until_complete(self, server_url: str):
        """evaluate_and_wait returns a complete EvaluateResponse after polling."""
        Registry.register("analyzer.stub", StubAnalyzer("stub"))
        Registry.register("judge.stub", StubJudge("stub"))
        Registry.register("scorer", StubScorer())

        client = RascalClient(server_url)
        pairs = [InputOutputPair(input_text="hello", output_text="world")]
        config = ScoringConfig(thresholds={})

        result = client.evaluate_and_wait(
            pairs, config, poll_interval=0.1, timeout=10.0,
        )

        assert isinstance(result, EvaluateResponse)
        assert result.status == EvaluationStatus.COMPLETE
        assert result.result is not None
        assert isinstance(result.result, ScoringResult)
        assert result.error is None

    def test_raises_timeout_error(self, server_url: str):
        """evaluate_and_wait raises TimeoutError when evaluation stays pending."""
        Registry.register("analyzer.stub", StubAnalyzer("stub"))
        Registry.register("judge.stub", StubJudge("stub"))
        Registry.register("scorer", StubScorer())

        client = RascalClient(server_url)
        pairs = [InputOutputPair(input_text="hello", output_text="world")]
        config = ScoringConfig(thresholds={})

        # Patch _run_evaluation_async to never complete (no-op)
        with patch("rascal.app._run_evaluation_async"):
            with pytest.raises(TimeoutError):
                client.evaluate_and_wait(
                    pairs, config, poll_interval=0.05, timeout=0.2,
                )
