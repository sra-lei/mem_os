"""provider 无关的 extraction caller 框架 —— 干净契约 + 工厂。

本模块只承载 **provider 无关** 的部分，具体实现内聚在各自子模块
（deepseek 专属逻辑见 ``callers/deepseek_caller.py``），不与任何 provider 耦合：

- ``ExtractionCaller``：任务侧 caller 协议（``extract(dialog_text, *, validate)
  -> CallResult``）；
- ``build_extraction_caller(client, profile=None)``：按 ``profile.caller`` 分发的
  工厂——具体 provider 模块在函数体内 lazy import（避免上层反向依赖具体实现、
  防环）；未注册 caller 标识回退 deepseek（v1 唯一实现）。

任务层（FactExtractor）只认识干净契约 ``extract(dialog_text, *, validate)
-> CallResult``（facts|None = 合法结果或明确全败，stats = 遥测）；validate
（schema 校验权：分类白名单/confidence 边界）由任务注入。分段编排/去重/
verbatim 兜底/降级仍是任务层语义。单一恢复循环 ``ExtractionCore`` 在
``extraction_core.py``。

依赖方向（无环）：本模块 → extraction_core / utils.token_utils / model.models /
infra；具体实现（deepseek_caller 等）→ 本模块；工厂只在函数体内 lazy import
具体实现。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from os_mem.extractor.extraction_core import ExtractionCore
from os_mem.extractor.model.models import CallResult, ModelProfile
from os_mem.infra.llm.base_client import ChatClient
from os_mem.infra.logger import get_logger

__all__ = [
    'ExtractionCaller',
    'ExtractionCore',
    'build_extraction_caller',
]

_logger = get_logger('os_mem.extractor.callers.framework')


# ------------------------------------------------------------------ #
#  caller 协议（任务侧只依赖此契约，不认识具体 provider 实现）
# ------------------------------------------------------------------ #
class ExtractionCaller(Protocol):
    """提取 caller 协议：facts|None = 合法结果或明确全败，stats = 遥测。"""

    def extract(
        self,
        dialog_text: str,
        *,
        validate: Callable[[str], list],
        retries: int = 2,
    ) -> CallResult: ...


# ------------------------------------------------------------------ #
#  工厂：按 profile.caller 分发到具体 provider 实现（lazy import 防环）
# ------------------------------------------------------------------ #
# caller 标识 → 具体实现模块路径（新增 provider 在此登记，上层不动）。
_CALLER_IMPL_MODULES = {
    'deepseek': 'os_mem.extractor.callers.deepseek_caller',
}


def build_extraction_caller(
    client: ChatClient,
    profile: ModelProfile | None = None,
) -> ExtractionCaller:
    """构造 provider 自愈提取 caller：按 ``profile.caller`` 分发具体实现。

    ``profile`` 缺省 → settings 现值默认画像（其 caller='deepseek'）；显式画像的
    caller 标识决定具体实现模块（注册表见 ``_CALLER_IMPL_MODULES``），未登记标识
    回退 deepseek 并告警（v1 唯一实现）。具体实现的 max_facts 渲染进 prompt、
    chunk_caps 由任务层分段取用。

    ``build_extract_complete``（旧 client → complete 回调适配）与本工厂现在返回
    同一实现实例——旧回调用法（outcome/__call__/repair 鸭子）与任务侧新契约
    （extract）并存。
    具体实现模块须暴露标准工厂 ``build_caller(client, profile)``；新增 provider
    只需写实现模块并在 ``_CALLER_IMPL_MODULES`` 登记，上层不动。
    """
    if profile is None:
        from os_mem.extractor.llm_util import build_default_profile

        profile = build_default_profile()
    caller_name = profile.caller or 'deepseek'
    module_path = _CALLER_IMPL_MODULES.get(caller_name)
    if module_path is None:
        _logger.warning(
            f'未登记的 caller 实现（{caller_name}），回退 deepseek caller'
        )
        module_path = _CALLER_IMPL_MODULES['deepseek']
    import importlib

    module = importlib.import_module(module_path)
    return module.build_caller(client, profile)
