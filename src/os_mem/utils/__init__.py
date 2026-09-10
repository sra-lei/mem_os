"""os_mem.utils —— 纯逻辑/可复用工具（不属于 core 业务编排，也不属 infra 底层 IO）。

现状：仅剩通用 prompt 指纹（prompt_fp）；事实提取域已于 2026-09-08 迁至
``os_mem.extractor``（fact_extractor/callers/deepseek_caller/regular_extractor/
common/models/profile/normalize；2026-09-09 包更名 extraction→extractor）。
"""
