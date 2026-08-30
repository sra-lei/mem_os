from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Iterator
import os

from sqlalchemy import Engine
from sqlmodel import Session, SQLModel, create_engine

from os_mem.configs.mem_settings import memory_settings
from ..logger.logger import get_logger

# 锚定 os_mem 包目录：相对路径始终落在 src/os_mem/ 下，不随运行目录(cwd)漂移
_OS_MEM_ROOT = Path(__file__).resolve().parents[2]  # .../src/os_mem


class MemoryDatabase:
    _logger = get_logger("os_mem.storage")
    _engines: dict[Path, Engine] = {}  # 多数据库路径支持

    _db_raw = memory_settings.MEMORY_DB_PATH
    db_path = (
        _OS_MEM_ROOT / _db_raw
        if not Path(_db_raw).is_absolute()
        else Path(_db_raw)
    ).resolve()

    def __init__(self):
        pass
    
    @classmethod
    def default_db_path(cls) -> Path:
        return Path(os.getenv("MEMOS_DB_PATH", "os_mem.db")).resolve()
    
    def get_engine(self) -> Engine:
        if self.db_path not in MemoryDatabase._engines:
            # SQLite 不会自动创建父目录：确保数据库文件所在目录存在
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            MemoryDatabase._engines[self.db_path] = create_engine(
                f"sqlite:///{self.db_path}",
                echo=False,
                connect_args={"check_same_thread": False},
            )
        return MemoryDatabase._engines[self.db_path]
    
    def init_db(self) -> None:
        engine = self.get_engine()
        # 只建 os_mem 自己的表：SQLModel.metadata 是全局的，评测表
        # （testing.db.models）也注册在里面，绝不能建进记忆库
        from os_mem.entries.mem_models import ConversationMemory, Message

        SQLModel.metadata.create_all(
            engine,
            tables=[ConversationMemory.__table__, Message.__table__],
        )
        self._logger.info(f"Database initialized at {self.db_path}")


@contextmanager
def get_session() -> Iterator[Session]:
    """Provide a transactional scope around a series of operations.

    Ensures the db directory and tables exist before use (create_all is
    idempotent), so callers never hit "unable to open database file" or
    "no such table" on first write.
    """
    db = MemoryDatabase()
    db.init_db()
    with Session(db.get_engine()) as session:
        yield session