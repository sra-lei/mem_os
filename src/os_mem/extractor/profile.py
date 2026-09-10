"""模型数据画像注册表与解析 —— 纯数据类在 ``models.py``，本模块只放注册/解析逻辑。

归属：``os_mem.extractor`` 记忆提取域。分层意图（docs/方案-提取任务与LLM模型画像
解耦.md §2 v2 / §4 步骤 3-4）：模型数据画像（模型名、输出预算、温度、分段上限、
prompt 覆盖，见 ``models.ModelProfile`` / ``models.ChunkCaps``）是**纯数据**，随
provider/model 换；恢复策略（repair/切段/重试）是**代码**，收敛在 ``callers.py``
各 caller 内部——画像里刻意没有策略字段。

约束（防环 / 防双份模板）：
- 本模块只依赖 stdlib、``os_mem.configs.mem_settings`` 与 ``os_mem.extractor.models``
  （告警用 stdlib logging，避免拉入 loguru 初始化副作用）；
- ``system_prompt`` / ``repair_prompt`` 为 None = 用 ``deepseek_caller`` 的
  SYSTEM_PROMPT / REPAIR_PROMPT 现行单源模板（None 即单源，防双份文本漂移）；
- 默认画像 ``build_default_profile()`` 从 memory_settings **现值**固化——settings
  现值即默认画像；不显式传 profile 的路径行为与现状逐字节等价；
- 注册表 ``EXTRACTION_PROFILES``：key = ``f'{provider}:{model}'``，显式注册条目
  优先，未注册回退默认画像并告警（见 ``resolve_extraction_profile``）。
"""

from __future__ import annotations

import logging

from os_mem.configs.mem_settings import memory_settings
from os_mem.extractor.models import ChunkCaps, ModelProfile

_logger = logging.getLogger('os_mem.extractor.profile')


# 提取画像注册表：key = f'{provider}:{model}'（显式注册条目优先于默认画像）。
EXTRACTION_PROFILES: dict[str, ModelProfile] = {}


def register_extraction_profile(profile: ModelProfile) -> None:
    """注册 / 覆盖一个提取画像（key = f'{provider}:{model}'，后注册覆盖先注册）。"""
    if not profile.provider or not profile.model:
        raise ValueError('画像注册需要非空 provider 与 model')
    EXTRACTION_PROFILES[f'{profile.provider}:{profile.model}'] = profile


def build_default_profile() -> ModelProfile:
    """从 memory_settings 现值固化默认画像（settings 现值 = 默认画像，等价现状）。"""
    return ModelProfile(
        model=memory_settings.DEEPSEEK_MODEL,
        max_output_tokens=memory_settings.DEEPSEEK_MAX_TOKENS,
        temperature=memory_settings.DEEPSEEK_TEMPERATURE,
        max_facts=memory_settings.DEEPSEEK_EXTRACT_MAX_FACTS,
        chunk_caps=ChunkCaps.from_settings(),
    )


def _default_provider_name() -> str:
    """provider 缺省值：LLM_PROVIDERS 主路（首个，与 infra.llm.factory 语义一致）。

    LLM_PROVIDERS 为逗号分隔有序 provider 名，第一个为主路；空/未配置回退
    'deepseek'（与 ModelProfile.provider 默认值一致）。
    """
    names = [
        name.strip()
        for name in (memory_settings.LLM_PROVIDERS or '').split(',')
        if name.strip()
    ]
    return names[0] if names else 'deepseek'


def resolve_extraction_profile(
    provider: str | None = None,
    model: str | None = None,
) -> ModelProfile:
    """解析提取画像：显式注册条目优先，否则默认画像（settings 现值）并告警。

    缺省 provider / model 从 memory_settings 取（provider = LLM_PROVIDERS 主路，
    model = DEEPSEEK_MODEL）。未注册组合回退 ``build_default_profile()`` 并
    warning——不抛错：默认路径 = 现状，注册表只做显式覆盖。
    """
    provider_name = provider or _default_provider_name()
    model_name = model or memory_settings.DEEPSEEK_MODEL
    profile = EXTRACTION_PROFILES.get(f'{provider_name}:{model_name}')
    if profile is not None:
        return profile
    _logger.warning(
        f'提取模型画像未注册（{provider_name}:{model_name}），'
        '使用 settings 默认画像'
    )
    return build_default_profile()
