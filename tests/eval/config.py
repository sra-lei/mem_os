"""eval.config —— 评测运行库（eval 包）的集中配置。

从 testing.config 随运行侧迁入（原 testing 收窄为纯管理，不再承载评测配置）。

注意（历史现状，非本拆分引入）：eval.config 与 os_mem.configs.mem_settings
各自定义并读取同一份根目录 .env 的 DeepSeek 相关字段（评测回答/提取共用 env，
但配置类重复）。如需统一可后续收敛，属已知技术债。
"""
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
    # Moonshot (Kimi) — used by eval.judge as the LLM-as-Judge provider
    # ------------------------------------------------------------------
    MOONSHOT_BASE_URL: str = "https://api.moonshot.cn/v1"
    MOONSHOT_API_KEY: str | None = None
    MOONSHOT_MODEL: str = "kimi-k3"
    # judge 请求最小间隔（秒）：RPM=3 时代需 20s；充值/升配额后调小
    # （如 RPM=60 → 1.0s；若仍 429 再调大）
    MOONSHOT_MIN_INTERVAL: float = Field(default=1.0, ge=0)

    # ------------------------------------------------------------------
    # DeepSeek — used by eval.llm.DeepSeekLLM as the answer generator
    # ------------------------------------------------------------------
    DEEPSEEK_BASE_URL: str = "https://api.deepseek.com"
    DEEPSEEK_API_KEY: str | None = None
    DEEPSEEK_MODEL: str = "deepseek-v4-flash"
    DEEPSEEK_TIMEOUT: int = Field(default=60, ge=1)


# Singleton — load once and reuse everywhere via ``from eval.config import settings``
settings = Setting()
