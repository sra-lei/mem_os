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

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # ------------------------------------------------------------------
    # DeepSeek — used by testing.llm.DeepSeekLLM as the answer generator
    # ------------------------------------------------------------------
    DEEPSEEK_BASE_URL: str = "https://api.deepseek.com"
    DEEPSEEK_API_KEY: str | None = None
    DEEPSEEK_MODEL: str = "deepseek-v4-flash"
    DEEPSEEK_TEMPERATURE: float = Field(default=0.1, ge=0, le=1)
    DEEPSEEK_TIMEOUT: int = Field(default=60, ge=1)
    DEEPSEEK_MAX_TOKENS: int = Field(default=4096, ge=1)

    # ------------------------------------------------------------------
    #
    # Ali — used by testing.llm.AliLLM as the answer generator
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
    MEMORY_DB_PATH:str = Field(default="data/memories.db", description="Path to the SQLite database file for storing memories.")


# Singleton — load once and reuse everywhere via `from os_mem.configs.mem_settings import memory_settings`
memory_settings = MemorySetting()
