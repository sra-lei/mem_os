# 使用 pydantic-settings 统一读取环境变量 / .env 文件
from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Setting(BaseSettings):
    """All runtime configuration for the evaluation pipeline.

    Fields are populated from environment variables (highest priority) or
    a ``.env`` file in the project root. Variable names are matched
    case-sensitively to the uppercase field names below.
    """

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # ------------------------------------------------------------------
    # Moonshot (Kimi) — used by judge.py as the LLM-as-Judge provider
    # ------------------------------------------------------------------
    MOONSHOT_BASE_URL: str = "https://api.moonshot.cn/v1"
    MOONSHOT_API_KEY: str | None = None
    MOONSHOT_MODEL: str = "kimi-k3"

    # ------------------------------------------------------------------
    # DeepSeek — used by testing.llm.DeepSeekLLM as the answer generator
    # ------------------------------------------------------------------
    DEEPSEEK_BASE_URL: str = "https://api.deepseek.com"
    DEEPSEEK_API_KEY: str | None = None
    DEEPSEEK_MODEL: str = "deepseek-v4-flash"
    DEEPSEEK_TIMEOUT: int = Field(default=60, ge=1)


# Singleton — load once and reuse everywhere via ``from testing.config import settings``
settings = Setting()
