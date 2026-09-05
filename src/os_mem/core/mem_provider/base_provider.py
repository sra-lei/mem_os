import time

from os_mem.core.services.conv_meta_service import get_conversation_meta_service
from os_mem.core.services.note_mem_service import (
    get_note_mem_service,
    has_conversation_messages,
    upsert_conversation_messages,
)
from os_mem.infra.logger.logger import get_logger
from os_mem.infra.p2check import mask_pii
from os_mem.infra.storage import get_session
from os_mem.models.mem_models import Conversation

_logger = get_logger("os_mem.provider.base")


class BaseProvider():
    """v0.1 记忆 provider：原文落库（conv_messages）+ BM25 note 检索。

    conv_memories 已退役（方案1 合并进 conv_meta）—— 本 provider 的会话登记走
    conv_meta.ensure_registered（只登记、永不改处理状态，状态由 struct 管线驱动）；
    消息写入走公共 value 冲突 upsert（幂等，双 provider 双写免疫）。
    """

    def __init__(self, user_id: str) -> None:
        self.user_id = user_id
        self.store = get_note_mem_service()  # 仅检索用（search_user_memories）

    def ingest(self, conversation: Conversation) -> None:
        t0 = time.perf_counter()
        session_id = conversation.source_session_id or conversation.id or ""
        messages = conversation.messages
        if not messages:
            raise ValueError("没有消息内容")
        _logger.info(
            f"ingest 开始 user={self.user_id} session={session_id} "
            f"messages={len(messages)}"
        )

        # 1) 会话登记（conv_meta）：仅登记元数据，不驱动处理状态（与 struct 协作见
        #    conv_meta_service.ensure_registered）。已登记过的会话不动任何字段。
        meta_svc = get_conversation_meta_service()
        meta_svc.ensure_registered(
            user_id=self.user_id,
            source_session_id=session_id,
            message_count=len(messages),
            started_at=conversation.started_at,
            ended_at=conversation.ended_at,
        )

        # 2) 重复投递门禁：原文已落库 = 本 provider 已处理过（base 无昂贵步骤，
        #    其余由 upsert 幂等兜底，这里只省无谓写）
        if has_conversation_messages(self.user_id, session_id):
            _logger.info(
                f"ingest 跳过 user={self.user_id} session={session_id} "
                f"（原文已落库）"
            )
            return

        # 3) 原文落库：value 冲突 upsert（同键一致 no-op / 不同覆盖 + 归档）
        with get_session() as session:
            written = upsert_conversation_messages(
                session,
                user_id=self.user_id,
                source_session_id=session_id,
                messages=messages,
                started_at=conversation.started_at,
            )
            session.commit()
        _logger.info(
            f"ingest 完成 user={self.user_id} session={session_id} "
            f"写入={written} 耗时={(time.perf_counter() - t0) * 1000:.0f}ms"
        )

    def retrieve(self, query: str, top_k: int = 3) -> str:
        t0 = time.perf_counter()
        _logger.info(
            f"retrieve 开始 user={self.user_id} query={mask_pii(query)} top_k={top_k}"
        )
        memories = self.store.search_user_memories(user_id=self.user_id, query=query, top_k=top_k)
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
