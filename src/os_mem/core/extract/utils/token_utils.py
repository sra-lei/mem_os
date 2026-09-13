"""精确信息 token 口径 —— 提取 R1 剪枝 / 检索冗余过滤共享，改此即同步两侧语义。

通用口径——**不向任何评测判分器看齐**：凡对用户记忆有价值的精确信息都收：
金额 / 字母数字编号 / 长数字串（≥4 位）/ 百分比 / 时刻 / 日期（归一为月-日）。
eval judge 在 tests/ 下持有自己的独立窄口径副本（分属测试，注明同步义务），
两边互不依赖：判分器可以只硬核验金额编号，存储/检索不能因此丢弃 %/时间/日期。

本模块是唯一实现源；``regular_extractor`` 入库门复用 ``_MONTH_ALIASES``，
检索侧 ``core.retrieve.strategies`` 复用 ``fact_tokens``。
"""

from __future__ import annotations

import re

# --------------------------------------------------------------------------- #
#  精确信息 token 口径
# --------------------------------------------------------------------------- #
_AMOUNT_RE = re.compile(r'\$\s?\d{1,3}(?:,\d{3})+(?:\.\d+)?|\$\s?\d+(?:\.\d+)?')
_CODE_RE = re.compile(r'\b[A-Z]{1,8}-?\d{2,}[A-Za-z0-9-]*\b', re.IGNORECASE)
_CARD_RE = re.compile(r'\b\d{4,}(?:[-\s]\d{4,}){1,}\b')
_NUM4_RE = re.compile(r'(?<!\d)\d{4,}(?!\d)')
# 百分比：3% / 1.2% / 50%（归一后与裸短数字无碰撞——短数字本就不收）
_PCT_RE = re.compile(r'\d{1,3}(?:\.\d+)?%')
# 时刻：2:30 / 2:30 PM / 14:35（归一保留时分，AM/PM 小写粘连）
_CLOCK_RE = re.compile(r'\b\d{1,2}[:：]\d{2}\s*(?:[APap]\.?[Mm]\.?)?\b')
# 日期统一归一为「月-日」（忽略年份：少剪优于错剪）：
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
