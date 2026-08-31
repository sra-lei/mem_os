import uuid

from sqlmodel import Field, SQLModel
from datetime import datetime

class ConversationMemory(SQLModel, table=True):
    __tablename__:str = "conv_memories"
    
    id: str = Field(default_factory=lambda: uuid.uuid4().hex, primary_key=True)
    user_id: str = Field(index=True, nullable=False)
    # 存储对话消息摘要信息
    summary: str = Field(default="")       # 可选：简要摘要（留空即可）
    # 元数据
    source_session_id: str = Field(index=True, nullable=False)
    started_at: datetime = Field(default_factory=datetime.utcnow)
    ended_at: datetime = Field(default_factory=datetime.utcnow)
    message_count: int = Field(default=0)


class Message(SQLModel, table=True):
    __tablename__:str = "conv_messages"
    id: str = Field(default_factory=lambda: uuid.uuid4().hex, primary_key=True)
    user_id: str = Field(index=True, nullable=False)
    source_session_id: str = Field(index=True, nullable=False)
    content: str
    # 新增：PII 标记
    contains_pii: bool = Field(default=False)  # 是否包含 PII
    masked_text: str = Field(default="")       # 脱敏后的文本（调试用）
    
    create_at: datetime = Field(default_factory=datetime.utcnow)