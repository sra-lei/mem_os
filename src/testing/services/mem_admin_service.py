"""EvalView 记忆管理服务（src/testing 管理侧，见 docs/方案-EvalView记忆管理.md）。

读写 memories.db（业务权威源：struct_memories / conv_messages / conv_meta）；
Milvus 投影同步以 duck-type 依赖注入接入 —— 测试传 fake、路由 lazy 构造真实
对象（LiveProjection）。本模块只 import 无副作用的 ORM 模型，engine/向量对象
一律函数内 lazy 构造：EvalView 进程 import 本模块不连云端、离线可读可写。

铁律（与 A 批投影方案一致）：
1. SQLite 先写（确定成功、权威）；
2. 投影尽力同步：删除失败不插新（避免同 key 双版本）、失败只警示不阻断不回滚，
   提示「重建投影」兜底（rebuild_projection = 删该用户全部向量 → 批量 embed → 重插）。
"""
from __future__ import annotations

import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any, Iterator

from sqlalchemy import delete as sa_delete
from sqlalchemy import update as sa_update
from sqlmodel import Session, func, or_, select

from os_mem.entries.mem_models import ConversationMeta, Message, StructuredMemory

# ========================================================================== #
#  DB 访问
# ========================================================================== #


def get_memory_engine():
    """业务库引擎：复用 os_mem.MemoryDatabase（相对路径恒锚定 src/os_mem/data/）。

    引擎缓存由 MemoryDatabase._engines 按 db_path 统一管理 —— 测试夹具沿用
    mem_storage 单测同款：monkeypatch MemoryDatabase.db_path + _engines.clear()。
    """
    from os_mem.infra.storage.mem_storage import MemoryDatabase

    return MemoryDatabase().get_engine()


@contextmanager
def memory_session() -> Iterator[Session]:
    """业务库会话。只读写行，绝不 init_db（防止对业务库做建表/迁移副作用）。"""
    with Session(get_memory_engine()) as session:
        yield session


def _utcnow() -> datetime:
    """naive UTC（与 memories.db 存量时间戳口径一致）。"""
    return datetime.now(UTC).replace(tzinfo=None)


def _row_to_projection_record(row: StructuredMemory) -> dict[str, Any]:
    """ORM 行 → 投影写入记录（字段与 vec_storage.add_structured_memories 对齐）。

    注意：投影行 id 是独立随机 uuid（与 SQLite id 无关，删除只能按
    (user_id, category, key) 过滤）；updated_at 用 naive UTC ISO 字符串
    （与管线 struc_mem_service 写投影的格式一致，供元数据比较）。
    """
    return {
        "id": uuid.uuid4().hex,
        "fact": row.fact,
        "category": row.category,
        "key": row.key,
        "value": row.value,
        "user_id": row.user_id,
        "updated_at": row.updated_at.isoformat() if isinstance(row.updated_at, datetime) else str(row.updated_at),
    }


# ========================================================================== #
#  读操作
# ========================================================================== #


def aggregate_users(session: Session) -> list[dict[str, Any]]:
    """聚合用户级摘要：事实数/类别分布/消息数/会话数+状态/最近活动。

    数据源三表并集（可能有记忆但无事实的用户也展示）。
    """
    out: dict[str, dict[str, Any]] = {}

    def bucket(user_id: str) -> dict[str, Any]:
        b = out.get(user_id)
        if b is None:
            b = {
                "user_id": user_id,
                "fact_count": 0,
                "categories": {},
                "message_count": 0,
                "session_count": 0,
                "conv_status": {},
                "latest_activity": None,
            }
            out[user_id] = b
        return b

    # struct_memories：事实数 + 最近活动 + 类别分布
    fact_rows = session.exec(
        select(
            StructuredMemory.user_id,
            func.count(StructuredMemory.id),
            func.max(StructuredMemory.updated_at),
        ).group_by(StructuredMemory.user_id)
    ).all()
    cat_rows = session.exec(
        select(
            StructuredMemory.user_id,
            StructuredMemory.category,
            func.count(StructuredMemory.id),
        ).group_by(StructuredMemory.user_id, StructuredMemory.category)
    ).all()
    for user_id, count, latest in fact_rows:
        b = bucket(user_id)
        b["fact_count"] = int(count or 0)
        b["latest_activity"] = latest
    for user_id, cat, count in cat_rows:
        b = bucket(user_id)
        b["categories"][cat] = int(count or 0)

    # conv_messages：原文条数
    for user_id, count in session.exec(
        select(Message.user_id, func.count(Message.id)).group_by(Message.user_id)
    ).all():
        bucket(user_id)["message_count"] = int(count or 0)

    # conv_meta：会话行数 + 状态分布
    for user_id, status, count in session.exec(
        select(
            ConversationMeta.user_id,
            ConversationMeta.status,
            func.count(ConversationMeta.id),
        ).group_by(ConversationMeta.user_id, ConversationMeta.status)
    ).all():
        b = bucket(user_id)
        b["session_count"] += int(count or 0)
        b["conv_status"][status] = b["conv_status"].get(status, 0) + int(count or 0)

    return sorted(out.values(), key=lambda x: x["user_id"])


