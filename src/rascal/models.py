"""Data models."""
from __future__ import annotations

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
