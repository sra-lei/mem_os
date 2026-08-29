"""Memory provider factory for the evaluation framework.

The contract (Memory, MemoryProvider) now lives in the os_mem package — this
module only re-exports the factory so existing imports keep working.
Register your own implementation in os_mem.provider (register_provider).
"""
from os_mem import Memory, MemoryProvider, build_memory_provider  # noqa: F401

__all__ = ["Memory", "MemoryProvider", "build_memory_provider"]
