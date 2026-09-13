"""os_mem.utils —— 纯逻辑/可复用工具（不属于 core 业务编排，也不属 infra 底层 IO）。

现状：仅剩通用 prompt 指纹（``prompt_fp``）；事实提取域位于
``os_mem.core.extract``（fact_extractor / callers / regular_extractor /
model / utils），不依赖本包。
"""
