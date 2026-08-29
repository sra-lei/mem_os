"""Memory data model (需求文档 v0.1 module 1.2)."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Memory:
    """One extracted memory.

    v0.1 keeps memories unstructured (plain text only). Structured fields
    (category/key/value/...) arrive in v0.2 — do not add them yet.
    """

    id: str
    user_id: str            # namespace; evaluation uses case_id so cases don't leak into each other
    fact: str               # plain-text fact, e.g. "用户邮箱是 john@example.com"
    source_session_id: str  # conversation_id that produced this memory
    created_at: datetime = field(default_factory=datetime.utcnow)
