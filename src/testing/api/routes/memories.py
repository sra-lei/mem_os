"""记忆管理 API routes（prefix /api/memories）。

浏览/管理 memories.db（业务权威源）的记忆数据；写操作遵循
「SQLite 先写 + 投影尽力同步」—— 投影对象 lazy 构造（LiveProjection），
构造/调用失败降级为仅 SQLite + 警示，UI 可点「重建投影」兜底。

方案见 docs/方案-EvalView记忆管理.md。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from sqlmodel import Session

from testing.services.mem_admin_service import (
    aggregate_users,
    clear_user,
    delete_fact,
    get_fact,
    list_facts,
    list_messages,
    memory_session,
    rebuild_projection,
    update_fact,
    upsert_fact,
)

from ..schemas import (
    ClearUserRequest,
    FactCreateRequest,
    FactUpdateRequest,
    MemWriteResponse,
    MemoryFactItem,
    MemoryFactListResponse,
    MemoryMessageItem,
    MemoryUserSummary,
)

router = APIRouter(prefix="/api/memories", tags=["memories"])

# ---------------------------------------------------------------------------
# 投影 lazy 接入（模块 import 无副作用；首次写操作/重建才连 Milvus）
# ---------------------------------------------------------------------------

_live_projection: Any = None


def _make_projection() -> tuple[Any, str | None]:
    """返回 (projection | None, warning | None)。构造失败降级为仅 SQLite。"""
    global _live_projection
    try:
        if _live_projection is None:
            from testing.services.mem_projection import LiveProjection

            _live_projection = LiveProjection()
        return _live_projection, None
    except Exception as e:  # noqa: BLE001 - 离线可管理 SQLite，投影用重建按钮兜底
        return None, f"向量库不可用（{type(e).__name__}: {e}）—— 本次仅更新 SQLite，可稍后重建投影"


def _write_response(operation: str, user_id: str, result: dict[str, Any]) -> MemWriteResponse:
    """服务返回 dict → 统一信封。"""
    return MemWriteResponse(
        operation=operation,
        sqlite=True,
        projection=result.get("projection", "skipped"),
        warning=result.get("warning"),
        user_id=user_id,
        fact_id=result.get("fact_id"),
        affected=result.get("deleted_facts", result.get("synced", result.get("affected"))),
    )


# ---------------------------------------------------------------------------
# 读：用户列表 / 事实 / 原文
# ---------------------------------------------------------------------------


@router.get("/users", response_model=list[MemoryUserSummary])
def list_users() -> list[MemoryUserSummary]:
    with memory_session() as session:
        return [MemoryUserSummary.model_validate(u) for u in aggregate_users(session)]


@router.get("/users/{user_id}/facts", response_model=MemoryFactListResponse)
def list_user_facts(
    user_id: str,
    category: str | None = Query(None, description="category 精确过滤"),
    q: str | None = Query(None, description="对 fact/key/value 子串模糊匹配"),
    offset: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
) -> MemoryFactListResponse:
    with memory_session() as session:
        page = list_facts(
            session,
            user_id,
            category=category or None,
            q=q or None,
            offset=offset,
            limit=limit,
        )
        return MemoryFactListResponse(
            items=[MemoryFactItem.model_validate(f) for f in page["items"]],
            total=page["total"],
        )


@router.get("/users/{user_id}/facts/{fact_id}", response_model=MemoryFactItem)
def get_user_fact(user_id: str, fact_id: str) -> MemoryFactItem:
    with memory_session() as session:
        try:
            return MemoryFactItem.model_validate(get_fact(session, user_id, fact_id))
        except LookupError:
            raise HTTPException(status_code=404, detail="记忆事实不存在或不属于该用户")


@router.get(
    "/users/{user_id}/conversations/{conversation_id}/messages",
    response_model=list[MemoryMessageItem],
)
def get_conversation_messages(user_id: str, conversation_id: str) -> list[MemoryMessageItem]:
    with memory_session() as session:
        rows = list_messages(session, user_id, conversation_id)
        if not rows:
            raise HTTPException(
                status_code=404,
                detail=f"未找到该用户的会话原文：user={user_id} conversation={conversation_id}",
            )
        return [MemoryMessageItem.model_validate(m) for m in rows]


# ---------------------------------------------------------------------------
# 写：新增(upsert) / 编辑 / 删除 / 清空 / 重建投影
# ---------------------------------------------------------------------------


@router.post("/users/{user_id}/facts", response_model=MemWriteResponse, status_code=201)
def create_user_fact(user_id: str, req: FactCreateRequest) -> MemWriteResponse:
    for field, value in (("category", req.category), ("key", req.key), ("fact", req.fact), ("value", req.value)):
        if not value or not str(value).strip():
            raise HTTPException(status_code=422, detail=f"{field} 不能为空")
    projection, warning = _make_projection()
    with memory_session() as session:
        result = upsert_fact(
            session,
            user_id=user_id,
            category=req.category.strip(),
            key=req.key.strip(),
            fact=req.fact.strip(),
            value=str(req.value).strip(),
            confidence=req.confidence,
            source_conversation_id=req.source_conversation_id,
            source_chunk_id=req.source_chunk_id,
            projection=projection,
        )
        result["warning"] = result.get("warning") or warning
        return _write_response("upsert_fact", user_id, result)


@router.patch("/users/{user_id}/facts/{fact_id}", response_model=MemWriteResponse)
def edit_user_fact(user_id: str, fact_id: str, req: FactUpdateRequest) -> MemWriteResponse:
    if req.fact is None and req.value is None and req.confidence is None:
        raise HTTPException(status_code=422, detail="至少提供 fact/value/confidence 之一")
    projection, warning = _make_projection()
    with memory_session() as session:
        try:
            result = update_fact(
                session,
                user_id,
                fact_id,
                fact=req.fact.strip() if req.fact is not None else None,
                value=req.value.strip() if req.value is not None else None,
                confidence=req.confidence,
                projection=projection,
            )
        except LookupError:
            raise HTTPException(status_code=404, detail="记忆事实不存在或不属于该用户")
        result["warning"] = result.get("warning") or warning
        return _write_response("update_fact", user_id, result)


@router.delete("/users/{user_id}/facts/{fact_id}", response_model=MemWriteResponse)
def remove_user_fact(user_id: str, fact_id: str) -> MemWriteResponse:
    projection, warning = _make_projection()
    with memory_session() as session:
        try:
            result = delete_fact(session, user_id, fact_id, projection=projection)
        except LookupError:
            raise HTTPException(status_code=404, detail="记忆事实不存在或不属于该用户")
        result["warning"] = result.get("warning") or warning
        return _write_response("delete_fact", user_id, result)


@router.post("/users/{user_id}/clear", response_model=MemWriteResponse)
def clear_user_memories(user_id: str, req: ClearUserRequest) -> MemWriteResponse:
    projection, warning = _make_projection()
    with memory_session() as session:
        result = clear_user(
            session,
            user_id,
            reset_conv_meta=req.reset_conv_meta,
            projection=projection,
        )
        result["warning"] = result.get("warning") or warning
        return _write_response("clear_user", user_id, result)


@router.post("/users/{user_id}/rebuild-projection", response_model=MemWriteResponse)
def rebuild_user_projection(user_id: str) -> MemWriteResponse:
    projection, warning = _make_projection()
    with memory_session() as session:
        result = rebuild_projection(session, user_id, projection=projection)
        result["warning"] = result.get("warning") or warning
        return _write_response("rebuild_projection", user_id, result)
