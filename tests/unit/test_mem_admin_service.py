"""os_mem 管理窗口单测（os_mem.admin.MemAdminService，无 LLM / 无网络）。

覆盖：用户聚合、事实 upsert/编辑/删除/清空（含 conv_meta 门禁重置）、
投影重建，以及「投影失败/离线不阻断 SQLite」的尽力同步语义。

隔离：
- SQLite 走 tmp 库（monkeypatch MemoryDatabase.db_path + _engines.clear()）；
- 投影用 FakeProjection 注入（MemAdminService(projection=fake)），
  离线场景用 MemAdminService(allow_live=False)（禁止触碰真实向量库）。
"""
from __future__ import annotations

import copy
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterator

import pytest
from sqlmodel import Session, select

from os_mem.admin import MemAdminService
from os_mem.entries.mem_models import ConversationMeta, Message, StructuredMemory

USER = "layer1_01_bank_account"
CONV = "bank_setup_001"


# ---------------------------------------------------------------------------
# 夹具
# ---------------------------------------------------------------------------


@pytest.fixture()
def tmp_memory_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """把记忆库指向临时文件并建表，避免污染真实 memories.db。"""
    from os_mem.infra.storage import mem_storage

    db_file = tmp_path / "test_memories.db"
    monkeypatch.setattr(mem_storage.MemoryDatabase, "db_path", db_file)
    mem_storage.MemoryDatabase._engines.clear()
    mem_storage.MemoryDatabase().init_db()
    yield db_file


class FakeProjection:
    """记录 delete/upsert 调用；可注入失败模拟 Milvus 不可用。"""

    def __init__(self) -> None:
        self.delete_calls: list[dict[str, Any]] = []
        self.upsert_calls: list[list[dict[str, Any]]] = []
        self.delete_error: Exception | None = None
        self.upsert_error: Exception | None = None

    def delete(
        self, user_id: str, category: str | None = None, keys: list[str] | None = None
    ) -> int:
        if self.delete_error:
            raise self.delete_error
        self.delete_calls.append({"user_id": user_id, "category": category, "keys": keys})
        return len(keys) if keys else 1

    def upsert(self, records: list[dict[str, Any]]) -> int:
        if self.upsert_error:
            raise self.upsert_error
        self.upsert_calls.append(copy.deepcopy(records))
        return len(records)


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _seed() -> None:
    """一个用户：3 条事实（含 1 条带 previous_fact）+ 3 条原文 + 1 条 COMPLETED 会话。"""
    from os_mem.infra.storage.mem_storage import MemoryDatabase

    with Session(MemoryDatabase().get_engine()) as session:
        session.add_all(
            [
                StructuredMemory(
                    user_id=USER,
                    fact="用户全名是 Michael James Robertson",
                    previous_fact="用户全名是 Harrison Robertson",
                    category="personal",
                    key="full_name",
                    value="Michael James Robertson",
                    confidence=1.0,
                    source_conversation_id=CONV,
                    created_at=_now(),
                    updated_at=_now(),
                ),
                StructuredMemory(
                    user_id=USER,
                    fact="用户的支票账户号码是 4429853327",
                    previous_fact="",
                    category="finance",
                    key="checking_account_number",
                    value="4429853327",
                    confidence=0.95,
                    source_conversation_id=CONV,
                    created_at=_now(),
                    updated_at=_now(),
                ),
                StructuredMemory(
                    user_id=USER,
                    fact="用户的电子邮箱是 mrobertson85@email.com",
                    previous_fact="",
                    category="contact",
                    key="email",
                    value="mrobertson85@email.com",
                    confidence=0.95,
                    source_conversation_id=CONV,
                    created_at=_now(),
                    updated_at=_now(),
                ),
            ]
        )
        for seq, content in enumerate(
            ["我叫 Michael Robertson", "我的支票账户是 4429853327", "邮箱 mrobertson85@email.com"]
        ):
            session.add(
                Message(
                    user_id=USER,
                    source_session_id=CONV,
                    content=content,
                    seq=seq,
                    create_at=_now(),
                )
            )
        session.add(
            ConversationMeta(
                user_id=USER,
                source_session_id=CONV,
                message_count=3,
                status="COMPLETED",
                attempts=1,
                created_at=_now(),
                updated_at=_now(),
            )
        )
        session.commit()


