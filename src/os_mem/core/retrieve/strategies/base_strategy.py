"""检索注入：宽窗 + 单一质量闸门（2026-09-12 简化）。

见 docs/方案/方案-检索注入简化-宽窗替代策略链.md。

历史（2026-09-05~09-07）：策略链把「候选 → 注入列表」拆成 5 段单一职责策略
（噪声剔除 → 结构化去重 → verbatim 冗余剔除 → 双配额 → 终装配），用于在
固定窄窗（top_k=15）里分配稀缺槽位。

2026-09-12 实测（本机 D4 口径库 933 行 / 20 user）后确认该分配器的三个前提已消失：
- 2 号 `StructuredKeyDedup`：`(category, 投影键)` 重复组 **0** —— 入库侧签名唯一性
  不变量 + 投影按 D4 收敛键删旧插新，检索候选里本就没有同键重复；
- 3 号 `RedundantVerbatimFilter`：verbatim token ⊆ 结构化 **0/157** —— 入库侧 R1
  （`RegularExtractor.prune_redundant_verbatim`）用同一个 `fact_tokens` 已前置吸收；
- 4/5 号双配额：不截断就没有「槽」可分配。
只剩 1 号（形式干扰句闸门）仍有正向价值（实测命中 18/157，13 条真丢失项里 12 条
确为判分不想要的干扰值）→ 链退化为「单一闸门 + 预算内全入」。

注意：被移出链的策略类（2/3/4/5 号）暂留磁盘与单测，待 A/B/C 端到端对照确认后
再单独删除（见方案 §十二）。
"""

from typing import Any, Protocol

from .verbatim_utils import _is_verbatim

# 宽窗取回条数：单 case 库内约 46 行（layer1 单会话），256 足以覆盖全库。
RETRIEVAL_WIDE_FETCH_K = 256

# 注入预算（字符口径，替代原「top_k 条数」截断）：基准 run 实测回归
# tokens_input ≈ 299 + 0.241 × 窗口字符 → 12,000 字符 ≈ 2.9k tokens；
# layer1 全入约 3.8k 字符/例（≈954 tokens）远低于预算，layer2/3 累积时由它兜底。
INJECTION_CHAR_BUDGET = 12000


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
#  2026-09-12 端到端对照结论：链**清空**——宽窗后已无任何需要"窗口内再裁决"
#  的组件（2/3/4/5 号实测 no-op / 配额失去对象；1 号按形式删整句会连带删掉
#  合法内容，A/B/C 三组实测 B 16/20 < C 19/20，故一并移出）。
#  见 docs/方案/方案-检索注入简化-宽窗替代策略链.md §六-续。
#  各策略类暂留磁盘与组件单测，待 ≥2 轮确认后随模块删除。
# ---------------------------------------------------------------------- #
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
