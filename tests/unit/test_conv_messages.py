"""conv_messages 消息双写冲突（打架）专项测试。

场景：note(base) 与 struct/full 都可能写同一会话原文 —— 不做 provider 互斥，
统一走 value 冲突 upsert（冲突键 (user_id, source_session_id, seq)）：
- 同键一致（双 provider 写同一会话）→ 不产生重复行（幂等）；
- 后入内容更新 → 同键覆盖，旧值归档 previous_content；
- 新增 seq → INSERT；只增不删。
另覆盖老库迁移（init_db 补列 + 回填 seq + 唯一索引）。

无 LLM / Milvus，临时 sqlite。
"""
from __future__ import annotations

from datetime import datetime

import pytest


@pytest.fixture()
def tmp_memory_db(tmp_path, monkeypatch):
    from os_mem.infra.storage import mem_storage

    db_file = tmp_path / "test_memories.db"
    monkeypatch.setattr(mem_storage.MemoryDatabase, "db_path", db_file)
    mem_storage.MemoryDatabase._engines.clear()
    yield db_file
    mem_storage.MemoryDatabase._engines.clear()


def _msgs(*contents):
    import json

    return [json.dumps({"role": r, "content": c}, ensure_ascii=False)
            for r, c in contents]


def _upsert(user_id, session_id, msgs, started_at=None):
    from os_mem.core.services.note_mem_service import upsert_conversation_messages
    from os_mem.infra.storage import get_session

    with get_session() as session:
        n = upsert_conversation_messages(
            session, user_id=user_id, source_session_id=session_id,
            messages=msgs, started_at=started_at,
        )
        session.commit()
        return n


def _rows(user_id=None, session_id=None):
    from sqlmodel import select

    from os_mem.entries.mem_models import Message
    from os_mem.infra.storage import get_session

    with get_session() as session:
        q = select(Message)
        if user_id:
            q = q.where(Message.user_id == user_id)
        if session_id:
            q = q.where(Message.source_session_id == session_id)
        q = q.order_by(Message.seq)
        return session.exec(q).all()


# --------------------------------------------------------------------- #
#  value 冲突 upsert 语义
# --------------------------------------------------------------------- #
def test_identical_second_write_no_duplicates(tmp_memory_db):
    """双 provider 写同一会话、数据一致 → 不产生重复行（打架核心场景）。"""
    msgs = _msgs(("user", "hello"), ("assistant", "hi there"))

    n1 = _upsert("u1", "s1", msgs)
    assert n1 == 2

    # 模拟第二个 provider 再写完全一致的会话
    n2 = _upsert("u1", "s1", msgs)
    assert n2 == 0  # 全部 no-op
    rows = _rows(user_id="u1")
    assert len(rows) == 2
    assert [r.seq for r in rows] == [0, 1]


def test_later_update_archives_previous_content(tmp_memory_db):
    """后入内容更新 → 同 seq 覆盖 + 旧值归档 previous_content。"""
    _upsert("u1", "s1", _msgs(("user", "我的账户是 4429853327")))

    # 同一会话位置的消息内容变化（新版本对话/更正）
    n = _upsert("u1", "s1", _msgs(("user", "更正：账户是 8847293001")))
    assert n == 1
    rows = _rows(user_id="u1")
    assert len(rows) == 1  # 不新增
    assert rows[0].seq == 0
    assert rows[0].content == "更正：账户是 8847293001"
    assert rows[0].previous_content == "我的账户是 4429853327"


def test_appended_messages_insert(tmp_memory_db):
    """会话变长：新增序号 INSERT，旧行不动。"""
    _upsert("u1", "s1", _msgs(("user", "a")))
    n = _upsert("u1", "s1", _msgs(("user", "a"), ("assistant", "b")))
    assert n == 1
    rows = _rows(user_id="u1")
    assert [r.seq for r in rows] == [0, 1]
    assert rows[0].previous_content == ""  # 首条未被改动


