"""会话处理账本（conv_process）+ 不可逆状态机 单测。

覆盖 docs/方案-会话处理状态机与原子入库.md：
- §3.2 状态机：合法链通过，回退/跳级/终止态外出全部拒绝，FAILED 从任一活跃态可达
- §3.3 claim 门禁：首登 PENDING + 原数据入库；同会话二次 claim → None（任意状态都跳过）
- §3.4 StructProvider 编排：首次 ingest 走到 COMPLETED；重复 ingest 不再触达
  add_structured_memory；异常 → FAILED + last_error 且不可逆

全部无 LLM / 无 Milvus，走临时 sqlite。
"""
from __future__ import annotations

import sys
from datetime import datetime
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
    from os_mem.core.services.conv_process_service import get_conv_process_service

    return get_conv_process_service()


def _load_rows(user_id=None, session_id=None):
    from sqlmodel import select

    from os_mem.entries.mem_models import ConversationProcess
    from os_mem.infra.storage import get_session

    with get_session() as session:
        q = select(ConversationProcess)
        if user_id:
            q = q.where(ConversationProcess.user_id == user_id)
        if session_id:
            q = q.where(ConversationProcess.source_session_id == session_id)
        return session.exec(q).all()


def _conversation(user_id, session_id, messages=('{"role":"user","content":"hi"}',)):
    from os_mem.models import Conversation

    return Conversation(
        id=session_id,
        user_id=user_id,
        messages=list(messages),
        source_session_id=session_id,
        started_at=datetime.utcnow(),
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
    from os_mem.core.services.conv_process_service import (
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
    from os_mem.core.services.conv_process_service import (
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
        (STATUS_COMPLETED, STATUS_EXTRACTING),            # 终止态外出
        (STATUS_COMPLETED, STATUS_FAILED),                # 终止态外出
        (STATUS_FAILED, STATUS_EXTRACTING),               # 失败态不可逆
        ("UNKNOWN", STATUS_EXTRACTING),                   # 未知状态
    ]
    for cur, nxt in illegal:
        with pytest.raises(IllegalTransitionError):
            m.validate(cur, nxt)


def test_machine_failed_reachable_from_every_active_state():
    from os_mem.core.services.conv_process_service import process_state_machine

    m = process_state_machine()
    for cur in m.active_states:
        assert m.can_transition(cur, "FAILED"), f"{cur} → FAILED 应合法"


# --------------------------------------------------------------------- #
#  claim 门禁
# --------------------------------------------------------------------- #
def test_claim_inserts_pending_with_raw_payload(tmp_memory_db):
    svc = _svc()
    proc = svc.claim("u1", "s1", raw_payload='["m1","m2"]')

    assert proc is not None
    assert proc.status == "PENDING"
    assert proc.raw_payload == '["m1","m2"]'

    rows = _load_rows(user_id="u1")
    assert len(rows) == 1
    assert rows[0].source_session_id == "s1"
    assert rows[0].status == "PENDING"
    assert rows[0].created_at is not None


def test_claim_second_time_skips_regardless_of_status(tmp_memory_db):
    svc = _svc()
    proc = svc.claim("u1", "s1", raw_payload="[]")
    assert proc is not None

    # PENDING 中重复投递 → 跳过
    assert svc.claim("u1", "s1", raw_payload="[]") is None

    # 推进到 COMPLETED 后重复投递 → 仍跳过，且不改动原记录（不可逆）
    svc.mark(proc.id, "EXTRACTING")
    svc.mark(proc.id, "SAVING_SQLITE")
    svc.mark(proc.id, "SAVING_VECTOR")
    svc.mark(proc.id, "COMPLETED")

    assert svc.claim("u1", "s1", raw_payload="[]") is None
    rows = _load_rows(user_id="u1")
    assert len(rows) == 1
    assert rows[0].status == "COMPLETED"

    # 不同会话不受影响
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
def test_provider_first_ingest_runs_to_completed_duplicate_skipped(tmp_memory_db):
    p = _provider_with_dummy_service()
    conv = _conversation("u1", "conv-1")

    p.ingest(conv)
    assert p.service.calls == 1
    assert p.service.seen_stages == ["EXTRACTING", "SAVING_SQLITE", "SAVING_VECTOR"]
    rows = _load_rows(user_id="u1")
    assert len(rows) == 1
    assert rows[0].status == "COMPLETED"

    # 重复投递同一会话：直接跳过，不再触达 add_structured_memory（LLM/落库都不发生）
    p.ingest(conv)
    assert p.service.calls == 1
    assert len(_load_rows(user_id="u1")) == 1
    assert _load_rows(user_id="u1")[0].status == "COMPLETED"


def test_provider_ingest_failure_marks_failed_and_is_irreversible(tmp_memory_db):
    p = _provider_with_dummy_service(fail_on_ingest=True)
    conv = _conversation("u1", "conv-2")

    with pytest.raises(RuntimeError, match="boom"):
        p.ingest(conv)

    rows = _load_rows(user_id="u1")
    assert len(rows) == 1
    assert rows[0].status == "FAILED"
    assert "boom" in rows[0].last_error

    # FAILED 会话：状态不可逆（不能重新推进），重复投递同样整体跳过
    svc = _svc()
    with pytest.raises(Exception):
        svc.mark(rows[0].id, "EXTRACTING")
    p.ingest(conv)
    assert p.service.calls == 1  # 未重跑
    assert _load_rows(user_id="u1")[0].status == "FAILED"


def test_provider_ingest_raw_payload_stores_conversation(tmp_memory_db):
    import json

    p = _provider_with_dummy_service()
    msgs = ('{"role":"user","content":"a"}',)
    p.ingest(_conversation("u1", "conv-3", messages=msgs))

    rows = _load_rows(user_id="u1", session_id="conv-3")
    assert len(rows) == 1
    # raw_payload = messages 列表的 JSON 原文（往返一致）
    assert json.loads(rows[0].raw_payload) == list(msgs)
