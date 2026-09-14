# ------------------------------------------------------------------ #
#  caller 协议（任务侧只依赖此契约，不认识具体 provider 实现）
# ------------------------------------------------------------------ #
import importlib
from collections.abc import Callable, Sequence
from typing import Protocol

from os_mem.core.extract.model import CallResult
from os_mem.infra.llm import ChatClient
from os_mem.infra.logger import get_logger


class ExtractionCaller(Protocol):
    """提取 caller 协议：facts|None = 合法结果或明确全败，stats = 遥测。"""

    def extract(
        self,
        dialog_text: str,
        *,
        validate: Callable[[str], list],
        retries: int = 2,
        attribute_hints: Sequence[tuple[str, str]] | None = None,
    ) -> CallResult: ...


_logger = get_logger('os_mem.extractor.callers.framework')

# ------------------------------------------------------------------ #
#  工厂：按 client.client_name() 分发到具体 provider 实现（lazy import 防环）
# ------------------------------------------------------------------ #
# caller 标识 → 具体实现模块路径（新增 provider 在此登记，上层不动）。
_CALLER_IMPL_MODULES = {
    'deepseek': 'os_mem.core.extract.callers.deepseek_caller',
}

def build_extraction_caller(
    client: ChatClient,
) -> ExtractionCaller:
    """构造 provider 自愈提取 caller：按 ``client.client_name()`` 分发具体实现。

    client 名决定具体实现模块（注册表见 ``_CALLER_IMPL_MODULES``），未登记标识
    回退 deepseek 并告警（v1 唯一实现）。max_facts 等调参由具体实现直接读
    memory_settings 渲染进 prompt，chunk_caps 由任务层分段取用。

    返回实例同时承载旧鸭子接口（outcome/__call__/repair）与任务侧新契约
    （extract）。
    具体实现模块须暴露标准工厂 ``build_caller(client)``；新增 provider
    只需写实现模块并在 ``_CALLER_IMPL_MODULES`` 登记，上层不动。
    """
    caller_name = client.client_name() or 'deepseek'
    module_path = _CALLER_IMPL_MODULES.get(caller_name)
    if module_path is None:
        _logger.warning(
            f'未登记的 caller 实现（{caller_name}），回退 deepseek caller'
        )
        module_path = _CALLER_IMPL_MODULES['deepseek']
    module = importlib.import_module(module_path)
    return module.build_caller(client)