"""提取域共享纯函数与常量（单一实现源，消歧用）。

归属：``os_mem.extractor`` 记忆提取域。本模块只放**无副作用**的纯函数与常量，
供任务层（fact_extractor.py）与 provider 自愈 caller（callers/）共用——
两侧**不得各自再实现副本**，改这里即同步两侧语义：

- ``split_text_midpoint(text) -> tuple[str, str] | None``：消息中点对半切
  （截断空返回的切段递归用）；少于 2 条消息或任一侧为空 → None（不可切）；
- ``dedup_facts(facts) -> facts``：按 ``(category, key, value)`` 去重
  （caller 切段两半合并 / 任务层跨段去重共用）；
- ``MAX_TRUNC_SPLIT_DEPTH``：单段恢复循环的切段递归最大层数（每层把段再
  切半，≤1 层已足够收敛输出预算）；
- ``EXTRACTION_STATS_KEYS``：恢复循环遥测 keys（``empty_extraction_stats`` 与
  任务层实例计数共用；任务层计数另含任务语义的 ``degrade_rows``，见 fact_extractor）。

精确信息 token 抽取口径（``fact_tokens`` / ``norm_token``）在 ``token_utils.py``
（金额/编号/卡号/≥4 位数字/百分比/时刻/日期；通用口径不看齐判分器）——提取侧 R1
覆盖剪枝（regular_extractor）与检索侧 verbatim 冗余过滤
（core.retrieval_strategies）共享同一口径，以该模块为唯一源。

依赖方向（无环）：本模块不 import 包内其他模块，仅用 stdlib，可被
fact_extractor / callers / regular_extractor，乃至检索侧
core.retrieval_strategies 任意引用。
"""

from __future__ import annotations

# 恢复循环遥测 keys（与 FactExtractor 实例计数一致；degrade_rows 属任务层，
# 由 fact_extractor 实例计数在共享 keys 之外自行追加）。
# in_tokens / out_tokens：每次 generate 后由恢复核心按 usage 累计（None→0），
# 递归/重试全路径自然计入——见 extraction_core.ExtractionCore 与方案 §4 步骤 4 观测增强。
EXTRACTION_STATS_KEYS = (
    'llm_calls',
    'trunc_empties',
    'split_recursions',
    'repair_calls',
    'repair_ok',
    'in_tokens',
    'out_tokens',
)

# 单段提取恢复循环的切段递归最大层数（每层把段再切半，≤1 层已足够收敛输出预算）
MAX_TRUNC_SPLIT_DEPTH = 1


def empty_extraction_stats() -> dict[str, int]:
    """恢复循环遥测计数的零值 dict（keys 与 FactExtractor 实例计数一致；
    degrade_rows 属任务层，由任务层在共享 keys 之外自行追加）。

    CallResult.stats 的 default_factory 与 callers 恢复循环共用此单一实现源。
    in_tokens / out_tokens：每次 generate 后由恢复核心按 usage 累计（None→0），
    递归/重试全路径自然计入——见 extraction_core.ExtractionCore 与方案 §4
    步骤 4 观测增强。
    """
    return {key: 0 for key in EXTRACTION_STATS_KEYS}


def split_text_midpoint(text: str) -> tuple[str, str] | None:
    """消息中点对半切（截断空返回的切段递归用）。

    少于 2 条消息或任一侧为空 → None（不可切）。caller 切段递归与任务侧
    （fact_extractor）共用本函数，是唯一实现源。
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

    caller 切段两半合并与任务层跨段去重共用本函数，是唯一实现源。
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
