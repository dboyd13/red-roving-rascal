"""DynamoDB storage layer for evaluations."""
from __future__ import annotations

import os
import json
from decimal import Decimal
from typing import Any

import boto3
from rascal.models import (
    EvaluateRequest,
    EvaluateResponse,
    EvaluationStatus,
    ScoringResult,
)


class Storage:
    """Stores and retrieves evaluations from DynamoDB."""

    @staticmethod
    def _to_dynamo(obj: Any) -> Any:
        """Recursively convert floats to Decimal for DynamoDB compatibility."""
        if isinstance(obj, float):
            return Decimal(str(obj))
        if isinstance(obj, dict):
            return {k: Storage._to_dynamo(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [Storage._to_dynamo(v) for v in obj]
        return obj

    def __init__(
        self,
        evaluations_table: str | None = None,
        region: str | None = None,
    ) -> None:
        self.evaluations_table = evaluations_table or os.environ.get("EVALUATIONS_TABLE", "rascal-evaluations")
        self._ddb = boto3.resource("dynamodb", region_name=region or os.environ.get("AWS_REGION"))

    def save_evaluation(self, evaluation: EvaluateResponse, request: EvaluateRequest) -> None:
        """Persist an evaluation record."""
        table = self._ddb.Table(self.evaluations_table)
        item: dict[str, Any] = {
            "evaluationId": evaluation.evaluation_id,
            "status": evaluation.status.value,
            "request": self._to_dynamo(json.loads(request.model_dump_json())),
            "created_at": Decimal(str(evaluation.created_at)),
            "ttl": int(evaluation.created_at) + 86400,
        }
        if evaluation.result is not None:
            item["result"] = self._to_dynamo(json.loads(evaluation.result.model_dump_json()))
        if evaluation.error is not None:
            item["error"] = evaluation.error
        table.put_item(Item=item)

    def get_evaluation(self, evaluation_id: str) -> EvaluateResponse | None:
        """Retrieve an evaluation record by ID."""
        table = self._ddb.Table(self.evaluations_table)
        resp = table.get_item(Key={"evaluationId": evaluation_id})
        item = resp.get("Item")
        if not item:
            return None
        result = None
        if "result" in item:
            result = ScoringResult.model_validate(item["result"])
        return EvaluateResponse(
            evaluation_id=item["evaluationId"],
            status=EvaluationStatus(item["status"]),
            result=result,
            error=item.get("error"),
            created_at=float(item["created_at"]),
        )

    def update_evaluation_status(
        self,
        evaluation_id: str,
        status: EvaluationStatus,
        result: ScoringResult | None = None,
        error: str | None = None,
    ) -> None:
        """Atomically update the status (and optionally result/error) of an evaluation."""
        table = self._ddb.Table(self.evaluations_table)
        expr_names = {"#s": "status"}
        expr_values: dict[str, Any] = {":s": status.value}
        update_parts = ["#s = :s"]

        if result is not None:
            update_parts.append("#r = :r")
            expr_names["#r"] = "result"
            expr_values[":r"] = self._to_dynamo(json.loads(result.model_dump_json()))
        if error is not None:
            update_parts.append("#e = :e")
            expr_names["#e"] = "error"
            expr_values[":e"] = error

        table.update_item(
            Key={"evaluationId": evaluation_id},
            UpdateExpression="SET " + ", ".join(update_parts),
            ExpressionAttributeNames=expr_names,
            ExpressionAttributeValues=expr_values,
        )
