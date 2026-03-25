"""HTTP application with routing, job processing, and plugin support."""
from __future__ import annotations

import json
import os
import uuid
from http.server import BaseHTTPRequestHandler

from rascal.models import JobRequest, JobResponse, Summary, TestResult
from rascal.registry import Registry, Checker, Processor, DataSource, Reporter, Scorer
from rascal.storage import Storage


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


# ── HTTP handler ─────────────────────────────────────────────────────

class AppHandler(BaseHTTPRequestHandler):

    def do_GET(self):
        if self.path == "/health":
            self._json(200, {"status": "ok", "plugins": Registry.keys()})
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
        if self.path == "/jobs":
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            request = JobRequest.model_validate_json(body)
            job = _process_job(request)
            self._json(200, json.loads(job.model_dump_json()))
        else:
            self._json(404, {"error": "not found"})

    def _json(self, status: int, data: dict):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