def _facts(user_id: str = USER) -> list[dict[str, Any]]:
    return MemAdminService(allow_live=False).list_facts(user_id)["items"]


# ---------------------------------------------------------------------------
# 读：聚合 / 列表 / 原文
# ---------------------------------------------------------------------------


def test_aggregate_users(tmp_memory_db: Path) -> None:
    _seed()
    users = MemAdminService(allow_live=False).list_users()
    assert len(users) == 1
    u = users[0]
    assert u["user_id"] == USER
    assert u["fact_count"] == 3
    assert u["categories"] == {"personal": 1, "finance": 1, "contact": 1}
    assert u["message_count"] == 3
    assert u["session_count"] == 1
    assert u["conv_status"] == {"COMPLETED": 1}
    assert u["latest_activity"] is not None


def test_list_facts_filters(tmp_memory_db: Path) -> None:
    _seed()
    admin = MemAdminService(allow_live=False)
    page = admin.list_facts(USER)
    assert page["total"] == 3 and len(page["items"]) == 3
    page_cat = admin.list_facts(USER, category="finance")
    assert page_cat["total"] == 1
    assert page_cat["items"][0]["key"] == "checking_account_number"
    page_q = admin.list_facts(USER, q="4429853")
    assert page_q["total"] == 1
    assert page_q["items"][0]["category"] == "finance"
    # 空结果用户：不报错
    assert admin.list_facts("nobody")["total"] == 0


def test_get_fact_and_messages(tmp_memory_db: Path) -> None:
    _seed()
    admin = MemAdminService(allow_live=False)
    row = admin.get_fact(USER, _facts()[0]["id"])
    assert row["user_id"] == USER
    with pytest.raises(LookupError):
        admin.get_fact(USER, "no-such-id")
    with pytest.raises(LookupError):
        admin.get_fact("other_user", _facts()[0]["id"])
    msgs = admin.list_messages(USER, CONV)
    assert [m["seq"] for m in msgs] == [0, 1, 2]
    assert msgs[0]["content"] == "我叫 Michael Robertson"


# ---------------------------------------------------------------------------
# 写：upsert / 编辑 / 删除 / 清空（投影尽力同步）
# ---------------------------------------------------------------------------


def test_upsert_fact_insert(tmp_memory_db: Path) -> None:
    fake = FakeProjection()
    _seed()
    admin = MemAdminService(projection=fake)
    res = admin.upsert_fact(
        USER,
        category="work",
        key="employer",
        fact="用户在 Acme 公司工作",
        value="Acme",
    )
    assert res["created"] is True
    assert res["projection"] == "synced"
    assert fake.delete_calls == [
        {"user_id": USER, "category": "work", "keys": ["employer"]}
    ]
    rec = fake.upsert_calls[0][0]
    assert rec["fact"] == "用户在 Acme 公司工作"
    assert rec["user_id"] == USER and rec["category"] == "work" and rec["key"] == "employer"
    assert len(rec["id"]) == 32  # 独立投影 uuid（非 SQLite id）
    assert "T" in rec["updated_at"]  # naive UTC ISO 字符串
    assert len(_facts()) == 4


def test_upsert_fact_existing_key_updates_and_archives(tmp_memory_db: Path) -> None:
    fake = FakeProjection()
    _seed()
    admin = MemAdminService(projection=fake)
    res = admin.upsert_fact(
        USER,
        category="finance",
        key="checking_account_number",
        fact="用户的支票账户号码是 9988776655",
        value="9988776655",
        confidence=1.0,
    )
    assert res["created"] is False
    assert res["projection"] == "synced"
    same_key = [r for r in _facts() if r["key"] == "checking_account_number"]
    assert len(same_key) == 1  # 同键覆盖不产生重复行
    assert same_key[0]["value"] == "9988776655"
    assert same_key[0]["previous_fact"] == "用户的支票账户号码是 4429853327"
    # 投影：删旧 + 插新各一次
    assert fake.delete_calls[-1]["keys"] == ["checking_account_number"]
    assert fake.upsert_calls[-1][0]["value"] == "9988776655"


