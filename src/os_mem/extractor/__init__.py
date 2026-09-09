"""os_mem.extractor —— 记忆提取域（2026-09-08 由 os_mem.utils 迁入 extraction/，
2026-09-09 包更名 extraction→extractor 并按内聚性拆分：extractor.py → fact_extractor.py，
新增 common.py 收拢共享纯函数/常量）。

定位：被编排的**领域执行器**——不属 utils 小工具，也不属 core 业务编排：
- ``fact_extractor.py``：FactExtractor（分段/并行/降级/去重/兜底/R1 剪枝，任务语义）
- ``callers.py``     ：provider 自愈 extraction caller（「LLM 调用 + 恢复策略」=
  repair/截断切段/整段重试收敛于此，见 docs/方案-提取任务与LLM模型画像解耦.md §2 v2）
- ``prompt.py``      ：提取任务 prompt（SYSTEM_PROMPT/REPAIR_PROMPT 渲染与指纹）
- ``tokens.py``      ：数值 token 口径（提取 R1 与检索冗余过滤共享）
- ``common.py``      ：共享纯函数/常量（split_text_midpoint / dedup_facts /
  MAX_TRUNC_SPLIT_DEPTH / 统计 keys）——单一实现源，fact_extractor 与 callers 共用

对外入口：本包暴露提取链路需要的三个高层能力——
``FactExtractor``（执行器，任务语义）与 ``build_extraction_caller``（provider 自愈
caller 工厂，编排/存储侧首选入口）以及 ``build_extract_complete``（旧 client →
complete 回调薄兼容，返回同 caller 实例；测试/AB 脚本用）；编排与存储由
core/services/struc_mem_service 负责；纯数据变换、无存储/网络副作用（LLM 经注入
回调或 caller），可离线单测。
"""

from __future__ import annotations

from os_mem.extractor.callers import build_extraction_caller
from os_mem.extractor.fact_extractor import FactExtractor
from os_mem.extractor.prompt import (
    build_extract_complete,
    build_extract_messages,
    build_repair_messages,
)

__all__ = [
    'FactExtractor',
    'build_extract_complete',
    'build_extract_messages',
    'build_extraction_caller',
    'build_repair_messages',
]
