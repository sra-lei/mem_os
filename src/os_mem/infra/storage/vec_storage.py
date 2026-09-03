"""
doc-kit - 向量库存储能力：把 (文本, 向量, 元数据) 写入 Milvus (Zilliz Cloud Serverless)
使用 pymilvus 官方 SDK 接入（开启动态字段，无需预声明 metadata schema）。

MemOS 扩展：MemoryVectorStore —— mem_os collection 存储 StructuredMemory，
fact 向量化，category/key/value/user_id/updated_at 作为元数据。
"""
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
    ):
        self.collection_name = collection_name
        self.dim = dim or getattr(memory_settings, "embedding_dim", 1024)
        self.client: MilvusClient = MilvusClient(
            uri=memory_settings.MILVUS_URI,
            token=memory_settings.MILVUS_API_KEY,
        )
        _logger.info(f"MemoryVectorStore 初始化完成 | collection={self.collection_name} dim={self.dim}")

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
                        f"collection {self.collection_name} 是旧结构（无 BM25 sparse 字段），"
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
            raise RuntimeError(f"创建 Milvus collection {self.collection_name} 失败: {e}") from e

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

        # 稠密路：fact embedding → vector 字段（COSINE）；filter 作用在该路召回
        dense_req = AnnSearchRequest(
            data=[query_vector],
            anns_field="vector",
            param={"metric_type": "COSINE"},
            limit=top_k * 2,
            filter=expr,
        )
        reqs: list[AnnSearchRequest] = [dense_req]

        # 稀疏路：query_text → sparse 字段（BM25 full-text search）
        if query_text:
            sparse_req = AnnSearchRequest(
                data=[query_text],
                anns_field="sparse",
                param={"metric_type": "BM25"},
                limit=top_k * 2,
                filter=expr,
            )
            reqs.append(sparse_req)

        res = self.client.hybrid_search(
            collection_name=self.collection_name,
            reqs=reqs,
            ranker=RRFRanker() if len(reqs) > 1 else None,
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
