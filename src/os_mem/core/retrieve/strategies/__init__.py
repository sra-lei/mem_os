"""检索注入策略包：单一质量闸门 + 固定顺序 STRATEGY_CHAIN。

对外唯一出口（生产侧用 apply_retrieval_strategies / RETRIEVAL_WIDE_FETCH_K；
单测另需逐策略类与 STRATEGY_CHAIN）。

2026-09-12 简化后：链上只剩 1 号 `VerbatimNoiseFilter`；2/3/4/5 号的类保留在
本包（各自单测仍在跑），但已不在 `STRATEGY_CHAIN` 中，待端到端对照确认后删除。
"""
from .base_strategy import (
    INJECTION_CHAR_BUDGET,
    RETRIEVAL_WIDE_FETCH_K,
    STRATEGY_CHAIN,
    RetrievalStrategy,
    apply_retrieval_strategies,
    trim_to_budget,
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
    "INJECTION_CHAR_BUDGET",
    "RETRIEVAL_FETCH_MULTIPLIER",
    "RETRIEVAL_WIDE_FETCH_K",
    "STRATEGY_CHAIN",
    "VERBATIM_MIN_RATIO",
    "RedundantVerbatimFilter",
    "RetrievalStrategy",
    "StructuredKeyDedup",
    "StructuredQuota",
    "VerbatimNoiseFilter",
    "VerbatimQuota",
    "apply_retrieval_strategies",
    "trim_to_budget",
]
