"""Database models for MemOS evaluation system."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlmodel import Field, SQLModel, Column
import sqlalchemy.dialects.sqlite as sqlite


# ---------- test_runs ----------
class TestRun(SQLModel, table=True):
    __tablename__ = "test_runs"

    id: str = Field(default_factory=lambda: uuid.uuid4().hex, primary_key=True)
    version: str = Field(index=True)  # 'v0.1' | 'v0.2' | ...
    phase: str = Field(index=True)    # 'base' | 'multi_session' | 'proactive'
    run_at: datetime = Field(default_factory=datetime.utcnow, index=True)
    total_cases: int
    passed_count: int
    pass_rate: float
    duration_seconds: Optional[float] = None
    config_snapshot: Optional[str] = Field(default=None, sa_column=Column(sqlite.TEXT))  # JSON
    notes: Optional[str] = None
    triggered_by: str = Field(default="manual")
    status: str = Field(default="completed")  # 'running' | 'completed' | 'failed'
    progress: Optional[float] = None  # 0.0 ~ 1.0


# ---------- test_case_results ----------
class TestCaseResult(SQLModel, table=True):
    __tablename__ = "test_case_results"

    id: str = Field(default_factory=lambda: uuid.uuid4().hex, primary_key=True)
    run_id: str = Field(index=True, foreign_key="test_runs.id")
    case_id: str = Field(index=True)  # 'R-001' etc.
    case_name: str
    category: str = Field(index=True)
    version: str
    passed: int = Field(default=0)  # 0 | 1
    score: Optional[float] = None
    expected_answer: Optional[str] = Field(default=None, sa_column=Column(sqlite.TEXT))
    actual_answer: Optional[str] = Field(default=None, sa_column=Column(sqlite.TEXT))
    retrieved_memories: Optional[str] = Field(default=None, sa_column=Column(sqlite.TEXT))  # JSON
    error_message: Optional[str] = None
    latency_ms: Optional[int] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)


# ---------- test_case_definitions ----------
class TestCaseDefinition(SQLModel, table=True):
    __tablename__ = "test_case_definitions"

    case_id: str = Field(primary_key=True)
    name: str
    category: str = Field(index=True)
    version_target: str
    description: Optional[str] = None
    query: Optional[str] = None
    expected_answer: Optional[str] = Field(default=None, sa_column=Column(sqlite.TEXT))
    tags: Optional[str] = Field(default=None, sa_column=Column(sqlite.TEXT))  # JSON array
    # --- Real test-set fields (loaded from tests/test_cases/**/*.yaml) ---
    # Full conversation_histories JSON snapshot (preserves conversation boundaries,
    # timestamps and metadata; structured tables come in a later step)
    conversation_histories_raw: Optional[str] = Field(default=None, sa_column=Column(sqlite.TEXT))
    # LLM-as-Judge grading criteria (multi-line), distinct from expected_behavior
    evaluation_criteria: Optional[str] = Field(default=None, sa_column=Column(sqlite.TEXT))
    # Short expected-behavior summary (kept separate from evaluation_criteria)
    expected_behavior: Optional[str] = Field(default=None, sa_column=Column(sqlite.TEXT))
    # Relative path of the source YAML, e.g. tests/test_cases/layer1/01_bank_account_setup.yaml
    source_path: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: Optional[datetime] = None
