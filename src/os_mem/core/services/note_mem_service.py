import json
from typing import List

from sqlmodel import select

from os_mem.models.mem_models import Memory
from os_mem.entries.mem_models import ConversationMemory, Message
from os_mem.infra.logger.logger import LoggerHelper, get_logger
from os_mem.infra.retriever import SimpleBM25, RankBM25
from os_mem.infra.storage import get_session
from os_mem.infra.p2check import has_pii, mask_pii

_logger: LoggerHelper = get_logger("StoreService")

class NoteMemService:
     def save_user_memories(self, conversation: ConversationMemory, messages: List[str]) -> None:
        with get_session() as session:
            records = session.exec(
                select(ConversationMemory).where(
                    (ConversationMemory.user_id == conversation.user_id)
                    & (ConversationMemory.source_session_id == conversation.source_session_id)
                )
            ).all()
            if records:
                _logger.warning(f"用户 {conversation.user_id} 的会话 {conversation.source_session_id} 已存在，跳过存储")
                return
            
            for m in messages:
                msg = json.loads(m) if isinstance(m, str) else m
                try:
                    # user + assistant 都存储：关键事实（账号、路由号等）常由
                    # 客服(assistant)告知用户，只存 user 会丢失正确答案
                    role = msg.get("role")
                    if role in ("user", "assistant"):
                        msg_content = msg.get("content")
                        hasPii = has_pii(msg_content)
                        message = Message(
                            user_id=conversation.user_id,
                            source_session_id=conversation.source_session_id,
                            content=msg_content,
                            # 新增字段：PII 标记
                            contains_pii=hasPii,
                            # 可选：存一份脱敏后的文本供日志查看
                            masked_text=mask_pii(msg_content) if hasPii else None,
                            create_at=conversation.started_at,
                        )
                        session.add(message)
                        session.commit()
                        session.refresh(message)
                except Exception as e:
                    _logger.error(f"存储消息失败: {e} - {m}")
                    continue

            session.add(conversation)
            session.commit()
            session.refresh(conversation)
            print(f"✅ 已存储对话: {conversation.id} ({len(messages)} 条消息)")


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
            _logger.info(f"检索用户 {user_id} 的记忆: query={query}, top_k={top_k}")
            with get_session() as session:
                records = session.exec(
                    select(ConversationMemory).where(ConversationMemory.user_id == user_id)
                ).all()

                messages = session.exec(
                    select(Message).where(Message.user_id == user_id)
                ).all()
            
            if not records or not messages:
                return []
            _logger.info(f"检索用户 {user_id} 的对话记录: {len(records)} 条, 消息记录: {len(messages)} 条")
            # 构建文档（对话摘要）
            user_contents = []
            for msg in messages:
                user_contents.append(msg.content)
            _logger.info(f"构建文档: {len(user_contents)} 条")
            # BM25 检索
            # bm25 = SimpleBM25(user_contents)
            bm25 = RankBM25(user_contents)
            results = bm25.retrieve(query, top_k)
    
            memories:List[Memory] = []
            for doc_idx, doc_text, score in results:
                _logger.info(f"检索结果: {doc_idx}, {messages[doc_idx].masked_text}, {score}")
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