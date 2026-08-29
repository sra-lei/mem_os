"""Injection format (需求文档 v0.1 module 1.5).

Retrieved memories are injected into the agent's system prompt before each
agent call, so the agent answers grounded in what it remembers about the user.
"""
from __future__ import annotations

from typing import List

from .memory import Memory

_SECTION_HEADER = "## 关于用户的长久记忆"


def format_injection(memories: List[Memory]) -> str:
    """Render the memory-injection block appended to the agent's system prompt.

    Pure function — reusable by the evaluation framework and your own agent.
    """
    lines = [
        "你是一个具备记忆能力的客服助手。请基于下面的用户长久记忆回答用户当前的问题，",
        "只使用记忆中出现过的信息，不要编造。",
        "",
        _SECTION_HEADER,
    ]
    if not memories:
        lines.append("（当前没有可用记忆）")
    for m in memories:
        lines.append(f"- {m.fact}")
    return "\n".join(lines)
