"""Pluggable LLM client for the evaluation pipeline.

Two LLM roles exist in the pipeline:
  1. AnswerGenerator — the customer-service agent that answers user_question
     with retrieved memories injected into context (injection format comes
     from os_mem.prompt — 需求文档 v0.1 module 1.5).
  2. Judge          — independent LLM that grades the answer (see judge.py).

Both go through this LLMClient protocol so a real provider (local Ollama /
OpenAI-compatible API) can be dropped in later without changing the runner.
The default MockLLM keeps the framework runnable with zero external services.
"""
from __future__ import annotations

from typing import List, Protocol

from os_mem import Memory, format_injection


class LLMClient(Protocol):
    """Minimal chat interface. Implementations must be pure (no hidden state)."""

    def complete(self, system: str, user: str) -> str:
        """Return the assistant reply for one turn."""
        ...


class MockLLM:
    """Placeholder LLM: returns a fixed, clearly-marked string.

    Replace with a real client (Ollama/OpenAI-compatible) by implementing
    LLMClient and registering it in build_llm_client().
    """

    name = "mock"

    def complete(self, system: str, user: str) -> str:
        return f"[mock-answer] 未接入真实LLM。注入记忆 {len(system)} 字符 / 问题: {user[:60]}"


class AnswerGenerator:
    """The evaluated agent: answers the query with retrieved memories injected.

    Injection is delegated to os_mem.prompt.format_injection (module 1.5).
    """

    def __init__(self, llm: LLMClient):
        self._llm = llm

    def answer(self, query: str, memories: List[Memory]) -> str:
        system = format_injection(memories)
        return self._llm.complete(system=system, user=query)


def build_llm_client(name: str) -> LLMClient:
    """Factory used by the runner/CLI. Register your real provider here."""
    if name == "mock":
        return MockLLM()
    raise ValueError(f"unknown llm client: {name!r} (available: mock)")
