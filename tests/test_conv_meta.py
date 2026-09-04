"""对话元数据表（conv_meta）+ 不可逆状态机 单测。

覆盖 docs/方案-会话处理状态机与原子入库.md：
- §3.2 状态机：合法链通过，回退/跳级/终止态外出全部拒绝，FAILED 从任一活跃态可达，
  同态 no-op 允许
- §3.3 claim 门禁：仅 COMPLETED 跳过；FAILED/过期(崩溃残留)接管重启；
  未过期中途状态视为处理中 → 跳过；并发由 CAS + 唯一约束保证
- §3.4 StructProvider 编排：首次 ingest → COMPLETED；重复 ingest 跳过；
  失败 → FAILED；再次 ingest 重启接着处理
- 元数据(message_count/started_at/ended_at)入库；原文 messages 不入本表

全部无 LLM / 无 Milvus，走临时 sqlite。
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture()
def tmp_memory_db(tmp_path, monkeypatch):
    """把记忆库指向临时文件，避免污染真实 os_mem.db。"""
    from os_mem.infra.storage import mem_storage

    db_file = tmp_path / "test_memories.db"
    monkeypatch.setattr(mem_storage.MemoryDatabase, "db_path", db_file)
    mem_storage.MemoryDatabase._engines.clear()
    yield db_file
    mem_storage.MemoryDatabase._engines.clear()


def _svc():
    from os_mem.core.services.conv_meta_service import get_conversation_meta_service

    return get_conversation_meta_service()


def _load_rows(user_id=None, session_id=None):
    from sqlmodel import select

    from os_mem.entries.mem_models import ConversationMeta
    from os_mem.infra.storage import get_session

    with get_session() as session:
        q = select(ConversationMeta)
        if user_id:
            q = q.where(ConversationMeta.user_id == user_id)
        if session_id:
            q = q.where(ConversationMeta.source_session_id == session_id)
        return session.exec(q).all()


def _age_row(meta_id, seconds=3600):
    """把行改旧，模拟「进程崩溃后租约过期」的残留会话。"""
    from sqlmodel import select

    from os_mem.entries.mem_models import ConversationMeta
    from os_mem.infra.storage import get_session

    with get_session() as session:
        row = session.exec(
            select(ConversationMeta).where(ConversationMeta.id == meta_id)
        ).first()
        assert row is not None
        row.updated_at = datetime.utcnow() - timedelta(seconds=seconds)
        session.add(row)
        session.commit()


def _conversation(user_id, session_id, messages=('{"role":"user","content":"hi"}',)):
    from os_mem.models import Conversation

    return Conversation(
        id=session_id,
        user_id=user_id,
        messages=list(messages),
        source_session_id=session_id,
        started_at=datetime.utcnow() - timedelta(minutes=1),
        ended_at=datetime.utcnow(),
    )


def _provider_with_dummy_service(user_id="u1", fail_on_ingest: bool = False):
    """用假 service 替换真实（LLM/Milvus）链路，只验证编排逻辑。"""
    from os_mem.core.mem_provider.struct_provider import StructProvider

    class DummyService:
        def __init__(self):
            self.calls = 0
            self.seen_stages: list[str] = []
            self.fail_on_ingest = fail_on_ingest

        def add_structured_memory(self, conversation, on_stage=None):
            self.calls += 1
            if on_stage is not None:
                for stage in ("EXTRACTING", "SAVING_SQLITE", "SAVING_VECTOR"):
                    on_stage(stage)
                    self.seen_stages.append(stage)
            if self.fail_on_ingest:
                raise RuntimeError("boom: vector write failed")

    p = StructProvider.__new__(StructProvider)  # 跳过 __init__（避免构造真实 service）
    p.user_id = user_id
    p.service = DummyService()
    return p


# --------------------------------------------------------------------- #
#  状态机
# --------------------------------------------------------------------- #
def test_machine_full_forward_chain_ok():
    from os_mem.core.services.conv_meta_service import (
        STATUS_COMPLETED,
        STATUS_EXTRACTING,
        STATUS_PENDING,
        STATUS_SAVING_SQLITE,
        STATUS_SAVING_VECTOR,
        process_state_machine,
    )

    m = process_state_machine()
    chain = [
        (STATUS_PENDING, STATUS_EXTRACTING),
        (STATUS_EXTRACTING, STATUS_SAVING_SQLITE),
        (STATUS_SAVING_SQLITE, STATUS_SAVING_VECTOR),
        (STATUS_SAVING_VECTOR, STATUS_COMPLETED),
    ]
    for cur, nxt in chain:
        assert m.can_transition(cur, nxt), f"{cur} → {nxt} 应合法"
        m.validate(cur, nxt)  # 不抛即通过
    assert m.is_terminal(STATUS_COMPLETED)


def test_machine_rejects_illegal_transitions():
    from os_mem.core.state_machine import IllegalTransitionError
    from os_mem.core.services.conv_meta_service import (
        STATUS_COMPLETED,
        STATUS_EXTRACTING,
        STATUS_FAILED,
        STATUS_PENDING,
        STATUS_SAVING_SQLITE,
        STATUS_SAVING_VECTOR,
        process_state_machine,
    )

    m = process_state_machine()
    illegal = [
        (STATUS_EXTRACTING, STATUS_PENDING),              # 回退
        (STATUS_SAVING_VECTOR, STATUS_EXTRACTING),        # 回退
        (STATUS_PENDING, STATUS_SAVING_SQLITE),           # 跳级
        (STATUS_PENDING, STATUS_COMPLETED),               # 跳级
        (STATUS_COMPLETED, STATUS_EXTRACTING),            # 完成态外出
        (STATUS_COMPLETED, STATUS_FAILED),                # 完成态外出
        (STATUS_FAILED, STATUS_EXTRACTING),               # 失败态不可经 mark 重启（重启只走 claim CAS）
        ("UNKNOWN", STATUS_EXTRACTING),                   # 未知状态
    ]
    for cur, nxt in illegal:
        with pytest.raises(IllegalTransitionError):
            m.validate(cur, nxt)


def test_machine_same_state_noop_allowed():
    from os_mem.core.services.conv_meta_service import (
        STATUS_EXTRACTING,
        process_state_machine,
    )

    m = process_state_machine()
    # claim 接管后状态已是 EXTRACTING，首个阶段标记仍上报 EXTRACTING → no-op 通过
    m.validate(STATUS_EXTRACTING, STATUS_EXTRACTING)


def test_machine_failed_reachable_from_every_active_state():
    from os_mem.core.services.conv_meta_service import process_state_machine

    m = process_state_machine()
    for cur in m.active_states:
        assert m.can_transition(cur, "FAILED"), f"{cur} → FAILED 应合法"


# --------------------------------------------------------------------- #
#  claim 门禁
# --------------------------------------------------------------------- #
def test_claim_inserts_pending_with_metadata(tmp_memory_db):
    svc = _svc()
    started = datetime.utcnow() - timedelta(minutes=5)
    proc = svc.claim(
        "u1", "s1",
        message_count=3, started_at=started, ended_at=started + timedelta(minutes=1),
    )

    assert proc is not None
    assert proc.status == "PENDING"
    assert proc.message_count == 3
    assert proc.started_at == started
    assert proc.attempts == 0

    rows = _load_rows(user_id="u1")
    assert len(rows) == 1
    assert rows[0].source_session_id == "s1"
    assert rows[0].status == "PENDING"
    assert not hasattr(rows[0], "raw_payload")  # 原文不存元数据表


def test_claim_skips_completed_but_restarts_failed(tmp_memory_db):
    svc = _svc()
    proc = svc.claim("u1", "s1")

    # COMPLETED → 跳过（唯一真正终止态）
    for st in ("EXTRACTING", "SAVING_SQLITE", "SAVING_VECTOR", "COMPLETED"):
        svc.mark(proc.id, st)
    assert svc.claim("u1", "s1") is None
    rows = _load_rows(user_id="u1")
    assert len(rows) == 1
    assert rows[0].status == "COMPLETED"
    assert rows[0].attempts == 0

    # FAILED → 立即重启：attempts+1、last_error 清空、状态回 EXTRACTING
    proc2 = svc.claim("u1", "s2")
    svc.fail(proc2.id, "some error")
    assert _load_rows(user_id="u1", session_id="s2")[0].status == "FAILED"

    restarted = svc.claim("u1", "s2")
    assert restarted is not None
    assert restarted.status == "EXTRACTING"
    assert restarted.attempts == 1
    assert restarted.last_error == ""
    # 重启后可继续走完
    svc.mark(restarted.id, "SAVING_SQLITE")
    svc.mark(restarted.id, "SAVING_VECTOR")
    svc.mark(restarted.id, "COMPLETED")
    assert _load_rows(user_id="u1", session_id="s2")[0].status == "COMPLETED"


def test_claim_active_fresh_is_in_flight_but_stale_is_takeover(tmp_memory_db):
    svc = _svc()
    proc = svc.claim("u1", "s1")

    # 中途状态（未过期）→ 视为处理中，跳过
    svc.mark(proc.id, "EXTRACTING")
    assert svc.claim("u1", "s1") is None
    assert _load_rows(user_id="u1")[0].status == "EXTRACTING"

    # 租约过期（崩溃残留）→ 接管重启
    _age_row(proc.id, seconds=3600)
    taken = svc.claim("u1", "s1")
    assert taken is not None
    assert taken.status == "EXTRACTING"
    assert taken.attempts == 1


def test_claim_different_sessions_and_users_independent(tmp_memory_db):
    svc = _svc()
    assert svc.claim("u1", "s1") is not None
    assert svc.claim("u1", "s2") is not None
    assert svc.claim("u2", "s1") is not None
    assert len(_load_rows(user_id="u1")) == 2


def test_claim_rejects_empty_ids(tmp_memory_db):
    svc = _svc()
    with pytest.raises(ValueError):
        svc.claim("", "s1")
    with pytest.raises(ValueError):
        svc.claim("u1", "")


# --------------------------------------------------------------------- #
#  StructProvider 编排
# --------------------------------------------------------------------- #
def test_provider_first_ingest_completed_duplicate_skipped(tmp_memory_db):
    p = _provider_with_dummy_service()
    conv = _conversation("u1", "conv-1")

    p.ingest(conv)
    assert p.service.calls == 1
    assert p.service.seen_stages == ["EXTRACTING", "SAVING_SQLITE", "SAVING_VECTOR"]
    rows = _load_rows(user_id="u1")
    assert len(rows) == 1
    assert rows[0].status == "COMPLETED"
    assert rows[0].message_count == 1
    assert rows[0].attempts == 0

    # COMPLETED 会话重复投递：整体跳过，不再触达 add_structured_memory
    p.ingest(conv)
    assert p.service.calls == 1
    assert _load_rows(user_id="u1")[0].status == "COMPLETED"


def test_provider_failure_then_reingest_restarts_to_completed(tmp_memory_db):
    p = _provider_with_dummy_service(fail_on_ingest=True)
    conv = _conversation("u1", "conv-2")

    # 第一次：异常 → FAILED + last_error，异常向上抛
    with pytest.raises(RuntimeError, match="boom"):
        p.ingest(conv)
    rows = _load_rows(user_id="u1")
    assert rows[0].status == "FAILED"
    assert "boom" in rows[0].last_error

    # 第二次（非完成态 → 接着处理）：仍失败 → FAILED，attempts 递增
    with pytest.raises(RuntimeError, match="boom"):
        p.ingest(conv)
    assert p.service.calls == 2
    assert _load_rows(user_id="u1")[0].status == "FAILED"
    assert _load_rows(user_id="u1")[0].attempts == 1

    # 第三次（故障恢复）：重启接着处理直到 COMPLETED
    p.service.fail_on_ingest = False
    p.ingest(conv)
    assert p.service.calls == 3
    assert _load_rows(user_id="u1")[0].status == "COMPLETED"
    assert _load_rows(user_id="u1")[0].attempts == 2
    assert _load_rows(user_id="u1")[0].last_error == ""


def test_provider_metadata_stored_no_payload(tmp_memory_db):
    p = _provider_with_dummy_service()
    p.ingest(_conversation("u1", "conv-3"))

    rows = _load_rows(user_id="u1", session_id="conv-3")
    assert len(rows) == 1
    assert rows[0].message_count == 1
    assert rows[0].started_at is not None
    assert rows[0].ended_at is not None
