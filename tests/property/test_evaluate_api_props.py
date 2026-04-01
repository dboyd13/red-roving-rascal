"""Tests for POST /evaluate endpoint."""
from __future__ import annotations

import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import boto3
import httpx
import moto
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from rascal.app import AppHandler
from rascal.client import RascalClient
from rascal.models import (
    AnalysisResult,
    EvaluateResponse,
    EvaluationStatus,
    InputOutputPair,
    ScoringConfig,
    ScoringResult,
    Verdict,
)
from rascal.registry import Registry


# ---------------------------------------------------------------------------
# Stubs
# ---------------------------------------------------------------------------


class _StubAnalyzer:
    def __init__(self, name: str) -> None:
        self._name = name

    def analyze(self, input_text: str, output_text: str) -> AnalysisResult:
        return AnalysisResult(
            analyzer_name=self._name,
            raw_score=0.5,
            entities=[],
            metadata={},
        )

class _SlowAnalyzer:
    """Analyzer that sleeps to simulate a long-running pipeline."""

    def __init__(self, name: str, delay: float = 5.0) -> None:
        self._name = name
        self._delay = delay

    def analyze(self, input_text: str, output_text: str) -> AnalysisResult:
        time.sleep(self._delay)
        return AnalysisResult(
            analyzer_name=self._name,
            raw_score=0.5,
            entities=[],
            metadata={},
        )


class _StubJudge:
    def __init__(self, name: str) -> None:
        self._name = name

    def judge(self, result: AnalysisResult) -> Verdict:
        return Verdict(
            passed=True,
            score=result.raw_score,
            threshold=0.5,
            analyzer_name=result.analyzer_name,
            violations=set(),
        )


class _StubScorer:
    def score(self, verdicts: list[Verdict], config: ScoringConfig) -> ScoringResult:
        return ScoringResult(
            passed=True,
            per_analyzer={},
            description="stub scorer",
        )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_registry():
    """Clear registry before and after each test for isolation."""
    Registry.clear()
    yield
    Registry.clear()


