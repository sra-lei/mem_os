"""事实 category 受控词表单测（os_mem.vocab + mem_storage seed，无 LLM/网络）。

覆盖：seed 幂等、读取/渲染双语、active 过滤、表缺失/空表兜底回退。
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterator

import pytest
from sqlmodel import Session, func, select

from os_mem.entries.mem_models import FactCategory
from os_mem.infra.storage.mem_storage import MemoryDatabase
from os_mem.vocab import CATEGORY_SEED, list_active_categories, render_categories_section


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
