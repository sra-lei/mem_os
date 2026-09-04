import uuid

from sqlmodel import Field, SQLModel
from datetime import datetime
from sqlalchemy import UniqueConstraint

# v1: conversation memory
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

# v1: note message
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

## v2 structured memory
class StructuredMemory(SQLModel, table=True):
    __tablename__:str = "struct_memories"
    '''
    -- struct_memories 表（新增结构化表）
    CREATE TABLE struct_memories (
        id TEXT PRIMARY KEY,
        user_id TEXT NOT NULL,
        fact TEXT NOT NULL,              -- "用户支票账户号码是 4429853327"
        category TEXT NOT NULL,          -- "finance" / "contact" / "preference"
        key TEXT NOT NULL,               -- "checking_account_number"
        value TEXT NOT NULL,             -- "4429853327"
        confidence REAL DEFAULT 0.8,
        source_conversation_id TEXT,
        source_chunk_id TEXT,            -- 关联回原始块
        created_at DATETIME
    );

    -- 索引
    CREATE INDEX idx_memories_user_category ON struct_memories(user_id, category);
    CREATE INDEX idx_memories_key ON struct_memories(key);
    '''
     # ========== 主键 ==========
    id: str = Field(default_factory=lambda: uuid.uuid4().hex, primary_key=True)
    # ========== 核心字段 ==========
    user_id: str = Field(index=True, nullable=False)  # ✅ 创建索引 (idx_memories_user_id)
    fact: str = Field(index=True, nullable=False)  # ✅ 创建索引 (idx_memories_fact)
    previous_fact: str = Field(default="")  # 上一个 fact
    category: str = Field(index=True, nullable=False)  # ✅ 创建索引 (idx_memories_category)
    key: str = Field(index=True, nullable=False)  # ✅ 创建索引 (idx_memories_key)
    value: str = Field(nullable=False)
    confidence: float = Field(default=0.8)
    # ========== 关联 ==========
    source_conversation_id: str = Field(index=True, default="")  # ✅ 创建索引 (idx_memories_source_conversation)
    # ========== 冲突检测用 ==========
    source_chunk_id: str = Field(index=True, default="")  # ✅ 创建索引 (idx_memories_source_chunk)
    # ========== 时间戳 ==========
    created_at: datetime = Field(index=True, default_factory=datetime.utcnow) # ✅ 创建索引 (idx_memories_created_at)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


# ========== 会话处理账本（struct 入库原子化/状态机，见 docs/方案-会话处理状态机与原子入库.md） ==========
class ConversationProcess(SQLModel, table=True):
    """一次会话处理的账本行：保存会话原数据 + 不可逆的处理状态。

    - 唯一键 (user_id, source_session_id)：DB 级兜底防重（claim 门禁的原子性双保险）
    - status 只允许沿状态机合法边前进（PENDING → EXTRACTING → SAVING_SQLITE →
      SAVING_VECTOR → COMPLETED；任一活跃态 → FAILED），见 core/state_machine.py
    """
    __tablename__: str = "conv_process"
    __table_args__ = (
        UniqueConstraint("user_id", "source_session_id", name="uq_conv_process_user_session"),
    )

    id: str = Field(default_factory=lambda: uuid.uuid4().hex, primary_key=True)
    user_id: str = Field(index=True, nullable=False)
    source_session_id: str = Field(index=True, nullable=False)
    # 初始状态：已登记（claim 成功）；详见状态机
    status: str = Field(index=True, default="PENDING")
    # 会话原数据：messages 列表的 JSON 原文（审计/回溯用）
    raw_payload: str = Field(default="")
    # FAILED 时的错误摘要
    last_error: str = Field(default="")

    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)