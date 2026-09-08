"""os_mem 对外管理窗口（admin）—— 记忆数据受控访问与维护的唯一入口。

背景：EvalView 等外部管理面曾直接持有 MemoryDatabase engine / 直接查询
ORM 表（越权、绕过领域规则）。本模块是 os_mem 对外的**管理窗口**：
外部只调用这里的高层业务操作（浏览/检索、新增/编辑/删除事实、清空+门禁
重置、重建投影），不接触 engine / session / ORM / 向量库实现细节。

窗口内封装的一致性语义（与 docs/方案-EvalView记忆管理.md 一致）：
1. SQLite 是权威源，写操作先提交业务库（确定性成功）；
2. 投影（Milvus）尽力同步：删旧插新、失败只警示不阻断不回滚；
3. 身份字段 (user_id, category, key) 不可经编辑修改（改身份 = 删旧建新）；
4. 每用户「重建投影」兜底一切投影漂移。

分层铁律：os_mem 不得 import testing；testing 反向 import os_mem 只经本窗口。
模块 import 无重副作用：顶层不构造 LLM / Milvus client（投影对象 lazy，
见 ``MemAdminService._projection``），import 链只经过 os_mem/__init__
（settings + logger）与 infra.storage（类引用，不建立连接）。
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
#  DB 会话（引擎复用 MemoryDatabase：相对路径恒锚定 src/os_mem/data/）
# ========================================================================== #


@contextmanager
def _session() -> Iterator[Session]:
    from os_mem.infra.storage.mem_storage import MemoryDatabase

    # expire_on_commit=False：commit 后属性保留在实例上 —— 写方法在会话提交后
    # 还要读行字段做投影同步（record 构建在 with 之外），避免 detached 后惰性
    # 重载抛 DetachedInstanceError。全列查询已加载，无需担心读到过期值。
    with Session(MemoryDatabase().get_engine(), expire_on_commit=False) as session:
        yield session


def _utcnow() -> datetime:
    """naive UTC（与 memories.db 存量时间戳口径一致）。"""
    return datetime.now(UTC).replace(tzinfo=None)


def _fact_to_dict(row: StructuredMemory) -> dict[str, Any]:
    """ORM 行 → 对外数据（不泄露 ORM 对象；datetime 保持 naive UTC 由 API 层序列化）。"""
    return {
        "id": row.id,
        "user_id": row.user_id,
        "fact": row.fact,
        "previous_fact": row.previous_fact,
        "category": row.category,
        "key": row.key,
        "value": row.value,
        "confidence": row.confidence,
        "source_conversation_id": row.source_conversation_id,
        "source_chunk_id": row.source_chunk_id,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


def _message_to_dict(row: Message) -> dict[str, Any]:
    return {
        "seq": row.seq,
        "content": row.content,
        "contains_pii": row.contains_pii,
        "masked_text": row.masked_text,
        "create_at": row.create_at,
    }


# ========================================================================== #
#  投影适配（lazy；Milvus 不可用时所有写操作降级为“仅 SQLite + 警示”）
# ========================================================================== #


class _LiveProjection:
    """真实投影适配器：delete 按 (user_id, category, keys)；upsert 批量 embed+插。"""

    def __init__(self, vector_store: Any, vectorizer: Any) -> None:
        self._vector_store = vector_store
        self._vectorizer = vectorizer

    def delete(
        self, user_id: str, category: str | None = None, keys: list[str] | None = None
    ) -> int:
        return self._vector_store.delete_memories(
            user_id=user_id, category=category, keys=keys
        )

    def upsert(self, records: list[dict[str, Any]]) -> int:
        if not records:
            return 0
        texts = [r["fact"] for r in records]
        embeddings = self._vectorizer.embed_batch(texts)
        return self._vector_store.add_structured_memories(records, embeddings)


class MemAdminService:
    """记忆管理窗口。外部通过本类方法读写记忆，禁止直接持有引擎/ORM。

    - ``projection``：注入投影适配器（测试用 fake）；缺省走 ``allow_live``。
    - ``allow_live=True``（默认）：首次写操作 lazy 构造真实 Milvus/DashScope
      依赖；构造或调用失败 → 操作返回 ``projection='failed'`` + 警示，SQLite 不回滚。
    - ``allow_live=False``：离线模式，永不连接向量库（纯 SQLite 管理）。
    """

    def __init__(self, projection: Any | None = None, *, allow_live: bool = True) -> None:
        self._injected_projection = projection
        self._allow_live = allow_live
        self._live_projection: Any | None = None
        self._live_error: str | None = None

    # ------------------------------------------------------------------ #
    #  读
    # ------------------------------------------------------------------ #

    def list_users(self) -> list[dict[str, Any]]:
        """用户级摘要聚合（struct_memories / conv_messages / conv_meta 三表并集）。"""
        with _session() as session:
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

            for user_id, count, latest in session.exec(
                select(
                    StructuredMemory.user_id,
                    func.count(StructuredMemory.id),
                    func.max(StructuredMemory.updated_at),
                ).group_by(StructuredMemory.user_id)
            ).all():
                b = bucket(user_id)
                b["fact_count"] = int(count or 0)
                b["latest_activity"] = latest
            for user_id, cat, count in session.exec(
                select(
                    StructuredMemory.user_id,
                    StructuredMemory.category,
                    func.count(StructuredMemory.id),
                ).group_by(StructuredMemory.user_id, StructuredMemory.category)
            ).all():
                bucket(user_id)["categories"][cat] = int(count or 0)
            for user_id, count in session.exec(
                select(Message.user_id, func.count(Message.id)).group_by(Message.user_id)
            ).all():
                bucket(user_id)["message_count"] = int(count or 0)
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
        self,
        user_id: str,
        *,
        category: str | None = None,
        q: str | None = None,
        offset: int = 0,
        limit: int = 100,
    ) -> dict[str, Any]:
        """分页列出事实；category 精确 + q 对 fact/key/value 子串模糊匹配。"""
        with _session() as session:
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
            total = int(
                session.exec(select(func.count()).select_from(stmt.subquery())).one() or 0
            )
            rows = session.exec(
                stmt.order_by(StructuredMemory.updated_at.desc())
                .offset(max(0, offset))
                .limit(min(max(1, limit), 500))
            ).all()
            return {"items": [_fact_to_dict(r) for r in rows], "total": total}

    def get_fact(self, user_id: str, fact_id: str) -> dict[str, Any]:
        with _session() as session:
            row = session.exec(
                select(StructuredMemory).where(
                    StructuredMemory.id == fact_id,
                    StructuredMemory.user_id == user_id,
                )
            ).first()
            if row is None:
                raise LookupError(f"fact not found: user={user_id} id={fact_id}")
            return _fact_to_dict(row)

    def list_messages(self, user_id: str, conversation_id: str) -> list[dict[str, Any]]:
        """只读原文：按 (user_id, source_session_id) + seq 升序。"""
        with _session() as session:
            rows = session.exec(
                select(Message)
                .where(
                    Message.user_id == user_id,
                    Message.source_session_id == conversation_id,
                )
                .order_by(Message.seq.asc())
                .limit(5000)
            ).all()
            return [_message_to_dict(r) for r in rows]

    # ------------------------------------------------------------------ #
    #  投影一致性 helpers（写操作内部）
    # ------------------------------------------------------------------ #

    def _projection(self) -> Any | None:
        """返回投影适配器；无可用返回 None（写操作将投影标记 failed + 警示）。

        注入的 projection 优先；否则 lazy 构造真实依赖（失败记录原因，下次重试）。
        """
        if self._injected_projection is not None:
            return self._injected_projection
        if not self._allow_live:
            return None
        if self._live_projection is None:
            try:
                from os_mem.infra.storage import get_memory_vector_store, get_vectorizer

                self._live_projection = _LiveProjection(
                    get_memory_vector_store(), get_vectorizer()
                )
            except Exception as e:  # noqa: BLE001 - lazy 降级
                self._live_error = f"{type(e).__name__}: {e}"
                return None
        return self._live_projection

    def _unavailable_warning(self) -> str:
        reason = f"（{self._live_error}）" if self._live_error else "（离线/未注入）"
        return f"向量库不可用{reason}—— 本次仅更新 SQLite，可稍后在用户页点「重建投影」修复"

    def _sync_replace(
        self, user_id: str, category: str, key: str, record: dict[str, Any]
    ) -> tuple[str, str | None]:
        """删旧插新：删除成功才 embed+插新（防同 key 双版本）。"""
        projection = self._projection()
        if projection is None:
            return "failed", self._unavailable_warning()
        try:
            projection.delete(user_id, category=category, keys=[key])
            projection.upsert([record])
            return "synced", None
        except Exception as e:  # noqa: BLE001 - 尽力同步，失败只警示
            return "failed", f"投影同步失败（{type(e).__name__}: {e}）；SQLite 已更新，点「重建投影」可修复"

    def _sync_delete(
        self,
        user_id: str,
        *,
        category: str | None = None,
        keys: list[str] | None = None,
    ) -> tuple[str, str | None]:
        projection = self._projection()
        if projection is None:
            return "failed", self._unavailable_warning()
        try:
            projection.delete(user_id, category=category, keys=keys)
            return "synced", None
        except Exception as e:  # noqa: BLE001
            return "failed", f"投影同步失败（{type(e).__name__}: {e}）；SQLite 已更新，点「重建投影」可修复"

    @staticmethod
    def _row_to_projection_record(row: StructuredMemory) -> dict[str, Any]:
        """ORM 行 → 投影写入记录（字段与 add_structured_memories 对齐）。

        投影行 id 是独立随机 uuid（删除只能按 (user, category, key) 过滤）；
        updated_at 用 naive UTC ISO 字符串（与管线写投影格式一致）。
        """
        return {
            "id": uuid.uuid4().hex,
            "fact": row.fact,
            "category": row.category,
            "key": row.key,
            "value": row.value,
            "user_id": row.user_id,
            "updated_at": (
                row.updated_at.isoformat()
                if isinstance(row.updated_at, datetime)
                else str(row.updated_at)
            ),
        }

    # ------------------------------------------------------------------ #
    #  写（SQLite 先写 → 投影尽力同步）
    # ------------------------------------------------------------------ #

    def upsert_fact(
        self,
        user_id: str,
        *,
        category: str,
        key: str,
        fact: str,
        value: str,
        confidence: float = 0.8,
        source_conversation_id: str = "",
        source_chunk_id: str = "",
    ) -> dict[str, Any]:
        """按 (user, category, key) upsert 一条事实（同键覆盖 + 旧句归档）。"""
        with _session() as session:
            existing = session.exec(
                select(StructuredMemory).where(
                    StructuredMemory.user_id == user_id,
                    StructuredMemory.category == category,
                    StructuredMemory.key == key,
                )
            ).first()

            now = _utcnow()
            if existing is not None:
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

        # 投影尽力同步（commit 后行未 expire（见 _session），会话外访问安全）
        record = self._row_to_projection_record(row)
        projection_status, warning = self._sync_replace(
            row.user_id, row.category, row.key, record
        )
        return {
            "fact_id": row.id,
            "created": created,
            "projection": projection_status,
            "warning": warning,
        }

    def update_fact(
        self,
        user_id: str,
        fact_id: str,
        *,
        fact: str | None = None,
        value: str | None = None,
        confidence: float | None = None,
    ) -> dict[str, Any]:
        """编辑单条事实：只允许 fact/value/confidence（身份字段 category/key 不可改）。"""
        with _session() as session:
            row = session.exec(
                select(StructuredMemory).where(
                    StructuredMemory.id == fact_id,
                    StructuredMemory.user_id == user_id,
                )
            ).first()
            if row is None:
                raise LookupError(f"fact not found: user={user_id} id={fact_id}")
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

        projection_status, warning = self._sync_replace(
            row.user_id, row.category, row.key, self._row_to_projection_record(row)
        )
        return {"fact_id": row.id, "changed": True, "projection": projection_status, "warning": warning}

    def delete_fact(self, user_id: str, fact_id: str) -> dict[str, Any]:
        """删除单条事实（SQLite 删行 → 投影按 (user, category, key) 删向量，幂等）。"""
        with _session() as session:
            row = session.exec(
                select(StructuredMemory).where(
                    StructuredMemory.id == fact_id,
                    StructuredMemory.user_id == user_id,
                )
            ).first()
            if row is None:
                raise LookupError(f"fact not found: user={user_id} id={fact_id}")
            session.delete(row)
            session.commit()

        projection_status, warning = self._sync_delete(
            row.user_id, category=row.category, keys=[row.key]
        )
        return {
            "fact_id": fact_id,
            "affected": 1,
            "projection": projection_status,
            "warning": warning,
        }

    def clear_user(
        self, user_id: str, *, reset_conv_meta: bool = False
    ) -> dict[str, Any]:
        """清空某用户全部事实（保留 conv_messages 原文）。

        reset_conv_meta=True：额外重置 conv_meta → PENDING（打开 ingest 门禁，
        下次提取从原文再生——LLM 费用与时机由调用方显式发起）。
        """
        with _session() as session:
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

        projection_status, warning = self._sync_delete(user_id)
        return {
            "deleted_facts": deleted_facts,
            "reset_sessions": reset_sessions,
            "projection": projection_status,
            "warning": warning,
        }

    def rebuild_projection(self, user_id: str) -> dict[str, Any]:
        """重建用户投影：SQLite 全量 → 删用户全部向量 → 批量 embed → 重插。"""
        projection = self._projection()
        if projection is None:
            return {
                "synced": 0,
                "projection": "failed",
                "warning": self._unavailable_warning(),
            }
        with _session() as session:
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
                records = [self._row_to_projection_record(r) for r in rows]
                projection.upsert(records)
            return {"synced": len(rows), "projection": "synced", "warning": None}
        except Exception as e:  # noqa: BLE001
            return {
                "synced": 0,
                "projection": "failed",
                "warning": f"重建投影失败（{type(e).__name__}: {e}）；SQLite 权威数据完好，可重试",
            }


# ========================================================================== #
#  单例（生产入口；测试直接用 MemAdminService(projection=fake) 注入）
# ========================================================================== #

_admin_service: MemAdminService | None = None


def get_mem_admin_service() -> MemAdminService:
    """EvalView 等管理面的默认窗口：lazy 构造，首次写操作才连向量库。"""
    global _admin_service
    if _admin_service is None:
        _admin_service = MemAdminService()
    return _admin_service
