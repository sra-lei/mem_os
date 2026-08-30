"""Retriever (需求文档 v0.1 module 1.4) — YOUR implementation.

Responsibility: given the user's current query, return the top-k most relevant
memories. v0.1 plan: tokenize query (split on spaces/punctuation), BM25 over
the user's memories (k1=1.5, b=0.75), top-k with k=3 default; full scan is fine
while a user has <100 memories.
"""
from __future__ import annotations

from typing import List

from ..logger import get_logger
from ..memory import Memory

_logger = get_logger("os_mem.retriever")

class Retriever:
    def retrieve(query: str, memories: List[Memory], top_k: int = 3) -> List[Memory]:
        """Rank `memories` by BM25 relevance to `query`, return top-k.

        TODO(user): implement BM25 scoring per 需求文档 v0.1 module 1.4.
        """
        _logger.warning(f"retrieve 未实现 BM25（占位）：按原序返回前 {top_k} 条")
        return memories[:top_k]
