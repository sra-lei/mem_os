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

from dataclasses import dataclass
from typing import Protocol

from openai import OpenAI
from testing.config import settings


@dataclass
class Completion:
    """一次回答的文本 + token 消耗（只统计回答 LLM，不含 judge 消耗）。"""

    text: str
    prompt_tokens: int = 0
    completion_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


class LLMClient(Protocol):
    """Minimal chat interface. Implementations must be pure (no hidden state)."""

    def complete(self, memories: str, user: str) -> Completion:
        """Return the assistant reply (with token usage) for one turn."""
        ...


class MockLLM:
    """Placeholder LLM: returns a fixed, clearly-marked string.

    Replace with a real client (Ollama/OpenAI-compatible) by implementing
    LLMClient and registering it in build_llm_client().
    """

    name = "mock"

    def complete(self, memories: str, user: str) -> Completion:
        return Completion(
            text=f"[mock-answer] 未接入真实LLM。注入记忆 {len(memories)} 字符 / 问题: {user[:60]}",
        )

SYSTEM_PROMPT = '''
# 角色
你是一个具备用户长期记忆的客服助手，负责用用户的记忆档案回答当前问题。

## 回答依据
- 记忆档案（system 中随对话给出）是用户历史提供过的真实信息，是权威答案来源。
- 用户问题：用户当前询问的内容。

## 规则
1. 记忆档案中明确出现的信息（账号/卡号/路由号/日期/金额/号码/姓名/偏好等）必须直接采用；
   档案里有的信息，严禁回答"没有记录 / 无法提供 / 信息缺失"。
2. 回答要完整覆盖问题涉及的所有要点；若问题隐含多项信息（如"账户和路由号"），尽量全部给出。
3. 仅当记忆档案完全不包含相关信息时，才如实说明"记忆中没有该信息"，并可引用档案中最相关的部分协助用户。
4. 严禁编造档案中不存在的信息；数值/号码不确定时不要虚构。
5. 用中文回答，礼貌、专业、简洁直接。
'''

class DeepSeekLLM:
    name = "deepseek"

    def __init__(self) -> None:
        self.client = OpenAI(
            api_key=settings.DEEPSEEK_API_KEY,
            base_url=settings.DEEPSEEK_BASE_URL,
        )

    def complete(self, memories: str, user: str) -> Completion:
        # 记忆档案放进 system（作为权威背景资料），不要放 assistant 消息——
        # 否则模型会当成"自己说过的话"而非"用户的记忆"，容易忽视/否认
        system = SYSTEM_PROMPT + f"\n\n{memories}"
        resp = self.client.chat.completions.create(
            model=settings.DEEPSEEK_MODEL,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        usage = resp.usage
        return Completion(
            text=resp.choices[0].message.content or "",
            prompt_tokens=getattr(usage, "prompt_tokens", 0) or 0,
            completion_tokens=getattr(usage, "completion_tokens", 0) or 0,
        )

class AnswerGenerator:
    """The evaluated agent: answers the query with retrieved memories injected.

    Injection is delegated to os_mem.prompt.format_injection (module 1.5).
    """

    def __init__(self, llm: LLMClient):
        self._llm = llm

    def answer(self, query: str, memories: str) -> Completion:
        return self._llm.complete(memories, user=query)


def build_llm_client(name: str) -> LLMClient:
    """Factory used by the runner/CLI. Register your real provider here."""
    if name == "mock":
        return MockLLM()
    elif name == "deepseek":
        return DeepSeekLLM()
    raise ValueError(f"unknown llm client: {name!r} (available: mock)")
