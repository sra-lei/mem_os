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
ENABLE_DIVERSITY = True
# 过滤低信息 verbatim 句（治 02 新旧混入 / 碎片噪音）
ENABLE_VERBATIM_GATE = True
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
    """多样性覆盖 + verbatim 降级：结构化 fact 优先，verbatim 句补位。

    调用方需以 top_k * DIVERSITY_FETCH_MULTIPLIER 取回候选
    （见 get_structured_memories）。

    两段式：
    1. **结构化 fact 优先**：候选已按检索相关性排序（distance 升序），遍历取
       非 verbatim 的命中，按 (category, key) 去重（同 key 保留首见 = 最相关版），
       直到填满 top_k 或候选耗尽；
    2. **verbatim 补位**：结构化不足 top_k 时，把剩余 verbatim 句按相关性补入
       （仍按 (category, key) 去重——verbatim 每句唯一 key，故此处主要靠相关性
       顺序截断），但 verbatim 总量不超过 top_k 的 1/3（防英文兜底句霸榜挤掉
       结构化事实）。

    背景（2026-09-05 教训）：verbatim 指纹 key（B 批）使每条兜底句独立 key，
    naive 的"同 key 去重"对 verbatim 完全失效——英文 verbatim 句凭 BM25 命中
    霸榜前 15，中文结构化 fact（含关键编号/课表）被挤出（run_835b24aeb5 55%）。
    """

    # verbatim 补位上限占比（结构化 fact 不足时 verbatim 最多占这么多）
    VERBATIM_CAP_RATIO = 1 / 3

    def apply(
        self,
        query: str,
        hits: list[dict[str, Any]],
        top_k: int,
    ) -> list[dict[str, Any]]:
        structured: list[dict[str, Any]] = []
        verbatim_candidates: list[dict[str, Any]] = []
        seen_structured: set[tuple[str, str]] = set()
        seen_verbatim: set[tuple[str, str]] = set()
        for hit in hits:
            cat = hit.get('category') or ''
            key = hit.get('key') or ''
            is_verbatim = key.startswith('verbatim_')
            if is_verbatim:
                sig = (cat, key)
                if sig not in seen_verbatim:
                    seen_verbatim.add(sig)
                    verbatim_candidates.append(hit)
                continue
            sig = (cat, key)
            if sig in seen_structured:
                continue
            seen_structured.add(sig)
            structured.append(hit)
            if len(structured) >= top_k:
                return structured

        # 结构化不足 top_k：verbatim 补位（最多占 top_k 的 1/3）
        budget = int(top_k * self.VERBATIM_CAP_RATIO)
        verbatim_budget = max(0, min(len(verbatim_candidates), budget))
        return structured + verbatim_candidates[:verbatim_budget]


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
