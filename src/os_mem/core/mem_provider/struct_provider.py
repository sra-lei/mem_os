from os_mem.core.services.conv_meta_service import (
    STATUS_COMPLETED,
    get_conversation_meta_service,
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

        - claim（conv_meta 对话元数据表）：该会话已入库完成(COMPLETED) → 整体跳过；
          他人正在处理（租约未过期）→ 本次跳过；非完成态（FAILED/崩溃残留）→
          接管接着处理；从未见过 → 登记元数据后处理。并发由单语句 CAS 保证
          只有一个执行者（见 conv_meta_service.claim）。
        - 每个流程阶段开始前把状态沿状态机合法边推进（EXTRACTING → SAVING_SQLITE
          → SAVING_VECTOR）；任一异常 → FAILED（记录 last_error）后 re-raise，
          下次 ingest 会重启接着处理。
        设计见 docs/方案-会话处理状态机与原子入库.md
        """
        session_id = conversation.source_session_id or conversation.id or ""
        meta_svc = get_conversation_meta_service()

        meta = meta_svc.claim(
            user_id=conversation.user_id,
            source_session_id=session_id,
            message_count=len(conversation.messages),
            started_at=conversation.started_at,
            ended_at=conversation.ended_at,
        )
        if meta is None:
            _logger.info(f"会话 {session_id} 已入库完成或正在处理中，跳过 struct 入库流程")
            return

        def _advance_stage(target: str) -> None:
            meta_svc.mark(meta.id, target)

        try:
            self.service.add_structured_memory(conversation, on_stage=_advance_stage)
            meta_svc.mark(meta.id, STATUS_COMPLETED)
            _logger.info(f"  存储记忆: 完成 (session={session_id})")
        except Exception as e:
            meta_svc.fail(meta.id, e)
            _logger.exception(f"会话 {session_id} 结构化入库失败（已标记 FAILED，可重跑）: {e}")
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
