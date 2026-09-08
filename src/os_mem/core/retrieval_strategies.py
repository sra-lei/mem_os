"""检索注入策略链 —— 把「检索候选 hits → 最终注入列表」拆成固定顺序、单一职责的小策略。

背景（2026-09-05/06/07）：
- struct 检索（混合搜索）返回 top_k 条相关 fact，但 70-127 条/case 只注入 top-15
  （12-20% 覆盖），同主题句扎堆挤掉关键事实（13/14/15/18/19 失败主因）；
- verbatim 兜底句里既有噪音（问句/过程中间值/比较调整句），也有**唯一信息载体**
  （case 18/20 的关键数值只存在于 verbatim 句，压掉 verbatim 会直接丢答案）；
- v1 区分准入验证（2026-09-07）：layer1 struct/assert/top_k=15 通过率 11/20 → 14/20
  （run_c887cb12 → run_c087f9ee），覆盖漏 30 → 18 期望点，恢复 17/18/20 且零新增失败。
  详见 docs/方案-检索注入verbatim区分策略.md。

设计（v2 重构，2026-09-07）：
- **单一职责**：每个策略类只做一件事；注册为固定顺序策略链 ``STRATEGY_CHAIN``，
  **默认全部加载，无 Enable 开关**（基线对比不再靠运行时开关，靠 git/代码版本 +
  run 落库对照）；
- 策略只消费「search 返回的 dict 列表」→ 产出注入列表，不触碰存储/提取；
- 顺序即语义：噪声剔除 → 结构化 (cat,key) 去重 → verbatim 冗余剔除 → verbatim 配额
  → 结构化配额 → 终装配（结构化在前、verbatim 补位，截断 top_k）；
- fetch 放大取回（top_k × RETRIEVAL_FETCH_MULTIPLIER）由调用方无条件执行，不再依赖开关
  （见 struc_mem_service.get_structured_memories）。
"""

from __future__ import annotations

import re
from typing import Any, Protocol

from os_mem.utils.fact_tokens import fact_tokens

# fetch 放大取回系数：候选 = top_k * N，再经策略链收敛回 top_k（无条件生效）
RETRIEVAL_FETCH_MULTIPLIER = 3

# verbatim 预留槽位占比下限（结构化充足时 carrier 至少占 top_k 的这么多）
VERBATIM_MIN_RATIO = 1 / 3

# ---------------------------------------------------------------------- #
#  噪声判定（verbatim 专属，留本文件）；数值 token 口径见
#  os_mem.utils.fact_tokens（共享，与提取侧 R1 覆盖去重同源）
# ---------------------------------------------------------------------- #
# 疑问句/口语碎片（如 "So it would be $30 instead of $35?" / "okay" 等）
_VERBATIM_NOISE = re.compile(
    r'^.*(\?|？)$|'  # 疑问句
    r'^(yes|no|okay|ok|right|correct|exactly|great|perfect|thanks|thank you|sure|'
    r'alright|got it|could you|can you|what about|how about|is that|do you|would you|'
    r'so that|so it|and that|well|hmm|uh)[\s,.;:!?。，；：！？]*$',  # 口语碎片
    re.IGNORECASE,
)

# 比较/调整句标记：句子在对比两个值（过程性中间值/纠错），非定论句
# （例：case 20 "$308.75 instead of $617.50 for that week"）
_VERBATIM_ADJUST = re.compile(r'\binstead of\b|\brather than\b', re.IGNORECASE)


def _is_verbatim_noise(fact: str) -> bool:
    """verbatim 噪声判定：疑问句/口语碎片 ∪ 比较/调整标记。"""
    return bool(
        _VERBATIM_NOISE.match(fact.strip()) or _VERBATIM_ADJUST.search(fact)
    )


def _is_verbatim(hit: dict[str, Any]) -> bool:
    return (hit.get('key') or '').startswith('verbatim_')