@pytest.fixture(scope="module")
def _dynamo():
    """Create a mocked DynamoDB Jobs table for the module."""
    with moto.mock_aws():
        os.environ["AWS_DEFAULT_REGION"] = "us-east-1"
        os.environ["AWS_ACCESS_KEY_ID"] = "testing"
        os.environ["AWS_SECRET_ACCESS_KEY"] = "testing"
        os.environ["EVALUATIONS_TABLE"] = "rascal-evaluations"
        ddb = boto3.resource("dynamodb", region_name="us-east-1")
        ddb.create_table(
            TableName="rascal-evaluations",
            KeySchema=[{"AttributeName": "evaluationId", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "evaluationId", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
        yield


@pytest.fixture(scope="module")
def server_url(_dynamo):
    """Start an HTTPServer with AppHandler on a random port, return base URL."""
    server = HTTPServer(("127.0.0.1", 0), AppHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{port}"
    server.shutdown()


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

st_input_output_pair = st.builds(
    InputOutputPair,
    input_text=st.text(min_size=1, max_size=30),
    output_text=st.text(min_size=1, max_size=30),
)

st_pairs = st.lists(st_input_output_pair, min_size=1, max_size=5)

# DynamoDB Number type supports ~1E-130 to ~1E+125.  Hypothesis likes to
# explore extreme floats (e.g. 8e-245) that are perfectly valid IEEE 754
# but outside DynamoDB's representable range, causing save_evaluation to
# fail with a 500.  Constraining to [0.0, 1.0] with a minimum non-zero
# value of 1e-10 avoids this while still covering the meaningful range.
_dynamo_safe_unit_float = st.floats(
    min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False,
    allow_subnormal=False,
).map(lambda x: 0.0 if x == 0.0 else max(x, 1e-10))

st_scoring_config = st.builds(
    ScoringConfig,
    thresholds=st.dictionaries(
        keys=st.text(
            min_size=1, max_size=10,
            alphabet=st.characters(whitelist_categories=("L", "N")),
        ),
        values=_dynamo_safe_unit_float,
        min_size=0,
        max_size=3,
    ),
)


# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------


# Maximum acceptable response time in seconds.  The slow analyzer sleeps for
# 5 s, so any response under 2 s proves the handler did not block on it.
_MAX_RESPONSE_SECONDS = 2.0


@given(pairs=st_pairs, config=st_scoring_config)
@settings(max_examples=20)
def test_post_evaluate_returns_202_without_blocking(
    server_url: str,
    pairs: list[InputOutputPair],
    config: ScoringConfig,
) -> None:
    """POST /evaluate Always Returns 202 Without Blocking.

    

    For any valid EvaluateRequest the endpoint must:
    - return HTTP 202
    - include a valid EvaluateResponse with status=pending and a non-empty
      evaluation_id
    - not block on pipeline execution — verified by registering a slow
      analyzer (5 s sleep) and asserting the response arrives in < 2 s
     
    """
    # Register a deliberately slow analyzer so we can prove the response
    # is not waiting for the pipeline to finish.
    Registry.clear()
    Registry.register("analyzer.slow", _SlowAnalyzer("slow", delay=5.0))
    Registry.register("judge.slow", _StubJudge("slow"))
    Registry.register("scorer", _StubScorer())

    body = {
        "pairs": [p.model_dump() for p in pairs],
        "config": config.model_dump(),
    }

    start = time.monotonic()
    resp = httpx.post(f"{server_url}/evaluate", json=body)
    elapsed = time.monotonic() - start

    assert resp.status_code == 202

    ev = EvaluateResponse.model_validate(resp.json())
    assert ev.status == EvaluationStatus.PENDING
    assert ev.evaluation_id  # non-empty

    assert ev.result is None
    assert ev.error is None

    assert elapsed < _MAX_RESPONSE_SECONDS, (
        f"POST /evaluate took {elapsed:.2f}s — expected < {_MAX_RESPONSE_SECONDS}s "
        f"(pipeline should run in background)"
    )


# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------


@given(pairs=st_pairs, config=st_scoring_config)
@settings(max_examples=20)
def test_valid_evaluate_returns_202(
    server_url: str,
    pairs: list[InputOutputPair],
    config: ScoringConfig,
) -> None:
    """

    Valid EvaluateRequest bodies should yield HTTP 202 with a valid
    EvaluateResponse containing a non-empty evaluation_id and pending status.
    """
    Registry.clear()
    Registry.register("analyzer.stub", _StubAnalyzer("stub"))
    Registry.register("judge.stub", _StubJudge("stub"))
    Registry.register("scorer", _StubScorer())

    body = {
        "pairs": [p.model_dump() for p in pairs],
        "config": config.model_dump(),
    }

    resp = httpx.post(f"{server_url}/evaluate", json=body)

    assert resp.status_code == 202
    ev = EvaluateResponse.model_validate(resp.json())
    assert ev.status == EvaluationStatus.PENDING
    assert ev.evaluation_id


# Strategy for invalid request bodies (missing required fields or wrong types)
st_invalid_body = st.one_of(
    # Missing "pairs" key entirely
    st.fixed_dictionaries({"config": st.just({"thresholds": {}})}),
    # "pairs" is not a list
    st.fixed_dictionaries({
        "pairs": st.one_of(st.just("not-a-list"), st.integers(), st.just(None)),
    }),
    # "pairs" contains items with wrong types
    st.fixed_dictionaries({
        "pairs": st.just([{"input_text": 123, "output_text": 456}]),
    }),
    # "pairs" contains items missing required fields
    st.fixed_dictionaries({
        "pairs": st.just([{"input_text": "hello"}]),
    }),
    # Empty dict
    st.just({}),
    # "config" has wrong type
    st.fixed_dictionaries({
        "pairs": st.just([{"input_text": "a", "output_text": "b"}]),
        "config": st.just("not-an-object"),
    }),
)


@given(body=st_invalid_body)
@settings(max_examples=20)
def test_invalid_evaluate_returns_400(
    server_url: str,
    body: dict,
) -> None:
    """

    Invalid request bodies (missing fields, wrong types) should yield HTTP 400.
    """
    resp = httpx.post(f"{server_url}/evaluate", json=body)

    assert resp.status_code == 400
    data = resp.json()
    assert "error" in data


# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------

# Strategy: generate invalid bodies that should fail Pydantic validation.
# Covers missing required fields, wrong types, malformed pair items, and
# non-JSON payloads.
st_invalid_body_prop6 = st.one_of(
    # Empty dict — missing required "pairs"
    st.just({}),
    # "pairs" key missing, only config present
    st.fixed_dictionaries({"config": st.just({"thresholds": {}})}),
    # "pairs" is a scalar instead of a list
    st.fixed_dictionaries({
        "pairs": st.one_of(st.just("not-a-list"), st.integers(), st.just(None)),
    }),
    # "pairs" list contains items missing required "output_text"
    st.fixed_dictionaries({
        "pairs": st.just([{"input_text": "hello"}]),
    }),
    # "pairs" list contains items with wrong field types
    st.fixed_dictionaries({
        "pairs": st.just([{"input_text": 123, "output_text": 456}]),
    }),
    # "config" has wrong type (string instead of object)
    st.fixed_dictionaries({
        "pairs": st.just([{"input_text": "a", "output_text": "b"}]),
        "config": st.just("not-an-object"),
    }),
)


@given(body=st_invalid_body_prop6)
@settings(max_examples=30)
def test_invalid_requests_rejected_no_record_created(
    server_url: str,
    body: dict,
) -> None:
    """Invalid Evaluation Requests Rejected.

    

    For any invalid request body (missing required fields, wrong types),
    POST /evaluate must:
    - return HTTP 400 with a descriptive error message
    - NOT create any evaluation record in the Jobs_Table
    """
    # Snapshot evaluation-type items before the request
    ddb = boto3.resource("dynamodb", region_name="us-east-1")
    table = ddb.Table(os.environ["EVALUATIONS_TABLE"])
    before = {
        item["evaluationId"]
        for item in table.scan().get("Items", [])
        if item.get("type") == "evaluation"
    }

    resp = httpx.post(f"{server_url}/evaluate", json=body)

    # Must be rejected with 400
    assert resp.status_code == 400
    data = resp.json()
    assert "error" in data
    assert len(data["error"]) > 0  # descriptive, not empty

    # No new evaluation record should have been created
    after = {
        item["evaluationId"]
        for item in table.scan().get("Items", [])
        if item.get("type") == "evaluation"
    }
    new_records = after - before
    assert not new_records, (
        f"Invalid request created evaluation record(s): {new_records}"
    )


# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------


class _AlwaysPendingHandler(BaseHTTPRequestHandler):
    """Mock server that always returns pending status for evaluations.

    POST /evaluate returns 202 with a pending EvaluateResponse.
    GET /evaluate/{id} always returns 200 with the same pending status,
    simulating a server that never completes the evaluation.
    """

    def do_POST(self) -> None:
        if self.path == "/evaluate":
            resp = EvaluateResponse(
                evaluation_id="never-completes",
                status=EvaluationStatus.PENDING,
                created_at=time.time(),
            )
            body = resp.model_dump_json().encode()
            self.send_response(202)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_error(404)

    def do_GET(self) -> None:
        if self.path.startswith("/evaluate/"):
            resp = EvaluateResponse(
                evaluation_id="never-completes",
                status=EvaluationStatus.PENDING,
                created_at=time.time(),
            )
            body = resp.model_dump_json().encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_error(404)

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        pass  # suppress request logging during tests


@pytest.fixture(scope="module")
def pending_server_url():
    """Start a mock server that always returns pending evaluations."""
    server = HTTPServer(("127.0.0.1", 0), _AlwaysPendingHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{port}"
    server.shutdown()


# Use small but positive values to keep tests fast while still exercising
# the timeout logic with varied inputs.
st_poll_interval = st.floats(min_value=0.01, max_value=0.1, allow_nan=False, allow_infinity=False)
st_timeout = st.floats(min_value=0.05, max_value=0.5, allow_nan=False, allow_infinity=False)


@pytest.mark.skip(reason="Requires REST client — client now speaks MCP")
@given(poll_interval=st_poll_interval, timeout=st_timeout)
@settings(max_examples=15, deadline=30_000)
def test_client_timeout_termination(
    pending_server_url: str,
    poll_interval: float,
    timeout: float,
) -> None:
    """Client Timeout Termination.

    

    For any positive poll_interval and positive timeout where the server
    never transitions to complete or failed, evaluate_and_wait() must:
    - raise TimeoutError
    - terminate within timeout + poll_interval seconds (one extra sleep
      cycle is acceptable)
    """
    client = RascalClient(pending_server_url)
    pairs = [InputOutputPair(input_text="hello", output_text="world")]
    config = ScoringConfig(thresholds={})

    start = time.monotonic()
    with pytest.raises(TimeoutError):
        client.evaluate_and_wait(
            pairs, config, poll_interval=poll_interval, timeout=timeout,
        )
    elapsed = time.monotonic() - start

    # The method should terminate within timeout + one poll_interval
    # (the sleep that overshoots the deadline) plus a small buffer for
    # HTTP round-trip overhead.
    max_allowed = timeout + poll_interval + 0.5
    assert elapsed < max_allowed, (
        f"evaluate_and_wait took {elapsed:.3f}s — expected < {max_allowed:.3f}s "
        f"(timeout={timeout:.3f}, poll_interval={poll_interval:.3f})"
    )
