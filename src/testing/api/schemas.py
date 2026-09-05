"""Pydantic response / request schemas."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, PlainSerializer, field_validator


def _utc_iso(dt: datetime) -> str:
    """Serialize a datetime to ISO-8601 with an explicit UTC offset.

    All timestamps in the DB are written as UTC (via datetime.now(timezone.utc)
    and stored naive by SQLite). Pydantic's default JSON encoding of a naive
    datetime omits the timezone suffix ("2026-08-30T08:00:00"), and JavaScript
    `new Date()` parses offset-less date-time strings as LOCAL time — which
    shifts every displayed time by the local offset (8h in Asia/Shanghai).
    Marking naive values as UTC and emitting "+00:00" makes the chain correct:
    store UTC -> emit UTC-with-offset -> JS converts to browser local time.
    """
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.isoformat()


# JSON-serialization-only: in-process model_dump() keeps real datetimes
# (arithmetic in routes still works); FastAPI responses get the offset string.
UtcDateTime = Annotated[
    datetime,
    PlainSerializer(_utc_iso, return_type=str, when_used='json'),
]


# ---------- TestRun schemas ----------
class RunSummary(BaseModel):
    """Aligned with TestRun table + 2 derived fields consumed by the React
    Runs table.
    """

    model_config = ConfigDict(from_attributes=True)

    id: str
    version: str
    phase: str
    run_at: UtcDateTime
    total_cases: int
    passed_count: int
    pass_rate: float
    duration_seconds: float | None = None
    notes: str | None = None
    triggered_by: str | None = None
    status: str | None = None
    progress: float | None = None
    config_snapshot: dict[str, Any] | None = None
    # --- Aggregated token usage (sum of per-case answer LLM tokens) ---
    tokens_input: int | None = None
    tokens_output: int | None = None
    # --- Derived fields (filled by routes/runs.py & stats.py helpers) ---
    # Human-friendly label rendered on Runs list cards
    name: str | None = None
    # total_cases - passed_count, avoids duplicated subtraction on every React component
    failed: int | None = None
    # --- Backward-compat aliases (matches legacy React TS interface keys) ---
    # start_time = run_at (all table/sort code currently references start_time)
    start_time: UtcDateTime | None = None
    # end_time   = run_at + duration_seconds when duration is available
    end_time: UtcDateTime | None = None
    # `passed`  is an alias for passed_count (used by old TS RunSummary)
    passed: int | None = None

    @field_validator('config_snapshot', mode='before')
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
    runs: list[RunSummary]
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
    score: float | None = None
    expected_answer: str | None = None
    actual_answer: str | None = None
    retrieved_memories: str | None = None
    error_message: str | None = None
    latency_ms: int | None = None
    tokens_input: int | None = None
    tokens_output: int | None = None
    created_at: UtcDateTime
    # --- JOINed from TestCaseDefinition (only populated on run detail endpoint) ---
    query: str | None = None
    description: str | None = None
    tags: str | None = None
    evaluation_criteria: str | None = None
    expected_behavior: str | None = None
    conversation_histories_raw: str | None = None
    source_path: str | None = None


class RunDetailResponse(RunSummary):
    results: list[CaseResult]


class RunProgress(BaseModel):
    status: str
    completed: int
    total: int
    percent: float


class CreateRunRequest(BaseModel):
    version: str
    phase: str
    config: dict[str, Any] | None = None
    notes: str | None = None


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
    description: str | None = None
    query: str | None = None
    expected_answer: str | None = None
    tags: str | None = None
    # --- Real test-set detail fields (updated use-case library) ---
    conversation_histories_raw: str | None = None
    evaluation_criteria: str | None = None
    expected_behavior: str | None = None
    source_path: str | None = None
    # --- Audit timestamps ---
    created_at: UtcDateTime
    updated_at: UtcDateTime | None = None
    # --- Derived aggregate counters (filled by routes/cases.py) ---
    total_runs: int | None = 0
    pass_count: int | None = 0
    fail_count: int | None = 0


class CaseHistoryEntry(BaseModel):
    run_id: str
    version: str
    passed: bool
    score: float | None = None
    run_at: UtcDateTime
    # --- Enriched for Case History page detail rendering ---
    latency_ms: int | None = None
    expected_answer: str | None = None
    actual_answer: str | None = None
    error_message: str | None = None
    retrieved_memories: str | None = None


class CaseHistoryResponse(BaseModel):
    case_id: str
    name: str
    history: list[CaseHistoryEntry]


# ---------- Stats schemas ----------
class LatestRun(BaseModel):
    version: str
    phase: str
    pass_rate: float
    run_at: UtcDateTime


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
    pass_count: int | None = 0
    last_run_id: str | None = None
    last_run_version: str | None = None
    last_run_time: UtcDateTime | None = None
    last_passed: bool | None = None
    # --- Legacy alias: most front-end components render this key as a "PhaseBadge";
    #     on a case-level card it equals the case's category
    #     (base / multi_session / proactive).
    phase: str | None = None
    # --- Legacy compat aliases (filled by stats.py helpers) ---
    # case_name = name (ListCards.FailingCasesList / FailedCasesGrid use this key)
    case_name: str | None = None
    # last_run_name: "<last_run_version>" (no real run.name column; fills
    # what cards render)
    last_run_name: str | None = None


class OverviewStats(BaseModel):
    total_runs: int
    total_cases: int
    latest_run: LatestRun | None = None
    by_version: dict[str, ByVersionStat]
    case_categories: dict[str, int]
    failing_cases: list[FailingCase]


class TrendPoint(BaseModel):
    """Data point for the historical pass-rate trend chart."""

    # backend uses UUID hex; TS type has lenient `number` which accepts
    # strings at runtime
    run_id: str
    name: str  # label: "<version> · <phase>"
    version: str
    start_time: UtcDateTime  # ISO serializable
    pass_rate: float
    total_cases: int
    passed: int
    failed: int
    phase: str | None = None


class CategoryStat(BaseModel):
    """Per-category (value lives in `phase` field for TS compatibility)
    pass-rate + latency.
    """

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
    longest_phase: LongestPhaseStat | None = None
    failing_cases: list[FailingCase]
    recent_runs: list[RunSummary]  # noqa: F821 - defined above, forward-ref string accepted by pydantic v2
    trend: list[TrendPoint]
    by_category: list[CategoryStat]
