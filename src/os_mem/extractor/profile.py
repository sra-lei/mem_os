"""模型数据画像（ModelProfile）—— 纯数据，不携带任何策略/行为。

归属：``os_mem.extractor`` 记忆提取域。分层意图（docs/方案-提取任务与LLM模型画像
解耦.md §2 v2 / §4 步骤 3-4）：模型数据画像（模型名、输出预算、温度、分段上限、
prompt 覆盖）是**纯数据**，随 provider/model 换；恢复策略（repair/切段/重试）是
**代码**，收敛在 ``callers.py`` 各 caller 内部——画像里刻意没有策略字段。

约束（防环 / 防双份模板）：
- 本模块**不 import 包内其他模块**，只依赖 stdlib 与 ``os_mem.configs.mem_settings``
  （告警用 stdlib logging，避免拉入 loguru 初始化副作用）；
- ``system_prompt`` / ``repair_prompt`` 为 None = 用 ``extractor.prompt`` 的
  SYSTEM_PROMPT / REPAIR_PROMPT 现行单源模板（None 即单源，防双份文本漂移）；
- 默认画像 ``build_default_profile()`` 从 memory_settings **现值**固化——settings
  现值即默认画像；不显式传 profile 的路径行为与现状逐字节等价；
- 注册表 ``EXTRACTION_PROFILES``：key = ``f'{provider}:{model}'``，显式注册条目
  优先，未注册回退默认画像并告警（见 ``resolve_extraction_profile``）。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from os_mem.configs.mem_settings import memory_settings

_logger = logging.getLogger('os_mem.extractor.profile')


@dataclass(frozen=True)
class ChunkCaps:
    """单次提取调用的输入分段上限（纯数据；任务层分段编排取此供给）。

    对应 memory_settings 的 DEEPSEEK_EXTRACT_MAX_CHARS / MAX_MSGS / OVERLAP
    （双维分段：字符数 OR 消息数任一超限即切，段间冗余 overlap 条）。
    """

    max_chars: int
    max_msgs: int
    overlap: int

    @classmethod
    def from_settings(cls) -> ChunkCaps:
        """读 memory_settings 现值构造（settings 即默认画像的单一数据源）。"""
        return cls(
            max_chars=memory_settings.DEEPSEEK_EXTRACT_MAX_CHARS,
            max_msgs=memory_settings.DEEPSEEK_EXTRACT_MAX_MSGS,
            overlap=memory_settings.DEEPSEEK_EXTRACT_OVERLAP,
        )


@dataclass(frozen=True)
class ModelProfile:
    """模型数据画像（frozen 纯数据，无策略字段——策略随 caller 实现走）。

    字段语义：
    - ``provider`` / ``model``：提供方与模型名（注册表 key = f'{provider}:{model}'）；
    - ``caller``：provider 内自愈实现标识（策略注册点；v1 仅 'deepseek'）；
    - ``max_output_tokens`` / ``temperature``：单次调用输出预算与温度（数据）；
    - ``max_facts``：单次（每段）提取事实上限——渲染进 system/repair prompt 的
      {max_facts} 占位（``prompt.build_extract_messages`` /
      ``build_repair_messages``）；
    - ``chunk_caps``：输入分段上限（任务层 ``chunk_dialog`` 取此供给）；
    - ``system_prompt`` / ``repair_prompt``：模板覆盖；None = 用 ``extractor.prompt``
      现行单源模板（防双份文本漂移）。

    Python dataclass 要求无默认字段在前，故必填的 ``max_output_tokens`` /
    ``temperature`` / ``max_facts`` / ``chunk_caps`` 排在带默认的字段之前。
    """

    max_output_tokens: int
    temperature: float
    max_facts: int
    chunk_caps: ChunkCaps
    provider: str = 'deepseek'
    model: str = ''
    caller: str = 'deepseek'
    system_prompt: str | None = None
    repair_prompt: str | None = None


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
