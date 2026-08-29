"""Pydantic response / request schemas."""
from __future__ import annotations

from datetime import datetime
from typing import Any, List, Optional

from pydantic import BaseModel, ConfigDict


# ---------- TestRun schemas ----------
class RunSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    version: str
    phase: str
    run_at: datetime
    total_cases: int
    passed_count: int
    pass_rate: float
    duration_seconds: Optional[float] = None
    notes: Optional[str] = None
    triggered_by: Optional[str] = None
    status: Optional[str] = None


class RunListResponse(BaseModel):
    runs: List[RunSummary]
    total: int


class CaseResult(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    run_id: str
    case_id: str
    case_name: str
    category: str
    version: str
    passed: int
    score: Optional[float] = None
    expected_answer: Optional[str] = None
    actual_answer: Optional[str] = None
    retrieved_memories: Optional[str] = None
    error_message: Optional[str] = None
    latency_ms: Optional[int] = None
    created_at: datetime


class RunDetailResponse(RunSummary):
    results: List[CaseResult]


class RunProgress(BaseModel):
    status: str
    completed: int
    total: int
    percent: float


class CreateRunRequest(BaseModel):
    version: str
    phase: str
    config: Optional[dict[str, Any]] = None
    notes: Optional[str] = None


class CreateRunResponse(BaseModel):
    run_id: str
    status: str


# ---------- Case Definition schemas ----------
class CaseDefinition(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    case_id: str
    name: str
    category: str
    version_target: str
    description: Optional[str] = None
    query: Optional[str] = None
    expected_answer: Optional[str] = None
    tags: Optional[str] = None
    created_at: datetime
    updated_at: Optional[datetime] = None


class CaseHistoryEntry(BaseModel):
    run_id: str
    version: str
    passed: bool
    score: Optional[float] = None
    run_at: datetime


class CaseHistoryResponse(BaseModel):
    case_id: str
    name: str
    history: List[CaseHistoryEntry]


# ---------- Stats schemas ----------
class LatestRun(BaseModel):
    version: str
    phase: str
    pass_rate: float
    run_at: datetime


class ByVersionStat(BaseModel):
    runs: int
    avg_pass_rate: float


class FailingCase(BaseModel):
    case_id: str
    name: str
    last_result: str
    fail_count: int


class OverviewStats(BaseModel):
    total_runs: int
    total_cases: int
    latest_run: Optional[LatestRun] = None
    by_version: dict[str, ByVersionStat]
    case_categories: dict[str, int]
    failing_cases: List[FailingCase]
