import json
import time

from os_mem import get_logger
from os_mem.infra.p2check import mask_pii
from os_mem.models.mem_models import Conversation

_logger = get_logger("os_mem.provider.full")


class FullTextProvider():
    """Full-text memory provider：近期会话常驻内存，快速响应调用端（不落库、不调 LLM）。"""

    def __init__(self, user_id: str):
        self.user_id = user_id
        self.messages: list[str] = []

    def ingest(self, conversation: Conversation) -> None:
        t0 = time.perf_counter()
        session_id = conversation.source_session_id or conversation.id or ""
        _logger.info(
            f"ingest 开始 user={self.user_id} session={session_id} "
            f"messages={len(conversation.messages)}"
        )
        # v1: 暴力保留所有对话消息（内存），供近期会话即时回答
        self.messages = conversation.messages
        _logger.info(
            f"ingest 完成 user={self.user_id} session={session_id} "
            f"耗时={(time.perf_counter() - t0) * 1000:.0f}ms"
        )

    def retrieve(self, query: str, top_k: int = 3) -> str:
        t0 = time.perf_counter()
        _logger.info(
            f"retrieve 开始 user={self.user_id} query={mask_pii(query)} top_k={top_k}"
        )
        memories = self.messages
        _SECTION_HEADER = "## 关于用户的长久记忆"
        memory_lines = [_SECTION_HEADER]
        if not memories:
            memory_lines.append("（当前没有可用记忆）")
        for m in memories:
            memory = json.loads(m) if isinstance(m, str) else m
            memory_lines.append(f"- {memory.get('content', '')}")
        _logger.info(
            f"retrieve 完成 user={self.user_id} 命中={len(memories)} "
            f"耗时={(time.perf_counter() - t0) * 1000:.0f}ms"
        )
        return "\n".join(memory_lines)
