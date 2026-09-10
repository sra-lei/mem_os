"""数值 token 口径 —— 提取 R1 剪枝 / 检索冗余过滤共享，改此即同步两侧语义。

与判分（tests/eval/judge/impl/assert_judger.py）期望信息点口径一致：
金额 / 字母数字编号 / 长数字串 / ≥4 位纯数字，归一（去 $ 千分位连字符，小写）。
"""

from __future__ import annotations

import re

# --------------------------------------------------------------------------- #
#  数值 token 口径
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
