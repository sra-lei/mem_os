"""默认模型数据画像构造 —— 纯数据类在 ``model.models``，本模块只放默认画像构造。

归属：``os_mem.extractor`` 记忆提取域。分层意图：模型数据画像（模型名、输出
预算、温度、分段上限，见 ``model.models.ModelProfile`` / ``model.models.ChunkCaps``）
是**纯数据**，随 provider/model 换；恢复策略（repair/切段/重试）是**代码**，收敛在
各 provider caller 内部（``callers/deepseek_caller.py`` 等）——画像里刻意没有策略字段。

约束（防环 / 防双份模板）：
- 本模块只依赖 stdlib、``os_mem.configs.mem_settings`` 与
  ``os_mem.extractor.model.models``；
- 默认画像 ``build_default_profile()`` 从 memory_settings **现值**固化——settings
  现值即默认画像；不显式传 profile 的路径行为与现状一致。

provider→caller 的真实分发点是 ``callers/framework.py`` 的
``_CALLER_IMPL_MODULES``（按 ``profile.caller``）；待第二家 provider 落地、
确有多画像需求时再引入注册表（YAGNI）。
"""

from __future__ import annotations

from os_mem.configs.mem_settings import memory_settings
from os_mem.extractor.model.models import ChunkCaps, ModelProfile


def build_default_profile() -> ModelProfile:
    """从 memory_settings 现值固化默认画像（settings 现值 = 默认画像）。"""
    return ModelProfile(
        model=memory_settings.DEEPSEEK_MODEL,
        max_output_tokens=memory_settings.DEEPSEEK_MAX_TOKENS,
        temperature=memory_settings.DEEPSEEK_TEMPERATURE,
        max_facts=memory_settings.DEEPSEEK_EXTRACT_MAX_FACTS,
        chunk_caps=ChunkCaps.from_settings(),
    )
