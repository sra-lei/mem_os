"""检索注入策略链（历史） —— 固定顺序、单一职责的小策略集合。

> ⚠️ **2026-09-12 起本模块不在生产链路中**（`STRATEGY_CHAIN` 已清空，检索执行与
> 装配迁至 `os_mem/core/retrieve/strategies_retriever.py`）。保留原因：类实现与组件
> 单测仍是"若将来要做标注/降权"的参考实现，待更多轮次或 layer2 验证后随模块删除。
> 端到端依据见 docs/方案/方案-检索注入简化-宽窗替代策略链.md §1.4 与 §六-续。

背景（2026-09-05/06/07）：
- struct 检索（混合搜索）返回 top_k 条相关 fact，但 70-127 条/case 只注入 top-15
  （12-20% 覆盖），同主题句扎堆挤掉关键事实（13/14/15/18/19 失败主因）；
- verbatim 兜底句里既有噪音（问句/过程中间值/比较调整句），也有**唯一信息载体**
  （case 18/20 的关键数值只存在于 verbatim 句，压掉 verbatim 会直接丢答案）；
- v1 区分准入验证（2026-09-07）：layer1 struct/assert/top_k=15 通过率 11/20 → 14/20
  （run_c887cb12 → run_c087f9ee），覆盖漏 30 → 18 期望点，恢复 17/18/20 且零新增失败。
  详见 docs/方案/方案-检索注入verbatim区分策略.md。
  注：该收益来自「固定窄窗内把 verbatim 载体放进窗口」，宽窗（2026-09-12）后自动兑现。

设计（v2 重构，2026-09-07，已被简化取代）：
- **单一职责**：每个策略类只做一件事；原注册为固定顺序策略链 ``STRATEGY_CHAIN``；
- 策略只消费「search 返回的 dict 列表」→ 产出注入列表，不触碰存储/提取；
- 原顺序：噪声剔除 → 结构化 (cat,key) 去重 → verbatim 冗余剔除 → verbatim 配额
  → 结构化配额 → 终装配；
- 原 fetch 放大取回（`top_k × RETRIEVAL_FETCH_MULTIPLIER`）**已废除**，改为宽窗取回
  （见 strategies_retriever.RETRIEVAL_WIDE_FETCH_K）。
"""

from __future__ import annotations

from typing import Any

from .verbatim_utils import _is_verbatim

# ⚠️ 已废弃（保留仅为历史对照）：放大取回系数，已被宽窗取回取代，无任何生产消费者。
RETRIEVAL_FETCH_MULTIPLIER = 3

# ⚠️ 未使用（历史预留）：verbatim 预留槽位占比下限；配额机制已移出链路。
VERBATIM_MIN_RATIO = 1 / 3


# ---------------------------------------------------------------------- #
#  5. 结构化配额 —— 为已准入的 carrier 让位
# ---------------------------------------------------------------------- #
class StructuredQuota:
    """单一职责：结构化数量不超过 top_k - verbatim 数（为 carrier 预留槽）。

    结构化事实按相关性序截断；防止结构化占满窗口后 carrier 无位可坐
    （v1 的 take_s = min(structured, top_k - take_v)）。
    """

    def apply(
        self,
        query: str,
        hits: list[dict[str, Any]],
        top_k: int,
    ) -> list[dict[str, Any]]:
        n_verbatim = sum(1 for h in hits if _is_verbatim(h))
        allowance = max(0, top_k - n_verbatim)
        kept = 0
        out: list[dict[str, Any]] = []
        for hit in hits:
            if not _is_verbatim(hit):
                if kept >= allowance:
                    continue
                kept += 1
            out.append(hit)
        return out
