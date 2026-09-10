"""
doc-kit - 向量库存储能力：把 (文本, 向量, 元数据) 写入 Milvus (Zilliz Cloud Serverless)
使用 pymilvus 官方 SDK 接入（开启动态字段，无需预声明 metadata schema）。

MemOS 扩展：MemoryVectorStore —— mem_os collection 存储 StructuredMemory，
fact 向量化，category/key/value/user_id/updated_at 作为元数据。
"""
from __future__ import annotations

import math
import time
from typing import Any

from pymilvus import (
    AnnSearchRequest,
    DataType,
    Function,
    FunctionType,
    MilvusClient,
    RRFRanker,
)
from pymilvus.exceptions import MilvusException

from os_mem.configs.mem_settings import memory_settings
from os_mem.infra.logger import get_logger

_logger = get_logger("os_mem.storage")


def fuse_dense_lead(
    dense_hits: list[dict],
    sparse_hits: list[dict],
    top_k: int,
    blind_ratio: float = 1 / 3,
) -> list[dict]:
    """dense 主导 + sparse 补盲融合（2026-09-10，替代等权 RRF 的新默认）。

    规则（输入均已按各自相关性排序）：
    - dense 为骨架，顺序不动——语义命中永不被词面命中反压；
    - sparse 仅补「dense 在整条召回深度内都没有」的 id（真盲区，如问题词不含
      答案词时 BM25 的精确词面命中），按 sparse 名次取，配额 = ceil(top_k*ratio)；
    - 补盲项追加在 dense 之后，占用尾部配额（dense 取前 top_k-n），总数恒为 top_k。

    取舍：补盲会挤掉等量 dense 尾部；因补盲只收 sparse 高名次且 dense 完全没召回到
    的项，属高置信词面证据。配额上限 1/3 防词面噪音倒灌。
    """
    dense_ids = {h.get("id") for h in dense_hits}
    blind = [h for h in sparse_hits if h.get("id") not in dense_ids]
    quota = math.ceil(top_k * blind_ratio)
    take_blind = blind[: min(quota, len(blind), top_k)]
    take_dense = dense_hits[: max(0, top_k - len(take_blind))]
    return take_dense + take_blind

# =========================================================================== #

