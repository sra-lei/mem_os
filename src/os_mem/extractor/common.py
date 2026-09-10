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
- ``fact_tokens(text)`` / ``norm_token(token)``：可核验数值 token 抽取与归一
  （金额/编号/卡号/≥4 位数字）——提取侧 R1 覆盖剪枝（regular_extractor）与
  检索侧 verbatim 冗余过滤（core.retrieval_strategies）共享同一口径，本处是唯一源；
- ``MAX_TRUNC_SPLIT_DEPTH``：单段恢复循环的切段递归最大层数（每层把段再
  切半，≤1 层已足够收敛输出预算）；
- ``EXTRACTION_STATS_KEYS``：恢复循环遥测 keys（``empty_extraction_stats`` 与
  任务层实例计数共用；任务层计数另含任务语义的 ``degrade_rows``，见 fact_extractor）。

依赖方向（无环）：本模块不 import 包内其他模块（亦不 import 本包任何模块，
仅用 stdlib re），可被 fact_extractor / callers / regular_extractor，乃至检索侧
core.retrieval_strategies 任意引用。
"""

from __future__ import annotations

import re

# 恢复循环遥测 keys（与 FactExtractor 实例计数一致；degrade_rows 属任务层，
# 由 fact_extractor 实例计数在共享 keys 之外自行追加）。
# in_tokens / out_tokens：每次 generate 后由恢复核心按 usage 累计（None→0），
# 递归/重试全路径自然计入——见 callers.ExtractionCore 与方案 §4 步骤 4 观测增强。
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
    递归/重试全路径自然计入——见 callers.ExtractionCore 与方案 §4 步骤 4 观测增强。
    """
    return {key: 0 for key in EXTRACTION_STATS_KEYS}


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


# --------------------------------------------------------------------------- #
#  数值 token 口径（提取 R1 剪枝 / 检索冗余过滤共享，改此即同步两侧语义）
#  与判分（tests/eval/judge/impl/assert_judger.py）期望信息点口径一致：
#  金额 / 字母数字编号 / 长数字串 / ≥4 位纯数字，归一（去 $ 千分位连字符，小写）。
# --------------------------------------------------------------------------- #
_AMOUNT_RE = re.compile(r'\$\s?\d{1,3}(?:,\d{3})+(?:\.\d+)?|\$\s?\d+(?:\.\d+)?')
_CODE_RE = re.compile(r'\b[A-Z]{1,8}-?\d{2,}[A-Za-z0-9-]*\b', re.IGNORECASE)
_CARD_RE = re.compile(r'\b\d{4,}(?:[-\s]\d{4,}){1,}\b')
_NUM4_RE = re.compile(r'(?<!\d)\d{4,}(?!\d)')
_TOKEN_NORM = re.compile(r'[\s,$%_\'"-]+')


def norm_token(token: str) -> str:
    """单 token 归一化：去格式噪音（$ 千分位连字符等），小写。"""
    return _TOKEN_NORM.sub('', token).lower()


def fact_tokens(text: str) -> set[str]:
    """从文本抽取可核验数值 token（金额/编号/卡片/≥4 位数字），归一化去重。"""
    tokens: set[str] = set()
    for match in _AMOUNT_RE.finditer(text):
        tokens.add(norm_token(match.group(0)))
    rest = _AMOUNT_RE.sub(' ', text)
    for match in _CODE_RE.finditer(rest):
        tokens.add(norm_token(match.group(0)))
    rest = _CODE_RE.sub(' ', rest)
    for match in _CARD_RE.finditer(rest):
        tokens.add(norm_token(match.group(0)))
    rest = _CARD_RE.sub(' ', rest)
    for number in _NUM4_RE.findall(rest):
        tokens.add(norm_token(number))
    return tokens
