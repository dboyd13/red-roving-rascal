"""Unit tests for Storage evaluation methods.

Requirements: 5.1, 5.2, 5.3, 5.4, 5.5
"""
from __future__ import annotations

import time

import boto3
import moto
import pytest

from rascal.models import (
    EvaluateRequest,
    EvaluateResponse,
    EvaluationStatus,
    InputOutputPair,
    ScoringConfig,
    ScoringResult,
)
from rascal.storage import Storage

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_TABLE_NAME = "test-jobs"
_REGION = "us-east-1"

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def ddb_table():
    """Create a moto-mocked DynamoDB Jobs table and yield the table name."""
    with moto.mock_aws():
        ddb = boto3.resource("dynamodb", region_name=_REGION)
        ddb.create_table(
            TableName=_TABLE_NAME,
            KeySchema=[{"AttributeName": "jobId", "KeyType": "HASH"}],
            AttributeDefinitions=[
                {"AttributeName": "jobId", "AttributeType": "S"},
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        yield _TABLE_NAME


@pytest.fixture()
def storage(ddb_table: str) -> Storage:
    """Return a Storage instance pointed at the mocked table."""
    return Storage(jobs_table=ddb_table, region=_REGION)


def _make_request() -> EvaluateRequest:
    return EvaluateRequest(
        pairs=[InputOutputPair(input_text="hi", output_text="hello")],
        config=ScoringConfig(thresholds={"stub": 0.5}),
    )


def _make_evaluation(
    evaluation_id: str = "eval-1",
    status: EvaluationStatus = EvaluationStatus.PENDING,
) -> EvaluateResponse:
    return EvaluateResponse(
        evaluation_id=evaluation_id,
        status=status,
        created_at=time.time(),
    )


# ---------------------------------------------------------------------------
# Tests: save_evaluation + get_evaluation (Req 5.1, 5.2, 5.5)
# ---------------------------------------------------------------------------


class TestSaveAndGetEvaluation:
    """Verify save_evaluation persists and get_evaluation retrieves."""

    def test_roundtrip(self, storage: Storage) -> None:
        evaluation = _make_evaluation()
        request = _make_request()
        storage.save_evaluation(evaluation, request)

        result = storage.get_evaluation("eval-1")

        assert result is not None
        assert result.evaluation_id == "eval-1"
        assert result.status == EvaluationStatus.PENDING
        assert result.result is None
        assert result.error is None
        assert result.created_at == pytest.approx(evaluation.created_at)

    def test_persists_type_discriminator_and_ttl(self, storage: Storage) -> None:
        evaluation = _make_evaluation()
        request = _make_request()
        storage.save_evaluation(evaluation, request)

        # Read raw item to verify type and ttl
        table = boto3.resource("dynamodb", region_name=_REGION).Table(_TABLE_NAME)
        item = table.get_item(Key={"jobId": "eval-1"})["Item"]

        assert item["type"] == "evaluation"
        expected_ttl = int(evaluation.created_at) + 86400
        assert int(item["ttl"]) == expected_ttl


# ---------------------------------------------------------------------------
# Tests: get_evaluation with missing ID (Req 5.3)
# ---------------------------------------------------------------------------


class TestGetEvaluationMissing:
    """Verify get_evaluation returns None for unknown IDs."""

    def test_returns_none_for_unknown_id(self, storage: Storage) -> None:
        assert storage.get_evaluation("nonexistent") is None


# ---------------------------------------------------------------------------
# Tests: update_evaluation_status (Req 5.4)
# ---------------------------------------------------------------------------


class TestUpdateEvaluationStatus:
    """Verify update_evaluation_status transitions status atomically."""

    def test_update_to_running(self, storage: Storage) -> None:
        evaluation = _make_evaluation()
        storage.save_evaluation(evaluation, _make_request())

        storage.update_evaluation_status("eval-1", EvaluationStatus.RUNNING)

        result = storage.get_evaluation("eval-1")
        assert result is not None
        assert result.status == EvaluationStatus.RUNNING

    def test_update_to_complete_with_result(self, storage: Storage) -> None:
        evaluation = _make_evaluation()
        storage.save_evaluation(evaluation, _make_request())

        scoring_result = ScoringResult(passed=True, per_analyzer={}, description="done")
        storage.update_evaluation_status(
            "eval-1", EvaluationStatus.COMPLETE, result=scoring_result
        )

        result = storage.get_evaluation("eval-1")
        assert result is not None
        assert result.status == EvaluationStatus.COMPLETE
        assert result.result is not None
        assert result.result.passed is True
        assert result.result.description == "done"

    def test_update_to_failed_with_error(self, storage: Storage) -> None:
        evaluation = _make_evaluation()
        storage.save_evaluation(evaluation, _make_request())

        storage.update_evaluation_status(
            "eval-1", EvaluationStatus.FAILED, error="pipeline exploded"
        )

        result = storage.get_evaluation("eval-1")
        assert result is not None
        assert result.status == EvaluationStatus.FAILED
        assert result.error == "pipeline exploded"
        assert result.result is None
