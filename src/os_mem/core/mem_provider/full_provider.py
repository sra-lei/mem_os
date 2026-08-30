import json

from os_mem import get_logger
from os_mem.models.mem_models import Conversation

_logger = get_logger("full_provider")

class FullTextProvider():
    """
    Full-text memory provider that uses long context llm.
    """
    messages: list[str]
         
    def __init__(self, user_id: str):
        self.user_id = user_id

    def ingest(self, conversation: Conversation) -> None:
        _logger.info(f"  存储记忆: start")
        # v1: 暴力存储所有对话消息
        self.messages = conversation.messages
        _logger.info(f"  存储记忆: 完成")

    def retrieve(self, query: str, top_k: int = 3) -> str:
        memories = self.messages
        _logger.info(f"  检索到 {len(memories)} 条记忆")
        _SECTION_HEADER = "## 关于用户的长久记忆"
        memory_lines = [_SECTION_HEADER]
        if not memories:
            memory_lines.append("（当前没有可用记忆）")
        for m in memories:
            memory = json.loads(m) if isinstance(m, str) else m
            memory_lines.append(f"- {memory.get('content', '')}")
        return "\n".join(memory_lines)