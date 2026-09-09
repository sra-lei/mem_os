# 使用 pydantic-settings 统一读取环境变量 / .env 文件
from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class MemorySetting(BaseSettings):
    """All runtime configuration for the evaluation pipeline.

    Fields are populated from environment variables (highest priority) or
    a ``.env`` file in the project root. Variable names are matched
    case-sensitively to the uppercase field names below.
    """

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # ------------------------------------------------------------------
    # DeepSeek — used by eval.llm.DeepSeekLLM as the answer generator
    # ------------------------------------------------------------------
    DEEPSEEK_BASE_URL: str = "https://api.deepseek.com"
    DEEPSEEK_API_KEY: str | None = None
    DEEPSEEK_MODEL: str = "deepseek-v4-flash"
    DEEPSEEK_TEMPERATURE: float = Field(default=0.1, ge=0, le=1)
    DEEPSEEK_TIMEOUT: int = Field(default=60, ge=1)
    DEEPSEEK_MAX_TOKENS: int = Field(default=8192, ge=1)
    # 长对话分段提取参数（2026-09-09 双维化：字符数 OR 消息数任一超限即切。
    # 真正瓶颈是输出预算（由事实条数≈消息数决定），非输入长度；
    # 初值保守估计，观测修复后按真实 usage 校准——见方案：事实提取鲁棒性与成本优化）
    DEEPSEEK_EXTRACT_MAX_CHARS: int = Field(default=4500, ge=256)  # 每段最大字符数
    DEEPSEEK_EXTRACT_MAX_MSGS: int = Field(default=35, ge=1)  # 每段最大消息条数
    DEEPSEEK_EXTRACT_OVERLAP: int = Field(default=3, ge=0)  # 段间重叠消息条数（冗余）
    # 单次（每段）提取事实数量上限（防失控保险；分段后每段 60 足够，
    # 100×~70tok≈7000 逼近 8192 输出预算无收尾余量）
    DEEPSEEK_EXTRACT_MAX_FACTS: int = Field(default=60, ge=1)

    # ------------------------------------------------------------------
    # LLM client 编排（os_mem.infra.llm 工厂，见 infra/llm/factory.py）
    # ------------------------------------------------------------------
    # 有序 provider 列表（逗号分隔，顺序即优先级）。第一个为主路，其余为降级备胎；
    # 仅保留 "deepseek" 即维持单 client 的既有行为（向后兼容）。
    LLM_PROVIDERS: str = "deepseek"
    # 主路故障降级到备胎后，距上次主路失败超过该秒数才再次探测主路是否恢复；
    # 冷却期内请求直接走备胎，避免每次请求都先承担一次主路失败开销。
    LLM_FAILOVER_PROBE_INTERVAL: int = Field(default=300, ge=1)

    # ------------------------------------------------------------------
    #
    # Ali — used by eval.llm.AliLLM as the answer generator
    # ------------------------------------------------------------------
    DASHSCOPE_API_KEY: str | None = None
    DASHSCOPE_BASE_URL: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    EMBEDDING_MODEL: str = "text-embedding-v4"
    # embedding 向量维度（text-embedding-v4 输出 1024；与 vec_storage 建 collection 用）
    embedding_dim: int = Field(default=1024, description="embedding 向量维度")

    MILVUS_API_KEY: str | None = None
    MILVUS_URI: str = "https://in03-668dd52c256b1d8.serverless.aws-eu-central-1.cloud.zilliz.com"
    MILVUS_PORT: int = 19530

    # Memory — used by os_mem.storage.StorageProvider as the SQLite database path
    # ------------------------------------------------------------------
    MEMORY_DB_PATH:str = Field(
        default="data/memories.db",
        description="Path to the SQLite database file for storing memories.",
    )


# Singleton — load once and reuse everywhere via
# `from os_mem.configs.mem_settings import memory_settings`
memory_settings = MemorySetting()
