"""Data models."""
from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class TestInput(BaseModel):
    """A single test input."""
    text: str
    category: str = "default"
    metadata: dict = Field(default_factory=dict)


class TestResult(BaseModel):
    """Result of processing a single input."""
    input_text: str
    output_text: str
    score: int = Field(ge=1, le=5)
    detail: str = ""
    checker: str = ""


class Summary(BaseModel):
    """Aggregated results."""
    total: int
    pass_count: int
    fail_count: int
    pass_rate: float = Field(ge=0.0, le=1.0)
    threshold: float = Field(ge=0.0, le=1.0)
    passed: bool
    results: list[TestResult] = Field(default_factory=list)


class JobRequest(BaseModel):
    """Request to run a job."""
    inputs: list[str]
    target: str
    threshold: float = 0.8
    tags: list[str] = Field(default_factory=list)


class JobResponse(BaseModel):
    """Response from a job."""
    job_id: str
    status: str = "pending"
    summary: Summary | None = None


# --- Pipeline Models ---


class ThresholdDirection(str, Enum):
    """Whether a Judge passes when score is above or below the threshold."""

    ABOVE = "above"
    BELOW = "below"


class AnalysisResult(BaseModel):
    """Output of an Analyzer."""

    analyzer_name: str
    raw_score: float
    entities: list[str] = Field(default_factory=list)
    metadata: dict = Field(default_factory=dict)


class Verdict(BaseModel):
    """Output of a Judge."""

    passed: bool
    score: float
    threshold: float
    analyzer_name: str
    violations: set[str] = Field(default_factory=set)


class ScoringConfig(BaseModel):
    """Configuration for the Scorer."""

    thresholds: dict[str, float] = Field(default_factory=dict)


class PerAnalyzerResult(BaseModel):
    """Per-analyzer breakdown within a ScoringResult."""

    pass_rate: float = Field(ge=0.0, le=1.0)
    threshold: float = Field(ge=0.0, le=1.0)


class ScoringResult(BaseModel):
    """Output of a Scorer."""

    passed: bool
    per_analyzer: dict[str, PerAnalyzerResult] = Field(default_factory=dict)
    description: str = ""


class InputOutputPair(BaseModel):
    """A pairing of input text with the output produced by the client's application."""

    input_text: str
    output_text: str


class TestCase(BaseModel):
    """A single test case within a TestSuite."""

    input_text: str
    category: str = "default"
    metadata: dict = Field(default_factory=dict)


class TestSuite(BaseModel):
    """A named collection of test cases."""

    suite_id: str
    name: str
    test_cases: list[TestCase] = Field(default_factory=list)
    metadata: dict = Field(default_factory=dict)


class EvaluateRequest(BaseModel):
    """Request body for POST /evaluate."""

    pairs: list[InputOutputPair]
    config: ScoringConfig = Field(
        default_factory=lambda: ScoringConfig(thresholds={})
    )


class EvaluationStatus(str, Enum):
    """Lifecycle states of an async evaluation."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETE = "complete"
    FAILED = "failed"


class EvaluateResponse(BaseModel):
    """Response for async evaluation endpoints."""

    evaluation_id: str
    status: EvaluationStatus
    result: ScoringResult | None = None
    error: str | None = None
    created_at: float
