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
- ``fact_tokens(text)`` / ``norm_token(token)``：精确信息 token 抽取与归一
  （金额/编号/卡号/≥4 位数字/**百分比/时刻/日期**，通用口径不看齐判分器）——
  提取侧 R1 覆盖剪枝（regular_extractor）与检索侧 verbatim 冗余过滤
  （core.retrieval_strategies）共享同一口径，本处是唯一源；
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
#  精确信息 token 口径（提取 R1 剪枝 / 检索冗余过滤共享，改此即同步两侧语义）
#
#  通用口径——**不向任何评测判分器看齐**：凡对用户记忆有价值的精确信息都收：
#  金额 / 字母数字编号 / 长数字串（≥4 位）/ 百分比 / 时刻 / 日期（归一为月-日）。
#  eval judge 在 tests/ 下持有自己的独立窄口径副本（分属测试，注明同步义务），
#  两边互不依赖：判分器可以只硬核验金额编号，存储/检索不能因此丢弃 %/时间/日期。
# --------------------------------------------------------------------------- #
_AMOUNT_RE = re.compile(r'\$\s?\d{1,3}(?:,\d{3})+(?:\.\d+)?|\$\s?\d+(?:\.\d+)?')
_CODE_RE = re.compile(r'\b[A-Z]{1,8}-?\d{2,}[A-Za-z0-9-]*\b', re.IGNORECASE)
_CARD_RE = re.compile(r'\b\d{4,}(?:[-\s]\d{4,}){1,}\b')
_NUM4_RE = re.compile(r'(?<!\d)\d{4,}(?!\d)')
# 百分比：3% / 1.2% / 50%（归一后与裸短数字无碰撞——短数字本就不收）
_PCT_RE = re.compile(r'\d{1,3}(?:\.\d+)?%')
# 时刻：2:30 / 2:30 PM / 14:35（归一保留时分，AM/PM 小写粘连）
_CLOCK_RE = re.compile(r'\b\d{1,2}[:：]\d{2}\s*(?:[APap]\.?[Mm]\.?)?\b')
# 日期统一归一为「月-日」（忽略年份：与判分日期哲学一致，且少剪优于错剪）：
# 数字 11/21[/2024]、英文 November 21st / Nov 21、中文 11月21日
_NUM_DATE_RE = re.compile(r'\b(\d{1,2})/(\d{1,2})(?:/\d{2,4})?\b')
_CJK_DATE_RE = re.compile(r'(\d{1,2})\s*月\s*(\d{1,2})\s*日?')
_MONTH_ALIASES = {
    'january': 1, 'jan': 1, 'february': 2, 'feb': 2, 'march': 3, 'mar': 3,
    'april': 4, 'apr': 4, 'may': 5, 'june': 6, 'jun': 6, 'july': 7, 'jul': 7,
    'august': 8, 'aug': 8, 'september': 9, 'sept': 9, 'sep': 9,
    'october': 10, 'oct': 10, 'november': 11, 'nov': 11, 'december': 12, 'dec': 12,
}
_MONTH_NAME_RE = re.compile(
    r'\b(?:' + '|'.join(sorted(_MONTH_ALIASES, key=len, reverse=True)) +
    r')\.?\s+(\d{1,2})(?:st|nd|rd|th)?\b',
    re.IGNORECASE,
)
_TOKEN_NORM = re.compile(r'[\s,$%_\'"-]+')
_DATE_BLOCKS = (_NUM_DATE_RE, _MONTH_NAME_RE)


def norm_token(token: str) -> str:
    """单 token 归一化：去格式噪音（$ 千分位连字符空格等），小写。"""
    return _TOKEN_NORM.sub('', token).lower()


def _date_tokens(text: str) -> tuple[set[str], str]:
    """抽日期 token（统一 ``M-D`` 形态），返回 (tokens, 抽除日期后的文本)。

    日期整块先于裸数字抽除，避免 ``11/21/2024`` 里的年份被当成普通 4 位数字。
    """
    tokens: set[str] = set()
    for m, d in _NUM_DATE_RE.findall(text):
        tokens.add(f'{int(m)}-{int(d)}')
    for m in _MONTH_NAME_RE.finditer(text):
        month = _MONTH_ALIASES[m.group(0).split()[0].rstrip('.').lower()]
        tokens.add(f'{month}-{int(m.group(1))}')
    for m, d in _CJK_DATE_RE.findall(text):
        tokens.add(f'{int(m)}-{int(d)}')
    for rx in _DATE_BLOCKS:
        text = rx.sub(' ', text)
    text = _CJK_DATE_RE.sub(' ', text)
    return tokens, text


def fact_tokens(text: str) -> set[str]:
    """抽取精确信息 token（金额/编号/卡号/≥4位数字/百分比/时刻/日期），归一去重。"""
    tokens: set[str] = set()
    for match in _AMOUNT_RE.finditer(text):
        tokens.add(norm_token(match.group(0)))
    rest = _AMOUNT_RE.sub(' ', text)
    date_tokens, rest = _date_tokens(rest)
    tokens |= date_tokens
    for match in _CODE_RE.finditer(rest):
        tokens.add(norm_token(match.group(0)))
    rest = _CODE_RE.sub(' ', rest)
    for match in _CARD_RE.finditer(rest):
        tokens.add(norm_token(match.group(0)))
    rest = _CARD_RE.sub(' ', rest)
    for match in _PCT_RE.finditer(rest):
        # 归一为 `3pct`/`1.2pct`：与裸短数字/金额区分（norm_token 会把 % 抹成 3）
        tokens.add(match.group(0).strip().lower().replace('%', 'pct'))
    rest = _PCT_RE.sub(' ', rest)
    for match in _CLOCK_RE.finditer(rest):
        tokens.add(norm_token(match.group(0)))
    rest = _CLOCK_RE.sub(' ', rest)
    for number in _NUM4_RE.findall(rest):
        tokens.add(norm_token(number))
    return tokens
