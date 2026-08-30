"""os_mem — the MemOS memory system core (独立于评测框架).

Package layout:

    os_mem/
    ├── __init__.py   对外公共 API：仅 provider 契约 + 两个评测必需能力
    ├── provider.py   MemoryProvider 协议 / register_provider / build_memory_provider（对外主接口）
    ├── memory.py     Memory 数据模型（契约签名的一部分）
    ├── core/         core 记忆实现：generator（提取）/ retriever（检索）/ prompt（注入）/ stub
    ├── storage/      存储层：独立库 os_mem.db 与评测 memos.db 解耦
    └── guide/        用户实现指南骨架：sanitizer（日志脱敏，需求文档 1.1）

Architecture rule: os_mem must not import testing (testing.db / testing.api)
or anything else outside itself. External consumers interact with os_mem
through the public exports below — primarily the MemoryProvider contract.
"""


from .configs.mem_settings import memory_settings
from .provider import MemoryProvider, build_memory_provider

__all__ = [
    "memory_settings",
    "MemoryProvider",
    "build_memory_provider"
]