def test_shorter_conversation_keeps_tail_rows(tmp_memory_db):
    """只增不删：消息变短时旧尾行保留（与 struct facts 一致，无 tombstone）。"""
    _upsert("u1", "s1", _msgs(("user", "a"), ("user", "b"), ("user", "c")))
    n = _upsert("u1", "s1", _msgs(("user", "a")))
    assert n == 0  # seq0 一致 no-op
    assert len(_rows(user_id="u1")) == 3  # 尾行 b/c 保留


def test_system_messages_skipped_but_seq_keeps_position(tmp_memory_db):
    """只存 user/assistant；seq 用原始消息序号（system 占位不压缩）。"""
    _upsert("u1", "s1", _msgs(("system", "ctx"), ("user", "hi")))
    rows = _rows(user_id="u1")
    assert len(rows) == 1
    assert rows[0].seq == 1  # 保持原始位置，双写/跨 provider 稳定


def test_pii_marked_on_write(tmp_memory_db):
    from os_mem.infra.p2check import has_pii

    pii_text = "我的卡号是 4532-8876-9901-3345"
    assert has_pii(pii_text)
    _upsert("u1", "s1", _msgs(("user", pii_text)))
    rows = _rows(user_id="u1")
    assert rows[0].contains_pii is True
    assert rows[0].masked_text  # 已脱敏


def test_unique_index_blocks_duplicate_key(tmp_memory_db):
    """DB 唯一索引兜底：绕过 upsert 直插同键行 → IntegrityError。"""
    from sqlalchemy.exc import IntegrityError

    _upsert("u1", "s1", _msgs(("user", "a")))
    with pytest.raises(IntegrityError):
        _upsert_raw_dup("u1", "s1")


def _upsert_raw_dup(user_id, session_id):
    """绕开 upsert 直接插同 (user, session, seq=0) 行，验证唯一索引。"""
    from os_mem.entries.mem_models import Message
    from os_mem.infra.storage import get_session

    with get_session() as session:
        session.add(Message(
            user_id=user_id, source_session_id=session_id, seq=0,
            content="dup",
        ))
        session.commit()


# --------------------------------------------------------------------- #
#  老库迁移（init_db 幂等）
# --------------------------------------------------------------------- #
def test_legacy_db_migrated(tmp_memory_db):
    """旧 schema（无 seq/previous_content）→ init_db 补列、回填序号、建唯一索引。"""
    from sqlalchemy import text

    from os_mem.infra.storage import get_session, mem_storage

    # 用旧 DDL 手工建表并塞旧数据（无 seq 列）
    with get_session() as session:
        session.exec(text("DROP TABLE IF EXISTS conv_messages"))
        session.exec(text(
            "CREATE TABLE conv_messages ("
            " id TEXT PRIMARY KEY, user_id TEXT NOT NULL, source_session_id TEXT NOT NULL,"
            " content TEXT, contains_pii BOOLEAN DEFAULT 0, masked_text TEXT DEFAULT '',"
            " create_at DATETIME)"
        ))
        session.exec(text(
            "INSERT INTO conv_messages (id, user_id, source_session_id, content, create_at) VALUES "
            "('m1','u9','s9','first', '2026-01-01 00:00:00'),"
            "('m2','u9','s9','second', '2026-01-01 00:00:01'),"
            "('m3','u9','s9','third', '2026-01-01 00:00:02'),"
            "('m4','u9','s8','solo', '2026-01-01 00:00:03')"
        ))
        session.commit()

    # 下一次 get_session（init_db）触发迁移；再触发一次验证幂等
    mem_storage.MemoryDatabase().init_db()
    mem_storage.MemoryDatabase().init_db()

    rows = _rows(user_id="u9")
    # 补列成功 + 按 (user, session) 分组、rowid 序回填 seq
    rows_s9 = sorted(
        (r for r in rows if r.source_session_id == "s9"), key=lambda r: r.seq,
    )
    assert [r.seq for r in rows_s9] == [0, 1, 2]  # s9 三条
    rows_s8 = [r for r in rows if r.source_session_id == "s8"]
    assert len(rows_s8) == 1 and rows_s8[0].seq == 0
    assert all(hasattr(r, "previous_content") for r in rows)

    # 唯一索引已生效
    with pytest.raises(Exception):
        _upsert_raw_dup("u9", "s9")
