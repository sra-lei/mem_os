"""Fact generator (需求文档 v0.1 module 1.3) — YOUR implementation.

Responsibility: after each conversation ends, extract durable facts from the
conversation history (user + agent turns). v0.1 plan: sync LLM extraction
(light model, ≤3 facts per session), output JSON array of fact strings; on LLM
failure log the error and skip silently; retry writes 3x. All logs go through
sanitizer.sanitize_log.
"""
from __future__ import annotations

from typing import List

from .memory import Memory


def extract_facts(conversation: dict) -> List[str]:
    """Return the list of durable fact strings extracted from one conversation.

    TODO(user): implement LLM extraction per 需求文档 v0.1 module 1.3.
    Args:
        conversation: dict with conversation_id / timestamp / metadata /
                      messages[{role, content}]
    """
    return []
