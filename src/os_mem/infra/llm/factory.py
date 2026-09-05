"""LLM client 工厂 —— 按配置实例化具体实现，多实现时提供降级保底。

职责划分：
- 契约（``ChatClient``/``Message``）与实现解耦：``deepseek`` 是内置实现；
  接入新的 provider（网关 / 其他模型 / 本地推理等）只需满足 ``ChatClient``
  契约并 ``register_llm_client(name, builder)`` 注册，业务侧无感。
- ``LLMClientFactory.build``：把 ``LLM_PROVIDERS``（逗号分隔的有序 provider
  名，第一个为主路）实例化为 client 链——单个直接返回该 client（与旧的
  单例行为一致），多个包成 ``FailoverClient`` 做降级保底。
- ``get_llm_client``：进程级单例入口，按 ``memory_settings`` 构建并缓存，
  保持历史 API（包级 re-export：``os_mem.infra.llm`` / ``os_mem.infra``）。

provider 不可用处理：构造器抛 ``ClientConfigError``（如缺凭证/配置非法）
会被跳过并告警；全部不可用/未配置时 ``build`` 抛 ``ClientConfigError``，
不会静默返回 None 或空实现。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping

from os_mem.configs.mem_settings import memory_settings
from os_mem.infra.llm.base_client import ChatClient
from os_mem.infra.llm.deepseek_client import DeepSeekClient
from os_mem.infra.llm.failover import FailoverClient
from os_mem.infra.logger import get_logger

_logger = get_logger('os_mem.llm.factory')


class ClientConfigError(RuntimeError):
    """provider 无法按当前配置构建 / 没有可用的 LLM client。"""


def _build_deepseek() -> ChatClient:
    # 无 key 也允许实例化（OpenAI SDK 惰性，调用时才报错）；保持既有行为：
    # 单测/离线 import 不触发网络与异常，缺 key 的运行时失败交给 failover 处理。
    return DeepSeekClient()


# provider 名 -> 无参构造器（默认注册表；可用 register_llm_client 扩展）。
_LLM_CLIENT_BUILDERS: dict[str, Callable[[], ChatClient]] = {
    'deepseek': _build_deepseek,
}


def register_llm_client(name: str, builder: Callable[[], ChatClient]) -> None:
    """注册新 provider 的构造器，供 ``LLM_PROVIDERS`` 按名选用。"""
    if not name:
        raise ValueError('llm provider 名不能为空')
    if name in _LLM_CLIENT_BUILDERS:
        raise ValueError(f'llm provider 已注册: {name!r}')
    _LLM_CLIENT_BUILDERS[name] = builder
    _logger.info(f'LLM provider 注册: {name}')


class LLMClientFactory:
    """按配置构建 LLM client 链的工厂（可注入 builders 便于测试）。"""

    def __init__(
        self,
        builders: Mapping[str, Callable[[], ChatClient]] | None = None,
    ) -> None:
        self._builders = dict(
            _LLM_CLIENT_BUILDERS if builders is None else builders
        )

    def available_providers(self) -> list[str]:
        """当前注册的 provider 名（有序）。"""
        return sorted(self._builders)

    def build(self, providers: str | list[str] | None = None) -> ChatClient:
        """按配置实例化；多 provider 时包成降级保底链，单 provider 原样返回。

        ``providers`` 缺省时读 ``memory_settings.LLM_PROVIDERS``（逗号分隔）。
        """
        names = self._resolve_names(providers)
        clients: list[ChatClient] = []
        for name in names:
            builder = self._builders.get(name)
            if builder is None:
                _logger.warning(
                    f'未知 LLM provider {name!r}，已跳过'
                    f'（可选: {sorted(self._builders)}）'
                )
                continue
            try:
                clients.append(builder())
            except ClientConfigError as e:
                _logger.warning(f'LLM provider {name!r} 不可用，已跳过: {e}')
            except Exception as e:  # 构造期兜底：任何异常都视为该 provider 不可用
                _logger.warning(f'LLM provider {name!r} 构建失败，已跳过: {e}')
        if not clients:
            raise ClientConfigError(
                f'无可用 LLM client: providers={names!r} 全部不可用'
                f'（可选: {sorted(self._builders)}），请检查凭证与配置'
            )
        if len(clients) == 1:
            return clients[0]
        return FailoverClient(
            clients,
            probe_interval=memory_settings.LLM_FAILOVER_PROBE_INTERVAL,
        )

    @staticmethod
    def _resolve_names(providers: str | list[str] | None) -> list[str]:
        if providers is None:
            providers = memory_settings.LLM_PROVIDERS
        if isinstance(providers, str):
            raw = providers.split(',')
        else:
            raw = list(providers)
        return [p.strip() for p in raw if p.strip()]


_default_client: ChatClient | None = None


def get_llm_client() -> ChatClient:
    """进程级单例：按 ``memory_settings.LLM_PROVIDERS`` 构建并缓存。

    返回满足 ``ChatClient`` 契约的 client（单 provider 时即该实现本身，
    多 provider 时为 ``FailoverClient`` 降级链）。业务侧入口保持兼容。
    """
    global _default_client
    if _default_client is None:
        _default_client = LLMClientFactory().build()
    return _default_client
