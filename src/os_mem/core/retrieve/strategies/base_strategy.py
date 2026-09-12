"""检索策略协议（``RetrievalStrategy``）。

只定义契约：输入候选 hits（Milvus search 返回的 dict 列表），输出经本策略
过滤/调整后的 hits 列表。检索链路与注入装配在
``os_mem.core.retrieve.strategies_retriever``；本模块**不自带策略实现**，
也**不定义** `STRATEGY_CHAIN`（2026-09-12 简化时随装配逻辑一起移到检索侧入口）。
"""

from typing import Any, Protocol


class RetrievalStrategy(Protocol):
    """策略协议：输入 hits（dict 列表），输出经本策略过滤/调整后的 hits 列表。"""

    def apply(
        self,
        query: str,
        hits: list[dict[str, Any]],
        top_k: int,
    ) -> list[dict[str, Any]]:
        ...
