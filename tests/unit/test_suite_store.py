"""Unit tests for DynamoDBSuiteStore.

Requirements: 8.1, 8.2, 8.7, 8.8
"""
from __future__ import annotations

import json
import os

import boto3
import moto
import pytest

from rascal.models import TestCase, TestSuite
from rascal.registry import ComponentNotFoundError
from rascal.suite_store import DynamoDBSuiteStore

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_TABLE_NAME = "test-suites"
_REGION = "us-east-1"

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def ddb_table():
    """Create a moto-mocked DynamoDB table and yield the table name."""
    with moto.mock_aws():
        ddb = boto3.resource("dynamodb", region_name=_REGION)
        ddb.create_table(
            TableName=_TABLE_NAME,
            KeySchema=[{"AttributeName": "suiteId", "KeyType": "HASH"}],
            AttributeDefinitions=[
                {"AttributeName": "suiteId", "AttributeType": "S"},
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        yield _TABLE_NAME


@pytest.fixture()
def store(ddb_table: str) -> DynamoDBSuiteStore:
    """Return a DynamoDBSuiteStore pointed at the mocked table."""
    return DynamoDBSuiteStore(table_name=ddb_table, region=_REGION)


def _put_suite(suite: TestSuite) -> None:
    """Write a TestSuite item directly into the mocked DynamoDB table."""
    ddb = boto3.resource("dynamodb", region_name=_REGION)
    table = ddb.Table(_TABLE_NAME)
    table.put_item(Item={"suiteId": suite.suite_id, "data": suite.model_dump_json()})


# ---------------------------------------------------------------------------
# Tests: get_suite with known suite (Req 8.1, 8.2)
# ---------------------------------------------------------------------------


class TestGetSuiteKnown:
    """Verify get_suite returns the correct TestSuite for a known ID."""

    def test_returns_stored_suite(self, store: DynamoDBSuiteStore) -> None:
        suite = TestSuite(
            suite_id="s-1",
            name="My Suite",
            test_cases=[TestCase(input_text="hello")],
            metadata={"env": "test"},
        )
        _put_suite(suite)

        result = store.get_suite("s-1")

        assert result == suite
        assert result.suite_id == "s-1"
        assert result.name == "My Suite"
        assert len(result.test_cases) == 1
        assert result.test_cases[0].input_text == "hello"

    def test_returns_suite_with_multiple_test_cases(self, store: DynamoDBSuiteStore) -> None:
        suite = TestSuite(
            suite_id="s-multi",
            name="Multi",
            test_cases=[
                TestCase(input_text="a", category="cat1"),
                TestCase(input_text="b", category="cat2"),
            ],
        )
        _put_suite(suite)

        result = store.get_suite("s-multi")

        assert len(result.test_cases) == 2
        assert result.test_cases[0].category == "cat1"
        assert result.test_cases[1].category == "cat2"


# ---------------------------------------------------------------------------
# Tests: get_suite with unknown ID (Req 8.8)
# ---------------------------------------------------------------------------


class TestGetSuiteUnknown:
    """Verify get_suite raises ComponentNotFoundError for missing suites."""

    def test_raises_component_not_found_error(self, store: DynamoDBSuiteStore) -> None:
        with pytest.raises(ComponentNotFoundError, match="not found"):
            store.get_suite("nonexistent-id")


# ---------------------------------------------------------------------------
# Tests: list_suites (Req 8.1, 8.2)
# ---------------------------------------------------------------------------


class TestListSuites:
    """Verify list_suites returns all stored suite IDs."""

    def test_returns_empty_list_when_no_suites(self, store: DynamoDBSuiteStore) -> None:
        assert store.list_suites() == []

    def test_returns_all_stored_ids(self, store: DynamoDBSuiteStore) -> None:
        for sid in ["alpha", "beta", "gamma"]:
            _put_suite(TestSuite(suite_id=sid, name=f"Suite {sid}"))

        ids = store.list_suites()

        assert sorted(ids) == ["alpha", "beta", "gamma"]

    def test_returns_single_id(self, store: DynamoDBSuiteStore) -> None:
        _put_suite(TestSuite(suite_id="only-one", name="Solo"))

        assert store.list_suites() == ["only-one"]


# ---------------------------------------------------------------------------
# Tests: default table name from env var (Req 8.7)
# ---------------------------------------------------------------------------


class TestDefaultTableName:
    """Verify DynamoDBSuiteStore reads SUITES_TABLE env var."""

    def test_uses_env_var_when_no_table_name_given(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SUITES_TABLE", "my-custom-table")
        monkeypatch.setenv("AWS_REGION", _REGION)

        with moto.mock_aws():
            store = DynamoDBSuiteStore()
            assert store.table_name == "my-custom-table"

    def test_falls_back_to_default_name(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("SUITES_TABLE", raising=False)
        monkeypatch.setenv("AWS_REGION", _REGION)

        with moto.mock_aws():
            store = DynamoDBSuiteStore()
            assert store.table_name == "rascal-suites"

    def test_explicit_table_name_overrides_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SUITES_TABLE", "env-table")
        monkeypatch.setenv("AWS_REGION", _REGION)

        with moto.mock_aws():
            store = DynamoDBSuiteStore(table_name="explicit-table")
            assert store.table_name == "explicit-table"
