"""MemoryProvider contract: the interface between os_mem and its consumers
(the evaluation framework, your own agent, etc.).

The evaluation framework (src/testing) depends ONLY on this contract.
Implementations live in os_mem (generator.py + retriever.py + storage.py are
YOUR work — reference 需求文档 v0.1 modules 1.2~1.4).
"""
from __future__ import annotations

from typing import Protocol

from os_mem.models.mem_models import Conversation

from .infra.logger.logger import get_logger

_logger = get_logger("os_mem.memory_provider")

class MemoryProvider(Protocol):
    """Contract the evaluation framework requires from the memory system.

    Reference implementation points (YOUR work, per 需求文档 v0.1):
      - ingest:   module 1.3 — after each conversation, extract durable facts
                  (LLM extraction, ≤3 facts per session, PII-redacted logging)
      - retrieve: module 1.4 — BM25 over the user's memories, top-k, k=3 default
    """
    def __init__(self, user_id: str):
        """Initialize the memory provider with the given user ID."""
        ...

    def ingest(self, conversation: Conversation) -> None:
        """Extract and store memories from one conversation (a single item of
        test_case `conversation_histories`).

        Args:
            conversation: dict with keys
                conversation_id, timestamp, metadata, messages[{role, content}]
        Returns:
            list of stored memories (used for logging / future audit).
        """
        ...

    def retrieve(self, query: str, top_k: int = 3) -> str:
        """Return the top-k most relevant memories for the user's query."""
        ...

from os_mem.core.mem_provider import BaseProvider,FullTextProvider, StructProvider  # noqa: E402

# Registered provider names -> factory.
_PROVIDER_REGISTRY: dict[str, type] = {
    "full": FullTextProvider,
    "base": BaseProvider,
    "struct": StructProvider,
}

def build_memory_provider(name: str, user_id: str, **kwargs) -> MemoryProvider:
    _logger.info(f"构建 memory provider: name={name} user_id={user_id}")
    if name in _PROVIDER_REGISTRY:
        return _PROVIDER_REGISTRY[name](user_id=user_id, **kwargs)
    raise ValueError(
        f"unknown memory provider: {name!r} (available: base)"
    )
