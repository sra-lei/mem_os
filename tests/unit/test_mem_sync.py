"""跨机记忆镜像合并单测（os_mem.admin export/import，无 LLM/网络）。

覆盖：export JSON-safe 与字段完整性、import 幂等（重复导入数据稳定）、
conv_meta 门禁语义（本地 COMPLETED 不覆盖 / 非完成态被镜像完成态覆盖）、
struct whole-row 镜像优先（previous_fact 用镜像归档链）、messages 异值覆盖归档。
"""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterator

import pytest

from os_mem.admin import MemAdminService
from os_mem.entries.mem_models import ConversationMeta, Message, StructuredMemory
from os_mem.infra.storage.mem_storage import MemoryDatabase

USER = "layer2_01_multiple_vehicles"
SESS = "vehicles_s1"


@pytest.fixture()
def tmp_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    db_file = tmp_path / "test_memories.db"
    monkeypatch.setattr(MemoryDatabase, "db_path", db_file)
    MemoryDatabase._engines.clear()
    MemoryDatabase().init_db()
    yield db_file


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _seed(admin: MemAdminService) -> None:
    """源端形态：1 用户 2 会话元数据 + 3 事实（1 条带 previous_fact）+ 3 条原文。"""
    now = _now()
    admin.import_memory_batch(
        {
            "conv_meta": [
                {"id": "m1", "user_id": USER, "source_session_id": "vehicles_s1",
                 "message_count": 2, "started_at": now.isoformat(),
                 "ended_at": now.isoformat(), "status": "COMPLETED", "attempts": 1,
                 "last_error": "", "created_at": now.isoformat(), "updated_at": now.isoformat()},
                {"id": "m2", "user_id": USER, "source_session_id": "vehicles_s2",
                 "message_count": 1, "started_at": None, "ended_at": None,
                 "status": "COMPLETED", "attempts": 1, "last_error": "",
                 "created_at": now.isoformat(), "updated_at": now.isoformat()},
            ],
            "struct_memories": [
                {"id": "f1", "user_id": USER, "fact": "用户拥有一辆本田雅阁", "previous_fact": "",
                 "category": "personal", "key": "car_1", "value": "本田雅阁", "confidence": 0.95,
                 "source_conversation_id": "vehicles_s1", "source_chunk_id": "",
                 "created_at": now.isoformat(), "updated_at": now.isoformat()},
                {"id": "f2", "user_id": USER, "fact": "用户第二辆车是丰田卡罗拉",
                 "previous_fact": "用户曾拥有一辆福特福克斯", "category": "personal", "key": "car_2",
                 "value": "丰田卡罗拉", "confidence": 0.9, "source_conversation_id": "vehicles_s1",
                 "source_chunk_id": "", "created_at": now.isoformat(), "updated_at": now.isoformat()},
                {"id": "f3", "user_id": USER, "fact": "用户车辆保险由平安承保", "previous_fact": "",
                 "category": "finance", "key": "insurance", "value": "平安",
                 "confidence": 0.85, "source_conversation_id": "vehicles_s2", "source_chunk_id": "",
                 "created_at": now.isoformat(), "updated_at": now.isoformat()},
            ],
            "conv_messages": [
                {"id": "msg1", "user_id": USER, "source_session_id": "vehicles_s1",
                 "content": "我有一辆本田雅阁", "contains_pii": False, "masked_text": "",
                 "seq": 0, "previous_content": "", "create_at": now.isoformat()},
                {"id": "msg2", "user_id": USER, "source_session_id": "vehicles_s1",
                 "content": "第二辆是丰田", "contains_pii": False, "masked_text": "",
                 "seq": 1, "previous_content": "", "create_at": now.isoformat()},
            ],
        }
    )


def _export(admin: MemAdminService, with_messages: bool = False) -> dict[str, Any]:
    return admin.export_user_data([USER], with_messages=with_messages)


def test_export_shape_json_safe(tmp_db: Path) -> None:
    admin = MemAdminService(allow_live=False)
    _seed(admin)
    # 默认不含原文
    data = _export(admin)
    assert data["conv_messages"] == []
    assert len(data["conv_meta"]) == 2
    assert len(data["struct_memories"]) == 3
    # JSON-safe：无 datetime 对象
    import json

    json.dumps(data, ensure_ascii=False)  # 不抛 TypeError 即全部可序列化
    assert data["struct_memories"][0]["id"] == "f1"
    # with_messages 附带原文
    data2 = _export(admin, with_messages=True)
    assert len(data2["conv_messages"]) == 2
    assert data2["conv_messages"][0]["seq"] == 0


def test_import_idempotent_data_stable(tmp_db: Path) -> None:
    admin = MemAdminService(allow_live=False)
    _seed(admin)
    first = _export(admin, with_messages=True)
    # 重复导入：数据不变（whole-row 覆盖同值 no-op、conv_meta COMPLETED 保护）
    res = admin.import_memory_batch(first)
    assert res["conv_meta_ins"] == 0 and res["conv_meta_upd"] == 0  # 本地 COMPLETED 不覆盖
    assert res["struct_ins"] == 0  # 同键已存在（whole-row 覆盖，非新增）
    assert res["msg_ins"] == 0 and res["msg_upd"] == 0  # 同值 no-op
    second = _export(admin, with_messages=True)
    assert second == first  # 数据稳定


