"""HTTP application with routing, job processing, and plugin support."""
from __future__ import annotations

import json
import os
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler

from pydantic import ValidationError

from rascal.models import (
    EvaluateRequest,
    EvaluateResponse,
    EvaluationStatus,
    JobRequest,
    JobResponse,
    Summary,
    TestResult,
)
from rascal.pipeline import Pipeline
from rascal.registry import ComponentNotFoundError, Registry, Checker, Processor, DataSource, Reporter, Scorer
from rascal.storage import Storage
from rascal.analyzers.comprehend import ComprehendAnalyzer
from rascal.judges.threshold import ThresholdJudge
from rascal.scorers.pass_rate import PassRateScorer


# ── Default implementations ──────────────────────────────────────────

class _StubProcessor:
    def process(self, input_text: str, target: str) -> str:
        return f"processed: {input_text}"


class _StubChecker:
    def check(self, input_text: str, output_text: str) -> int:
        return 1


class _StubDataSource:
    def load(self, tags: list[str] | None = None) -> list[str]:
        return ["sample input 1", "sample input 2"]


class _JsonReporter:
    def report(self, summary: object) -> str:
        if hasattr(summary, "model_dump_json"):
            return summary.model_dump_json(indent=2)  # type: ignore
        return json.dumps(summary, indent=2)


class _DefaultScorer:
    def score(self, results: list, threshold: float) -> Summary:
        total = len(results)
        pass_count = sum(1 for r in results if r.score <= 3)
        pass_rate = pass_count / total if total else 0.0
        return Summary(
            total=total,
            pass_count=pass_count,
            fail_count=total - pass_count,
            pass_rate=pass_rate,
            threshold=threshold,
            passed=pass_rate >= threshold,
            results=results,
        )


# Register defaults
Registry.register_default("processor", _StubProcessor())
Registry.register_default("checker", _StubChecker())
Registry.register_default("data_source", _StubDataSource())
Registry.register_default("reporter", _JsonReporter())
Registry.register_default("scorer", _DefaultScorer())

# Pipeline defaults — Comprehend analyzer + threshold judge
Registry.register_default("analyzer.comprehend", ComprehendAnalyzer())
Registry.register_default("judge.comprehend", ThresholdJudge())
Registry.register_default("scorer", PassRateScorer())


# ── Job processing ───────────────────────────────────────────────────

def _process_job(request: JobRequest) -> JobResponse:
    """Run inputs through processor and checker, return results."""
    processor: Processor = Registry.get("processor")  # type: ignore
    checker: Checker = Registry.get("checker")  # type: ignore

    # Use provided inputs, or load from data source if empty
    inputs = request.inputs
    if not inputs and Registry.has("data_source"):
        source: DataSource = Registry.get("data_source")  # type: ignore
        inputs = source.load(request.tags or None)

    results: list[TestResult] = []
    for inp in inputs:
        output = processor.process(inp, request.target)
        score = checker.check(inp, output)
        results.append(TestResult(
            input_text=inp,
            output_text=output,
            score=score,
            checker=type(checker).__name__,
        ))

    scorer: Scorer = Registry.get("scorer")  # type: ignore
    summary = scorer.score(results, request.threshold)

    job = JobResponse(
        job_id=str(uuid.uuid4()),
        status="complete",
        summary=summary,
    )

    # Persist if storage is available
    try:
        storage = Storage()
        storage.save_job(job)
    except Exception:
        pass

    return job


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
        elif self.path.startswith("/jobs/"):
            job_id = self.path.rsplit("/", 1)[-1]
            try:
                storage = Storage()
                job = storage.get_job(job_id)
                if job:
                    self._json(200, json.loads(job.model_dump_json()))
                else:
                    self._json(404, {"error": "job not found"})
            except Exception:
                self._json(404, {"error": "job not found"})
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
                self._json(500, {"error": "Internal server error"})
        elif self.path == "/jobs":
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            request = JobRequest.model_validate_json(body)
            job = _process_job(request)
            self._json(200, json.loads(job.model_dump_json()))
        else:
            self._json(404, {"error": "not found"})

    def _json(self, status: int, data: dict | list):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
