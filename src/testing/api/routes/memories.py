"""记忆管理 API routes（prefix /api/memories）—— 纯 HTTP 适配层。

业务逻辑全部委托给 os_mem 对外管理窗口（os_mem.admin.MemAdminService）：
本模块不接触 engine / ORM / 向量库，只做 参数校验 → 调窗口 → 组响应模型。
投影一致性（SQLite 权威 + 尽力同步 + 失败警示 + 重建兜底）由窗口封装。

方案见 docs/需求/EvalView需求文档.md 第十三章。
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from os_mem.admin import get_mem_admin_service

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

# 生产单例：内部 lazy 连向量库，写操作尽力同步投影；失败降级仅 SQLite + 警示。
_admin = get_mem_admin_service()


def _write_response(operation: str, user_id: str, result: dict) -> MemWriteResponse:
    """窗口写返回 dict → 统一信封（affected 兼容 delete/clear/rebuild 的计数字段）。"""
    return MemWriteResponse(
        operation=operation,
        sqlite=True,
        projection=result.get("projection", "skipped"),
        warning=result.get("warning"),
        user_id=user_id,
        fact_id=result.get("fact_id"),
        affected=result.get("affected", result.get("deleted_facts", result.get("synced"))),
    )


# ---------------------------------------------------------------------------
# 读：用户列表 / 事实 / 原文
# ---------------------------------------------------------------------------


@router.get("/users", response_model=list[MemoryUserSummary])
def list_users() -> list[MemoryUserSummary]:
    return [MemoryUserSummary.model_validate(u) for u in _admin.list_users()]


@router.get("/users/{user_id}/facts", response_model=MemoryFactListResponse)
def list_user_facts(
    user_id: str,
    category: str | None = Query(None, description="category 精确过滤"),
    q: str | None = Query(None, description="对 fact/key/value 子串模糊匹配"),
    offset: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
) -> MemoryFactListResponse:
    page = _admin.list_facts(
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
    try:
        return MemoryFactItem.model_validate(_admin.get_fact(user_id, fact_id))
    except LookupError:
        raise HTTPException(status_code=404, detail="记忆事实不存在或不属于该用户")


@router.get(
    "/users/{user_id}/conversations/{conversation_id}/messages",
    response_model=list[MemoryMessageItem],
)
def get_conversation_messages(user_id: str, conversation_id: str) -> list[MemoryMessageItem]:
    rows = _admin.list_messages(user_id, conversation_id)
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
    for field, value in (
        ("category", req.category),
        ("key", req.key),
        ("fact", req.fact),
        ("value", req.value),
    ):
        if not value or not str(value).strip():
            raise HTTPException(status_code=422, detail=f"{field} 不能为空")
    result = _admin.upsert_fact(
        user_id,
        category=req.category.strip(),
        key=req.key.strip(),
        fact=req.fact.strip(),
        value=str(req.value).strip(),
        confidence=req.confidence,
        source_conversation_id=req.source_conversation_id,
        source_chunk_id=req.source_chunk_id,
    )
    return _write_response("upsert_fact", user_id, result)


@router.patch("/users/{user_id}/facts/{fact_id}", response_model=MemWriteResponse)
def edit_user_fact(user_id: str, fact_id: str, req: FactUpdateRequest) -> MemWriteResponse:
    if req.fact is None and req.value is None and req.confidence is None:
        raise HTTPException(status_code=422, detail="至少提供 fact/value/confidence 之一")
    try:
        result = _admin.update_fact(
            user_id,
            fact_id,
            fact=req.fact.strip() if req.fact is not None else None,
            value=req.value.strip() if req.value is not None else None,
            confidence=req.confidence,
        )
    except LookupError:
        raise HTTPException(status_code=404, detail="记忆事实不存在或不属于该用户")
    return _write_response("update_fact", user_id, result)


@router.delete("/users/{user_id}/facts/{fact_id}", response_model=MemWriteResponse)
def remove_user_fact(user_id: str, fact_id: str) -> MemWriteResponse:
    try:
        result = _admin.delete_fact(user_id, fact_id)
    except LookupError:
        raise HTTPException(status_code=404, detail="记忆事实不存在或不属于该用户")
    return _write_response("delete_fact", user_id, result)


@router.post("/users/{user_id}/clear", response_model=MemWriteResponse)
def clear_user_memories(user_id: str, req: ClearUserRequest) -> MemWriteResponse:
    result = _admin.clear_user(user_id, reset_conv_meta=req.reset_conv_meta)
    return _write_response("clear_user", user_id, result)


@router.post("/users/{user_id}/rebuild-projection", response_model=MemWriteResponse)
def rebuild_user_projection(user_id: str) -> MemWriteResponse:
    result = _admin.rebuild_projection(user_id)
    return _write_response("rebuild_projection", user_id, result)
