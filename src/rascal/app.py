"""HTTP application: Analyzer → Judge → Scorer pipeline with async evaluation."""
from __future__ import annotations

import json
import os
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler

from pydantic import ValidationError

from rascal.models import EvaluateRequest, EvaluateResponse, EvaluationStatus
from rascal.pipeline import Pipeline
from rascal.registry import ComponentNotFoundError, Registry
from rascal.storage import Storage
from rascal.analyzers.comprehend import ComprehendAnalyzer
from rascal.judges.threshold import ThresholdJudge
from rascal.scorers.pass_rate import PassRateScorer


# ── Register defaults ────────────────────────────────────────────────

Registry.register_default("analyzer.comprehend", ComprehendAnalyzer())
Registry.register_default("judge.comprehend", ThresholdJudge())
Registry.register_default("scorer", PassRateScorer())

if os.environ.get("SUITES_TABLE"):
    from rascal.suite_store import DynamoDBSuiteStore
    Registry.register_default("suite_store", DynamoDBSuiteStore())


# ── Background evaluation ────────────────────────────────────────────

def _run_evaluation_async(
    evaluation_id: str,
    pairs: list,
    config: object,
) -> None:
    """Run pipeline in background, update storage with result."""
    storage = Storage()
    try:
        storage.update_evaluation_status(evaluation_id, EvaluationStatus.RUNNING)
        pipeline = Pipeline()
        result = pipeline.run(pairs, config)  # type: ignore[arg-type]
        storage.update_evaluation_status(
            evaluation_id, EvaluationStatus.COMPLETE, result=result,
        )
    except Exception as exc:
        storage.update_evaluation_status(
            evaluation_id, EvaluationStatus.FAILED, error=str(exc),
        )


# ── HTTP handler ─────────────────────────────────────────────────────

class AppHandler(BaseHTTPRequestHandler):

    def do_GET(self):
        if self.path == "/health":
            self._json(200, {"status": "ok", "plugins": Registry.keys()})
        elif self.path == "/suites":
            if not Registry.has("suite_store"):
                self._json(501, {"error": "suite storage not configured"})
                return
            store = Registry.get("suite_store")
            suites = store.list_suites()  # type: ignore[union-attr]
            self._json(200, suites)
        elif self.path.startswith("/suites/"):
            if not Registry.has("suite_store"):
                self._json(501, {"error": "suite storage not configured"})
                return
            suite_id = self.path[len("/suites/"):]
            store = Registry.get("suite_store")
            try:
                suite = store.get_suite(suite_id)  # type: ignore[union-attr]
                self._json(200, json.loads(suite.model_dump_json()))
            except ComponentNotFoundError:
                self._json(404, {"error": "suite not found"})
        elif self.path.startswith("/evaluate/"):
            evaluation_id = self.path[len("/evaluate/"):]
            try:
                storage = Storage()
                evaluation = storage.get_evaluation(evaluation_id)
                if evaluation:
                    self._json(200, json.loads(evaluation.model_dump_json()))
                else:
                    self._json(404, {"error": "evaluation not found"})
            except Exception:
                self._json(500, {"error": "Internal server error"})
        else:
            self._json(404, {"error": "not found"})

    def do_POST(self):
        if self.path == "/evaluate":
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            try:
                request = EvaluateRequest.model_validate_json(body)
            except ValidationError as exc:
                self._json(400, {"error": str(exc)})
                return
            try:
                evaluation_id = str(uuid.uuid4())
                evaluation = EvaluateResponse(
                    evaluation_id=evaluation_id,
                    status=EvaluationStatus.PENDING,
                    created_at=time.time(),
                )
                storage = Storage()
                storage.save_evaluation(evaluation, request)
                thread = threading.Thread(
                    target=_run_evaluation_async,
                    args=(evaluation_id, request.pairs, request.config),
                    daemon=True,
                )
                thread.start()
                self._json(202, json.loads(evaluation.model_dump_json()))
            except Exception:
                import traceback
                traceback.print_exc()
                self._json(500, {"error": "Internal server error"})
        else:
            self._json(404, {"error": "not found"})

    def _json(self, status: int, data: dict | list):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
