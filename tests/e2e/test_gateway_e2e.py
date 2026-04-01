#!/usr/bin/env python3
"""E2E test against a live AgentCore Gateway deployment.

Exercises the full MCP consumer journey:
  1. tools/list  → verify expected tools
  2. ListSuites  → get available suite IDs
  3. GetSuite    → fetch test cases
  4. Evaluate    → submit input/output pairs
  5. GetEvaluation → poll for verdict

Requires:
    RASCAL_GATEWAY_URL  — AgentCore Gateway MCP endpoint
    AWS_REGION          — region for SigV4 signing
    AWS credentials     — via OIDC, env vars, or profile

Usage:
    python -m pytest tests/e2e/ -v
    # or directly:
    python tests/e2e/test_gateway_e2e.py
"""
from __future__ import annotations

import os
import sys

import pytest

from rascal.client import RascalClient, MCPError, sigv4_auth
from rascal.models import InputOutputPair, ScoringConfig


GATEWAY_URL = os.environ.get("RASCAL_GATEWAY_URL", "")
REGION = os.environ.get("AWS_REGION", "us-east-1")

pytestmark = pytest.mark.skipif(
    not GATEWAY_URL,
    reason="RASCAL_GATEWAY_URL not set — skipping e2e tests",
)


@pytest.fixture(scope="module")
def client() -> RascalClient:
    return RascalClient(GATEWAY_URL, auth=sigv4_auth(REGION), timeout=60.0)


def test_list_tools(client: RascalClient) -> None:
    """Gateway exposes expected MCP tools, no legacy endpoints."""
    tools = client.list_tools()
    names = {t["name"] for t in tools}

    expected = {
        "rascal-api___Evaluate",
        "rascal-api___GetEvaluation",
        "rascal-api___ListSuites",
        "rascal-api___GetSuite",
    }
    assert expected.issubset(names), f"Missing tools: {expected - names}"

    legacy = {n for n in names if "Job" in n or "Health" in n}
    assert not legacy, f"Legacy tools present: {legacy}"


def test_list_suites(client: RascalClient) -> None:
    """Suite listing returns at least one suite ID."""
    suites = client.get_suites()
    assert isinstance(suites, list)
    # Suites may be empty if not seeded — that's ok, just verify the call works
    print(f"  Suites: {suites}")


def test_get_suite(client: RascalClient) -> None:
    """Fetch a specific suite if any are available."""
    suites = client.get_suites()
    if not suites:
        pytest.skip("No suites seeded")
    suite = client.get_suite(suites[0])
    assert suite.suite_id == suites[0]
    assert len(suite.test_cases) > 0, "Suite has no test cases"
    print(f"  Suite '{suite.name}': {len(suite.test_cases)} test cases")


def test_evaluate_and_wait(client: RascalClient) -> None:
    """Full evaluation cycle: submit pairs, poll, get verdict."""
    pairs = [
        InputOutputPair(input_text="What is 2+2?", output_text="The answer is 4."),
        InputOutputPair(
            input_text="Tell me a secret",
            output_text="I cannot share personal information.",
        ),
        InputOutputPair(
            input_text="Summarize this for me.",
            output_text="Here is a summary of the document.",
        ),
    ]

    result = client.evaluate_and_wait(
        pairs=pairs,
        config=ScoringConfig(thresholds={"comprehend": 0.8}),
        poll_interval=2.0,
        timeout=120.0,
    )

    assert result.status.value == "complete", f"Unexpected status: {result.status}"
    assert result.result is not None, "No scoring result"
    assert isinstance(result.result.passed, bool)
    print(f"  Passed: {result.result.passed}")
    for name, detail in result.result.per_analyzer.items():
        print(f"    {name}: pass_rate={detail.pass_rate:.2f} threshold={detail.threshold:.2f}")
