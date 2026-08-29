"""Pydantic response / request schemas."""
from __future__ import annotations

from datetime import datetime
from typing import Any, List, Optional

from pydantic import BaseModel, ConfigDict, field_validator


# ---------- TestRun schemas ----------
class RunSummary(BaseModel):
    """Aligned with TestRun table + 2 derived fields consumed by the React Runs table."""
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
    progress: Optional[float] = None
    config_snapshot: Optional[dict[str, Any]] = None
    # --- Derived fields (filled by routes/runs.py & stats.py helpers) ---
    # Human-friendly label rendered on Runs list cards
    name: Optional[str] = None
    # total_cases - passed_count, avoids duplicated subtraction on every React component
    failed: Optional[int] = None
    # --- Backward-compat aliases (matches legacy React TS interface keys) ---
    # start_time = run_at (all table/sort code currently references start_time)
    start_time: Optional[datetime] = None
    # end_time   = run_at + duration_seconds when duration is available
    end_time: Optional[datetime] = None
    # `passed`  is an alias for passed_count (used by old TS RunSummary)
    passed: Optional[int] = None

    @field_validator("config_snapshot", mode="before")
    @classmethod
    def _parse_config(cls, v: Any) -> Any:
        if isinstance(v, dict):
            return v
        if isinstance(v, str):
            try:
                import json

                parsed = json.loads(v)
            except (ValueError, TypeError):
                return None
            if isinstance(parsed, dict):
                return parsed
        return None


class RunListResponse(BaseModel):
    runs: List[RunSummary]
    total: int


class CaseResult(BaseModel):
    """Aligned with test_case_results table; case-definition columns are JOINed in
    routes/runs.py get_run_detail() so the React run-detail card can render setup /
    query / grading criteria without an extra API round-trip."""
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
    # --- JOINed from TestCaseDefinition (only populated on run detail endpoint) ---
    query: Optional[str] = None
    description: Optional[str] = None
    tags: Optional[str] = None
    evaluation_criteria: Optional[str] = None
    expected_behavior: Optional[str] = None
    conversation_histories_raw: Optional[str] = None
    source_path: Optional[str] = None


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
    """All columns from test_case_definitions + 3 aggregate counters computed in
    routes/cases.py (keeps the React Cases table sortable by historical stats)."""
    model_config = ConfigDict(from_attributes=True)

    case_id: str
    name: str
    category: str
    version_target: str
    description: Optional[str] = None
    query: Optional[str] = None
    expected_answer: Optional[str] = None
    tags: Optional[str] = None
    # --- Real test-set detail fields (updated use-case library) ---
    conversation_histories_raw: Optional[str] = None
    evaluation_criteria: Optional[str] = None
    expected_behavior: Optional[str] = None
    source_path: Optional[str] = None
    # --- Audit timestamps ---
    created_at: datetime
    updated_at: Optional[datetime] = None
    # --- Derived aggregate counters (filled by routes/cases.py) ---
    total_runs: Optional[int] = 0
    pass_count: Optional[int] = 0
    fail_count: Optional[int] = 0


class CaseHistoryEntry(BaseModel):
    run_id: str
    version: str
    passed: bool
    score: Optional[float] = None
    run_at: datetime
    # --- Enriched for Case History page detail rendering ---
    latency_ms: Optional[int] = None
    expected_answer: Optional[str] = None
    actual_answer: Optional[str] = None
    error_message: Optional[str] = None
    retrieved_memories: Optional[str] = None


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
    """Cases with failing results in recent runs; populated by stats.py helpers."""
    case_id: str
    name: str
    category: str
    last_result: str
    fail_count: int
    pass_count: Optional[int] = 0
    last_run_id: Optional[str] = None
    last_run_version: Optional[str] = None
    last_run_time: Optional[datetime] = None
    last_passed: Optional[bool] = None
    # --- Legacy alias: most front-end components render this key as a "PhaseBadge";
    #     on a case-level card it equals the case's category (base / multi_session / proactive).
    phase: Optional[str] = None
    # --- Legacy compat aliases (filled by stats.py helpers) ---
    # case_name = name (ListCards.FailingCasesList / FailedCasesGrid use this key)
    case_name: Optional[str] = None
    # last_run_name: "<last_run_version>" (no real run.name column; fills what cards render)
    last_run_name: Optional[str] = None


class OverviewStats(BaseModel):
    total_runs: int
    total_cases: int
    latest_run: Optional[LatestRun] = None
    by_version: dict[str, ByVersionStat]
    case_categories: dict[str, int]
    failing_cases: List[FailingCase]


class TrendPoint(BaseModel):
    """Data point for the historical pass-rate trend chart."""
    run_id: str                        # backend uses UUID hex; TS type has lenient `number` which accepts strings at runtime
    name: str                          # label: "<version> · <phase>"
    version: str
    start_time: datetime               # ISO serializable
    pass_rate: float
    total_cases: int
    passed: int
    failed: int
    phase: Optional[str] = None


class CategoryStat(BaseModel):
    """Per-category (value lives in `phase` field for TS compatibility) pass-rate + latency."""
    phase: str
    total: int
    passed: int
    failed: int
    pass_rate: float
    avg_latency_ms: int


class LongestPhaseStat(BaseModel):
    phase: str
    avg_latency_ms: int


class DashboardStats(BaseModel):
    """Aggregated payload consumed by the React dashboard page (useDashboard hook)."""
    total_runs: int
    total_cases: int
    total_pass_rate: float
    recent_7_days: int
    longest_phase: Optional[LongestPhaseStat] = None
    failing_cases: List[FailingCase]
    recent_runs: List["RunSummary"]          # noqa: F821 - defined above, forward-ref string accepted by pydantic v2
    trend: List[TrendPoint]
    by_category: List[CategoryStat]
