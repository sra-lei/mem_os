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
    # Memory — used by os_mem.storage.StorageProvider as the SQLite database path
    # ------------------------------------------------------------------
    MEMORY_DB_PATH:str = Field(default="data/memories.db", description="Path to the SQLite database file for storing memories.")


# Singleton — load once and reuse everywhere via `from os_mem.configs.mem_settings import memory_settings`
memory_settings = MemorySetting()
