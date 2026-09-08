"""os_mem 管理窗口（admin）—— 记忆数据受控访问的唯一对外入口。

外部（如 EvalView / testing 管理面）只能通过本窗口操作记忆数据：
不得直接持有 MemoryDatabase engine / ORM / 向量库对象。

用法：
    from os_mem.admin import get_mem_admin_service

    admin = get_mem_admin_service()          # 生产（lazy 连向量库）
    admin.list_users() / admin.list_facts(user_id) / admin.upsert_fact(...)

测试注入 fake 投影：
    admin = MemAdminService(projection=fake_projection)          # 模拟可用
    admin = MemAdminService(allow_live=False)                    # 离线纯 SQLite
"""
from .mem_admin_service import MemAdminService, get_mem_admin_service

__all__ = ["MemAdminService", "get_mem_admin_service"]
