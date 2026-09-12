"""检索侧入口：检索注入（详见 .strategies 包）。"""
from .strategies import (
    INJECTION_CHAR_BUDGET,
    RETRIEVAL_FETCH_MULTIPLIER,
    RETRIEVAL_WIDE_FETCH_K,
    STRATEGY_CHAIN,
    RetrievalStrategy,
    apply_retrieval_strategies,
)

__all__ = [
    "INJECTION_CHAR_BUDGET",
    "RETRIEVAL_FETCH_MULTIPLIER",
    "RETRIEVAL_WIDE_FETCH_K",
    "STRATEGY_CHAIN",
    "RetrievalStrategy",
    "apply_retrieval_strategies",
]
