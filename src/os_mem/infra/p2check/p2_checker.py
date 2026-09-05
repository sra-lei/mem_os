# scripts/pii_detector.py
"""v0.1: 基于正则表达式的 PII 检测"""

import re
from typing import Any

# PII 规则：类型 + 正则 + 是否脱敏
PII_RULES = [
    {'type': 'ssn', 'pattern': r'\b\d{3}-\d{2}-\d{4}\b', 'replacement': '[SSN]'},
    {'type': 'phone', 'pattern': r'\b\d{3}-\d{3}-\d{4}\b', 'replacement': '[PHONE]'},
    {
        'type': 'email',
        'pattern': r'\b[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}\b',
        'replacement': '[EMAIL]',
    },
    {'type': 'account', 'pattern': r'\b\d{10}\b', 'replacement': '[ACCOUNT]'},
    {'type': 'routing', 'pattern': r'\b\d{9}\b', 'replacement': '[ROUTING]'},
    {'type': 'pin', 'pattern': r'\b\d{4}\b', 'replacement': '[PIN]'},
    {
        'type': 'address',
        'pattern': (
            r'\b\d{1,5}\s+[A-Za-z]+\s+(?:Street|St|Avenue|Ave|Road|Rd|'
            r'Boulevard|Blvd|Lane|Ln|Drive|Dr|Way|Court|Ct|Parkway|Pkwy)\b'
        ),
        'replacement': '[ADDRESS]',
    },
]


def detect_pii(text: str) -> list[dict[str, Any]]:
    """检测文本中的 PII，返回匹配列表"""
    matches = []
    for rule in PII_RULES:
        for match in re.finditer(rule['pattern'], text, re.IGNORECASE):
            matches.append(
                {
                    'type': rule['type'],
                    'matched': match.group(0),
                    'start': match.start(),
                    'end': match.end(),
                }
            )
    return matches


def has_pii(text: str) -> bool:
    """快速判断是否包含 PII"""
    return len(detect_pii(text)) > 0


def mask_pii(text: str) -> str:
    """将文本中的 PII 替换为占位符（用于日志）"""
    masked = text
    for rule in PII_RULES:
        masked = re.sub(
            rule['pattern'], rule['replacement'], masked, flags=re.IGNORECASE
        )
    return masked
