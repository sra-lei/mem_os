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

from openai.types.chat.chat_completion_chunk import ModerationInputModerationResultsResult

from os_mem import Memory
from openai import OpenAI
from testing.config import settings


class LLMClient(Protocol):
    """Minimal chat interface. Implementations must be pure (no hidden state)."""
    
    def complete(self, memories: str, user: str) -> str:
        """Return the assistant reply for one turn."""
        ...


class MockLLM:
    """Placeholder LLM: returns a fixed, clearly-marked string.

    Replace with a real client (Ollama/OpenAI-compatible) by implementing
    LLMClient and registering it in build_llm_client().
    """

    name = "mock"

    def complete(self, memories: str, user: str) -> str:
        return f"[mock-answer] 未接入真实LLM。注入记忆 {len(system)} 字符 / 问题: {user[:60]}"

SYSTEM_PROMPT = '''
# 角色
你是一个严肃认真的中文助手，请务必保持礼貌和专业，用中文回答客户的问题。
## 任务
给定用户记忆信息和用户问题，生成一个答案。
## 规则
1. 答案必须基于用户记忆信息和用户问题。
2. 答案必须用中文。
3. 答案必须详细且准确。
4. 答案必须避免使用敏感词和不当语言。
5. 答案必须尊重用户隐私。
6. 如果根据用户记忆信息无法生成答案，请返回"对不起，我无法回答这个问题。"
'''

class DeepSeekLLM:
    name = "deepseek"

    def __init__(self) -> None:
        self.client = OpenAI(
            api_key=settings.DEEPSEEK_API_KEY,
            base_url=settings.DEEPSEEK_BASE_URL,
        )

    def complete(self, memories: str, user: str) -> str:
        return self.client.chat.completions.create(
            model=settings.DEEPSEEK_MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "assistant", "content": memories},
                {"role": "user", "content": user},
            ],
        ).choices[0].message.content

class AnswerGenerator:
    """The evaluated agent: answers the query with retrieved memories injected.

    Injection is delegated to os_mem.prompt.format_injection (module 1.5).
    """

    def __init__(self, llm: LLMClient):
        self._llm = llm

    def answer(self, query: str, memories: str) -> str:
        return self._llm.complete(memories, user=query)


def build_llm_client(name: str) -> LLMClient:
    """Factory used by the runner/CLI. Register your real provider here."""
    if name == "mock":
        return MockLLM()
    elif name == "deepseek":
        return DeepSeekLLM()
    raise ValueError(f"unknown llm client: {name!r} (available: mock)")
