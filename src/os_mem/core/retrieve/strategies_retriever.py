"""检索执行 + 注入装配（检索侧唯一入口）。

职责（2026-09-12 重构后）：
- ``Retrieval``：一次检索的完整链路——query 向量化（DashScope）→
  Milvus 混合检索（dense + sparse，宽窗取回 ``RETRIEVAL_WIDE_FETCH_K``）→
  注入装配（``apply_retrieval_strategies``）；
- ``get_retriever()``：进程内单例（复用 ``get_vectorizer`` /
  ``get_memory_vector_store`` 的既有单例，不额外建连接）。

注入装配语义（``apply_retrieval_strategies``）：
1. 依序执行 ``STRATEGY_CHAIN``——**2026-09-12 起链为空**：宽窗后已无需要
   「窗口内再裁决」的组件；
2. 终装配：结构化事实在前、verbatim 兜底句补位（既有注入读序）；
3. 字符预算截断（``INJECTION_CHAR_BUDGET``）：预算内全入，超出才按相关性序截断，
   只为 layer2/3 多会话累积兜底，layer1 单会话实测不会触发。

链被清空的依据（详见 docs/方案/方案-检索注入简化-宽窗替代策略链.md §1.4 / §六-续）：
- 2 号 `StructuredKeyDedup`：投影键空间 `(category, 投影键)` 重复组实测 0，已 no-op；
- 3 号 `RedundantVerbatimFilter`：verbatim token ⊆ 结构化实测 0/157，被入库侧 R1
  前置吸收；
- 4/5 号双配额：窗口不再按条数截断，配额失去对象；
- 1 号 `VerbatimNoiseFilter`：5 轮端到端（layer1）里**未显示收益**——带闸门 16/20 落在
  零闸门 16~19 的波动区间内，且零闸门更简单。
  故移出链的依据是「无证据支持 + 更简单」，**不是**「已证明有害」。
  各策略类与组件单测暂留 ``strategies/`` 包内，待更多轮次或 layer2 后随模块删除。
"""

from typing import Any

from os_mem.core.retrieve.strategies.base_strategy import RetrievalStrategy
from os_mem.core.retrieve.strategies.verbatim_utils import _is_verbatim
from os_mem.infra.logger import get_logger
from os_mem.infra.p2check import mask_pii
from os_mem.infra.storage.vec_storage import MemoryVectorStore, get_memory_vector_store
from os_mem.infra.storage.vectorizer import Vectorizer, get_vectorizer

_logger = get_logger('os_mem.retrieve')

# 宽窗取回条数：单 case 库内约 46 行（layer1 单会话），256 足以覆盖全库。
RETRIEVAL_WIDE_FETCH_K = 256

# 注入预算（字符口径，替代原「top_k 条数」截断）：基准 run 实测回归
# tokens_input ≈ 299 + 0.241 × 窗口字符 → 12,000 字符 ≈ 2.9k tokens；
# layer1 全入约 3.8k 字符/例（≈954 tokens）远低于预算，layer2/3 累积时由它兜底。
INJECTION_CHAR_BUDGET = 12000

# 策略链（固定顺序，全部默认加载，无 Enable 开关）：见模块 docstring 的清空依据。
STRATEGY_CHAIN: list[RetrievalStrategy] = []


def trim_to_budget(
    hits: list[dict[str, Any]],
    budget_chars: int = INJECTION_CHAR_BUDGET,
) -> list[dict[str, Any]]:
    """按字符预算截断——预算内不动，超出则保留前缀（相关性序）。

    只为 layer2/3 多会话累积兜底；layer1 单会话实测不会触发。
    """
    out: list[dict[str, Any]] = []
    used = 0
    for hit in hits:
        cost = len(hit.get('fact') or '') + len(hit.get('value') or '')
        if out and used + cost > budget_chars:
            break
        out.append(hit)
        used += cost
    return out


def apply_retrieval_strategies(
    query: str,
    hits: list[dict[str, Any]],
    top_k: int,
    budget_chars: int = INJECTION_CHAR_BUDGET,
) -> list[dict[str, Any]]:
    """依序执行策略链后做终装配：结构化在前、verbatim 补位，再按字符预算截断。

    Args:
        query: 本次检索 query（透传给策略链）。
        hits: 候选列表，由调用方**宽窗**取回（不复用旧的 top_k × 放大系数）。
        top_k: 仅为调用方与策略协议兼容而保留——**不再是注入条数上限**，
            窗口大小改由 ``budget_chars`` 控制。
        budget_chars: 注入字符预算。
    """
    for strategy in STRATEGY_CHAIN:
        hits = strategy.apply(query, hits, top_k)
    structured = [h for h in hits if not _is_verbatim(h)]
    verbatim = [h for h in hits if _is_verbatim(h)]
    return trim_to_budget(structured + verbatim, budget_chars)


class Retrieval:
    """一次检索的执行器：向量化 → 混合检索（宽窗）→ 注入装配 + 预算。"""

    def __init__(self, vectorizer: Vectorizer, vector_store: MemoryVectorStore) -> None:
        self.vectorizer = vectorizer
        self.vector_store = vector_store

    def retrieve(self, query: str, top_k: int, user_id: str) -> list[dict[str, Any]]:
        """返回**可直接注入**的记忆列表（已装配、已在预算内）。

        Args:
            query: 本次 query（同时用于 dense 向量与 BM25 全文两路）。
            top_k: 兼容参数（条数上限已被字符预算取代，仅作为宽窗下限）。
            user_id: 记忆归属用户；Milvus 侧按 `user_id ==` 过滤。
        """
        # 宽窗取回：单 case 库内约 46 行，256 足以覆盖全库
        fetch_k = max(top_k, RETRIEVAL_WIDE_FETCH_K)
        masked_query = mask_pii(query)
        query_embedding: list[float] = []
        try:
            query_embedding = self.vectorizer.embed(query)
        except Exception as e:
            _logger.error(f'query 向量化失败 user={user_id} query={masked_query}: {e}')

        hits = (
            self.vector_store.search(
                query_embedding,
                query_text=query,
                top_k=fetch_k,
                user_id=user_id,
            )
            or []
        )
        if not hits:
            _logger.warning(f'struct 检索无命中 user={user_id} query={masked_query}')
            return []

        injected = apply_retrieval_strategies(query, hits, top_k)
        _logger.info(
            f'  检索命中 {len(hits)} 条（fetch={fetch_k}）'
            f'→ 注入 {len(injected)} 条（预算制）'
        )
        return injected


_retriever: Retrieval | None = None
_vectorizer = get_vectorizer()
_vector_store = get_memory_vector_store()


def get_retriever() -> Retrieval:
    """进程内单例检索器（复用既有 vectorizer / vector store 单例）。"""
    global _retriever
    if _retriever is None:
        _retriever = Retrieval(_vectorizer, _vector_store)
    return _retriever
