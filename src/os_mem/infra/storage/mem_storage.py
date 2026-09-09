from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Iterator
import os

from sqlalchemy import Engine, text
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

    def __init__(self) -> None:
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
        from os_mem.entries.mem_models import (
            ConversationMeta,
            FactCategory,
            Message,
            StructuredMemory,
        )

        SQLModel.metadata.create_all(
            engine,
            tables=[
                Message.__table__,
                StructuredMemory.__table__,
                ConversationMeta.__table__,
                FactCategory.__table__,
            ],
        )
        # 老库迁移：conv_memories 已被 conv_meta 取代（方案1 合并），数据不需迁移 → 删表
        with engine.begin() as conn:
            conn.execute(text("DROP TABLE IF EXISTS conv_memories"))
        # 老库迁移：conv_messages 补 seq/previous_content 列并回填 + 建唯一索引
        # （create_all 只建新表，不会给已存在表加列，故需显式 ALTER）
        self._migrate_conv_messages(engine)
        self._migrate_struct_memories_d4(engine)
        self._seed_fact_category(engine)
        self._logger.info(f"Database initialized at {self.db_path}")

    @staticmethod
    def _seed_fact_category(engine: Engine) -> None:
        """幂等 seed：fact_category 空表才灌入内置 10 类双语种子（不覆盖人工演进）。"""
        from sqlmodel import Session, func, select

        from os_mem.entries.mem_models import FactCategory
        from os_mem.vocab import CATEGORY_SEED

        with Session(engine) as session:
            exists = session.exec(
                select(func.count()).select_from(FactCategory)
            ).one()
            if exists:
                return
            for item in CATEGORY_SEED:
                session.add(FactCategory(**item))
            session.commit()

    @staticmethod
    def _migrate_conv_messages(engine: Engine) -> None:
        """幂等迁移：让既有 conv_messages 具备消息冲突键 (user_id, source_session_id, seq)。

        1) 缺列则 ALTER 补 seq / previous_content（带默认值）；
        2) 旧数据 seq 全为 0：按 (user_id, source_session_id, rowid) 序回填 0..n-1；
        3) 建唯一索引 uq_conv_messages_user_session_seq（新库由 create_all 已建，此步 no-op）。
        """
        from sqlalchemy import text

        with engine.begin() as conn:
            cols = {
                row[1]
                for row in conn.execute(text("PRAGMA table_info(conv_messages)")).fetchall()
            }
            if "seq" not in cols:
                conn.execute(text(
                    "ALTER TABLE conv_messages ADD COLUMN seq INTEGER NOT NULL DEFAULT 0"
                ))
            if "previous_content" not in cols:
                conn.execute(text(
                    "ALTER TABLE conv_messages ADD COLUMN previous_content TEXT NOT NULL DEFAULT ''"
                ))

            # 回填 seq=0 的行（旧数据特征）：按 (user, session) 分组、组内按 rowid 序
            # 编 0..n-1；新写入的行带真实 seq 不受影响
            rows = conn.execute(text(
                "SELECT user_id, source_session_id, id FROM conv_messages WHERE seq = 0 "
                "ORDER BY user_id, source_session_id, rowid"
            )).fetchall()
            group_seq: dict[tuple[str, str], int] = {}
            for uid, sid, mid in rows:
                key = (uid, sid)
                i = group_seq.get(key, 0)
                conn.execute(
                    text("UPDATE conv_messages SET seq = :s WHERE id = :i"),
                    {"s": i, "i": mid},
                )
                group_seq[key] = i + 1

            conn.execute(text(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_conv_messages_user_session_seq "
                "ON conv_messages(user_id, source_session_id, seq)"
            ))

    @staticmethod
    def _migrate_struct_memories_d4(engine: Engine) -> None:
        """幂等迁移 D4-0：struct_memories 补实体归属/版本裁决列（零行为变化）。

        新列：entity_ref / attribute / lifecycle / source_started_at / version /
        supersedes_id（create_all 对已存在表不补列，故显式 ALTER）。
        旧行回填语义（旧库已按 (user,category,key) upsert，每键一行=当前值）：
        - entity_ref='SELF'、lifecycle='current'、version=1、supersedes_id=''（列默认值）；
        - attribute 回填为旧 key（未归一前规范属性名等同 key）；
        - source_started_at 按 (user_id, source_conversation_id) join
          conv_meta.started_at 回填（拿对话内时间，供 D4-1 as-of 裁决）。
        """
        from sqlalchemy import text

        with engine.begin() as conn:
            cols = {
                row[1]
                for row in conn.execute(
                    text("PRAGMA table_info(struct_memories)")
                ).fetchall()
            }
            if "entity_ref" not in cols:
                conn.execute(text(
                    "ALTER TABLE struct_memories ADD COLUMN entity_ref "
                    "TEXT NOT NULL DEFAULT 'SELF'"
                ))
            if "attribute" not in cols:
                conn.execute(text(
                    "ALTER TABLE struct_memories ADD COLUMN attribute "
                    "TEXT NOT NULL DEFAULT ''"
                ))
            if "lifecycle" not in cols:
                conn.execute(text(
                    "ALTER TABLE struct_memories ADD COLUMN lifecycle "
                    "TEXT NOT NULL DEFAULT 'current'"
                ))
            if "source_started_at" not in cols:
                conn.execute(text(
                    "ALTER TABLE struct_memories ADD COLUMN source_started_at DATETIME"
                ))
            if "version" not in cols:
                conn.execute(text(
                    "ALTER TABLE struct_memories ADD COLUMN version "
                    "INTEGER NOT NULL DEFAULT 1"
                ))
            if "supersedes_id" not in cols:
                conn.execute(text(
                    "ALTER TABLE struct_memories ADD COLUMN supersedes_id "
                    "TEXT NOT NULL DEFAULT ''"
                ))

            # attribute 回填旧 key（只补空值行，幂等）
            conn.execute(text(
                "UPDATE struct_memories SET attribute = key "
                "WHERE attribute = '' AND key <> ''"
            ))
            # source_started_at 回填：join conv_meta 取对话内时间（只补 NULL 行，幂等）
            conn.execute(text(
                "UPDATE struct_memories SET source_started_at = ("
                "  SELECT c.started_at FROM conv_meta c"
                "  WHERE c.user_id = struct_memories.user_id"
                "    AND c.source_session_id = struct_memories.source_conversation_id"
                ") WHERE source_started_at IS NULL"
            ))
            # 新列索引（IF NOT EXISTS 幂等；新库由 create_all 已建则跳过）
            conn.execute(text(
                "CREATE INDEX IF NOT EXISTS idx_memories_entity_attribute "
                "ON struct_memories(user_id, entity_ref, attribute)"
            ))
            conn.execute(text(
                "CREATE INDEX IF NOT EXISTS idx_memories_lifecycle "
                "ON struct_memories(user_id, lifecycle)"
            ))


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