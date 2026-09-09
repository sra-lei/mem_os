"""D4-0 迁移单测：struct_memories 补实体归属/版本列 + 旧行回填。

不依赖 Milvus/LLM。构造一个「旧 schema」库（无 D4 列），灌入旧行与 conv_meta，
跑 MemoryDatabase.init_db() 幂等迁移后验证：
1. 6 个新列齐备；
2. 旧行回填 entity_ref='SELF' / attribute=旧 key / lifecycle='current' /
   version=1 / supersedes_id=''；
3. source_started_at 正确 join conv_meta.started_at（含无匹配会话的 NULL 兜底）；
4. 迁移幂等（再跑一次不报错、不重复回填）。
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterator

import pytest
from sqlalchemy import text


@pytest.fixture()
def tmp_memory_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    from os_mem.infra.storage import mem_storage

    db_file = tmp_path / "test_d4.db"
    monkeypatch.setattr(mem_storage.MemoryDatabase, "db_path", db_file)
    mem_storage.MemoryDatabase._engines.clear()
    yield db_file
    mem_storage.MemoryDatabase._engines.clear()


def _create_old_schema_db(db_file: Path) -> None:
    """用裸 SQL 造一个 D4 之前的 struct_memories（+ conv_meta 供时间回填）。"""
    from sqlalchemy import create_engine

    engine = create_engine(f"sqlite:///{db_file}")
    with engine.begin() as conn:
        conn.execute(text(
            """
            CREATE TABLE struct_memories (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                fact TEXT NOT NULL,
                previous_fact TEXT DEFAULT '',
                category TEXT NOT NULL,
                key TEXT NOT NULL,
                value TEXT NOT NULL,
                confidence REAL DEFAULT 0.8,
                source_conversation_id TEXT DEFAULT '',
                source_chunk_id TEXT DEFAULT '',
                created_at DATETIME,
                updated_at DATETIME
            )
            """
        ))
        conn.execute(text(
            """
            CREATE TABLE conv_meta (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                source_session_id TEXT NOT NULL,
                message_count INTEGER DEFAULT 0,
                started_at DATETIME,
                ended_at DATETIME,
                status TEXT DEFAULT 'PENDING',
                attempts INTEGER DEFAULT 0,
                last_error TEXT DEFAULT '',
                created_at DATETIME,
                updated_at DATETIME
            )
            """
        ))
        # 两个会话：有时间戳 + 无时间戳（验证 NULL 兜底）
        conn.execute(text(
            "INSERT INTO conv_meta (id,user_id,source_session_id,started_at) "
            "VALUES ('m1','u1','conv-a','2024-09-15 10:00:00')"
        ))
        conn.execute(text(
            "INSERT INTO conv_meta (id,user_id,source_session_id,started_at) "
            "VALUES ('m2','u1','conv-b',NULL)"
        ))
        conn.execute(text(
            "INSERT INTO struct_memories "
            "(id,user_id,fact,category,key,value,source_conversation_id,created_at,updated_at) "
            "VALUES ('r1','u1','wire is $85k','finance','wire_amount','$85,000',"
            "'conv-a','2024-09-15 10:00:00','2024-09-15 10:00:00')"
        ))
        conn.execute(text(
            "INSERT INTO struct_memories "
            "(id,user_id,fact,category,key,value,source_conversation_id,created_at,updated_at) "
            "VALUES ('r2','u1','email x','contact','email','a@b.com',"
            "'conv-b','2024-09-25 10:00:00','2024-09-25 10:00:00')"
        ))
    engine.dispose()


def test_d4_migration_adds_columns_and_backfills(tmp_memory_db: Path) -> None:
    from os_mem.infra.storage.mem_storage import MemoryDatabase
    from sqlalchemy import create_engine

    _create_old_schema_db(tmp_memory_db)
    MemoryDatabase().init_db()  # 触发幂等迁移

    engine = create_engine(f"sqlite:///{tmp_memory_db}")
    with engine.begin() as conn:
        cols = {
            r[1] for r in conn.execute(
                text("PRAGMA table_info(struct_memories)")
            ).fetchall()
        }
        assert {
            "entity_ref", "attribute", "lifecycle",
            "source_started_at", "version", "supersedes_id",
        } <= cols

        rows = {
            r[0]: r
            for r in conn.execute(text(
                "SELECT id,entity_ref,attribute,lifecycle,source_started_at,"
                "version,supersedes_id,key FROM struct_memories ORDER BY id"
            )).fetchall()
        }
    engine.dispose()

    r1 = rows["r1"]
    # (id, entity_ref, attribute, lifecycle, source_started_at, version, supersedes_id, key)
    assert r1[1] == "SELF"
    assert r1[2] == "wire_amount"      # attribute 回填旧 key
    assert r1[3] == "current"
    assert str(r1[4]).startswith("2024-09-15 10:00:00")  # join conv_meta
    assert r1[5] == 1
    assert r1[6] == ""

    r2 = rows["r2"]
    assert r2[2] == "email"
    assert r2[4] is None                # 会话 started_at 为 NULL → NULL 兜底不崩


def test_d4_migration_idempotent(tmp_memory_db: Path) -> None:
    from os_mem.infra.storage.mem_storage import MemoryDatabase
    from sqlalchemy import create_engine, text as sql_text

    _create_old_schema_db(tmp_memory_db)
    db = MemoryDatabase()
    db.init_db()
    db.init_db()  # 再跑一次：不应抛错、不应把 attribute 二次改坏

    engine = create_engine(f"sqlite:///{tmp_memory_db}")
    with engine.begin() as conn:
        n = conn.execute(
            sql_text("SELECT COUNT(*) FROM struct_memories WHERE attribute=key")
        ).scalar()
        total = conn.execute(
            sql_text("SELECT COUNT(*) FROM struct_memories")
        ).scalar()
    engine.dispose()
    assert n == total == 2
