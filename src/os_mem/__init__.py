"""os_mem — the MemOS memory system core (独立于评测框架).

This package implements the memory system (需求文档 v0.1 modules 1.1~1.5):

    1.1  sanitizer    PII-redacted logging
    1.2  memory       Memory data model
    1.3  generator    fact extraction from conversations (YOUR implementation)
    1.4  retriever    BM25 retrieval (YOUR implementation)
    1.5  prompt       injection format into the agent's context
    storage           memories live in their own database (os_mem.db),
                      deliberately separated from memos.db (evaluation data)

Architecture rule: os_mem must not import testing (testing.db / testing.api)
or anything else outside itself. The evaluation framework consumes os_mem
through the public exports below; os_mem never depends on the evaluation side.
"""
from .memory import Memory
from .prompt import format_injection
from .provider import MemoryProvider, build_memory_provider, register_provider
from .storage import default_db_path, temp_db_path
from .stub import StubMemoryProvider

__all__ = [
    "Memory",
    "MemoryProvider",
    "StubMemoryProvider",
    "build_memory_provider",
    "register_provider",
    "default_db_path",
    "temp_db_path",
    "format_injection",
]
