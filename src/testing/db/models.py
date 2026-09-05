"""Database models for MemOS evaluation system."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import sqlalchemy.dialects.sqlite as sqlite
from sqlmodel import Column, Field, SQLModel


def utcnow() -> datetime:
    """Naive UTC timestamp for DB columns.

    All timestamps are stored as UTC; SQLite columns are naive (no tzinfo),
    so we strip the offset here. The API layer (schemas._utc_iso) marks naive
    values back as UTC on output. Avoids the deprecated datetime.utcnow().
    """
    return datetime.now(UTC).replace(tzinfo=None)


# ---------- test_runs ----------
class TestRun(SQLModel, table=True):
    __tablename__: str = 'test_runs'

    id: str = Field(default_factory=lambda: uuid.uuid4().hex, primary_key=True)
    version: str = Field(index=True)  # 'v0.1' | 'v0.2' | ...
    phase: str = Field(index=True)  # 'base' | 'multi_session' | 'proactive'
    run_at: datetime = Field(default_factory=utcnow, index=True)
    total_cases: int
    passed_count: int
    pass_rate: float
    duration_seconds: float | None = None
    config_snapshot: str | None = Field(
        default=None, sa_column=Column(sqlite.TEXT)
    )  # JSON
    notes: str | None = None
    triggered_by: str = Field(default='manual')
    status: str = Field(default='completed')  # 'running' | 'completed' | 'failed'
    progress: float | None = None  # 0.0 ~ 1.0


# ---------- test_case_results ----------
class TestCaseResult(SQLModel, table=True):
    __tablename__: str = 'test_case_results'

    id: str = Field(default_factory=lambda: uuid.uuid4().hex, primary_key=True)
    run_id: str = Field(index=True, foreign_key='test_runs.id')
    case_id: str = Field(index=True)  # 'R-001' etc.
    case_name: str
    category: str = Field(index=True)
    version: str
    passed: int = Field(default=0)  # 0 | 1
    score: float | None = None
    expected_answer: str | None = Field(default=None, sa_column=Column(sqlite.TEXT))
    actual_answer: str | None = Field(default=None, sa_column=Column(sqlite.TEXT))
    retrieved_memories: str | None = Field(
        default=None, sa_column=Column(sqlite.TEXT)
    )  # JSON
    error_message: str | None = None
    latency_ms: int | None = None
    tokens_input: int | None = None  # answer LLM prompt tokens（只记录回答 LLM）
    tokens_output: int | None = None  # answer LLM completion tokens
    created_at: datetime = Field(default_factory=utcnow)


# ---------- test_case_definitions ----------
class TestCaseDefinition(SQLModel, table=True):
    __tablename__: str = 'test_case_definitions'

    case_id: str = Field(primary_key=True)
    name: str
    category: str = Field(index=True)
    version_target: str
    description: str | None = None
    query: str | None = None
    expected_answer: str | None = Field(default=None, sa_column=Column(sqlite.TEXT))
    tags: str | None = Field(default=None, sa_column=Column(sqlite.TEXT))  # JSON array
    # --- Real test-set fields (loaded from tests/test_cases/**/*.yaml) ---
    # Full conversation_histories JSON snapshot (preserves conversation boundaries,
    # timestamps and metadata; structured tables come in a later step)
    conversation_histories_raw: str | None = Field(
        default=None, sa_column=Column(sqlite.TEXT)
    )
    # LLM-as-Judge grading criteria (multi-line), distinct from expected_behavior
    evaluation_criteria: str | None = Field(default=None, sa_column=Column(sqlite.TEXT))
    # Short expected-behavior summary (kept separate from evaluation_criteria)
    expected_behavior: str | None = Field(default=None, sa_column=Column(sqlite.TEXT))
    # Relative path of the source YAML, e.g.
    # tests/test_cases/layer1/01_bank_account_setup.yaml
    source_path: str | None = None
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime | None = None
