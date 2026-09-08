"""Milvus 投影真实接入（EvalView 记忆管理用，lazy 构造无 import 副作用）。

LiveProjection 封装 os_mem 的向量库/embedder 单例，暴露与测试 fake 相同的
duck-type 接口（delete / upsert），由 routes/memories.py 按需创建：
- 构造/调用失败由调用方捕获 → 降级「仅 SQLite + 警示 + 重建兜底」；
- 本模块顶层不 import os_mem 基础设施（构造时才连 Milvus / DashScope）。
"""
from __future__ import annotations

from typing import Any


class LiveProjection:
    """真实投影适配器：delete 按 (user_id, category, keys) 删向量；upsert 批量 embed+插。"""

    def __init__(self) -> None:
        # lazy：首次创建记忆管理写操作/重建时才连云端
        from os_mem.infra.storage.vec_storage import get_memory_vector_store
        from os_mem.infra.storage.vectorizer import get_vectorizer

        self._vector_store = get_memory_vector_store()
        self._vectorizer = get_vectorizer()

    def delete(
        self,
        user_id: str,
        category: str | None = None,
        keys: list[str] | None = None,
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
