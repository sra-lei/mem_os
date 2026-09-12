from typing import Any, Protocol

from .redundant_verbatim import RedundantVerbatimFilter
from .struct_key_dedup import StructuredKeyDedup
from .structured_quota import StructuredQuota
from .verbatim_noise import VerbatimNoiseFilter
from .verbatim_quota import VerbatimQuota
from .verbatim_utils import _is_verbatim


class RetrievalStrategy(Protocol):
    """策略协议：输入 hits（dict 列表），输出经本策略过滤/调整后的 hits 列表。"""

    def apply(
        self,
        query: str,
        hits: list[dict[str, Any]],
        top_k: int,
    ) -> list[dict[str, Any]]:
        ...

# ---------------------------------------------------------------------- #
#  策略链（固定顺序，全部默认加载，无 Enable 开关）
# ---------------------------------------------------------------------- #
STRATEGY_CHAIN: list[RetrievalStrategy] = [
    VerbatimNoiseFilter(),
    StructuredKeyDedup(),
    RedundantVerbatimFilter(),
    VerbatimQuota(),
    StructuredQuota(),
]

def apply_retrieval_strategies(
    query: str,
    hits: list[dict[str, Any]],
    top_k: int,
) -> list[dict[str, Any]]:
    """依序执行策略链后做终装配：结构化在前、verbatim 补位，截断 top_k。

    输入 hits 由调用方放大取回（top_k × RETRIEVAL_FETCH_MULTIPLIER），
    链上各策略已把总量收敛到 ≤ top_k，此处仅稳定顺序（既有注入读序）。
    """
    for strategy in STRATEGY_CHAIN:
        hits = strategy.apply(query, hits, top_k)
    structured = [h for h in hits if not _is_verbatim(h)]
    verbatim = [h for h in hits if _is_verbatim(h)]
    return (structured + verbatim)[:top_k]
