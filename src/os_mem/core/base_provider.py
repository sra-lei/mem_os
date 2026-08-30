import uuid
import json

from typing import List

from ..logger import get_logger
from ..memory import Memory
from ..provider import MemoryProvider
from ..storage.storage import get_session

# TODO(user): 剩余实现问题（运行时才暴露，属于记忆系统实现部分）：
#   1. _logger(f"...") 是函数调用写法，应为 _logger.info(f"...")
#   2. json.loads(conversation) —— conversation 是 dict 不是 JSON 字符串
#   3. retrieve 里 len(retrieved) 应为 len(memories)
#   4. __init__ 缺 user_id 参数（build_memory_provider 会传 user_id=...）
#   5. ingest/retrieve 需通过 sanitizer.sanitize_log 脱敏后再记日志（需求文档 1.1）

_logger = get_logger("base_provider")

class BaseProvider(MemoryProvider):
    messages: dict = None
    
    def __init__(self, user_id: str):
        pass
   
    def ingest(self, conversation: dict) -> List[Memory]:
        _logger.info(f"  存储记忆: start")
        self.messages = json.dumps(conversation["messages"], ensure_ascii=False)
        _logger.info(f"  存储记忆: end")
        return []

    def retrieve(self, query: str, top_k: int = 3) -> str:
        # memories: List[Memory] = get_session().query(Memory).filter(Memory.fact.contains(query)).filter(Memory.user_id == self.user_id).limit(top_k).all()
        # _logger.info(f"  检索到 {len(memories)} 条记忆")
        # _SECTION_HEADER = "## 关于用户的长久记忆"
        # memory_lines = [_SECTION_HEADER]
        # if not memories:
        #     memory_lines.append("（当前没有可用记忆）")
        # for m in memories:
        #     memory_lines.append(f"- {m.fact}")
        # return "\n".join(memory_lines)
        return self.messages