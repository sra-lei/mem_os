"""事实 category 受控词表（os_mem 顶层轻模块，供提取链路与管理窗口共用）。

- 种子（CATEGORY_SEED）：首次建表由 ``mem_storage.init_db`` 灌入（幂等，空表才写）；
- 读取（list_active_categories / render_categories_section）：提取 prompt 渲染与
  ``validate_response`` 校验的数据源；
- **兜底**：词表表缺失/查询异常时回退内置种子（保证校验/渲染永不因词表故障全拒，
  宁可回到旧白名单行为）。词表演进走 ``os_mem.admin`` 窗口，见
  docs/方案-事实category与key词表管理.md。

本模块顶层 import 无副作用（仅常量）；DB 访问函数内 lazy。
"""
from __future__ import annotations

from typing import Any

# --------------------------------------------------------------------------- #
#  内置种子：英文规范 id 为稳定标识；name_zh/name_en 双语；sort 为展示/渲染顺序
# --------------------------------------------------------------------------- #

CATEGORY_SEED: list[dict[str, Any]] = [
    {"category": "personal", "name_zh": "个人", "name_en": "Personal", "sort": 1},
    {"category": "contact", "name_zh": "联系方式", "name_en": "Contact", "sort": 2},
    {"category": "preference", "name_zh": "偏好", "name_en": "Preferences", "sort": 3},
    {"category": "health", "name_zh": "健康", "name_en": "Health", "sort": 4},
    {"category": "travel", "name_zh": "出行", "name_en": "Travel", "sort": 5},
    {"category": "work", "name_zh": "工作", "name_en": "Work", "sort": 6},
    {"category": "finance", "name_zh": "财务", "name_en": "Finance", "sort": 7},
    {"category": "family", "name_zh": "家庭", "name_en": "Family", "sort": 8},
    {"category": "education", "name_zh": "教育", "name_en": "Education", "sort": 9},
    {"category": "other", "name_zh": "其他", "name_en": "Other", "sort": 10},
]

# 表读失败时的兜底白名单（同现状 ALLOWED_CATEGORIES 常量语义）
_FALLBACK_ACTIVE = [c["category"] for c in CATEGORY_SEED]


def _read_rows(active_only: bool) -> list[dict[str, Any]]:
    """读 fact_category 表；异常时回退内置种子。"""
    try:
        from os_mem.entries.mem_models import FactCategory
        from os_mem.infra.storage.mem_storage import MemoryDatabase
        from sqlmodel import Session, select

        with Session(MemoryDatabase().get_engine()) as session:
            stmt = select(FactCategory).order_by(FactCategory.sort, FactCategory.category)
            if active_only:
                stmt = stmt.where(FactCategory.active == 1)
            rows = session.exec(stmt).all()
        if not rows:
            # 空表（未 seed/被清空）→ 回退种子，保证校验不空转
            return [
                {k: c[k] for k in ("category", "name_zh", "name_en", "sort") if k in c}
                | {"active": 1}
                for c in CATEGORY_SEED
            ]
        return [
            {
                "category": r.category,
                "name_zh": r.name_zh,
                "name_en": r.name_en,
                "sort": r.sort,
                "active": r.active,
            }
            for r in rows
        ]
    except Exception:  # noqa: BLE001 - 兜底：词表故障不影响提取链路既有行为
        return [
            {k: c[k] for k in ("category", "name_zh", "name_en", "sort")}
            | {"active": 1}
            for c in CATEGORY_SEED
        ]


def list_active_categories() -> list[str]:
    """校验用：当前 active 的 category 英文 id 列表（词表故障回退内置 10 类）。"""
    rows = _read_rows(active_only=True)
    cats = [r["category"] for r in rows if r.get("active", 1) != 0]
    return cats or list(_FALLBACK_ACTIVE)


def render_categories_section() -> str:
    """prompt 渲染用：active category 双语列表段（如 ``personal（个人）, contact（联系方式）…``）。"""
    rows = _read_rows(active_only=True)
    active = [r for r in rows if r.get("active", 1) != 0] or [
        {"category": c, "name_zh": "", "name_en": ""} for c in _FALLBACK_ACTIVE
    ]
    parts: list[str] = []
    for r in active:
        label = r["category"]
        zh = (r.get("name_zh") or "").strip()
        if zh:
            label = f"{label}（{zh}）"
        parts.append(label)
    return ", ".join(parts)
