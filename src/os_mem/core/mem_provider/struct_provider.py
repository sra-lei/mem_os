import json

from os_mem.core.services.conv_process_service import (
    STATUS_COMPLETED,
    get_conv_process_service,
)
from os_mem.core.services.struc_mem_service import get_structured_mem_service
from os_mem.infra.logger import get_logger
from os_mem.models.mem_models import Conversation

_logger = get_logger("struct_provider")


class StructProvider():
    messages: list[str]

    def __init__(self, user_id: str):
        self.user_id = user_id
        self.service = get_structured_mem_service()

    def ingest(self, conversation: Conversation) -> None:
        """会话级原子入库：claim 门禁 → 分阶段推进处理状态 → COMPLETED / FAILED。

        - 先检查会话是否已登记/已入库（conv_process 账本）：已有则整体跳过，
          不再进入任何后续流程（LLM 提取 / 落库 / 向量写入都不会发生）；
        - 没有则先登记会话原数据 + 初始状态 PENDING（同一事务，唯一约束兜底并发）；
        - 每个流程阶段开始前把状态沿状态机合法边推进（EXTRACTING → SAVING_SQLITE
          → SAVING_VECTOR）；任一异常 → FAILED（记录 last_error）后 re-raise。
        状态不可逆：重复投递/重放天然幂等。设计见 docs/方案-会话处理状态机与原子入库.md
        """
        session_id = conversation.source_session_id or conversation.id or ""
        conv_svc = get_conv_process_service()

        proc = conv_svc.claim(
            user_id=conversation.user_id,
            source_session_id=session_id,
            raw_payload=json.dumps(conversation.messages, ensure_ascii=False),
        )
        if proc is None:
            _logger.info(f"会话 {session_id} 已登记/已入库，跳过 struct 入库流程")
            return

        def _advance_stage(target: str) -> None:
            conv_svc.mark(proc.id, target)

        try:
            self.service.add_structured_memory(conversation, on_stage=_advance_stage)
            conv_svc.mark(proc.id, STATUS_COMPLETED)
            _logger.info(f"  存储记忆: 完成 (session={session_id})")
        except Exception as e:
            conv_svc.fail(proc.id, e)
            _logger.exception(f"会话 {session_id} 结构化入库失败（已标记 FAILED）: {e}")
            raise

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