#  MemOS：mem_os collection —— StructuredMemory 向量化存储
#
#  fact 字段向量化（embedding）写入 vector；
#  category / key / value / user_id / updated_at 作为元数据（显式字段）。
# =========================================================================== #
class MemoryVectorStore:
    """mem_os collection：StructuredMemory 的向量化存储（Milvus / Zilliz Cloud）。"""

    def __init__(
        self,
        collection_name: str = "mem_os",
        dim: int | None = None,
    ) -> None:
        self.collection_name = collection_name
        self.dim = dim or getattr(memory_settings, "embedding_dim", 1024)
        self.client: MilvusClient = MilvusClient(
            uri=memory_settings.MILVUS_URI,
            token=memory_settings.MILVUS_API_KEY,
        )
        _logger.info(
            f"MemoryVectorStore 初始化完成 | "
            f"collection={self.collection_name} dim={self.dim}"
        )

    # ------------------------------------------------------------------ #
    #  建集合：id + vector(fact embedding) + sparse(BM25) + 元数据显式字段
    # ------------------------------------------------------------------ #
    def _ensure_collection(self) -> None:
        try:
            if self.client.has_collection(self.collection_name):
                # 旧结构（无 BM25 稀疏字段）无法在线加向量字段，提示重建
                try:
                    desc = self.client.describe_collection(self.collection_name)
                    fields = desc.get("fields", []) if isinstance(desc, dict) else []
                    has_sparse = any(f.get("name") == "sparse" for f in fields)
                except Exception:
                    has_sparse = True  # 无法探测时按新结构对待
                if not has_sparse:
                    raise RuntimeError(
                        f"collection {self.collection_name} 是旧结构"
                        "（无 BM25 sparse 字段），"
                        "混合检索需要重建：请先 drop 该 collection 再运行"
                    )
                return
        except MilvusException as e:
            _logger.error(f"[Milvus] 检测集合 {self.collection_name} 失败: {e}")
            raise

        schema = self.client.create_schema(auto_id=False, enable_dynamic_field=True)
        schema.add_field("id", DataType.VARCHAR, is_primary=True, max_length=64)
        schema.add_field("vector", DataType.FLOAT_VECTOR, dim=self.dim)
        # BM25 稀疏向量：由 BM25 function 从 fact 文本自动生成（full-text search）
        schema.add_field("sparse", DataType.SPARSE_FLOAT_VECTOR)
        # StructuredMemory 元数据；fact 是 BM25 function 的输入，必须启用 analyzer
        schema.add_field(
            "fact",
            DataType.VARCHAR,
            max_length=4096,
            enable_analyzer=True,
            analyzer_params={"type": "standard"},
        )
        schema.add_field("category", DataType.VARCHAR, max_length=64)
        schema.add_field("key", DataType.VARCHAR, max_length=128)
        schema.add_field("value", DataType.VARCHAR, max_length=1024)
        schema.add_field("user_id", DataType.VARCHAR, max_length=128)
        schema.add_field("updated_at", DataType.VARCHAR, max_length=64)
        # full-text search：服务端用 BM25 把 fact 文本稀疏化为 sparse 字段
        schema.add_function(
            Function(
                name="bm25_fact",
                input_field_names=["fact"],
                output_field_names=["sparse"],
                function_type=FunctionType.BM25,
            )
        )

        try:
            self.client.create_collection(
                collection_name=self.collection_name,
                schema=schema,
            )
            index_params = self.client.prepare_index_params()
            index_params.add_index(
                field_name="vector",
                index_type="AUTOINDEX",
                metric_type="COSINE",
            )
            index_params.add_index(
                field_name="sparse",
                index_type="SPARSE_INVERTED_INDEX",
                metric_type="BM25",
            )
            self.client.create_index(
                collection_name=self.collection_name,
                index_params=index_params,
            )
            self.client.load_collection(self.collection_name)
        except MilvusException as e:
            _logger.error(f"[Milvus] 创建 mem_os 集合失败: {e}")
            raise RuntimeError(
                f"创建 Milvus collection {self.collection_name} 失败: {e}"
            ) from e

        _logger.info(
            f"[Milvus] mem_os collection 创建完成"
            f"（dense AUTOINDEX/COSINE + sparse BM25, dim={self.dim}）"
        )

    # ------------------------------------------------------------------ #
    #  写入：StructuredMemory（fact 已向量化）
    # ------------------------------------------------------------------ #
    def add_structured_memories(
        self,
        memories: list[dict],
        embeddings: list[list[float]],
    ) -> int:
        """写入 StructuredMemory。

        memories 每项结构：
            {id, fact, category, key, value, user_id, updated_at}
        embeddings 与 memories 一一对应（fact 的 embedding 向量）。
        """
        if len(memories) != len(embeddings):
            raise ValueError(
                f"memories({len(memories)}) 与 embeddings({len(embeddings)}) 数量不一致"
            )
        self._ensure_collection()
        records: list[dict[str, Any]] = []
        for m, emb in zip(memories, embeddings):
            records.append({
                "id": m["id"],
                "vector": emb,
                "fact": m["fact"],
                "category": m.get("category", ""),
                "key": m.get("key", ""),
                "value": m.get("value", ""),
                "user_id": m["user_id"],
                "updated_at": m.get("updated_at", ""),
            })

        try:
            self.client.insert(collection_name=self.collection_name, data=records)
            self.client.flush(collection_name=self.collection_name)
        except MilvusException as e:
            _logger.error(f"[Milvus] 写入 StructuredMemory 失败: {e}")
            raise

        _logger.info(f"写入 {len(records)} 条 StructuredMemory → mem_os")
        return len(records)

    # ------------------------------------------------------------------ #
    #  删除：投影期收敛（删旧插新）——按 (user_id, category, key) 删旧向量
    # ------------------------------------------------------------------ #
    def delete_memories(
        self,
        user_id: str,
        category: str | None = None,
        keys: list[str] | None = None,
    ) -> int:
        """删除指定用户/类别/键集合的旧向量（批量 filter）。

        filter 语义：
        - user_id 必填（防误删其他用户）；
        - category 提供时限定类别；keys 提供时用 ``key in (...)`` 一次删多键；
        - 全部省略时删除该 user 全部向量（调用方需谨慎，仅显式场景使用）。
        删除记录完整日志（user_id/category/keys/delete_count/耗时），出问题可逐批排查。
        返回实际删除条数；删除不存在的键返回 0（幂等）。
        """
        if not user_id:
            raise ValueError('delete_memories 必须指定 user_id（防误删其他用户）')
        self._ensure_collection()
        parts = [f'user_id == "{user_id}"']
        if category:
            parts.append(f'category == "{category}"')
        if keys is not None:
            # 批量 filter：key in [...]（Milvus 布尔表达式用方括号列表，
            # 圆括号会解析失败）
            # key 由 LLM 生成，可能含引号等特殊字符，拼接处统一转义
            escaped = [k.replace('"', '\\"').replace("'", "\\'") for k in keys]
            keys_expr = ",".join(f'"{k}"' for k in escaped)
            parts.append(f"key in [{keys_expr}]")
        expr = " and ".join(parts)
        t0 = time.monotonic()
        try:
            res = self.client.delete(
                collection_name=self.collection_name,
                filter=expr,
            )
            if isinstance(res, dict):
                count = int(res.get("delete_count", 0) or 0)
            else:
                count = int(res or 0)
        except MilvusException as e:
            _logger.error(
                f"[Milvus] delete 失败 user={user_id} category={category} "
                f"keys={keys}: {e}"
            )
            raise
        _logger.info(
            f"[Milvus] delete user={user_id} category={category} "
            f"keys_n={len(keys) if keys else 0} deleted={count} "
            f"耗时={(time.monotonic() - t0) * 1000:.0f}ms filter={expr}"
        )
        return count

    # ------------------------------------------------------------------ #
    #  检索：混合检索（稠密向量 COSINE + BM25 全文）→ RRF 融合
    # ------------------------------------------------------------------ #
    def search(
        self,
        query_vector: list[float],
        query_text: str | None = None,
        top_k: int = 3,
        user_id: str | None = None,
        category: str | None = None,
        key: str | None = None,
        updated_at_min: str | None = None,
        updated_at_max: str | None = None,
    ) -> list[dict]:
        """混合检索：稠密向量（COSINE）+ BM25 全文（query_text）→ RRF 融合。

        - query_vector：稠密查询向量（fact 的 embedding）
        - query_text：BM25 全文查询文本（服务端对 sparse 字段做 full-text search）；
          为 None 时仅稠密检索（兼容单路用法）
        - 元数据字段（user_id / category / key / value / updated_at）为标量，
          仅用于过滤；updated_at 为 ISO 字符串，支持范围过滤。
        """
        self._ensure_collection()

        filters: list[str] = []
        if user_id:
            filters.append(f'user_id == "{user_id}"')
        if category:
            filters.append(f'category == "{category}"')
        if key:
            filters.append(f'key == "{key}"')
        if updated_at_min:
            filters.append(f'updated_at >= "{updated_at_min}"')
        if updated_at_max:
            filters.append(f'updated_at <= "{updated_at_max}"')
        expr = " and ".join(filters) or None

        output_fields = [
            "id", "fact", "category", "key", "value", "user_id", "updated_at",
        ]

        # 稠密路：fact embedding → vector 字段（COSINE）；filter 作用在该路召回。
        # 各路取数深度 = top_k*2（与原 RRF 每路 limit 一致），补盲需足够候选。
        per_route = top_k * 2
        dense_req = AnnSearchRequest(
            data=[query_vector],
            anns_field="vector",
            param={"metric_type": "COSINE"},
            limit=per_route,
            filter=expr,
        )

        def _sparse_req() -> AnnSearchRequest:
            return AnnSearchRequest(
                data=[query_text],
                anns_field="sparse",
                param={"metric_type": "BM25"},
                limit=per_route,
                filter=expr,
            )

        def _run_route(req: AnnSearchRequest) -> list[dict]:
            r = self.client.hybrid_search(
                collection_name=self.collection_name,
                reqs=[req],
                ranker=None,
                limit=per_route,
                output_fields=output_fields,
            )
            return [
                {**{k: row.get(k) for k in output_fields},
                 "distance": row.get("distance")}
                for row in (r[0] if isinstance(r, list) and r else [])
            ]

        # 无 BM25 查询文本：仅稠密路
        if not query_text:
            return _run_route(dense_req)[:top_k]

        fusion_mode = memory_settings.RETRIEVAL_FUSION_MODE
        if fusion_mode == "dense_lead":
            # 新默认：dense/sparse 各自取全序 → 应用层「dense 主导 + sparse 补盲」
            dense_hits = _run_route(dense_req)
            sparse_hits = _run_route(_sparse_req())
            return fuse_dense_lead(dense_hits, sparse_hits, top_k)

        # rrf 模式（原方案保留）：双路交 Milvus RRFRanker 等权融合
        res = self.client.hybrid_search(
            collection_name=self.collection_name,
            reqs=[dense_req, _sparse_req()],
            ranker=RRFRanker(),
            limit=top_k,
            output_fields=output_fields,
        )
        # 展平为 [{id, fact, category, key, value, user_id, updated_at, distance}, ...]
        hits: list[dict] = []
        for row in (res[0] if isinstance(res, list) and res else []):
            hits.append({
                **{k: row.get(k) for k in output_fields},
                "distance": row.get("distance"),
            })
        return hits

    def count(self) -> int:
        try:
            if not self.client.has_collection(self.collection_name):
                return 0
            # pymilvus 2.5 MilvusClient：get_collection_stats 返回 {"row_count": n}
            stats = self.client.get_collection_stats(self.collection_name)
            if isinstance(stats, dict):
                row_count = stats.get("row_count")
                if row_count is not None:
                    return int(row_count)
            # 兜底：describe_collection 的 rowCount / num_entities
            desc = self.client.describe_collection(self.collection_name)
            if isinstance(desc, dict):
                rc = desc.get("rowCount") or desc.get("num_entities")
                if rc is not None:
                    return int(rc)
            return -1
        except (MilvusException, AttributeError, TypeError, ValueError) as e:
            _logger.warning(f"[Milvus] count(mem_os) 失败: {e}")
            return -1


# 全局单例
_mem_store: MemoryVectorStore | None = None
_mem_store_dim: int | None = None


def get_memory_vector_store(dim: int | None = None) -> MemoryVectorStore:
    """获取 mem_os collection 的全局单例（dim 变化时重建）。"""
    global _mem_store, _mem_store_dim
    if _mem_store is None or (dim is not None and _mem_store_dim != dim):
        _mem_store = MemoryVectorStore(dim=dim)
        _mem_store_dim = dim
    return _mem_store
