"""默认模型数据画像构造 —— 纯数据类在 ``models.py``，本模块只放默认画像构造。

归属：``os_mem.extractor`` 记忆提取域。分层意图（docs/方案-提取任务与LLM模型画像
解耦.md §2 v2 / §4 步骤 3-4）：模型数据画像（模型名、输出预算、温度、分段上限，
见 ``models.ModelProfile`` / ``models.ChunkCaps``）是**纯数据**，随
provider/model 换；恢复策略（repair/切段/重试）是**代码**，收敛在
``deepseek_caller.py`` 等各 provider caller 内部——画像里刻意没有策略字段。

约束（防环 / 防双份模板）：
- 本模块只依赖 stdlib、``os_mem.configs.mem_settings`` 与 ``os_mem.extractor.models``；
- 默认画像 ``build_default_profile()`` 从 memory_settings **现值**固化——settings
  现值即默认画像；不显式传 profile 的路径行为与历史逐字节等价。

注：早期版本曾有 ``EXTRACTION_PROFILES`` 注册表 + ``resolve_extraction_profile``
（按 provider:model 显式覆盖、未注册回退默认并告警），但注册表生产零调用、且默认
路径每次刷一条「未注册」假警告——2026-09-10 作为死代码移除。provider→caller 的
真实分发点是 ``callers._CALLER_IMPL_MODULES``（按 ``profile.caller``）；待第二家
provider 落地、确有多画像需求时再恢复注册表（YAGNI）。
"""

from __future__ import annotations

from os_mem.configs.mem_settings import memory_settings
from os_mem.extractor.models import ChunkCaps, ModelProfile


def build_default_profile() -> ModelProfile:
    """从 memory_settings 现值固化默认画像（settings 现值 = 默认画像，等价现状）。"""
    return ModelProfile(
        model=memory_settings.DEEPSEEK_MODEL,
        max_output_tokens=memory_settings.DEEPSEEK_MAX_TOKENS,
        temperature=memory_settings.DEEPSEEK_TEMPERATURE,
        max_facts=memory_settings.DEEPSEEK_EXTRACT_MAX_FACTS,
        chunk_caps=ChunkCaps.from_settings(),
    )
