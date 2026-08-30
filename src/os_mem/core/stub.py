"""Stub memory provider: lets the evaluation pipeline run end-to-end before the
real memory system exists.

It deliberately does NOT implement extraction or retrieval — replace it with
your own MemoryProvider implementation. With this stub active, retrieval
returns nothing, so answers cannot be grounded in memory and evaluation results
will be meaningless — that is expected.
"""
from __future__ import annotations

from typing import List

from ..logger import get_logger
from ..memory import Memory

_logger = get_logger("os_mem.stub")


class StubMemoryProvider:
    def __init__(self, user_id: str = "stub"):
        self._user_id = user_id
        _logger.debug(f"StubMemoryProvider(user_id={user_id})")

    def ingest(self, conversation: dict) -> List[Memory]:
        # TODO(user): implement real extraction per 需求文档 v0.1 module 1.3
        _logger.info(
            f"[stub] ingest 会话 {conversation.get('conversation_id')} "
            f"(user={self._user_id}) → 0 条（占位，未实现提取）"
        )
        return []

    def retrieve(self, query: str, top_k: int = 3) -> List[Memory]:
        # TODO(user): implement real BM25 retrieval per 需求文档 v0.1 module 1.4
        _logger.info(f"[stub] retrieve '{query[:60]}' → 0 条（占位，未实现检索）")
        return []
