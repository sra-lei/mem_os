from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterator
import os
import tempfile
import uuid

from sqlalchemy import Engine
from sqlmodel import Session, SQLModel, create_engine

from ..logger import get_logger
from ..memory import Memory  # noqa: F401 — registers the memories table


def temp_db_path(prefix: str = "eval_mem_") -> Path:
    """Create an empty temp file usable as an evaluation-scoped memory database.

    The evaluation framework injects this path so a run never touches the
    production os_mem.db. Caller is responsible for deleting it after the run.
    """
    fd, name = tempfile.mkstemp(prefix=prefix, suffix=".db")
    os.close(fd)
    get_logger("os_mem.storage").info(f"创建评测临时记忆库: {name}")
    return Path(name)


def default_db_path() -> Path:
    """Production memory database path (project root / os_mem.db)."""
    return MemoryDatabase.default_db_path()


class MemoryDatabase:
    _logger = get_logger("os_mem.storage")
    _engines: dict[Path, Engine] = {}  # 多数据库路径支持
    db_path = Path("os_mem.db").resolve()
    def __init__(self):
        pass
    
    @classmethod
    def default_db_path(cls) -> Path:
        return Path(os.getenv("MEMOS_DB_PATH", "os_mem.db")).resolve()
    
    def get_engine(self) -> Engine:
        if self.db_path not in MemoryDatabase._engines:
            MemoryDatabase._engines[self.db_path] = create_engine(
                f"sqlite:///{self.db_path}",
                echo=False,
                connect_args={"check_same_thread": False},
            )
        return MemoryDatabase._engines[self.db_path]
    
    def init_db(self) -> None:
        engine = self.get_engine()
        SQLModel.metadata.create_all(engine)
        self._logger.info(f"Database initialized at {self.db_path}")


@contextmanager
def get_session() -> Iterator[Session]:
    """Provide a transactional scope around a series of operations."""
    db = MemoryDatabase()    
    with Session(db.get_engine()) as session:
        yield session