"""OpenAI 兼容 LLM 客户端 —— 只负责 client 创建与通用 chat 调用。

职责划分（2026-09 重构，为接入 LLM 网关铺路）：
- 契约（``Message``/``ChatClient``）定义在 ``os_mem.infra.llm.base_client``，
  本模块是它的 DeepSeek（OpenAI 兼容）实现：client 创建 + 通用 ``chat``，
  空 content 时按指数退避重试（与具体任务无关的基础可靠性）。
- 任务侧内容（如事实提取的 ``SYSTEM_PROMPT``、消息拼装、业务级重试/解析）
  一律不在本模块内 —— 事实提取的 prompt 与适配见
  ``os_mem.utils.extract_prompt``（把任意满足 ``ChatClient`` 的实例适配成
  提取链路需要的 ``complete(dialog_text)`` 回调）。

实例化入口在 ``os_mem.infra.llm.factory``：本实现注册为 ``deepseek``，
由 ``LLM_PROVIDERS`` 配置选用；接入其他 provider 时按工厂注册即可，
业务侧（提取 prompt、service）无需改动。
"""

from __future__ import annotations

from typing import Any

from openai import OpenAI

from os_mem.configs.mem_settings import memory_settings
from os_mem.infra.llm.base_client import Message


class DeepSeekClient:
    """DeepSeek（OpenAI 兼容）客户端：``ChatClient`` 契约的实现。

    只负责连接与调用，不感知任何业务 prompt/任务格式。
    """

    def __init__(self) -> None:
        self.client = OpenAI(
            api_key=memory_settings.DEEPSEEK_API_KEY,
            base_url=memory_settings.DEEPSEEK_BASE_URL,
        )

    def chat(
        self,
        messages: list[Message],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        response_format: dict[str, Any] | None = None,
        timeout: int | None = None,
        retries: int = 3,
    ) -> str:
        """调用底层模型，返回首个非空 content。

        模型偶发返回空 content（deepseek-v4-flash 对长文本不稳定），
        空返回时等待后重试（指数等待 3s/6s/9s…），仍空则返回 "" 交给上层。
        未显式指定的超参取 ``memory_settings`` 默认值。
        """
        import time

        model = memory_settings.DEEPSEEK_MODEL
        temperature = (
            temperature
            if temperature is not None
            else memory_settings.DEEPSEEK_TEMPERATURE
        )
        if max_tokens is None:
            max_tokens = memory_settings.DEEPSEEK_MAX_TOKENS
        if timeout is None:
            timeout = memory_settings.DEEPSEEK_TIMEOUT
        for attempt in range(retries):
            kwargs: dict[str, Any] = dict(
                model=model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                timeout=timeout,
            )
            if response_format is not None:
                kwargs['response_format'] = response_format
            resp = self.client.chat.completions.create(**kwargs)
            content = resp.choices[0].message.content
            if content:
                return content
            # 模型/API 偶发返回空 content（限流或模型不稳定），
            # 拉长退避时间等待恢复：3s / 6s / 9s
            if attempt < retries - 1:
                wait = 3.0 * (attempt + 1)
                time.sleep(wait)
        return ""
