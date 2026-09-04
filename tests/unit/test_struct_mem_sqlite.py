"""测试结构化记忆 SQLite 双写（``struct_memories`` 表）。

不依赖 Milvus / DashScope / LLM：直接调用
``StructuredMemService.save_structured_memories_to_sqlite`` 验证
INSERT 与冲突 UPDATE（旧值归档 ``previous_fact``）。

对照 v0.2 需求文档「变更 2.3 冲突检测」：同一 (user_id, category, key)
已有记录则 UPDATE 覆盖，旧值归档；否则 INSERT。
"""
from __future__ import annotations

import pytest


@pytest.fixture()
def tmp_memory_db(tmp_path, monkeypatch):
    """把记忆库指向临时文件，避免污染真实 memories.db。"""
    from os_mem.infra.storage import mem_storage

    db_file = tmp_path / "test_memories.db"
    monkeypatch.setattr(mem_storage.MemoryDatabase, "db_path", db_file)
    mem_storage.MemoryDatabase._engines.clear()
    yield db_file
    mem_storage.MemoryDatabase._engines.clear()


def _make_fact(fact, category, key, value, confidence=0.8):
    from os_mem.models.mem_models import MemoryFact

    return MemoryFact(
        fact=fact, category=category, key=key, value=value, confidence=confidence,
    )


def _query_rows(user_id):
    from sqlmodel import select

    from os_mem.entries.mem_models import StructuredMemory
    from os_mem.infra.storage import get_session

    with get_session() as session:
        return session.exec(
            select(StructuredMemory).where(StructuredMemory.user_id == user_id),
        ).all()


def test_insert_and_conflict_update(tmp_memory_db):
    from os_mem.core.services.struc_mem_service import StructuredMemService

    user_id = "user-1"

    # 首次写入：INSERT
    n = StructuredMemService.save_structured_memories_to_sqlite(
        user_id=user_id,
        source_conversation_id="conv-1",
        facts=[
            _make_fact(
                "用户支票账户号码是 4429853327",
                "finance",
                "checking_account_number",
                "4429853327",
                0.95,
            ),
        ],
    )
    assert n == 1

    rows = _query_rows(user_id)
    assert len(rows) == 1
    assert rows[0].value == "4429853327"
    assert rows[0].previous_fact == ""
    assert rows[0].source_conversation_id == "conv-1"

    # 冲突：同 (user_id, category, key) 新值 → UPDATE，旧值归档
    n2 = StructuredMemService.save_structured_memories_to_sqlite(
        user_id=user_id,
        source_conversation_id="conv-2",
        facts=[
            _make_fact(
                "用户支票账户号码改成 8847293001",
                "finance",
                "checking_account_number",
                "8847293001",
                0.9,
            ),
        ],
    )
    assert n2 == 1

    rows = _query_rows(user_id)
    assert len(rows) == 1  # 仍是 1 条（覆盖，不追加）
    assert rows[0].value == "8847293001"
    assert rows[0].previous_fact == "用户支票账户号码是 4429853327"
    assert rows[0].source_conversation_id == "conv-2"


def test_multi_fact_insert_and_no_conflict_on_different_key(tmp_memory_db):
    from os_mem.core.services.struc_mem_service import StructuredMemService

    user_id = "user-2"
    n = StructuredMemService.save_structured_memories_to_sqlite(
        user_id=user_id,
        source_conversation_id="conv-1",
        facts=[
            _make_fact("用户邮箱 a@b.com", "contact", "email", "a@b.com", 0.9),
            _make_fact(
                "用户电话 916-555-8899", "contact", "phone", "916-555-8899", 0.9,
            ),
        ],
    )
    assert n == 2

    rows = _query_rows(user_id)
    assert len(rows) == 2
    # 不同 key 不冲突，各自独立
    assert {r.key for r in rows} == {"email", "phone"}
    assert all(r.previous_fact == "" for r in rows)


def test_empty_facts_noop(tmp_memory_db):
    from os_mem.core.services.struc_mem_service import StructuredMemService

    assert (
        StructuredMemService.save_structured_memories_to_sqlite(
            user_id="user-3", source_conversation_id="conv-1", facts=[],
        )
        == 0
    )
