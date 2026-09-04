import uuid

from sqlmodel import Field, SQLModel
from datetime import datetime
from sqlalchemy import UniqueConstraint

# v1: note message
class Message(SQLModel, table=True):
    """会话原文逐条行（唯一权威原文表）。

    冲突键 (user_id, source_session_id, seq)：seq = 消息在会话中的序号（0 起）。
    双 provider（note/base 与 struct/full）都可能写同一会话原文 —— 不做互斥，
    统一走 value 冲突 upsert（见 note_mem_service.upsert_conversation_messages）：
    同键已存在 → 后入覆盖，内容变化时旧值归档 previous_content；一致则跳过；
    同键不存在 → INSERT。数据一致时双写无影响、不产生重复行。
    """
    __tablename__: str = "conv_messages"
    __table_args__ = (
        UniqueConstraint(
            "user_id", "source_session_id", "seq",
            name="uq_conv_messages_user_session_seq",
        ),
    )

    id: str = Field(default_factory=lambda: uuid.uuid4().hex, primary_key=True)
    user_id: str = Field(index=True, nullable=False)
    source_session_id: str = Field(index=True, nullable=False)
    content: str
    # 新增：PII 标记
    contains_pii: bool = Field(default=False)  # 是否包含 PII
    masked_text: str = Field(default="")       # 脱敏后的文本（调试用）
    # 消息冲突键：会话内序号（0 起）；旧库无此列的行由 init_db 迁移按 rowid 序回填
    seq: int = Field(default=0, index=True)
    # 内容被后入更新覆盖时的旧值归档（镜像 struct_memories.previous_fact）
    previous_content: str = Field(default="")

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


# ========== 对话元数据表（conv_meta）：会话元数据 + 处理状态 ==========
# 设计见 docs/方案-会话处理状态机与原子入库.md；命名定位为「对话元数据表」，
# 处理状态是顺带管理字段（非独立处理账本）。会话原文(messages)不落本表，
# 逐条消息存于 conv_messages（超长对话不入元数据行，避免膨胀/读放大风险）。
class ConversationMeta(SQLModel, table=True):
    """一次会话的元数据行，顺带管理处理状态（struct 入库会话级幂等/追踪）。

    - 唯一键 (user_id, source_session_id)：同一会话只允许一行（DB 级防重）
    - status 运行内只允许沿状态机合法边前进（PENDING→EXTRACTING→SAVING_SQLITE→
      SAVING_VECTOR→COMPLETED；任一活跃态→FAILED）
    - 重跑（非完成态 → EXTRACTING，含 FAILED 与崩溃残留的过期会话）只经 claim 的
      单语句 CAS 条件更新，见 core/services/conv_meta_service.py
    """
    __tablename__: str = "conv_meta"
    __table_args__ = (
        UniqueConstraint("user_id", "source_session_id", name="uq_conv_meta_user_session"),
    )

    id: str = Field(default_factory=lambda: uuid.uuid4().hex, primary_key=True)
    user_id: str = Field(index=True, nullable=False)
    source_session_id: str = Field(index=True, nullable=False)

    # ---------- 会话元数据 ----------
    message_count: int = Field(default=0)
    started_at: datetime | None = Field(default=None)
    ended_at: datetime | None = Field(default=None)

    # ---------- 处理状态 ----------
    # 初始：已登记（claim 成功）；详见状态机
    status: str = Field(index=True, default="PENDING")
    # 重启次数（claim take-over 次数；0 = 首次处理）
    attempts: int = Field(default=0)
    # FAILED 时的错误摘要（重启时清空）
    last_error: str = Field(default="")

    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)