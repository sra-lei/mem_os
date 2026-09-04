import time

from os_mem.models.mem_models import Conversation
from os_mem.core.services.note_mem_service import get_note_mem_service
from os_mem.entries.mem_models import ConversationMemory

from os_mem.infra.logger.logger import get_logger
from os_mem.infra.p2check import mask_pii

_logger = get_logger("os_mem.provider.base")


class BaseProvider():
    messages: list[str]

    def __init__(self, user_id: str):
        self.user_id = user_id
        self.store = get_note_mem_service()

    def ingest(self, conversation: Conversation) -> None:
        t0 = time.perf_counter()
        session_id = conversation.source_session_id or conversation.id or ""
        _logger.info(
            f"ingest 开始 user={self.user_id} session={session_id} "
            f"messages={len(conversation.messages)}"
        )
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
        self.store.save_user_memories(conversation=memory, messages=messages)
        _logger.info(
            f"ingest 完成 user={self.user_id} session={session_id} "
            f"耗时={(time.perf_counter() - t0) * 1000:.0f}ms"
        )

    def retrieve(self, query: str, top_k: int = 3) -> str:
        t0 = time.perf_counter()
        _logger.info(
            f"retrieve 开始 user={self.user_id} query={mask_pii(query)} top_k={top_k}"
        )
        memories = self.store.search_user_memories(user_id=self.user_id, query=query, top_k=top_k)
        _SECTION_HEADER = "## 关于用户的长久记忆"
        memory_lines = [_SECTION_HEADER]
        if not memories:
            memory_lines.append("（当前没有可用记忆）")
        for m in memories:
            memory_lines.append(f"- {m.fact}")
        _logger.info(
            f"retrieve 完成 user={self.user_id} 命中={len(memories)} "
            f"耗时={(time.perf_counter() - t0) * 1000:.0f}ms"
        )
        return "\n".join(memory_lines)