def test_conv_meta_noncompleted_overridden_by_mirror(tmp_db: Path) -> None:
    from sqlmodel import Session, select

    admin = MemAdminService(allow_live=False)
    # 本地先有一条 PENDING（如 dev 还没提取/提取失败）的同一会话
    now = _now()
    admin.import_memory_batch(
        {
            "conv_meta": [
                {"id": "local_m", "user_id": USER, "source_session_id": "vehicles_s1",
                 "message_count": 0, "started_at": None, "ended_at": None,
                 "status": "PENDING", "attempts": 0, "last_error": "boom",
                 "created_at": now.isoformat(), "updated_at": now.isoformat()}
            ],
            "struct_memories": [], "conv_messages": [],
        }
    )
    # 导入源端 COMPLETED 镜像 → 门禁打开为完成态（dev 免重提取）
    _seed(admin)
    with Session(MemoryDatabase().get_engine()) as s:
        row = s.exec(
            select(ConversationMeta).where(
                ConversationMeta.user_id == USER,
                ConversationMeta.source_session_id == "vehicles_s1",
            )
        ).first()
        assert row.status == "COMPLETED"
        assert row.last_error == ""
        assert row.attempts == 0  # 本地接管计数保留


def test_struct_whole_row_mirror_wins(tmp_db: Path) -> None:
    from sqlmodel import Session, select

    admin = MemAdminService(allow_live=False)
    # 本地已有同 (user, cat, key) 的旧值（dev 本地版本）
    now = _now()
    admin.import_memory_batch(
        {
            "conv_meta": [], "conv_messages": [],
            "struct_memories": [
                {"id": "local_f", "user_id": USER, "fact": "用户的车是本地旧值",
                 "previous_fact": "", "category": "personal", "key": "car_2",
                 "value": "本地旧值", "confidence": 0.5,
                 "source_conversation_id": "", "source_chunk_id": "",
                 "created_at": now.isoformat(), "updated_at": now.isoformat()}
            ],
        }
    )
    _seed(admin)  # 镜像：car_2 值为丰田卡罗拉、previous_fact=福特福克斯
    with Session(MemoryDatabase().get_engine()) as s:
        row = s.exec(
            select(StructuredMemory).where(
                StructuredMemory.user_id == USER,
                StructuredMemory.category == "personal",
                StructuredMemory.key == "car_2",
            )
        ).first()
        assert row.value == "丰田卡罗拉"          # 镜像优先
        assert row.previous_fact == "用户曾拥有一辆福特福克斯"  # previous_fact 用镜像归档链
        assert row.id == "local_f"                # 本地行 id 保留


def test_message_conflict_archive(tmp_db: Path) -> None:
    from sqlmodel import Session, select

    admin = MemAdminService(allow_live=False)
    # 本地同 (user, session, seq) 不同内容
    now = _now()
    admin.import_memory_batch(
        {
            "conv_meta": [], "struct_memories": [],
            "conv_messages": [
                {"id": "local_msg", "user_id": USER, "source_session_id": "vehicles_s1",
                 "content": "我有一辆本地车", "contains_pii": False, "masked_text": "",
                 "seq": 0, "previous_content": "", "create_at": now.isoformat()}
            ],
        }
    )
    _seed(admin)  # 镜像 seq0 = "我有一辆本田雅阁"
    with Session(MemoryDatabase().get_engine()) as s:
        row = s.exec(
            select(Message).where(
                Message.user_id == USER,
                Message.source_session_id == "vehicles_s1",
                Message.seq == 0,
            )
        ).first()
        assert row.content == "我有一辆本田雅阁"
        assert row.previous_content == "我有一辆本地车"  # 旧值归档


def test_import_scoped_to_mirror_users(tmp_db: Path) -> None:
    admin = MemAdminService(allow_live=False)
    _seed(admin)
    data = _export(admin)
    # 本地另有未被镜像覆盖的用户 → 不受影响
    now = _now()
    admin.import_memory_batch(
        {
            "conv_meta": [], "conv_messages": [],
            "struct_memories": [
                {"id": "other", "user_id": "layer1_01_bank_account",
                 "fact": "本地已有用户的事实", "previous_fact": "", "category": "personal",
                 "key": "full_name", "value": "X", "confidence": 1.0,
                 "source_conversation_id": "", "source_chunk_id": "",
                 "created_at": now.isoformat(), "updated_at": now.isoformat()}
            ],
        }
    )
    admin.import_memory_batch(data)  # 导入 layer2 用户镜像
    exported = _export(admin)
    assert len(exported["struct_memories"]) == 3  # layer2 用户仍是 3 条
    other = admin.list_facts("layer1_01_bank_account")
    assert other["total"] == 1  # 未被镜像触碰
