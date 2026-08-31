"""Log sanitizer (需求文档 v0.1 module 1.1) — YOUR implementation.

Responsibility: every write/retrieval log must be PII-redacted before it hits
disk. v0.1 plan: regex-based fast detection (ID card / phone / email / bank
card), sensitive values replaced with [REDACTED], keep structured info like
"提取到 1 条事实".
"""
from __future__ import annotations

from os_mem.infra.logger import get_logger
from os_mem.infra.p2check import has_pii, mask_pii

_logger = get_logger("os_mem.sanitizer")


def sanitize_log(message: str) -> str:
    """Return a PII-redacted version of the log message.
    implement regex rules per 需求文档 v0.1 module 1.1.
    """
    if has_pii(message):
        message = mask_pii(message)
    return message
