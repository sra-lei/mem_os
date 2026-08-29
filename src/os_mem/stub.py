"""Stub memory provider: lets the evaluation pipeline run end-to-end before the
real memory system exists.

It deliberately does NOT implement extraction or retrieval — replace it with
your own MemoryProvider implementation. With this stub active, retrieval
returns nothing, so answers cannot be grounded in memory and evaluation results
will be meaningless — that is expected.
"""
from __future__ import annotations

from typing import List

from .memory import Memory


class StubMemoryProvider:
    def __init__(self, user_id: str = "stub"):
        self._user_id = user_id

    def ingest(self, conversation: dict) -> List[Memory]:
        # TODO(user): implement real extraction per 需求文档 v0.1 module 1.3
        return []

    def retrieve(self, query: str, top_k: int = 3) -> List[Memory]:
        # TODO(user): implement real BM25 retrieval per 需求文档 v0.1 module 1.4
        return []
