"""Unit tests for Storage evaluation methods.

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

_TABLE_NAME = "test-evaluations"
_REGION = "us-east-1"


@pytest.fixture()
def ddb_table():
    """Create a moto-mocked DynamoDB evaluations table."""
    with moto.mock_aws():
        ddb = boto3.resource("dynamodb", region_name=_REGION)
        ddb.create_table(
            TableName=_TABLE_NAME,
            KeySchema=[{"AttributeName": "evaluationId", "KeyType": "HASH"}],
            AttributeDefinitions=[
                {"AttributeName": "evaluationId", "AttributeType": "S"},
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        yield _TABLE_NAME


@pytest.fixture()
def storage(ddb_table: str) -> Storage:
    return Storage(evaluations_table=ddb_table, region=_REGION)


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


class TestSaveAndGetEvaluation:
    def test_roundtrip(self, storage: Storage) -> None:
        evaluation = _make_evaluation()
        storage.save_evaluation(evaluation, _make_request())
        result = storage.get_evaluation("eval-1")
        assert result is not None
        assert result.evaluation_id == "eval-1"
        assert result.status == EvaluationStatus.PENDING
        assert result.result is None
        assert result.error is None
        assert result.created_at == pytest.approx(evaluation.created_at)

    def test_persists_ttl(self, storage: Storage) -> None:
        evaluation = _make_evaluation()
        storage.save_evaluation(evaluation, _make_request())
        table = boto3.resource("dynamodb", region_name=_REGION).Table(_TABLE_NAME)
        item = table.get_item(Key={"evaluationId": "eval-1"})["Item"]
        expected_ttl = int(evaluation.created_at) + 86400
        assert int(item["ttl"]) == expected_ttl


class TestGetEvaluationMissing:
    def test_returns_none_for_unknown_id(self, storage: Storage) -> None:
        assert storage.get_evaluation("nonexistent") is None


class TestUpdateEvaluationStatus:
    def test_update_to_running(self, storage: Storage) -> None:
        storage.save_evaluation(_make_evaluation(), _make_request())
        storage.update_evaluation_status("eval-1", EvaluationStatus.RUNNING)
        result = storage.get_evaluation("eval-1")
        assert result is not None
        assert result.status == EvaluationStatus.RUNNING

    def test_update_to_complete_with_result(self, storage: Storage) -> None:
        storage.save_evaluation(_make_evaluation(), _make_request())
        scoring_result = ScoringResult(passed=True, per_analyzer={}, description="done")
        storage.update_evaluation_status("eval-1", EvaluationStatus.COMPLETE, result=scoring_result)
        result = storage.get_evaluation("eval-1")
        assert result is not None
        assert result.status == EvaluationStatus.COMPLETE
        assert result.result is not None
        assert result.result.passed is True

    def test_update_to_failed_with_error(self, storage: Storage) -> None:
        storage.save_evaluation(_make_evaluation(), _make_request())
        storage.update_evaluation_status("eval-1", EvaluationStatus.FAILED, error="pipeline exploded")
        result = storage.get_evaluation("eval-1")
        assert result is not None
        assert result.status == EvaluationStatus.FAILED
        assert result.error == "pipeline exploded"
