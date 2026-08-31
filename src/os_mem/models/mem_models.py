from datetime import datetime

from pydantic.dataclasses import dataclass

@dataclass
class Conversation():
    id: str
    user_id: str
    summary: str | None
    messages: list[str]
    source_session_id: str
    started_at: datetime
    ended_at: datetime
    message_count: int

@dataclass
class Memory():
    user_id: str
    fact: str
    contains_pii: bool
    masked_text: str | None
    source_session_id: str
    created_at: datetime