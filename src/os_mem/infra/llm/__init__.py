"""os_mem LLM 抽象层 —— 业务侧统一从这里取能力。

分层：
- ``base_client``：``ChatClient``/``Message`` 契约（与具体实现 / 网关解耦）；
- ``deepseek_client``：DeepSeek（OpenAI 兼容）实现；
- ``factory``：按 ``memory_settings.LLM_PROVIDERS`` 配置实例化具体 client，
  多 provider 时包成 ``FailoverClient`` 降级链（见 ``failover``）。

接入新 provider：实现 ``ChatClient`` -> ``factory.register_llm_client``
-> ``LLM_PROVIDERS`` 配置按名选用。业务侧只 import 本层。
"""

from os_mem.infra.llm.base_client import ChatClient, Message
from os_mem.infra.llm.factory import get_llm_client

__all__ = ['ChatClient', 'Message', 'get_llm_client']
