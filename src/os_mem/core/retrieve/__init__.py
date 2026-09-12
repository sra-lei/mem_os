"""检索侧入口：检索执行（``Retrieval`` / ``get_retriever``）+ 注入装配。

- 检索链路与装配实现：``.strategies_retriever``（宽窗取回 + 结构化优先 + 字符预算）；
- 策略组件库（协议 + 各策略类，当前不在生产链路中）：``.strategies``。
"""
from .strategies import RetrievalStrategy
from .strategies_retriever import (
    INJECTION_CHAR_BUDGET,
    RETRIEVAL_WIDE_FETCH_K,
    STRATEGY_CHAIN,
    Retrieval,
    apply_retrieval_strategies,
    get_retriever,
    trim_to_budget,
)

__all__ = [
    "INJECTION_CHAR_BUDGET",
    "RETRIEVAL_WIDE_FETCH_K",
    "STRATEGY_CHAIN",
    "Retrieval",
    "RetrievalStrategy",
    "apply_retrieval_strategies",
    "get_retriever",
    "trim_to_budget",
]
