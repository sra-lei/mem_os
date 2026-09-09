"""事实 category 受控词表单测（os_mem.vocab + mem_storage seed，无 LLM/网络）。

覆盖：seed 幂等、读取/渲染双语、active 过滤、表缺失/空表兜底回退。
"""
from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlmodel import Session, func, select

from os_mem.entries.mem_models import FactCategory
from os_mem.extractor.fact_extractor import FactExtractor
from os_mem.extractor.prompt import SYSTEM_PROMPT, build_extract_messages
from os_mem.infra.storage.mem_storage import MemoryDatabase
from os_mem.vocab import (
    CATEGORY_SEED,
    list_active_categories,
    render_categories_section,
)


@pytest.fixture()
def tmp_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """临时记忆库（含词表 seed）。"""
    db_file = tmp_path / "test_memories.db"
    monkeypatch.setattr(MemoryDatabase, "db_path", db_file)
    MemoryDatabase._engines.clear()
    MemoryDatabase().init_db()
    yield db_file


def _count() -> int:
    with Session(MemoryDatabase().get_engine()) as s:
        return int(s.exec(select(func.count()).select_from(FactCategory)).one() or 0)


def _set_active(category: str, active: int) -> None:
    with Session(MemoryDatabase().get_engine()) as s:
        row = s.get(FactCategory, category)
        assert row is not None
        row.active = active
        s.add(row)
        s.commit()


def test_seed_idempotent(tmp_db: Path) -> None:
    assert _count() == len(CATEGORY_SEED) == 10
    # 再次 init_db：seed 不重复、不覆盖
    MemoryDatabase().init_db()
    assert _count() == 10
    # 人工演进不被 seed 覆盖：改名后再 init_db 应保持
    with Session(MemoryDatabase().get_engine()) as s:
        row = s.get(FactCategory, "personal")
        row.name_zh = "个人资料（改）"
        s.add(row)
        s.commit()
    MemoryDatabase().init_db()
    with Session(MemoryDatabase().get_engine()) as s:
        assert s.get(FactCategory, "personal").name_zh == "个人资料（改）"


def test_list_active_categories(tmp_db: Path) -> None:
    cats = list_active_categories()
    assert cats == [c["category"] for c in CATEGORY_SEED]  # 10 个，按 sort
    # 停用 finance → 校验白名单不再含它
    _set_active("finance", 0)
    cats2 = list_active_categories()
    assert "finance" not in cats2 and len(cats2) == 9


def test_render_categories_section_bilingual(tmp_db: Path) -> None:
    rendered = render_categories_section()
    # 双语：英文 id + 中文名
    assert "personal（个人）" in rendered
    assert "finance（财务）" in rendered
    assert "other（其他）" in rendered
    # 按 sort 排序：personal 开头
    assert rendered.startswith("personal（个人）")
    # 停用后不再渲染
    _set_active("finance", 0)
    rendered2 = render_categories_section()
    assert "finance" not in rendered2


def test_fallback_when_table_missing(tmp_db: Path) -> None:
    # 删表模拟词表故障：读取回退内置种子（校验不空转、不整批拒绝）
    with MemoryDatabase().get_engine().begin() as conn:
        conn.execute(__import__("sqlalchemy").text("DROP TABLE fact_category"))
    assert list_active_categories() == [c["category"] for c in CATEGORY_SEED]
    rendered = render_categories_section()
    assert "personal（个人）" in rendered and "finance（财务）" in rendered


def test_fallback_when_table_empty(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # 建表但不 seed（绕过 seed 场景）：空表回退种子
    db_file = tmp_path / "empty.db"
    monkeypatch.setattr(MemoryDatabase, "db_path", db_file)
    MemoryDatabase._engines.clear()
    from sqlalchemy import text

    from os_mem.entries.mem_models import ConversationMeta, Message, StructuredMemory
    from sqlmodel import SQLModel

    engine = MemoryDatabase().get_engine()
    SQLModel.metadata.create_all(
        engine,
        tables=[Message.__table__, StructuredMemory.__table__, ConversationMeta.__table__],
    )
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS fact_category"))
        # 手动只建空词表（无 seed 行）
        conn.execute(text("CREATE TABLE fact_category (category TEXT PRIMARY KEY, name_zh TEXT, name_en TEXT, sort INTEGER, active INTEGER, created_at DATETIME, updated_at DATETIME)"))
    assert _count() == 0
    assert list_active_categories() == [c["category"] for c in CATEGORY_SEED]


# ---------------------------------------------------------------------------
# 提取链路联动（批2）：prompt 渲染 / 校验读表
# ---------------------------------------------------------------------------


def _fact_json(category: str) -> str:
    return (
        '[{"fact":"用户邮箱是 a@b.com","category":"'
        + category
        + '","key":"email","value":"a@b.com","confidence":0.9}]'
    )


def test_validate_category_out_of_range(tmp_db: Path) -> None:
    # 合法 category：通过（返回 facts）
    facts = FactExtractor.validate_response(_fact_json("contact"))
    assert facts and facts[0].category == "contact"
    # 非法 category：整批拒绝（validate 返回 []，语义与旧 ALLOWED_CATEGORIES 一致）
    assert FactExtractor.validate_response(_fact_json("bogus")) == []


def test_deactivate_category_affects_validation_and_prompt(tmp_db: Path) -> None:
    _set_active("finance", 0)
    # 校验：finance 出界 → 整批拒绝
    assert FactExtractor.validate_response(_fact_json("finance")) == []
    assert FactExtractor.validate_response(_fact_json("contact"))  # 其余不受影响
    # prompt：渲染段不再含 finance
    assert "finance" not in render_categories_section()


def test_build_extract_messages_renders_categories_section(tmp_db: Path) -> None:
    # 模板保留占位（test_prompt_fp 依赖 {max_facts} 仍在模板）
    assert "{categories_section}" in SYSTEM_PROMPT
    msgs = build_extract_messages("你好")
    system = msgs[0]["content"]
    # 渲染完成：无占位残留，双语列表出现
    assert "{categories_section}" not in system
    assert "{max_facts}" not in system
    assert "category 必须从以下列表选取：personal（个人）, contact（联系方式）" in system
    assert msgs[1]["content"].startswith("请从以下对话中提取结构化事实")
