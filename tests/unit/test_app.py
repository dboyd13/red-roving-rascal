"""Unit tests for API routes in app.py.

Tests POST /evaluate, GET /suites, and GET /suites/{suite_id} endpoints.

Requirements: 10.1, 10.2, 10.3, 10.4, 10.5, 11.1, 11.2, 11.3, 11.4
Requirements: 2.1, 2.2, 2.3, 2.4, 2.5
"""
from __future__ import annotations

import json
import os
import threading
from http.server import HTTPServer
from unittest.mock import patch

import boto3
import httpx
import moto
import pytest

from rascal.app import AppHandler
from rascal.models import (
    AnalysisResult,
    EvaluateResponse,
    EvaluationStatus,
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
    """Deterministic analyzer returning a fixed result."""

    def __init__(self, name: str, score: float = 0.5) -> None:
        self._name = name
        self._score = score

    def analyze(self, input_text: str, output_text: str) -> AnalysisResult:
        return AnalysisResult(
            analyzer_name=self._name,
            raw_score=self._score,
        )


class StubJudge:
    """Deterministic judge returning a passing verdict."""

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
    """Scorer that always returns a passing result."""

    def score(self, verdicts: list[Verdict], config: ScoringConfig) -> ScoringResult:
        return ScoringResult(passed=True, per_analyzer={}, description="ok")


class ExplodingScorer:
    """Scorer that raises an exception to trigger HTTP 500."""

    def score(self, verdicts: list[Verdict], config: ScoringConfig) -> ScoringResult:
        raise RuntimeError("scorer exploded")


class StubSuiteStore:
    """In-memory suite store for testing."""

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
# Helpers
# ---------------------------------------------------------------------------

_VALID_BODY = {
    "pairs": [{"input_text": "hello", "output_text": "world"}],
    "config": {"thresholds": {}},
}

_SAMPLE_SUITE = TestSuite(
    suite_id="s1",
    name="Sample Suite",
    test_cases=[TestCase(input_text="hi")],
)


# ---------------------------------------------------------------------------
# POST /evaluate — valid request → 202 (Req 2.1, 2.2, 2.3)
# ---------------------------------------------------------------------------


class TestEvaluateValid:
    def test_returns_202_with_evaluate_response(self, server_url: str):
        Registry.register("analyzer.stub", StubAnalyzer("stub"))
        Registry.register("judge.stub", StubJudge("stub"))
        Registry.register("scorer", StubScorer())

        resp = httpx.post(f"{server_url}/evaluate", json=_VALID_BODY)

        assert resp.status_code == 202
        data = resp.json()
        ev = EvaluateResponse.model_validate(data)
        assert ev.status == EvaluationStatus.PENDING
        assert ev.evaluation_id
        assert ev.result is None
        assert ev.error is None

    def test_returns_valid_json_content_type(self, server_url: str):
        Registry.register("analyzer.stub", StubAnalyzer("stub"))
        Registry.register("judge.stub", StubJudge("stub"))
        Registry.register("scorer", StubScorer())

        resp = httpx.post(f"{server_url}/evaluate", json=_VALID_BODY)

        assert resp.headers["content-type"] == "application/json"


# ---------------------------------------------------------------------------
# POST /evaluate — invalid body → 400 (Req 10.4)
# ---------------------------------------------------------------------------


class TestEvaluateInvalid:
    def test_missing_pairs_returns_400(self, server_url: str):
        resp = httpx.post(f"{server_url}/evaluate", json={"config": {"thresholds": {}}})
        assert resp.status_code == 400
        assert "error" in resp.json()

    def test_pairs_wrong_type_returns_400(self, server_url: str):
        resp = httpx.post(f"{server_url}/evaluate", json={"pairs": "not-a-list"})
        assert resp.status_code == 400
        assert "error" in resp.json()

    def test_empty_body_returns_400(self, server_url: str):
        resp = httpx.post(f"{server_url}/evaluate", json={})
        assert resp.status_code == 400
        assert "error" in resp.json()

    def test_missing_required_fields_in_pair_returns_400(self, server_url: str):
        resp = httpx.post(
            f"{server_url}/evaluate",
            json={"pairs": [{"input_text": "hi"}]},
        )
        assert resp.status_code == 400
        assert "error" in resp.json()


# ---------------------------------------------------------------------------
# POST /evaluate — storage error → 500 (Req 2.5)
# ---------------------------------------------------------------------------


class TestEvaluateStorageError:
    def test_storage_failure_returns_500(self, server_url: str):
        Registry.register("analyzer.boom", StubAnalyzer("boom"))
        Registry.register("judge.boom", StubJudge("boom"))
        Registry.register("scorer", ExplodingScorer())

        with patch("rascal.app.Storage") as MockStorage:
            MockStorage.return_value.save_evaluation.side_effect = RuntimeError("db down")
            resp = httpx.post(f"{server_url}/evaluate", json=_VALID_BODY)

        assert resp.status_code == 500
        data = resp.json()
        assert data["error"] == "Internal server error"


# ---------------------------------------------------------------------------
# GET /suites — with registered SuiteStore → 200 (Req 11.1)
# ---------------------------------------------------------------------------


class TestGetSuites:
    def test_returns_200_with_suite_ids(self, server_url: str):
        store = StubSuiteStore({"s1": _SAMPLE_SUITE})
        Registry.register("suite_store", store)

        resp = httpx.get(f"{server_url}/suites")

        assert resp.status_code == 200
        assert resp.json() == ["s1"]

    def test_empty_store_returns_empty_list(self, server_url: str):
        Registry.register("suite_store", StubSuiteStore())

        resp = httpx.get(f"{server_url}/suites")

        assert resp.status_code == 200
        assert resp.json() == []


# ---------------------------------------------------------------------------
# GET /suites — no SuiteStore → 501 (Req 11.4)
# ---------------------------------------------------------------------------


class TestGetSuitesNoStore:
    def test_returns_501_when_no_store(self, server_url: str):
        resp = httpx.get(f"{server_url}/suites")

        assert resp.status_code == 501
        assert "error" in resp.json()


# ---------------------------------------------------------------------------
# GET /suites/{id} — valid ID → 200 (Req 11.2)
# ---------------------------------------------------------------------------


class TestGetSuiteById:
    def test_returns_200_with_suite_json(self, server_url: str):
        store = StubSuiteStore({"s1": _SAMPLE_SUITE})
        Registry.register("suite_store", store)

        resp = httpx.get(f"{server_url}/suites/s1")

        assert resp.status_code == 200
        data = resp.json()
        suite = TestSuite.model_validate(data)
        assert suite.suite_id == "s1"
        assert suite.name == "Sample Suite"
        assert len(suite.test_cases) == 1


# ---------------------------------------------------------------------------
# GET /suites/{id} — unknown ID → 404 (Req 11.3)
# ---------------------------------------------------------------------------


class TestGetSuiteNotFound:
    def test_returns_404_for_unknown_id(self, server_url: str):
        store = StubSuiteStore({"s1": _SAMPLE_SUITE})
        Registry.register("suite_store", store)

        resp = httpx.get(f"{server_url}/suites/nonexistent")

        assert resp.status_code == 404
        assert "error" in resp.json()


# ---------------------------------------------------------------------------
# GET /suites/{id} — no SuiteStore → 501 (Req 11.4)
# ---------------------------------------------------------------------------


class TestGetSuiteByIdNoStore:
    def test_returns_501_when_no_store(self, server_url: str):
        resp = httpx.get(f"{server_url}/suites/s1")

        assert resp.status_code == 501
        assert "error" in resp.json()


# ---------------------------------------------------------------------------
# GET /evaluate/{id} — existing evaluation → 200 (Req 4.1)
# ---------------------------------------------------------------------------


class TestGetEvaluationFound:
    def test_returns_200_with_evaluation_state(self, server_url: str):
        """Submit an evaluation, then GET it by ID — should return current state."""
        Registry.register("analyzer.stub", StubAnalyzer("stub"))
        Registry.register("judge.stub", StubJudge("stub"))
        Registry.register("scorer", StubScorer())

        post_resp = httpx.post(f"{server_url}/evaluate", json=_VALID_BODY)
        assert post_resp.status_code == 202
        evaluation_id = post_resp.json()["evaluation_id"]

        get_resp = httpx.get(f"{server_url}/evaluate/{evaluation_id}")

        assert get_resp.status_code == 200
        data = get_resp.json()
        ev = EvaluateResponse.model_validate(data)
        assert ev.evaluation_id == evaluation_id
        assert ev.status in (
            EvaluationStatus.PENDING,
            EvaluationStatus.RUNNING,
            EvaluationStatus.COMPLETE,
        )


# ---------------------------------------------------------------------------
# GET /evaluate/{id} — unknown ID → 404 (Req 4.2)
# ---------------------------------------------------------------------------


class TestGetEvaluationNotFound:
    def test_returns_404_for_unknown_id(self, server_url: str):
        resp = httpx.get(f"{server_url}/evaluate/nonexistent-id-12345")

        assert resp.status_code == 404
        data = resp.json()
        assert data["error"] == "evaluation not found"


# ---------------------------------------------------------------------------
# Background thread completes evaluation (Req 3.1, 3.2, 3.3)
# ---------------------------------------------------------------------------


class TestBackgroundEvaluationCompletes:
    def test_background_thread_sets_status_to_complete(self, server_url: str):
        """Submit, then poll until the background thread finishes."""
        Registry.register("analyzer.stub", StubAnalyzer("stub"))
        Registry.register("judge.stub", StubJudge("stub"))
        Registry.register("scorer", StubScorer())

        post_resp = httpx.post(f"{server_url}/evaluate", json=_VALID_BODY)
        assert post_resp.status_code == 202
        evaluation_id = post_resp.json()["evaluation_id"]

        # Poll until complete (or timeout after ~5s)
        import time

        deadline = time.monotonic() + 5.0
        final = None
        while time.monotonic() < deadline:
            get_resp = httpx.get(f"{server_url}/evaluate/{evaluation_id}")
            assert get_resp.status_code == 200
            final = EvaluateResponse.model_validate(get_resp.json())
            if final.status in (EvaluationStatus.COMPLETE, EvaluationStatus.FAILED):
                break
            time.sleep(0.1)

        assert final is not None
        assert final.status == EvaluationStatus.COMPLETE
        assert final.result is not None
        assert final.result.passed is True
        assert final.error is None

    def test_background_thread_sets_status_to_failed_on_error(self, server_url: str):
        """When the pipeline raises, the evaluation should end up as failed."""
        Registry.register("analyzer.stub", StubAnalyzer("stub"))
        Registry.register("judge.stub", StubJudge("stub"))
        Registry.register("scorer", ExplodingScorer())

        post_resp = httpx.post(f"{server_url}/evaluate", json=_VALID_BODY)
        assert post_resp.status_code == 202
        evaluation_id = post_resp.json()["evaluation_id"]

        import time

        deadline = time.monotonic() + 5.0
        final = None
        while time.monotonic() < deadline:
            get_resp = httpx.get(f"{server_url}/evaluate/{evaluation_id}")
            assert get_resp.status_code == 200
            final = EvaluateResponse.model_validate(get_resp.json())
            if final.status in (EvaluationStatus.COMPLETE, EvaluationStatus.FAILED):
                break
            time.sleep(0.1)

        assert final is not None
        assert final.status == EvaluationStatus.FAILED
        assert final.error is not None
        assert "scorer exploded" in final.error
        assert final.result is None
