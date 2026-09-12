"""检索注入策略组件库（策略协议 + 单一职责策略类）。

**注意**：本包只提供"策略组件"，不再承载检索链路——检索执行与注入装配
（宽窗取回 + 结构化优先 + 字符预算）在 ``os_mem.core.retrieve.strategies_retriever``。

2026-09-12 起 ``STRATEGY_CHAIN`` 为空（宽窗后已无需要窗口内再裁决的组件，
依据见该模块 docstring 与 docs/方案/方案-检索注入简化-宽窗替代策略链.md §六-续）。
下面这些策略类目前**不在生产链路中**，仅保留类实现与组件单测，待更多轮次
或 layer2 验证后随模块删除。
"""
from .base_strategy import RetrievalStrategy
from .redundant_verbatim import RedundantVerbatimFilter
from .struct_key_dedup import StructuredKeyDedup
from .structured_quota import StructuredQuota
from .verbatim_noise import VerbatimNoiseFilter
from .verbatim_quota import VerbatimQuota

__all__ = [
    "RedundantVerbatimFilter",
    "RetrievalStrategy",
    "StructuredKeyDedup",
    "StructuredQuota",
    "VerbatimNoiseFilter",
    "VerbatimQuota",
]
