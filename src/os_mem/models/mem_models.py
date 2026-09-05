from datetime import datetime

from pydantic.dataclasses import dataclass


@dataclass
class Conversation:
    id: str
    user_id: str
    messages: list[str]
    source_session_id: str
    started_at: datetime
    ended_at: datetime


@dataclass
class Memory:
    user_id: str
    fact: str
    contains_pii: bool
    masked_text: str | None
    source_session_id: str
    created_at: datetime


@dataclass
class MemoryFact:
    fact: str
    category: str
    key: str
    value: str
    confidence: float = 0.8


@dataclass
class MemoryFacts:
    facts: list[MemoryFact]