def _hit_tokens(hit: dict[str, Any]) -> set[str]:
    fact = hit.get('fact') or ''
    return fact_tokens(f'{fact} {hit.get("value") or ""}')


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
#  1. 噪声剔除 —— verbatim 质量闸门
# ---------------------------------------------------------------------- #
class VerbatimNoiseFilter:
    """单一职责：剔除噪声 verbatim 句（疑问句/口语碎片/比较调整句）。

    治 02 新旧混入、17/19 碎片噪音与 case 20 的过程性中间值
    （"$308.75 instead of $617.50 for that week"）。非 verbatim 不受影响。
    """

    def apply(
        self,
        query: str,
        hits: list[dict[str, Any]],
        top_k: int,
    ) -> list[dict[str, Any]]:
        return [
            h
            for h in hits
            if not (_is_verbatim(h) and _is_verbatim_noise(h.get('fact') or ''))
        ]


# ---------------------------------------------------------------------- #
#  2. 结构化去重 —— 同 (category, key) 保首见
# ---------------------------------------------------------------------- #
class StructuredKeyDedup:
    """单一职责：结构化事实按 (category, key) 去重，保留首见（最相关版）。

    治同主题多版本扎堆（如多条同一 key 的 balance 变体占满窗口）。
    verbatim 每句唯一指纹 key，天然不受影响。
    """

    def apply(
        self,
        query: str,
        hits: list[dict[str, Any]],
        top_k: int,
    ) -> list[dict[str, Any]]:
        seen: set[tuple[str, str]] = set()
        out: list[dict[str, Any]] = []
        for hit in hits:
            if _is_verbatim(hit):
                out.append(hit)
                continue
            sig = ((hit.get('category') or ''), (hit.get('key') or ''))
            if sig in seen:
                continue
            seen.add(sig)
            out.append(hit)
        return out


# ---------------------------------------------------------------------- #
#  3. verbatim 冗余剔除 —— 只保留携带新数值信息的 carrier
# ---------------------------------------------------------------------- #
class RedundantVerbatimFilter:
    """单一职责：剔除不携带新信息的 verbatim（无数值 token，或数值已被覆盖）。

    覆盖集按输入序累积 = 已保留结构化事实的 token ∪ 已保留 carrier 的 token；
    verbatim 的数值 token 全被覆盖即冗余（同值多版本/已被结构化表达的句子），
    不入窗——这是 v1 "信息唯一性"准入的落地。
    """

    def apply(
        self,
        query: str,
        hits: list[dict[str, Any]],
        top_k: int,
    ) -> list[dict[str, Any]]:
        coverage: set[str] = set()
        out: list[dict[str, Any]] = []
        for hit in hits:
            toks = _hit_tokens(hit)
            if not _is_verbatim(hit):
                out.append(hit)
                coverage |= toks
                continue
            if not toks or toks <= coverage:
                continue
            out.append(hit)
            coverage |= toks
        return out


# ---------------------------------------------------------------------- #
#  4. verbatim 配额 —— 结构化充足时压 carrier，稀缺时放行填满
# ---------------------------------------------------------------------- #
class VerbatimQuota:
    """单一职责：限制窗口内 verbatim 数量。

    配额 = max(ceil(top_k/3), top_k - 结构化数)：
    - 结构化充足（≥ top_k）→ verbatim ≤ 1/3（防英文兜底句霸榜，835b 55% 教训）；
    - 结构化稀缺 → verbatim 可填满剩余（case 18/20 唯一载体修复点）。
    超出部分按相关性序（输入序）截断。
    """

    def apply(
        self,
        query: str,
        hits: list[dict[str, Any]],
        top_k: int,
    ) -> list[dict[str, Any]]:
        n_structured = sum(1 for h in hits if not _is_verbatim(h))
        allowance = max((top_k + 2) // 3, top_k - n_structured)
        kept = 0
        out: list[dict[str, Any]] = []
        for hit in hits:
            if _is_verbatim(hit):
                if kept >= allowance:
                    continue
                kept += 1
            out.append(hit)
        return out


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
