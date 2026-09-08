"""os_mem.extraction —— 记忆提取域（2026-09-08 由 os_mem.utils 迁入）。

定位：被编排的**领域执行器**——不属 utils 小工具，也不属 core 业务编排：
- ``extractor.py``   ：FactExtractor（分段/并行/修复/降级/去重/兜底/R1 剪枝）
- ``prompt.py``      ：提取任务 prompt 与 LLM 适配（system/repair、回调封装）
- ``tokens.py``      ：数值 token 口径（提取 R1 与检索冗余过滤共享）

对外入口：本包只暴露提取链路需要的两个高层能力——
``FactExtractor``（执行器）与 ``build_extract_complete``（client → 回调适配），
编排与存储由 core/services/struc_mem_service 负责；纯数据变换、无存储/网络副作用
（LLM 经注入回调），可离线单测。
"""

from __future__ import annotations

from os_mem.extraction.extractor import FactExtractor
from os_mem.extraction.prompt import (
    build_extract_complete,
    build_extract_messages,
    build_repair_messages,
)

__all__ = [
    'FactExtractor',
    'build_extract_complete',
    'build_extract_messages',
    'build_repair_messages',
]
