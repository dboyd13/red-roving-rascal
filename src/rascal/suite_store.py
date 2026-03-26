"""DynamoDB-backed test suite storage."""
from __future__ import annotations

import os

import boto3

from rascal.models import TestSuite
from rascal.registry import ComponentNotFoundError


class DynamoDBSuiteStore:
    """SuiteStore backed by DynamoDB.

    Stores suites as JSON-serialized items with ``suiteId`` as the
    partition key and ``data`` containing the serialized TestSuite.
    """

    def __init__(self, table_name: str | None = None, region: str | None = None) -> None:
        self.table_name = table_name or os.environ.get("SUITES_TABLE", "rascal-suites")
        self._ddb = boto3.resource(
            "dynamodb",
            region_name=region or os.environ.get("AWS_REGION"),
        )
        self._table = self._ddb.Table(self.table_name)

    def get_suite(self, suite_id: str) -> TestSuite:
        """Retrieve a suite by ID. Raises ComponentNotFoundError if not found."""
        response = self._table.get_item(Key={"suiteId": suite_id})
        item = response.get("Item")
        if item is None:
            raise ComponentNotFoundError(f"Suite '{suite_id}' not found")
        return TestSuite.model_validate_json(item["data"])

    def list_suites(self) -> list[str]:
        """Scan for all suite IDs."""
        response = self._table.scan(ProjectionExpression="suiteId")
        return [item["suiteId"] for item in response.get("Items", [])]