def test_update_fact_fields(tmp_memory_db: Path) -> None:
    fake = FakeProjection()
    _seed()
    admin = MemAdminService(projection=fake)
    fid = [r for r in _facts() if r["key"] == "email"][0]["id"]
    # 只改 value/confidence：fact 不变 → previous_fact 不归档
    res = admin.update_fact(USER, fid, value="new@email.com", confidence=0.9)
    assert res["changed"] is True and res["projection"] == "synced"
    row = admin.get_fact(USER, fid)
    assert row["value"] == "new@email.com" and row["confidence"] == 0.9
    assert row["previous_fact"] == ""
    n_delete = len(fake.delete_calls)
    # no-op 更新：不触发投影
    res2 = admin.update_fact(USER, fid, value="new@email.com", confidence=0.9)
    assert res2["changed"] is False and res2["projection"] == "skipped"
    assert len(fake.delete_calls) == n_delete


def test_update_fact_archives_old_sentence(tmp_memory_db: Path) -> None:
    fake = FakeProjection()
    _seed()
    admin = MemAdminService(projection=fake)
    fid = [r for r in _facts() if r["key"] == "email"][0]["id"]
    admin.update_fact(USER, fid, fact="用户的电子邮箱是 patched@email.com")
    row = admin.get_fact(USER, fid)
    assert row["previous_fact"] == "用户的电子邮箱是 mrobertson85@email.com"
    assert row["fact"] == "用户的电子邮箱是 patched@email.com"


def test_delete_fact(tmp_memory_db: Path) -> None:
    fake = FakeProjection()
    _seed()
    admin = MemAdminService(projection=fake)
    fid = [r for r in _facts() if r["key"] == "email"][0]["id"]
    res = admin.delete_fact(USER, fid)
    assert res["affected"] == 1
    assert len(_facts()) == 2
    assert fake.delete_calls[-1] == {
        "user_id": USER,
        "category": "contact",
        "keys": ["email"],
    }
    with pytest.raises(LookupError):
        admin.get_fact(USER, fid)


def test_clear_user_keeps_messages_and_gate(tmp_memory_db: Path) -> None:
    fake = FakeProjection()
    _seed()
    admin = MemAdminService(projection=fake)
    res = admin.clear_user(USER, reset_conv_meta=False)
    assert res["deleted_facts"] == 3 and res["reset_sessions"] == 0
    assert len(_facts()) == 0
    # 原文保留
    assert len(admin.list_messages(USER, CONV)) == 3
    # conv_meta 门禁不动；投影整用户删除（无 category/keys 限定）
    from os_mem.infra.storage.mem_storage import MemoryDatabase

    with Session(MemoryDatabase().get_engine()) as s:
        meta = s.exec(select(ConversationMeta)).all()
        assert meta[0].status == "COMPLETED"
    assert fake.delete_calls[-1] == {"user_id": USER, "category": None, "keys": None}


def test_clear_user_with_reset_conv_meta(tmp_memory_db: Path) -> None:
    _seed()
    admin = MemAdminService(allow_live=False)  # 离线模式：永不连向量库
    res = admin.clear_user(USER, reset_conv_meta=True)
    assert res["deleted_facts"] == 3 and res["reset_sessions"] == 1
    assert res["projection"] == "failed"  # 离线 → failed + 警示
    from os_mem.infra.storage.mem_storage import MemoryDatabase

    with Session(MemoryDatabase().get_engine()) as s:
        meta = s.exec(select(ConversationMeta)).all()
        assert meta[0].status == "PENDING"  # 门禁已开：下次 ingest 可重新提取
        assert meta[0].last_error == ""
        assert len(s.exec(select(Message)).all()) == 3  # 原文仍是重提取素材


# ---------------------------------------------------------------------------
# 投影：重建 / 失败不阻断 SQLite / 离线
# ---------------------------------------------------------------------------


