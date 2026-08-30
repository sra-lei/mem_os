"""Memory data model (需求文档 v0.1 module 1.2).

Memory is the SQLModel table backing the memories storage. v0.1 keeps
memories unstructured (plain text only); structured fields (category/key/
value/...) arrive in v0.2 — do not add them yet.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlmodel import Field, SQLModel


class Memory(SQLModel, table=True):
    """One extracted memory."""

    __tablename__ = "memories"

    id: str = Field(default_factory=lambda: uuid.uuid4().hex, primary_key=True)
    user_id: str = Field(index=True, max_length=100)            # namespace; evaluation uses case_id so cases don't leak
    fact: str = Field(max_length=1000)                          # plain-text fact, e.g. "用户邮箱是 john@example.com"
    source_session_id: str = Field(default="", index=True, max_length=100)
    created_at: datetime = Field(default_factory=datetime.utcnow)
