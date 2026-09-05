import time
import uuid
from collections.abc import Callable
from datetime import datetime

from sqlmodel import select

from os_mem.core.services.conv_meta_service import (
    STATUS_EXTRACTING,
    STATUS_SAVING_SQLITE,
    STATUS_SAVING_VECTOR,
)
from os_mem.entries.mem_models import StructuredMemory
from os_mem.infra.llm.llm_client import LLMClient, get_llm_client
from os_mem.infra.logger import get_logger
from os_mem.infra.storage import (
    MemoryVectorStore,
    Vectorizer,
    get_memory_vector_store,
    get_session,
    get_vectorizer,
)
from os_mem.models import Conversation
from os_mem.models.mem_models import MemoryFact
from os_mem.utils.fact_extraction import FactExtractor

_logger = get_logger('os_mem.struc_mem')

# 事实抽取工具类（校验/分段/去重/数字兜底/编排）—— 从本服务内聚抽出，见 os_mem/utils/
_extractor = FactExtractor()


class StructuredMemService:
    def __init__(
        self, client: LLMClient, vectorizer: Vectorizer, vector_store: MemoryVectorStore
    ) -> None:
        self.client = client
        self.vectorizer = vectorizer
        self.vector_store = vector_store

    @staticmethod
    def save_structured_memories_to_sqlite(
        user_id: str,
        source_conversation_id: str,
        facts: list[MemoryFact],
    ) -> int:
        """把结构化事实落库到 SQLite ``struct_memories`` 表（与向量库双写）。

        冲突检测（v0.2 变更 2.3）：同一 ``(user_id, category, key)`` 已有记录则
        UPDATE —— 新值覆盖，旧值归档到 ``previous_fact``；否则 INSERT。

        纯本地落库，与 Milvus / LLM / 向量化解耦，供审计、回溯以及向量库重建兜底。
        返回本次写入（INSERT + UPDATE）的条数。
        """
        if not facts:
            return 0
        now = datetime.utcnow()
        written = 0
        with get_session() as session:
            for f in facts:
                existing = session.exec(
                    select(StructuredMemory).where(
                        StructuredMemory.user_id == user_id,
                        StructuredMemory.category == f.category,
                        StructuredMemory.key == f.key,
                    )
                ).first()
                if existing is not None:
                    # 冲突：新值覆盖，旧值归档到 previous_fact
                    existing.previous_fact = existing.fact
                    existing.fact = f.fact
                    existing.value = f.value
                    existing.confidence = f.confidence
                    existing.source_conversation_id = source_conversation_id
                    existing.updated_at = now
                    session.add(existing)
                else:
                    session.add(
                        StructuredMemory(
                            user_id=user_id,
                            fact=f.fact,
                            category=f.category,
                            key=f.key,
                            value=f.value,
                            confidence=f.confidence,
                            source_conversation_id=source_conversation_id,
                        )
                    )
                written += 1
            session.commit()
        return written

    def add_structured_memory(
        self,
        conversation: Conversation,
        on_stage: Callable[[str], None] | None = None,
    ) -> None:
        """把一段会话的结构化记忆写入 SQLite + 向量库。

        on_stage：可选阶段回调 —— 每个处理阶段「开始前」调用一次，参数为目标状态名
        （EXTRACTING / SAVING_SQLITE / SAVING_VECTOR），由调用方（StructProvider）
        接入会话处理状态机；为 None 时保持旧行为（不追踪）。
        事实抽取逻辑见 ``os_mem.utils.fact_extraction.FactExtractor``。
        """
        t0 = time.perf_counter()
        session_id = conversation.source_session_id or conversation.id or ''
        dialog_text = '\n'.join(item for item in conversation.messages)

        if on_stage:
            on_stage(STATUS_EXTRACTING)
        # LLM 结构化提取（分段/并行/降级，见 FactExtractor）
        llm_facts: list[MemoryFact] = _extractor.extract_structured_facts(
            dialog_text,
            complete=self.client.complete,
        )
        t_extract = time.perf_counter()

        # LLM 提取兜底：从原文把含金额/编号/日期/百分比等精确 token 的句子原样入库，
        # 避免结构化提取改写/省略精确数值（如 $2,400、CLM-2024-894327、2:30 PM）。
        fallback_facts = _extractor.fallback_numeric_facts(dialog_text)
        conv_facts = _extractor.dedup_facts(llm_facts + fallback_facts)
        if len(fallback_facts):
            _logger.info(
                f'  数字兜底补充: {len(fallback_facts)} 条'
                f'（LLM {len(llm_facts)} → 合并 {len(conv_facts)}）'
            )

        # SQLite 双写：结构化事实同步落库（审计/回溯 + 向量库重建兜底）。
        # 本地落库先于向量写入，保证即便 Milvus 写入失败，记忆仍持久化在 SQLite。
        if on_stage:
            on_stage(STATUS_SAVING_SQLITE)
        sqlite_written = self.save_structured_memories_to_sqlite(
            user_id=conversation.user_id,
            source_conversation_id=conversation.id
            or conversation.source_session_id
            or '',
            facts=conv_facts,
        )
        t_sqlite = time.perf_counter()
        _logger.info(f'  落库 SQLite struct_memories: {sqlite_written} 条')

        # 逐个 embed → 改为批量 embed（DashScope 单批上限 10，自动折半重试）
        if on_stage:
            on_stage(STATUS_SAVING_VECTOR)
        records: list[dict] = []
        texts: list[str] = []
        for conv_fact in conv_facts:
            records.append(
                {
                    'id': uuid.uuid4().hex,
                    'fact': conv_fact.fact,
                    'category': conv_fact.category,
                    'key': conv_fact.key,
                    'value': conv_fact.value,
                    'user_id': conversation.user_id,
                    'updated_at': datetime.utcnow().isoformat(),
                }
            )
            texts.append(conv_fact.fact)
        embeddings: list[list[float]] = (
            self.vectorizer.embed_batch(texts) if texts else []
        )

        if records:
            self.vector_store.add_structured_memories(records, embeddings)
        _logger.info(
            f'struct 入库完成 user={conversation.user_id} session={session_id} '
            f'facts={len(records)} 提取={(t_extract - t0) * 1000:.0f}ms '
            f'落库={(t_sqlite - t_extract) * 1000:.0f}ms '
            f'向量={(time.perf_counter() - t_sqlite) * 1000:.0f}ms '
            f'总={(time.perf_counter() - t0) * 1000:.0f}ms'
        )

    def get_structured_memories(
        self, user_id: str, query: str, top_k: int = 3
    ) -> list[StructuredMemory]:
        """根据 query 检索结构化记忆（混合检索 + 元数据过滤）"""
        from os_mem.infra.p2check import mask_pii

        masked_query = mask_pii(query)
        query_embedding: list[float] = []
        try:
            query_embedding = self.vectorizer.embed(query)
        except Exception as e:
            _logger.error(f'query 向量化失败 user={user_id} query={masked_query}: {e}')

        hits = (
            self.vector_store.search(
                query_embedding,
                query_text=query,
                top_k=top_k,
                user_id=user_id,
            )
            or []
        )
        if not hits:
            _logger.warning(f'struct 检索无命中 user={user_id} query={masked_query}')

        _logger.info(f'  获取结构化记忆: {len(hits)} 条')
        memories: list[StructuredMemory] = []
        allowed = {'id', 'fact', 'category', 'key', 'value', 'user_id', 'updated_at'}
        for hit in hits:
            memories.append(
                StructuredMemory(**{k: hit[k] for k in allowed if k in hit})
            )
        return memories


_llm_client = get_llm_client()
_vectorizer = get_vectorizer()
_vector_store = get_memory_vector_store()
_structured_mem_service = None


def get_structured_mem_service() -> StructuredMemService:
    global _structured_mem_service
    if _structured_mem_service is None:
        _structured_mem_service = StructuredMemService(
            get_llm_client(), _vectorizer, _vector_store
        )
    return _structured_mem_service
