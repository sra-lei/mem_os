"""os_mem — the MemOS memory system core (独立于评测框架).

Package layout:

    os_mem/
    ├── __init__.py   对外公共 API：memory_settings + get_logger
    ├── provider.py   MemoryProvider 协议 / 注册表（对外主接口）
    ├── memory.py     Memory 数据模型（契约签名的一部分）
    ├── admin/        管理窗口：外部管理面（EvalView/testing）操作记忆数据的唯一受控入口
    ├── extractor/    记忆提取域（2026-09-08 自 utils 迁入 extraction/，2026-09-09 包
    │                 更名 extraction→extractor）：fact_extractor（FactExtractor）/
    │                 callers（provider 自愈 caller）/ prompt / tokens / common（共享纯函数）
    ├── core/         核心实现：provider（provider 实现）/ retrieve/strategies（检索注入策略链）/
    │                 services（业务服务）/ state_machine
    ├── entries/      SQLModel 表定义（mem_models / conv 表）
    ├── infra/        底层 IO：storage（SQLite/Milvus）/ llm / logger / p2check
    ├── models/       运行时模型（Conversation / MemoryFact 等）
    ├── utils/        通用小工具（prompt_fp 指纹；提取域已迁至 extractor/）
    ├── vocab/        事实 category 词表（active 集驱动提取 prompt 与校验白名单）
    └── guide/        实现指南骨架：sanitizer（日志脱敏，需求文档 1.1）

    Architecture rule: os_mem must not import testing (testing.db / testing.api)
    or anything else outside itself. External consumers interact with os_mem
    through the public exports below — primarily the MemoryProvider contract.
"""


from .configs.mem_settings import memory_settings
from .infra.logger.logger import get_logger

__all__ = [
    "memory_settings",
    "get_logger",
]
