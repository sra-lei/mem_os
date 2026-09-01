"""单独验证 mem_os 向量存储（在线 Milvus / Zilliz Cloud）。

覆盖：建 collection（dense + BM25 sparse）→ 写入（含长度校验）
     → 混合检索（稠密 + BM25 全文 + RRF）→ 元数据过滤 → count。

前置：依赖已安装（uv sync），.env 已配置 MILVUS_URI / MILVUS_API_KEY。

用法：
    uv run python -m scripts.test_vec_storage

注意：若之前已建过 mem_os collection（旧结构无 sparse 字段），
需要先删除重建（混合检索要求新 schema）：
    python -c "from os_mem.infra.storage import get_memory_vector_store; \
get_memory_vector_store().client.drop_collection('mem_os')"

说明：测试用 mock 向量（1024 维随机归一化）验证存储/检索/过滤链路；
真实场景把 mock_embedding 换成 vectorizer.embed(fact)（DashScope text-embedding-v4）。
"""
from __future__ import annotations

import random
from datetime import datetime

from os_mem.infra.storage import get_memory_vector_store

DIM = 1024  # 与 mem_settings.embedding_dim 一致


def mock_embedding(text: str, seed: int | None = None) -> list[float]:
    """生成确定性 mock 向量（验证链路用，与文本无关）。"""
    rng = random.Random(seed)
    v = [rng.random() for _ in range(DIM)]
    norm = sum(x * x for x in v) ** 0.5
    return [x / norm for x in v]


def main() -> None:
    store = get_memory_vector_store()
    now = datetime.utcnow().isoformat()

    # 1. 写入三条测试记忆（不同 user_id / category）
    memories = [
        {
            "id": "test_001",
            "fact": "用户支票账户号码是 4429853327",
            "category": "finance",
            "key": "checking_account_number",
            "value": "4429853327",
            "user_id": "u_test",
            "updated_at": now,
        },
        {
            "id": "test_002",
            "fact": "用户邮箱是 john@example.com",
            "category": "contact",
            "key": "email",
            "value": "john@example.com",
            "user_id": "u_test",
            "updated_at": now,
        },
        {
            "id": "test_003",
            "fact": "用户喜欢靠窗座位",
            "category": "preference",
            "key": "seat",
            "value": "window",
            "user_id": "u_other",
            "updated_at": now,
        },
    ]
    embeddings = [mock_embedding(m["fact"], seed=i) for i, m in enumerate(memories)]

    n = store.add_structured_memories(memories, embeddings)
    print(f"[1] 写入 {n} 条 StructuredMemory → mem_os")

    # 2. 长度校验：数量不一致应抛 ValueError
    try:
        store.add_structured_memories(memories, embeddings[:2])
        print("[2] ✗ 未抛出 ValueError（校验失效）")
    except ValueError as e:
        print(f"[2] ✓ 长度校验生效: {e}")

    # 3. 混合检索（稠密 + BM25 全文 + RRF）
    hits = store.search(mock_embedding("账户", seed=99), query_text="支票账户", top_k=5)
    print(f"[3] 混合检索(query_text='支票账户'): {len(hits)} 条")
    for h in hits:
        print(f"    - {h['fact']} | user={h['user_id']} cat={h['category']} dist={h['distance']:.4f}")

    # 4. 纯稠密检索（query_text=None，兼容单路用法）
    hits = store.search(mock_embedding("账户", seed=99), top_k=5)
    print(f"[4] 纯稠密检索(query_text=None): {len(hits)} 条")

    # 5. 混合检索 + 按 user_id 过滤
    hits = store.search(
        mock_embedding("账户", seed=99), query_text="支票账户", top_k=5, user_id="u_test",
    )
    print(f"[5] 混合 + user_id='u_test' 过滤: {len(hits)} 条 "
          f"({[h['user_id'] for h in hits]})")

    # 6. 混合检索 + 按 category 过滤
    hits = store.search(
        mock_embedding("账户", seed=99), query_text="支票账户", top_k=5, category="finance",
    )
    print(f"[6] 混合 + category='finance' 过滤: {len(hits)} 条 "
          f"({[h['category'] for h in hits]})")

    # 7. 混合检索 + 组合过滤 user_id + key
    hits = store.search(
        mock_embedding("账户", seed=99), query_text="支票账户", top_k=5,
        user_id="u_test", key="email",
    )
    print(f"[7] 混合 + user_id + key='email' 过滤: {len(hits)} 条 "
          f"({[h['key'] for h in hits]})")

    # 8. 集合总数
    print(f"[8] collection 总数: {store.count()}")


if __name__ == "__main__":
    main()
