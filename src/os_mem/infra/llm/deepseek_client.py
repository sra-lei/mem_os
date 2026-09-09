"""OpenAI 兼容 LLM 客户端 —— 只负责 client 创建与通用 chat 调用。

职责划分（2026-09 重构，为接入 LLM 网关铺路）：
- 契约（``Message``/``ChatClient``/``ChatOutcome``）定义在
  ``os_mem.infra.llm.base_client``，本模块是它的 DeepSeek（OpenAI 兼容）
  实现：client 创建 + 通用 ``chat`` / ``chat_outcome``。
- 任务侧内容（如事实提取的 ``SYSTEM_PROMPT``、消息拼装、业务级重试/解析）
  一律不在本模块内 —— 事实提取的 prompt 与适配见
  ``os_mem.extractor.prompt``（把任意满足 ``ChatClient`` 的实例适配成
  提取链路需要的 ``complete(dialog_text)`` 回调）。

截断语义（2026-09-09 方案：事实提取鲁棒性与成本优化）：
- ``chat_outcome`` 区分两种空返回：
  * ``finish_reason=length`` —— 输出预算被烧满截断且 content 为空。
    **确定性失败**（同输入必复现），重试无信息增益 → 不指数重试，
    立即把 outcome 上抛，由上层（切段/repair）决定下一步；
  * 其他 finish_reason（stop/None 等）—— 真偶发空返回，
    保留指数退避重试（3s/6s/…）。

实例化入口在 ``os_mem.infra.llm.factory``：本实现注册为 ``deepseek``，
由 ``LLM_PROVIDERS`` 配置选用；接入其他 provider 时按工厂注册即可，
业务侧（提取 prompt、service）无需改动。
"""

from __future__ import annotations

import time
from typing import Any

from openai import OpenAI

from os_mem.configs.mem_settings import memory_settings
from os_mem.infra.llm.base_client import ChatOutcome, Message
from os_mem.infra.logger import get_logger

_logger = get_logger('os_mem.infra.llm.deepseek')


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
        """返回首个非空 content（重试后仍空返回 ""）。兼容旧契约。"""
        return self.chat_outcome(
            messages,
            temperature=temperature,
            max_tokens=max_tokens,
            response_format=response_format,
            timeout=timeout,
            retries=retries,
        ).content

    def chat_outcome(
        self,
        messages: list[Message],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        response_format: dict[str, Any] | None = None,
        timeout: int | None = None,
        retries: int = 3,
    ) -> ChatOutcome:
        """调用底层模型，返回 ``ChatOutcome``（含 finish_reason，供上层路由）。

        空返回按 finish_reason 分流：
        - ``length``（截断且 content 空）→ 确定性失败，**不重试**，立即上抛；
        - 其他（stop/None）→ 指数等待重试（3s/6s/9s…），仍空返回
          ``ChatOutcome('', None, usage)`` 交给上层。
        未显式指定的超参取 ``memory_settings`` 默认值。
        """
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
        t0 = time.monotonic()
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
            # 简单任务默认关思考（见 memory_settings.DEEPSEEK_THINKING 注释；
            # 探测实证：思考吃掉输出预算 → content 空截断 + 延迟数倍）
            if not memory_settings.DEEPSEEK_THINKING:
                kwargs['extra_body'] = {'thinking': {'type': 'disabled'}}
            try:
                resp = self.client.chat.completions.create(**kwargs)
            except Exception as exc:  # noqa: BLE001 —— 观测耗时后原样上抛
                _logger.error(
                    f'[llm] chat failed provider=deepseek model={model} '
                    f'attempt={attempt + 1}/{retries} '
                    f'ms={int((time.monotonic() - t0) * 1000)}: '
                    f'{type(exc).__name__}'
                )
                raise
            content = resp.choices[0].message.content
            # finish_reason/usage 提前读取：成功与空返回两条观测日志共用
            finish_reason = getattr(resp.choices[0], 'finish_reason', None)
            usage = getattr(resp, 'usage', None)
            if content:
                _logger.info(
                    f'[llm] chat ok provider=deepseek model={model} '
                    f'attempt={attempt + 1}/{retries} '
                    f'ms={int((time.monotonic() - t0) * 1000)} '
                    f'in_tok={getattr(usage, "prompt_tokens", None)} '
                    f'out_tok={getattr(usage, "completion_tokens", None)} '
                    f'finish={finish_reason}'
                )
                return ChatOutcome(content, finish_reason, usage)
            prompt_tokens = getattr(usage, 'prompt_tokens', None)
            completion_tokens = getattr(usage, 'completion_tokens', None)
            if finish_reason == 'length':
                # 输出预算截断且 content 空：确定性失败，重试必复现。
                # 立即上抛 outcome，由上层走「更小的段」而非「同样的段再试」。
                _logger.warning(
                    f'LLM 截断空返回（不重试）finish_reason=length '
                    f'prompt_tokens={prompt_tokens} '
                    f'completion_tokens={completion_tokens} max_tokens={max_tokens}'
                )
                return ChatOutcome('', finish_reason, usage)
            # 真偶发空返回（限流/模型不稳定，常见于长文本 + json 输出）
            _logger.warning(
                f'LLM 返回空 content（attempt={attempt + 1}/{retries} '
                f'finish_reason={finish_reason} prompt_tokens={prompt_tokens} '
                f'completion_tokens={completion_tokens} max_tokens={max_tokens}）'
            )
            if attempt < retries - 1:
                wait = 3.0 * (attempt + 1)
                time.sleep(wait)
        return ChatOutcome('', None, usage)