def list_facts(
    session: Session,
    user_id: str,
    *,
    category: str | None = None,
    q: str | None = None,
    offset: int = 0,
    limit: int = 100,
) -> dict[str, Any]:
    """分页列出某用户事实；category 精确 + q 对 fact/key/value 子串模糊匹配。"""
    stmt = select(StructuredMemory).where(StructuredMemory.user_id == user_id)
    if category:
        stmt = stmt.where(StructuredMemory.category == category)
    if q:
        kw = f"%{q}%"
        stmt = stmt.where(
            or_(
                StructuredMemory.fact.like(kw),
                StructuredMemory.key.like(kw),
                StructuredMemory.value.like(kw),
            )
        )
    total = int(session.exec(select(func.count()).select_from(stmt.subquery())).one() or 0)
    rows = session.exec(
        stmt.order_by(StructuredMemory.updated_at.desc())
        .offset(max(0, offset))
        .limit(min(max(1, limit), 500))
    ).all()
    return {"items": rows, "total": total}


def get_fact(session: Session, user_id: str, fact_id: str) -> StructuredMemory:
    row = session.exec(
        select(StructuredMemory).where(
            StructuredMemory.id == fact_id,
            StructuredMemory.user_id == user_id,
        )
    ).first()
    if row is None:
        raise LookupError(f"fact not found: user={user_id} id={fact_id}")
    return row


def list_messages(session: Session, user_id: str, conversation_id: str) -> list[Message]:
    """只读原文：按 (user_id, source_session_id) + seq 升序。"""
    return list(
        session.exec(
            select(Message)
            .where(
                Message.user_id == user_id,
                Message.source_session_id == conversation_id,
            )
            .order_by(Message.seq.asc())
            .limit(5000)
        ).all()
    )


# ========================================================================== #
#  投影同步 helper
# ========================================================================== #


def _sync_replace(
    projection: Any,
    user_id: str,
    category: str,
    key: str,
    record: dict[str, Any],
) -> tuple[str, str | None]:
    """删旧插新：先按 (user, category, key) 删旧向量，成功才 embed+插新。

    删除失败 → 不插新（避免投影出现同 key 双版本），返回 failed + 重建指引。
    """
    if projection is None:
        return "failed", "向量库未接入（离线/构造失败），SQLite 已更新；可稍后在用户页点「重建投影」修复"
    try:
        projection.delete(user_id, category=category, keys=[key])
        projection.upsert([record])
        return "synced", None
    except Exception as e:  # noqa: BLE001 - 尽力同步，失败只警示
        return "failed", f"投影同步失败（{type(e).__name__}: {e}）；SQLite 已更新，点「重建投影」可修复"


def _sync_delete(
    projection: Any,
    user_id: str,
    *,
    category: str | None = None,
    keys: list[str] | None = None,
) -> tuple[str, str | None]:
    if projection is None:
        return "failed", "向量库未接入（离线/构造失败），SQLite 已更新；可稍后在用户页点「重建投影」修复"
    try:
        projection.delete(user_id, category=category, keys=keys)
        return "synced", None
    except Exception as e:  # noqa: BLE001
        return "failed", f"投影同步失败（{type(e).__name__}: {e}）；SQLite 已更新，点「重建投影」可修复"


# ========================================================================== #
#  写操作（SQLite 先写 → 投影尽力同步）
# ========================================================================== #


def upsert_fact(
    session: Session,
    *,
    user_id: str,
    category: str,
    key: str,
    fact: str,
    value: str,
    confidence: float = 0.8,
    source_conversation_id: str = "",
    source_chunk_id: str = "",
    projection: Any = None,
) -> dict[str, Any]:
    """按 (user, category, key) upsert 一条事实（与管线冲突语义一致：同键覆盖+归档）。

    同键已存在 → 走更新语义（新 fact/value 落 previous_fact 归档）；否则 INSERT。
    返回 dict{fact_id, created, projection, warning}。
    """
    existing = session.exec(
        select(StructuredMemory).where(
            StructuredMemory.user_id == user_id,
            StructuredMemory.category == category,
            StructuredMemory.key == key,
        )
    ).first()

    now = _utcnow()
    if existing is not None:
        # 更新：fact 或 value 变化时归档旧句
        if existing.fact != fact or existing.value != value:
            existing.previous_fact = existing.fact
        existing.fact = fact
        existing.value = value
        existing.confidence = confidence
        if source_conversation_id:
            existing.source_conversation_id = source_conversation_id
        existing.updated_at = now
        row = existing
        created = False
        session.add(row)
        session.commit()
    else:
        row = StructuredMemory(
            user_id=user_id,
            fact=fact,
            previous_fact="",
            category=category,
            key=key,
            value=value,
            confidence=confidence,
            source_conversation_id=source_conversation_id,
            source_chunk_id=source_chunk_id,
            created_at=now,
            updated_at=now,
        )
        session.add(row)
        session.commit()
        session.refresh(row)
        created = True

    projection_status, warning = _sync_replace(
        projection, user_id, category, key, _row_to_projection_record(row)
    )
    return {
        "fact_id": row.id,
        "created": created,
        "projection": projection_status,
        "warning": warning,
    }


