"""Log sanitizer (需求文档 v0.1 module 1.1) — YOUR implementation.

Responsibility: every write/retrieval log must be PII-redacted before it hits
disk. v0.1 plan: regex-based fast detection (ID card / phone / email / bank
card), sensitive values replaced with [REDACTED], keep structured info like
"提取到 1 条事实".
"""
from __future__ import annotations

from ..infra.logger.logger import get_logger

_logger = get_logger("os_mem.sanitizer")


def sanitize_log(message: str) -> str:
    """Return a PII-redacted version of the log message.

    TODO(user): implement regex rules per 需求文档 v0.1 module 1.1.
    """
    _logger.debug(f"sanitize_log 未实现（占位）：原样返回 {len(message)} 字符")
    return message
