import json
from typing import List

from os_mem.models.mem_models import Conversation
from os_mem.core.services.note_mem_service import get_note_mem_service
from os_mem.entries.mem_models import ConversationMemory

from os_mem.infra.logger.logger import get_logger

# TODO(user): 剩余实现问题（运行时才暴露，属于记忆系统实现部分）：
#   1. _logger(f"...") 是函数调用写法，应为 _logger.info(f"...")
#   2. json.loads(conversation) —— conversation 是 dict 不是 JSON 字符串
#   3. retrieve 里 len(retrieved) 应为 len(memories)
#   4. __init__ 缺 user_id 参数（build_memory_provider 会传 user_id=...）
#   5. ingest/retrieve 需通过 sanitizer.sanitize_log 脱敏后再记日志（需求文档 1.1）

_logger = get_logger("base_provider")

class BaseProvider():
    messages: list[str]
    
    def __init__(self, user_id: str):
        self.user_id = user_id
        self.store = get_note_mem_service()
   
    def ingest(self, conversation: Conversation) -> None:
        _logger.info(f"  存储记忆: start")
        # v1: 暴力存储所有对话消息
        # self.messages = json.dumps(conversation["messages"], ensure_ascii=False)
        
        # v2: 存储对话内容到sqlite
        messages = conversation.messages
        if not messages:
            raise ValueError("没有消息内容")
        
        # 构造记录
        memory = ConversationMemory(
            user_id=self.user_id,
            source_session_id=conversation.id,
            started_at=conversation.started_at,
            ended_at=conversation.ended_at,
            message_count=len(messages),
        )
        self.store.save_user_memories(user_id=self.user_id, conversation=memory, messages=messages)
        _logger.info(f"  存储记忆: 完成")

    def retrieve(self, query: str, top_k: int = 3) -> str:
        memories = self.store.search_user_memories(user_id=self.user_id, query=query, top_k=top_k)
        _logger.info(f"  检索到 {len(memories)} 条记忆")
        _SECTION_HEADER = "## 关于用户的长久记忆"
        memory_lines = [_SECTION_HEADER]
        if not memories:
            memory_lines.append("（当前没有可用记忆）")
        for m in memories:
            memory_lines.append(f"- {m.fact}")
        return "\n".join(memory_lines)

   