def update_fact(
    session: Session,
    user_id: str,
    fact_id: str,
    *,
    fact: str | None = None,
    value: str | None = None,
    confidence: float | None = None,
    projection: Any = None,
) -> dict[str, Any]:
    """编辑单条事实：只允许 fact/value/confidence（身份字段 category/key 不可改）。

    fact 变化时旧句归档 previous_fact；全无变化 → no-op 不触发投影。
    """
    row = get_fact(session, user_id, fact_id)
    changed = False
    if fact is not None and fact != row.fact:
        row.previous_fact = row.fact
        row.fact = fact
        changed = True
    if value is not None and value != row.value:
        row.value = value
        changed = True
    if confidence is not None and confidence != row.confidence:
        row.confidence = confidence
        changed = True
    if not changed:
        return {"fact_id": row.id, "changed": False, "projection": "skipped", "warning": None}

    row.updated_at = _utcnow()
    session.add(row)
    session.commit()

    projection_status, warning = _sync_replace(
        projection, row.user_id, row.category, row.key, _row_to_projection_record(row)
    )
    return {"fact_id": row.id, "changed": True, "projection": projection_status, "warning": warning}


def delete_fact(
    session: Session,
    user_id: str,
    fact_id: str,
    *,
    projection: Any = None,
) -> dict[str, Any]:
    """删除单条事实（SQLite 删行 → 投影按 (user, category, key) 删向量，幂等）。"""
    row = get_fact(session, user_id, fact_id)
    session.delete(row)
    session.commit()
    projection_status, warning = _sync_delete(
        projection, row.user_id, category=row.category, keys=[row.key]
    )
    return {
        "fact_id": fact_id,
        "affected": 1,
        "projection": projection_status,
        "warning": warning,
    }


def clear_user(
    session: Session,
    user_id: str,
    *,
    reset_conv_meta: bool = False,
    projection: Any = None,
) -> dict[str, Any]:
    """清空某用户全部事实（保留 conv_messages 原文）。

    reset_conv_meta=True：额外把该用户 conv_meta 重置为 PENDING（打开 ingest
    门禁，下次提取从原文再生——重提取走 LLM，费用与时机由用户显式发起）。
    """
    deleted_facts = int(
        session.execute(
            sa_delete(StructuredMemory).where(StructuredMemory.user_id == user_id)
        ).rowcount
        or 0
    )
    reset_sessions = 0
    if reset_conv_meta:
        result = session.execute(
            sa_update(ConversationMeta)
            .where(ConversationMeta.user_id == user_id)
            .values(status="PENDING", last_error="")
        )
        reset_sessions = int(result.rowcount or 0)
    session.commit()

    projection_status, warning = _sync_delete(projection, user_id)
    return {
        "deleted_facts": deleted_facts,
        "reset_sessions": reset_sessions,
        "projection": projection_status,
        "warning": warning,
    }


def rebuild_projection(
    session: Session,
    user_id: str,
    *,
    projection: Any = None,
) -> dict[str, Any]:
    """重建某用户投影：SQLite 全量 → 删该用户全部向量 → 批量 embed → 重插。

    需要真实投影接入（projection 为 None → failed + 提示）。兼作一切投影漂移的兜底。
    """
    if projection is None:
        return {
            "synced": 0,
            "projection": "failed",
            "warning": "向量库未接入（离线/构造失败），无法重建投影",
        }
    rows = list(
        session.exec(
            select(StructuredMemory)
            .where(StructuredMemory.user_id == user_id)
            .order_by(StructuredMemory.created_at.asc())
        ).all()
    )
    try:
        projection.delete(user_id)
        if rows:
            records = [_row_to_projection_record(r) for r in rows]
            projection.upsert(records)
        return {"synced": len(rows), "projection": "synced", "warning": None}
    except Exception as e:  # noqa: BLE001
        return {
            "synced": 0,
            "projection": "failed",
            "warning": f"重建投影失败（{type(e).__name__}: {e}）；SQLite 权威数据完好，可重试",
        }
