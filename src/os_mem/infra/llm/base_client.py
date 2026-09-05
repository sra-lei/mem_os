"""LLM 抽象契约 —— 与具体实现 / 网关解耦的基础类型。

``ChatClient`` 是 os_mem 内所有「调用 LLM chat 能力」处的统一契约：
- ``os_mem.infra.llm.deepseek_client.DeepSeekClient`` 是当前
  DeepSeek（OpenAI 兼容）实现；
- 未来接入 LLM 网关时提供另一个满足该契约的实现即可，
  业务侧（``utils/extract_prompt``、``core/services/struc_mem_service``）无需改动。

契约层刻意不 import 任何具体 client / 网关 SDK，仅依赖标准库类型。
"""

from __future__ import annotations

from typing import Any, Protocol

# OpenAI 兼容 messages（role/content 条目）
type Message = dict[str, str]


class ChatClient(Protocol):
    """通用 LLM chat 能力契约 —— 网关可替换实现只需满足该接口。"""

    def chat(
        self,
        messages: list[Message],
        *,
        response_format: dict[str, Any] | None = None,
        retries: int = 3,
    ) -> str:
        """把完整 ``messages`` 交给 LLM，返回首个非空 content（重试后仍空返回 ""）。"""
        ...