def test_rebuild_projection(tmp_memory_db: Path) -> None:
    fake = FakeProjection()
    _seed()
    admin = MemAdminService(projection=fake)
    res = admin.rebuild_projection(USER)
    assert res["synced"] == 3 and res["projection"] == "synced"
    # 先整用户删除，再全量 upsert
    assert fake.delete_calls[-1] == {"user_id": USER, "category": None, "keys": None}
    assert len(fake.upsert_calls[-1]) == 3
    for rec in fake.upsert_calls[-1]:
        assert "T" in rec["updated_at"]


def test_rebuild_projection_offline(tmp_memory_db: Path) -> None:
    _seed()
    admin = MemAdminService(allow_live=False)
    res = admin.rebuild_projection(USER)
    assert res["projection"] == "failed" and res["synced"] == 0
    assert "重建投影" in (res["warning"] or "")


def test_projection_failure_does_not_rollback_sqlite(tmp_memory_db: Path) -> None:
    fake = FakeProjection()
    fake.delete_error = RuntimeError("milvus down")
    _seed()
    admin = MemAdminService(projection=fake)
    res = admin.upsert_fact(
        USER,
        category="finance",
        key="checking_account_number",
        fact="用户的支票账户号码是 12345",
        value="12345",
    )
    assert res["projection"] == "failed"
    assert "重建投影" in (res["warning"] or "")
    # SQLite 已提交、不回滚；失败时不插新（避免同 key 双版本）
    same_key = [r for r in _facts() if r["key"] == "checking_account_number"]
    assert len(same_key) == 1 and same_key[0]["value"] == "12345"
    assert fake.upsert_calls == []


def test_upsert_fact_offline_no_projection(tmp_memory_db: Path) -> None:
    _seed()
    admin = MemAdminService(allow_live=False)
    res = admin.upsert_fact(
        USER,
        category="work",
        key="employer",
        fact="用户待业",
        value="unemployed",
    )
    assert res["projection"] == "failed"
    assert "重建投影" in (res["warning"] or "")
    assert len(_facts()) == 4  # SQLite 侧照常生效


# ---------------------------------------------------------------------------
# fact_category 受控词表（窗口管理 → 词表读取联动）
# ---------------------------------------------------------------------------


def test_admin_list_categories_seeded(tmp_memory_db: Path) -> None:
    admin = MemAdminService(allow_live=False)
    cats = admin.list_categories()
    assert len(cats) == 10
    assert cats[0]["category"] == "personal" and cats[0]["name_zh"] == "个人"
    active = admin.list_categories(active_only=True)
    assert [c["category"] for c in active] == [c["category"] for c in cats]


def test_admin_upsert_category_reflects_in_vocab(tmp_memory_db: Path) -> None:
    from os_mem.vocab import list_active_categories, render_categories_section

    admin = MemAdminService(allow_live=False)
    res = admin.upsert_category("pet", name_zh="宠物", name_en="Pets", sort=11)
    assert res["created"] is True
    # 更新同名词条（覆盖而非新增）
    res2 = admin.upsert_category("pet", name_zh="宠物护理", name_en="Pets", sort=11)
    assert res2["created"] is False
    cats = admin.list_categories(active_only=False)
    assert len(cats) == 11
    # 词表读取（prompt 渲染/校验白名单）立即联动
    assert "pet" in list_active_categories()
    assert "pet（宠物护理）" in render_categories_section()


def test_admin_deactivate_category(tmp_memory_db: Path) -> None:
    from os_mem.vocab import list_active_categories, render_categories_section

    admin = MemAdminService(allow_live=False)
    res = admin.set_category_active("finance", False)
    assert res["active"] == 0
    assert "finance" not in list_active_categories()
    assert "finance" not in render_categories_section()
    assert len(admin.list_categories()) == 9
    # 不存在词条 → LookupError
    with pytest.raises(LookupError):
        admin.set_category_active("no_such_cat", False)
    # 重新启用
    admin.set_category_active("finance", True)
    assert "finance" in list_active_categories()
