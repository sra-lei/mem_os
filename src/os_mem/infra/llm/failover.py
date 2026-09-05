"""多 LLM client 降级保底（failover）—— 主路故障自动切换备胎。

包装一组有序的 ``ChatClient``：第一个为主路，其余为降级备胎。语义：
- 每个请求从「当前游标」指向的 client 开始。某 client 调用失败（抛异常）
  或重试后仍空返回，则视为该 client 当前不可用，按序尝试下一个；
- 某 client 成功产出内容 -> 游标停在它。这样主路故障期间不会每次请求都
  先承担一次主路失败的开销（网络超时 / 空返回退避可能拖很久）；
- 主路恢复探测：游标在备胎上时，距上次主路失败超过 ``probe_interval`` 的
  下一次请求会先试一次主路——成功即回主路，失败则刷新失败时间继续走备胎；
- 全部 provider 均失败：存在异常则抛最后一次异常（由上层按调用方契约处理），
  全部为空返回则返回 ``""``（与单个 client 的空返回语义一致）。

只依赖 ``ChatClient`` 契约与日志，不感知任何业务 prompt / 模型差异。
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from os_mem.infra.llm.base_client import ChatClient, Message
from os_mem.infra.logger import get_logger

_logger = get_logger('os_mem.llm.failover')


class FailoverClient:
    """多 provider 降级保底：按序尝试，失败切换，冷却后探测主路恢复。"""

    def __init__(
        self,
        clients: list[ChatClient],
        *,
        probe_interval: float = 300.0,
        now_fn: Callable[[], float] | None = None,
    ) -> None:
        if not clients:
            raise ValueError('FailoverClient 需要至少一个 client')
        self._clients = list(clients)
        self._probe_interval = probe_interval
        self._now = now_fn or time.monotonic
        self._cursor = 0
        self._last_primary_fail: float | None = None

    @property
    def clients(self) -> list[ChatClient]:
        """当前参与降级链的底层 clients（第一个为主路），只读语义。"""
        return list(self._clients)

    @property
    def active_index(self) -> int:
        """当前游标（上次成功的 provider 下标；0 为主路）。"""
        return self._cursor

    def _start_index(self) -> int:
        """决定本次请求从哪个 client 开始尝试。"""
        if self._cursor == 0:
            return 0
        if self._last_primary_fail is None or (
            self._now() - self._last_primary_fail >= self._probe_interval
        ):
            # 已过冷却期：先探测一次主路是否恢复
            return 0
        return self._cursor

    def chat(
        self,
        messages: list[Message],
        *,
        response_format: dict[str, Any] | None = None,
        retries: int = 3,
    ) -> str:
        """按降级链调用，返回首个非空 content（全部失败见类 docstring）。"""
        last_exc: Exception | None = None
        start = self._start_index()
        for idx in range(start, len(self._clients)):
            try:
                content = self._clients[idx].chat(
                    messages, response_format=response_format, retries=retries
                )
            except Exception as e:
                _logger.warning(
                    f'LLM provider #{idx} chat 失败（降级尝试 #{idx - start}）: {e}'
                )
                if idx == 0:
                    self._last_primary_fail = self._now()
                last_exc = e
                continue
            if content:
                self._cursor = idx
                if idx == 0:
                    # 主路恢复成功（含冷却后探测命中），清除失败标记
                    self._last_primary_fail = None
                return content
            # 重试后仍空返回：本 provider 当前不可用，尝试下一个
            _logger.warning(
                f'LLM provider #{idx} 空返回（降级尝试 #{idx - start}），切换到下一个'
            )
            if idx == 0:
                self._last_primary_fail = self._now()
        if last_exc is not None:
            raise last_exc
        return ""
