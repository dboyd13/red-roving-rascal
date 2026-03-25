"""DynamoDB storage layer for jobs and data."""
from __future__ import annotations

import os
import time
import json
from typing import Any

import boto3
from rascal.models import JobResponse, Summary


class Storage:
    """Stores and retrieves jobs from DynamoDB."""

    def __init__(
        self,
        jobs_table: str | None = None,
        data_table: str | None = None,
        region: str | None = None,
    ):
        self.jobs_table = jobs_table or os.environ.get("JOBS_TABLE", "rascal-jobs")
        self.data_table = data_table or os.environ.get("DATA_TABLE", "rascal-data")
        self._ddb = boto3.resource("dynamodb", region_name=region or os.environ.get("AWS_REGION"))

    def save_job(self, job: JobResponse) -> None:
        table = self._ddb.Table(self.jobs_table)
        item: dict[str, Any] = {
            "jobId": job.job_id,
            "status": job.status,
            "ttl": int(time.time()) + 86400,
        }
        if job.summary:
            item["summary"] = json.loads(job.summary.model_dump_json())
        table.put_item(Item=item)

    def get_job(self, job_id: str) -> JobResponse | None:
        table = self._ddb.Table(self.jobs_table)
        resp = table.get_item(Key={"jobId": job_id})
        item = resp.get("Item")
        if not item:
            return None
        summary = None
        if "summary" in item:
            summary = Summary.model_validate(item["summary"])
        return JobResponse(
            job_id=item["jobId"],
            status=item.get("status", "unknown"),
            summary=summary,
        )

    def save_data(self, pk: str, sk: str, data: dict) -> None:
        table = self._ddb.Table(self.data_table)
        table.put_item(Item={"pk": pk, "sk": sk, **data})

    def get_data(self, pk: str, sk: str) -> dict | None:
        table = self._ddb.Table(self.data_table)
        resp = table.get_item(Key={"pk": pk, "sk": sk})
        return resp.get("Item")
