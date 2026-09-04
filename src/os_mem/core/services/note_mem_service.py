import json
from datetime import datetime
from typing import List

from sqlmodel import select

from os_mem.models.mem_models import Memory
from os_mem.entries.mem_models import Message
from os_mem.infra.logger.logger import LoggerHelper, get_logger
from os_mem.infra.retriever import SimpleBM25, RankBM25
from os_mem.infra.storage import get_session
from os_mem.infra.p2check import has_pii, mask_pii

_logger: LoggerHelper = get_logger("os_mem.note_mem")


def upsert_conversation_messages(
    session,
    user_id: str,
    source_session_id: str,
    messages: List[str],
    started_at=None,
) -> int:
    """逐条把会话原文写入 conv_messages（value 冲突 upsert，双 provider 共用）。

    冲突键 (user_id, source_session_id, seq)，seq = 消息在会话中的原始序号（0 起）：
    - 同键不存在           → INSERT；
    - 同键已存在且内容不同 → 后入覆盖（content/PII 标记），旧值归档 previous_content；
    - 同键已存在且内容一致 → no-op（双写一致时无影响、不产生重复行）。
    与 struct_memories 的 (user_id, category, key) 冲突逻辑同构，供 note(base) 与
    未来 struct/full 的 register 共用；只存 role ∈ {user, assistant} 的消息。
    由调用方负责 commit。返回写入（INSERT + UPDATE）条数。
    """
    written = 0
    identical = 0
    for seq, m in enumerate(messages):
        try:
            msg = json.loads(m) if isinstance(m, str) else m
            if not isinstance(msg, dict) or msg.get("role") not in ("user", "assistant"):
                continue
            msg_content = msg.get("content") or ""
            has_pii_flag = has_pii(msg_content)
            masked = mask_pii(msg_content) if has_pii_flag else None

            existing = session.exec(
                select(Message).where(
                    Message.user_id == user_id,
                    Message.source_session_id == source_session_id,
                    Message.seq == seq,
                )
            ).first()
            if existing is None:
                session.add(Message(
                    user_id=user_id,
                    source_session_id=source_session_id,
                    seq=seq,
                    content=msg_content,
                    contains_pii=has_pii_flag,
                    masked_text=masked,
                    create_at=started_at or datetime.utcnow(),
                ))
                written += 1
            elif existing.content != msg_content:
                # 后入的走记忆更新逻辑：新值覆盖，旧值归档（镜像 previous_fact）
                existing.previous_content = existing.content
                existing.content = msg_content
                existing.contains_pii = has_pii_flag
                existing.masked_text = masked
                session.add(existing)
                written += 1
            else:
                identical += 1  # 数据一致 → 无影响（跳过）
        except Exception as e:
            _logger.error(f"存储消息失败: {e} - {m}")
            continue
    if written or identical:
        _logger.info(
            f"消息 upsert user={user_id} session={source_session_id} "
            f"总={len(messages)} 写入={written} 一致跳过={identical}"
        )
    return written


def has_conversation_messages(user_id: str, source_session_id: str) -> bool:
    """该会话是否已有原文落库（base 重复投递门禁用，消息存在 = 已处理过）。"""
    with get_session() as session:
        row = session.exec(
            select(Message.id).where(
                Message.user_id == user_id,
                Message.source_session_id == source_session_id,
            ).limit(1)
        ).first()
        return row is not None


class NoteMemService:
     def _expand_turn_with_context(self,
        messages: List[Message],
        turn_index: int, 
        expand_before: int = 3, 
        expand_after: int = 3
    ) -> str:
        """取某个回合前后各 N 个回合，组成一个上下文块"""
        start = max(0, turn_index - expand_before)
        end = min(len(messages), turn_index + expand_after + 1)
        return "\n".join([messages[i].content for i in range(start, end)])

     def search_user_memories(self, user_id: str, query: str, top_k: int = 3) -> List[Memory]:
            """检索用户相关对话"""
            from os_mem.infra.p2check import mask_pii

            _logger.info(
                f"note 检索 user={user_id} query={mask_pii(query)} top_k={top_k}"
            )
            with get_session() as session:
                messages = session.exec(
                    select(Message).where(Message.user_id == user_id)
                ).all()

            if not messages:
                _logger.info(f"note 检索 user={user_id}: 无消息记录，返回空")
                return []
            # 构建文档（对话摘要）
            user_contents = []
            for msg in messages:
                user_contents.append(msg.content)
            # BM25 检索
            bm25 = RankBM25(user_contents)
            results = bm25.retrieve(query, top_k)

            memories:List[Memory] = []
            for doc_idx, doc_text, score in results:
                _logger.debug(f"note 检索命中: doc={doc_idx} masked={messages[doc_idx].masked_text} score={score}")
                fact = self._expand_turn_with_context(messages, doc_idx, expand_before=1, expand_after=1)
                memory = Memory(
                    user_id=user_id,
                    fact=fact,
                    contains_pii=messages[doc_idx].contains_pii,
                    masked_text=messages[doc_idx].masked_text,
                    source_session_id=messages[doc_idx].source_session_id,
                    created_at=messages[doc_idx].create_at,
                )
                memories.append(memory)

            return memories

_note_mem_service = None
def get_note_mem_service() -> NoteMemService:
    global _note_mem_service
    if _note_mem_service is None:
        _note_mem_service = NoteMemService()
    return _note_mem_service