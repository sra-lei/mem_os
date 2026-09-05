"""可插拔检索策略层 —— 作用于「检索候选 hits → 最终注入列表」。

背景（2026-09-05）：struct 检索（混合搜索）返回 top_k 条相关 fact，但评测暴露
覆盖率不足——70-127 条 fact/case 只注入 top-15（12-20%），且同主题句扎堆挤掉
关键事实（13/14/15/18/19 的失败主因）。本层提供**候选后处理策略**：不改搜索
本身，只对召回结果做去重/多样性/过滤/重排，插在
``StructuredMemService.get_structured_memories`` 的 search 之后、组装注入文本之前。

可插拔与可验证（关键设计约束）：
- 策略对象化 + 注册表，默认全关（基线直出，与无策略行为一致）；
- 策略只消费「search 返回的 dict 列表」→ 产出「注入列表」，不触碰存储/提取；
- 因此当 §10 遗留（事实来源锚定/新旧判定）落地后，search 输出结构不变，本层可
  原样复用——重跑 关 vs 开 即可验证该策略的收益是否仍独立存在。
"""

from __future__ import annotations

import re
from typing import Any, Protocol

# ========================================================================= #
#  策略开关（评测用，唯一手动控制点）—— True/False 变量，手动调整即可。
#  §10 来源锚定落地后：用同样的开关复测，验证本策略收益是否仍独立存在。
# ========================================================================= #
# 按 (category, key) 去重保各类代表（治 13/14/15/18/19 覆盖不足）
ENABLE_DIVERSITY = False
# 过滤低信息 verbatim 句（治 02 新旧混入 / 碎片噪音）
ENABLE_VERBATIM_GATE = False
# ------------------------------------------------------------------------- #

# diversity 策略的放大取回系数：候选 = top_k * N，再收敛回 top_k
DIVERSITY_FETCH_MULTIPLIER = 3

# verbatim_gate：过滤"疑问句/无主语碎片"等低信息 verbatim 句
_VERBATIM_NOISE = re.compile(
    r'^.*(\?|？)$|'  # 疑问句（如 "So it would be $30 instead of $35?"）
    r'^(yes|no|okay|ok|right|correct|exactly|great|perfect|thanks|thank you|sure|'
    r'alright|got it|could you|can you|what about|how about|is that|do you|would you|'
    r'so that|so it|and that|well|hmm|uh)[\s,.;:!?。，；：！？]*$',  # 口语碎片
    re.IGNORECASE,
)


class RetrievalStrategy(Protocol):
    """策略协议：输入检索 hits（dict 列表），输出最终注入 hits（dict 列表）。"""

    def apply(
        self,
        query: str,
        hits: list[dict[str, Any]],
        top_k: int,
    ) -> list[dict[str, Any]]:
        ...


class DiversityStrategy:
    """多样性覆盖：按 (category, key) 去重，保证同主题不扎堆、各类事实都有代表。

    调用方需以 top_k * DIVERSITY_FETCH_MULTIPLIER 取回候选
    （见 get_structured_memories）。
    实现：候选已按检索相关性排序（distance 升序 = 越相关越前），
    遍历候选，优先保留每 (category, key) 首次出现的命中（相关性最高的那个），
    直到填满 top_k；同一 key 的后续版本（重复/变体）被丢弃，避免挤占名额。
    注：此处按候选顺序取首见（= 检索最相关版），不是按置信度——同 key 多版本
    的"谁是最新"属于 §10 来源锚定范畴，不在本策略解决。
    """

    def apply(
        self,
        query: str,
        hits: list[dict[str, Any]],
        top_k: int,
    ) -> list[dict[str, Any]]:
        seen: set[tuple[str, str]] = set()
        result: list[dict[str, Any]] = []
        for hit in hits:
            cat = hit.get('category') or ''
            key = hit.get('key') or ''
            sig = (cat, key)
            if sig in seen:
                continue
            seen.add(sig)
            result.append(hit)
            if len(result) >= top_k:
                break
        return result


class VerbatimGateStrategy:
    """verbatim 质量闸门：过滤低信息 verbatim 句（疑问句/口语碎片/无主语确认）。

    治 02 的"新旧电话混入"与 17/19 的碎片噪音——库里 verbatim 兜底句含
    "So it would be $30 instead of $35?" 这类确认式问句，注入后误导模型。
    策略按 key 前缀 verbatim_ 识别兜底句，过滤噪音；保留含明确数字/编号的句。
    """

    def apply(
        self,
        query: str,
        hits: list[dict[str, Any]],
        top_k: int,
    ) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for hit in hits:
            key = hit.get('key') or ''
            fact = hit.get('fact') or ''
            if key.startswith('verbatim_') and _VERBATIM_NOISE.match(fact.strip()):
                continue
            result.append(hit)
            if len(result) >= top_k:
                break
        return result


def enabled_strategies() -> list[str]:
    """按布尔开关返回激活策略名列表（顺序即应用顺序）。"""
    names: list[str] = []
    if ENABLE_DIVERSITY:
        names.append('diversity')
    if ENABLE_VERBATIM_GATE:
        names.append('verbatim_gate')
    return names


def apply_retrieval_strategies(
    query: str,
    hits: list[dict[str, Any]],
    top_k: int,
) -> list[dict[str, Any]]:
    """按激活开关依序应用策略；全关时直出前 top_k（基线）。

    输入 hits 由调用方决定取回量（diversity 开启时应放大取回，见调用处）。
    """
    if ENABLE_DIVERSITY:
        hits = DiversityStrategy().apply(query, hits, top_k)
    if ENABLE_VERBATIM_GATE:
        hits = VerbatimGateStrategy().apply(query, hits, top_k)
    return hits[:top_k]
