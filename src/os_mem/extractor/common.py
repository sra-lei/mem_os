"""提取域共享纯函数与常量（单一实现源，消歧用）。

归属：``os_mem.extractor`` 记忆提取域。本模块只放**无副作用**的纯函数与常量，
供任务层（fact_extractor.py）与 provider 自愈 caller（callers.py）共用——
两侧**不得各自再实现副本**，改这里即同步两侧语义（此前 callers.py 与
extractor.py 曾各持一份逐行同构实现，靠单测/评审手工同步防漂移，本次拆出
收拢）：

- ``split_text_midpoint(text) -> tuple[str, str] | None``：消息中点对半切
  （截断空返回的切段递归用）；少于 2 条消息或任一侧为空 → None（不可切）；
- ``dedup_facts(facts) -> facts``：按 ``(category, key, value)`` 去重
  （caller 切段两半合并 / 任务层跨段去重共用）；
- ``MAX_TRUNC_SPLIT_DEPTH``：单段恢复循环的切段递归最大层数（每层把段再
  切半，≤1 层已足够收敛输出预算）；
- ``EXTRACTION_STATS_KEYS``：恢复循环遥测 keys（``_empty_stats`` 与任务层
  实例计数共用；任务层计数另含任务语义的 ``degrade_rows``，见 fact_extractor）。

依赖方向（无环）：本模块不 import 包内其他模块（亦不 import 本包任何模块），
可被 fact_extractor / callers 任意引用。
"""

from __future__ import annotations

# 恢复循环遥测 keys（与 FactExtractor 实例计数一致；degrade_rows 属任务层，
# 由 fact_extractor 实例计数在共享 keys 之外自行追加）
EXTRACTION_STATS_KEYS = (
    'llm_calls',
    'trunc_empties',
    'split_recursions',
    'repair_calls',
    'repair_ok',
)

# 单段提取恢复循环的切段递归最大层数（每层把段再切半，≤1 层已足够收敛输出预算）
MAX_TRUNC_SPLIT_DEPTH = 1


def split_text_midpoint(text: str) -> tuple[str, str] | None:
    """消息中点对半切（截断空返回的切段递归用）。

    少于 2 条消息或任一侧为空 → None（不可切）。实现自原 callers.py（与
    原 extractor._split_text 逐行同构的双副本）收拢而来——本函数是唯一实现源。
    """
    lines = text.split('\n')
    if len(lines) < 2:
        return None
    mid = len(lines) // 2
    left = '\n'.join(lines[:mid])
    right = '\n'.join(lines[mid:])
    if not left or not right:
        return None
    return left, right


def dedup_facts(facts: list) -> list:
    """按 (category, key, value) 去重（切段递归合并 / 跨段去重共用）。

    实现自原 callers.py 同名函数（与原 extractor.dedup_facts 同构的双副本）
    收拢而来——本函数是唯一实现源。
    """
    seen_signatures: set[tuple[str, str, str]] = set()
    result: list = []
    for fact in facts:
        signature = (fact.category, fact.key, fact.value)
        if signature in seen_signatures:
            continue
        seen_signatures.add(signature)
        result.append(fact)
    return result
