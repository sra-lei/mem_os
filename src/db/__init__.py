"""Database connection management."""
from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from sqlmodel import Session, SQLModel, create_engine

from . import models  # noqa: F401  - ensures models registered

_DB_PATH = Path(__file__).resolve().parents[2] / "memos.db"
_DB_URL = f"sqlite:///{_DB_PATH}"

_engine = None


def get_engine():
    global _engine
    if _engine is None:
        _engine = create_engine(
            _DB_URL,
            echo=False,
            connect_args={"check_same_thread": False},
        )
    return _engine


def init_db() -> None:
    """Create tables if they don't exist."""
    engine = get_engine()
    SQLModel.metadata.create_all(engine)


@contextmanager
def get_session() -> Iterator[Session]:
    """Provide a transactional scope around a series of operations."""
    engine = get_engine()
    with Session(engine) as session:
        yield session
