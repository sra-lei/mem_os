"""测试结构化记忆 SQLite 双写（``struct_memories`` 表）。

不依赖 Milvus / DashScope / LLM：直接调用
``StructuredMemService.save_structured_memories_to_sqlite`` 验证
D4 版本链：同 (user, entity_ref, attribute) current 新值 → 追加新版本行、
旧行置 lifecycle=superseded（旧 fact 归档到新行 previous_fact）；
不同属性独立；historical 快照独立共存。
"""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Iterator

import pytest

if TYPE_CHECKING:
    from os_mem.entries.mem_models import StructuredMemory
    from os_mem.models.mem_models import MemoryFact


@pytest.fixture()
def tmp_memory_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """把记忆库指向临时文件，避免污染真实 memories.db。"""
    from os_mem.infra.storage import mem_storage

    db_file = tmp_path / "test_memories.db"
    monkeypatch.setattr(mem_storage.MemoryDatabase, "db_path", db_file)
    mem_storage.MemoryDatabase._engines.clear()
    yield db_file
    mem_storage.MemoryDatabase._engines.clear()


def _make_fact(
    fact: str,
    category: str,
    key: str,
    value: str,
    confidence: float = 0.8,
) -> MemoryFact:
    from os_mem.models.mem_models import MemoryFact

    return MemoryFact(
        fact=fact, category=category, key=key, value=value, confidence=confidence,
    )


def _query_rows(user_id: str) -> list[StructuredMemory]:
    from sqlmodel import select

    from os_mem.entries.mem_models import StructuredMemory
    from os_mem.infra.storage import get_session

    with get_session() as session:
        return session.exec(
            select(StructuredMemory).where(StructuredMemory.user_id == user_id),
        ).all()


def test_insert_and_conflict_update(tmp_memory_db: Path) -> None:
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

    # 冲突：同属性新值 → 追加 v2，旧 v1 置 superseded（旧 fact 归档 v2.previous_fact）
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
    assert len(rows) == 2  # 版本链：v1(superseded) + v2(current) 各一行
    current = [r for r in rows if r.lifecycle == "current"]
    superseded = [r for r in rows if r.lifecycle == "superseded"]
    assert len(current) == 1 and len(superseded) == 1
    assert current[0].value == "8847293001"
    assert current[0].version == 2
    assert current[0].supersedes_id == superseded[0].id
    assert current[0].previous_fact == "用户支票账户号码是 4429853327"
    assert current[0].source_conversation_id == "conv-2"
    assert superseded[0].value == "4429853327"
    assert superseded[0].version == 1


def test_earlier_started_at_does_not_supersede_current(tmp_memory_db: Path) -> None:
    """D4 as-of：迟到的更早会话（对话时间更早）不得覆盖 current。"""
    from datetime import datetime as DT

    from os_mem.core.services.struc_mem_service import StructuredMemService

    user_id = "user-asof"
    svc = StructuredMemService.save_structured_memories_to_sqlite
    # 最新会话 09-26：$95,000
    svc(user_id, "conv-new", [_make_fact("w 95k", "finance", "wire_amount", "$95,000")],
        started_at=DT(2024, 9, 26))
    # 迟到旧会话 09-15：$85,000 —— 必须被忽略
    n = svc(user_id, "conv-old", [_make_fact("w 85k", "finance", "wire_amount", "$85,000")],
            started_at=DT(2024, 9, 15))
    assert n == 0
    rows = [r for r in _query_rows(user_id) if r.lifecycle == "current"]
    assert len(rows) == 1
    assert rows[0].value == "$95,000"


def test_historical_snapshot_coexists_with_current(tmp_memory_db: Path) -> None:
    """original_* 快照与 current 是不同签名：独立共存，互不取代。"""
    from os_mem.core.services.struc_mem_service import StructuredMemService

    user_id = "user-hist"
    svc = StructuredMemService.save_structured_memories_to_sqlite
    svc(user_id, "conv-1", [_make_fact("wire now 95k", "finance", "wire_amount", "$95,000")])
    svc(user_id, "conv-2", [
        _make_fact("wire now 95k", "finance", "wire_amount", "$95,000"),
        _make_fact("original wire 85k", "finance", "original_wire_amount", "$85,000"),
    ])
    rows = _query_rows(user_id)
    current = [r for r in rows if r.lifecycle == "current" and r.attribute == "wire_amount"]
    historical = [r for r in rows if r.lifecycle == "historical"]
    assert len(current) == 1 and current[0].value == "$95,000"
    assert len(historical) == 1 and historical[0].value == "$85,000"


def test_multi_fact_insert_and_no_conflict_on_different_key(tmp_memory_db: Path) -> None:
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


def test_empty_facts_noop(tmp_memory_db: Path) -> None:
    from os_mem.core.services.struc_mem_service import StructuredMemService

    assert (
        StructuredMemService.save_structured_memories_to_sqlite(
            user_id="user-3", source_conversation_id="conv-1", facts=[],
        )
        == 0
    )
