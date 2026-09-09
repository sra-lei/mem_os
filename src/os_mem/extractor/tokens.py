"""数值 token 抽取（共享工具）—— 从事实文本抽取"可核验精确信息"token。

归属：``os_mem.extractor`` 记忆提取域（2026-09-08 由 os_mem.utils 迁入
extraction/，2026-09-09 包更名 extraction→extractor）。

口径与判分（tests/eval/judge/impl/assert_judger.py）的期望信息点一致：
金额 / 字母数字编号 / 长数字串 / ≥4 位纯数字，归一化（去 $ 千分位连字符，小写）。

用途：
- 提取侧（os_mem.extractor.fact_extractor）：verbatim 兜底句与 LLM 结构化事实的
  覆盖去重（R1：token 全被结构化覆盖的兜底句不存）；
- 检索侧（os_mem.core.retrieval_strategies）：verbatim 信息唯一性判定/冗余过滤；
- 判分侧与审计工具各保留独立副本（分属 tests / 工具，注明同步义务）。

本模块是 os_mem 内的唯一实现，改动此处即同步两侧语义。
"""

from __future__ import annotations

import re

_AMOUNT_RE = re.compile(r'\$\s?\d{1,3}(?:,\d{3})+(?:\.\d+)?|\$\s?\d+(?:\.\d+)?')
_CODE_RE = re.compile(r'\b[A-Z]{1,8}-?\d{2,}[A-Za-z0-9-]*\b', re.IGNORECASE)
_CARD_RE = re.compile(r'\b\d{4,}(?:[-\s]\d{4,}){1,}\b')
_NUM4_RE = re.compile(r'(?<!\d)\d{4,}(?!\d)')
_TOKEN_NORM = re.compile(r'[\s,$%_\'"-]+')


def norm_token(token: str) -> str:
    """单 token 归一化：去格式噪音（$ 千分位连字符等），小写。"""
    return _TOKEN_NORM.sub('', token).lower()


def fact_tokens(text: str) -> set[str]:
    """从事实文本抽取可核验数值 token（金额/编号/卡片/≥4 位数字），归一化去重。"""
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
