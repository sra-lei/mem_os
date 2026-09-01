from os_mem.core.services.struc_mem_service import get_structured_mem_service
from os_mem.models.mem_models import Conversation
from os_mem.infra.logger import get_logger

_logger = get_logger("base_provider")

class StructProvider():
    messages: list[str]
    
    def __init__(self, user_id: str):
        self.user_id = user_id
        self.service = get_structured_mem_service()
   
    def ingest(self, conversation: Conversation) -> None:
        _logger.info(f"  存储记忆: start")
        self.service.add_structured_memory(conversation)
        _logger.info(f"  存储记忆: 完成")

    def retrieve(self, query: str, top_k: int = 3) -> str:
        memories = self.service.get_structured_memories(user_id=self.user_id, query=query, top_k=top_k)
        _logger.info(f"  检索到 {len(memories)} 条记忆")
        _SECTION_HEADER = "## 关于用户的长久记忆"
        memory_lines = [_SECTION_HEADER]
        if not memories:
            memory_lines.append("（当前没有可用记忆）")
        for m in memories:
            memory_lines.append(f"- {m.fact}")
        return "\n".join(memory_lines)