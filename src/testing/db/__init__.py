"""Database connection management."""
from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from sqlmodel import Session, SQLModel, create_engine

from . import models  # noqa: F401  - ensures models registered

# 锚定 testing 包目录：评测库固定落在 src/testing/data/memos.db，不随 cwd 漂移
_DB_PATH = Path(__file__).resolve().parents[1] / "data" / "memos.db"
_DB_URL = f"sqlite:///{_DB_PATH}"

_engine = None


def get_engine():
    global _engine
    if _engine is None:
        # SQLite 不会自动创建父目录
        _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        _engine = create_engine(
            _DB_URL,
            echo=False,
            connect_args={"check_same_thread": False},
        )
    return _engine


def init_db() -> None:
    """Create evaluation tables if they don't exist.

    NOTE: SQLModel uses one global metadata for every table class; other
    packages (os_mem) register their own tables there too. We therefore build
    ONLY the evaluation tables here, so e.g. memory tables never get created
    inside memos.db (and vice versa in os_mem's own init_db).
    """
    engine = get_engine()
    SQLModel.metadata.create_all(
        engine,
        tables=[
            models.TestRun.__table__,
            models.TestCaseResult.__table__,
            models.TestCaseDefinition.__table__,
        ],
    )


@contextmanager
def get_session() -> Iterator[Session]:
    """Provide a transactional scope around a series of operations."""
    engine = get_engine()
    with Session(engine) as session:
        yield session
