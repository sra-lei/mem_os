"""检索注入策略链包：单一职责策略 + 固定顺序 STRATEGY_CHAIN。

对外唯一出口（生产侧用 apply_retrieval_strategies；单测另需逐策略类与
STRATEGY_CHAIN）。
"""
from .base_strategy import (
    STRATEGY_CHAIN,
    RetrievalStrategy,
    apply_retrieval_strategies,
)
from .redundant_verbatim import RedundantVerbatimFilter
from .struct_key_dedup import StructuredKeyDedup
from .structured_quota import (
    RETRIEVAL_FETCH_MULTIPLIER,
    VERBATIM_MIN_RATIO,
    StructuredQuota,
)
from .verbatim_noise import VerbatimNoiseFilter
from .verbatim_quota import VerbatimQuota

__all__ = [
    "RETRIEVAL_FETCH_MULTIPLIER",
    "STRATEGY_CHAIN",
    "VERBATIM_MIN_RATIO",
    "RedundantVerbatimFilter",
    "RetrievalStrategy",
    "StructuredKeyDedup",
    "StructuredQuota",
    "VerbatimNoiseFilter",
    "VerbatimQuota",
    "apply_retrieval_strategies",
]
