"""os_mem.extractor —— 记忆提取域（2026-09-08 由 os_mem.utils 迁入 extraction/，
2026-09-09 包更名 extraction→extractor 并按内聚性拆分：extractor.py → fact_extractor.py，
新增 common.py 收拢共享纯函数/常量）。

定位：被编排的**领域执行器**——不属 utils 小工具，也不属 core 业务编排：
- ``fact_extractor.py``：FactExtractor（校验/分段/LLM 提取委托/并行编排/全败降级/
  去重，LLM 结构化链路任务语义；正则兜底/R1 剪枝已移 RegularExtractor）
- ``callers.py``      ：provider 无关的上层（ExtractionCaller 协议 / _ExtractionCore
  单一恢复循环 / build_extraction_caller 按 profile.caller 分发，
  见 docs/方案-提取任务与LLM模型画像解耦.md §2 v2）
- ``deepseek_caller.py``：DeepSeek 具体实现（2026-09-10 吸收原 prompt.py）——
  SYSTEM_PROMPT/REPAIR_PROMPT 模板与渲染、build_extract_complete 薄兼容、指纹；
  DeepSeekExtractionCaller + build_caller；chat_outcome/json_object/usage 口径
- ``regular_extractor.py``：不依赖 LLM 的确定性正则提取（RegularExtractor）——
  verbatim 数字句兜底 / R1 覆盖剪枝 / fact_tokens 数值 token 口径
  （提取 R1 与检索冗余过滤共享，原 tokens.py + FactExtractor 静态方法整合于此）
- ``common.py``      ：共享纯函数/常量（split_text_midpoint / dedup_facts /
  MAX_TRUNC_SPLIT_DEPTH / 统计 keys / empty_extraction_stats）——单一实现源，
  fact_extractor 与 callers 共用
- ``models.py``      ：提取域数据类单一存放点（NormalizedKey / CallResult /
  ChunkCaps / ModelProfile，纯数据无策略）

对外入口：本包直接 re-export 提取链路需要的高层能力——
``FactExtractor``（执行器，任务语义）与 ``build_extraction_caller``（provider 自愈
caller 工厂，编排/存储侧首选入口）以及 ``build_extract_complete``（旧 client →
complete 回调薄兼容，返回 deepseek caller 实例；测试/AB 用）；编排与存储由
core/services/struc_mem_service 负责；纯数据变换、无存储/网络副作用（LLM 经注入
回调或 caller），可离线单测。
"""

from __future__ import annotations

from os_mem.extractor.callers import build_extraction_caller
from os_mem.extractor.deepseek_caller import (
    build_extract_complete,
    build_extract_messages,
    build_repair_messages,
)
from os_mem.extractor.fact_extractor import FactExtractor

__all__ = [
    'FactExtractor',
    'build_extract_complete',
    'build_extract_messages',
    'build_extraction_caller',
    'build_repair_messages',
]
