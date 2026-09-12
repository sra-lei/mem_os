"""检索侧入口：检索注入策略链（详见 .strategies 包）。"""
from .strategies import (
    RETRIEVAL_FETCH_MULTIPLIER,
    STRATEGY_CHAIN,
    RetrievalStrategy,
    apply_retrieval_strategies,
)

__all__ = [
    "RETRIEVAL_FETCH_MULTIPLIER",
    "STRATEGY_CHAIN",
    "RetrievalStrategy",
    "apply_retrieval_strategies",
]
