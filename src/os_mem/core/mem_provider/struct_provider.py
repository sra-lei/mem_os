import time

from os_mem.core.services.conv_meta_service import (
    STATUS_COMPLETED,
    get_conversation_meta_service,
)
from os_mem.core.services.struc_mem_service import get_structured_mem_service
from os_mem.infra.logger import get_logger
from os_mem.infra.p2check import mask_pii
from os_mem.models.mem_models import Conversation

_logger = get_logger("os_mem.provider.struct")


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
        t0 = time.perf_counter()
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
            _logger.info(
                f"ingest 跳过 user={conversation.user_id} session={session_id} "
                f"（已入库完成或处理中）"
            )
            return

        def _advance_stage(target: str) -> None:
            meta_svc.mark(meta.id, target)

        try:
            self.service.add_structured_memory(conversation, on_stage=_advance_stage)
            meta_svc.mark(meta.id, STATUS_COMPLETED)
            _logger.info(
                f"ingest 完成 user={conversation.user_id} session={session_id} "
                f"耗时={(time.perf_counter() - t0) * 1000:.0f}ms"
            )
        except Exception as e:
            meta_svc.fail(meta.id, e)
            _logger.exception(
                f"ingest 失败 user={conversation.user_id} session={session_id} "
                f"（已标记 FAILED，可重跑）: {e}"
            )
            raise

    def retrieve(self, query: str, top_k: int = 3) -> str:
        t0 = time.perf_counter()
        _logger.info(
            f"retrieve 开始 user={self.user_id} query={mask_pii(query)} top_k={top_k}"
        )
        memories = self.service.get_structured_memories(user_id=self.user_id, query=query, top_k=top_k)
        _SECTION_HEADER = "## 关于用户的长久记忆"
        memory_lines = [_SECTION_HEADER]
        if not memories:
            memory_lines.append("（当前没有可用记忆）")
        for m in memories:
            memory_lines.append(f"- {m.fact}")
        _logger.info(
            f"retrieve 完成 user={self.user_id} 命中={len(memories)} "
            f"耗时={(time.perf_counter() - t0) * 1000:.0f}ms"
        )
        return "\n".join(memory_lines)
