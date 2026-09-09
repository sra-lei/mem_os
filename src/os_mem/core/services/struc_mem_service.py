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
from os_mem.extractor import FactExtractor, build_extraction_caller
from os_mem.infra.llm import ChatClient, get_llm_client
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

_logger = get_logger('os_mem.struc_mem')

# 事实提取执行器（校验/分段/去重/数字兜底/R1 剪枝/编排）—— 提取域见 os_mem/extractor/
_extractor = FactExtractor()


class StructuredMemService:
    def __init__(
        self,
        client: ChatClient,
        vectorizer: Vectorizer,
        vector_store: MemoryVectorStore,
    ) -> None:
        self.client = client
        # provider 自愈提取 caller（prompt/恢复策略见 os_mem/extractor/{callers,prompt}；
        # validate 由 FactExtractor 注入——任务侧只见干净 extract 契约）
        self._caller = build_extraction_caller(client)
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

    @staticmethod
    def _converge_by_key(facts: list[MemoryFact]) -> list[MemoryFact]:
        """投影收敛：同 (category, key) 多条只保留一条（confidence 高者优先）。

        同批同刻无法用 updated_at 区分，confidence 由提取 LLM 给出（0-1）：
        - confidence 高者胜出；
        - confidence 相同 → 保留原序最后一条（稳定排序 confidence desc 后取每组末尾）。
        返回收敛后的事实列表（顺序按原列表首次出现排序，保持稳定可测）。
        """
        best: dict[tuple[str, str], MemoryFact] = {}
        for f in facts:
            sig = (f.category, f.key)
            prev = best.get(sig)
            # confidence 高者胜出；相等时后者覆盖前者（保留原序最后一条）
            if prev is None or f.confidence >= prev.confidence:
                best[sig] = f
        return list(best.values())

    def add_structured_memory(
        self,
        conversation: Conversation,
        on_stage: Callable[[str], None] | None = None,
    ) -> None:
        """把一段会话的结构化记忆写入 SQLite + 向量库。

        on_stage：可选阶段回调 —— 每个处理阶段「开始前」调用一次，参数为目标状态名
        （EXTRACTING / SAVING_SQLITE / SAVING_VECTOR），由调用方（StructProvider）
        接入会话处理状态机；为 None 时保持旧行为（不追踪）。
        事实抽取逻辑见 ``os_mem.extractor.fact_extractor.FactExtractor``。

        投影收敛（A 批，见 docs/方案-记忆更新收敛与Milvus投影一致性.md）：
        Milvus 是投影（SQLite 为权威源）；写入前先把本批 facts 按 (category, key)
        收敛为每键一条（confidence 高者优先），再按 category 批量删旧、插入新值——
        保证 mem_os 恒为"每 (user, key) 一条最新"的干净投影。
        """
        t0 = time.perf_counter()
        session_id = conversation.source_session_id or conversation.id or ''
        dialog_text = '\n'.join(item for item in conversation.messages)

        if on_stage:
            on_stage(STATUS_EXTRACTING)
        # LLM 结构化提取（分段/并行/降级，见 FactExtractor；调用经 provider 自愈 caller）
        stats_before = _extractor.stats_snapshot()
        llm_facts: list[MemoryFact] = _extractor.extract_structured_facts(
            dialog_text,
            caller=self._caller,
        )
        t_extract = time.perf_counter()
        # 提取账（观测/校准/成本记账）：本次会话的调用·截断·repair·降级统计
        extract_stats = _extractor.stats_delta(stats_before)
        _logger.info(
            f'  提取账: calls={extract_stats["llm_calls"]} '
            f'截断空={extract_stats["trunc_empties"]} '
            f'切段={extract_stats["split_recursions"]} '
            f'repair={extract_stats["repair_calls"]}'
            f'(成功 {extract_stats["repair_ok"]}) '
            f'降级行={extract_stats["degrade_rows"]} '
            f'{(t_extract - t0) * 1000:.0f}ms'
        )

        # LLM 提取兜底：从原文把含金额/编号/日期/百分比等精确 token 的句子原样入库，
        # 避免结构化提取改写/省略精确数值（如 $2,400、CLM-2024-894327、2:30 PM）。
        fallback_facts = _extractor.fallback_numeric_facts(dialog_text)
        # R1 覆盖去重：数值 token 全被结构化覆盖的兜底句不存（只保唯一信息，
        # 无负收益——删的是重复；详见 FactExtractor.prune_redundant_verbatim）。
        raw_fallback = len(fallback_facts)
        fallback_facts = _extractor.prune_redundant_verbatim(fallback_facts, llm_facts)
        conv_facts = _extractor.dedup_facts(llm_facts + fallback_facts)
        if raw_fallback:
            _logger.info(
                f'  数字兜底补充: {len(fallback_facts)} 条'
                f'（原始 {raw_fallback}，'
                f'R1 剪冗余 {raw_fallback - len(fallback_facts)}）'
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

        # ---- 投影期收敛：本批 facts 按 (category, key) 收敛为每键一条
        #      （confidence 高者优先，见方案文档 §9-3） ----
        conv_facts = self._converge_by_key(conv_facts)

        # ---- 删旧插新：按 category 分组批量删旧向量，再 embed + INSERT 新值 ----
        if on_stage:
            on_stage(STATUS_SAVING_VECTOR)
        user_id = conversation.user_id
        # 收集本批涉及的全部 key（收敛后每键一条），按 category 分组
        by_category: dict[str, list[MemoryFact]] = {}
        for conv_fact in conv_facts:
            by_category.setdefault(conv_fact.category, []).append(conv_fact)
        for cat, cat_facts in by_category.items():
            keys = [f.key for f in cat_facts]
            try:
                self.vector_store.delete_memories(
                    user_id=user_id, category=cat, keys=keys
                )
            except Exception as e:
                # 删除失败不阻断入库（SQLite 权威仍在，可重建）；
                # 记日志便于排查，后续重跑会再次删旧。
                _logger.error(
                    f'  投影删旧失败 user={user_id} category={cat} '
                    f'keys_n={len(keys)}: {e}'
                )

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
                    'user_id': user_id,
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
            f'struct 入库完成 user={user_id} session={session_id} '
            f'facts={len(records)} 提取={(t_extract - t0) * 1000:.0f}ms '
            f'落库={(t_sqlite - t_extract) * 1000:.0f}ms '
            f'向量={(time.perf_counter() - t_sqlite) * 1000:.0f}ms '
            f'总={(time.perf_counter() - t0) * 1000:.0f}ms'
        )

    def get_structured_memories(
        self, user_id: str, query: str, top_k: int = 3
    ) -> list[StructuredMemory]:
        """根据 query 检索结构化记忆（混合检索 + 元数据过滤）。

        检索策略层（可插拔，见 os_mem.core.retrieval_strategies）：
        - 固定顺序的单一职责策略链（噪声剔除/结构化去重/verbatim 冗余剔除/
          双配额），全部默认加载，无 Enable 开关；
        - 策略层需要放大取回候选（top_k × RETRIEVAL_FETCH_MULTIPLIER）再收敛，
          保证去重/配额后有足够不同 key 填满 top_k；
        - 策略只作用于候选列表 → 注入列表，不改搜索本身 → §10 来源锚定落地后可
          原样复用并复测收益。
        """
        from os_mem.core.retrieval_strategies import (
            RETRIEVAL_FETCH_MULTIPLIER,
            apply_retrieval_strategies,
        )
        from os_mem.infra.p2check import mask_pii

        # 放大取回：去重/配额收敛后仍需足够不同 key 填满 top_k（无条件生效）
        fetch_k = top_k * RETRIEVAL_FETCH_MULTIPLIER
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
                top_k=fetch_k,
                user_id=user_id,
            )
            or []
        )
        if not hits:
            _logger.warning(f'struct 检索无命中 user={user_id} query={masked_query}')

        # 检索策略后处理（全关时直出前 top_k = 基线）
        hits = apply_retrieval_strategies(query, hits, top_k)

        _logger.info(
            f'  获取结构化记忆: {len(hits)} 条'
            f'（fetch={fetch_k} top_k={top_k}）'
        )
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


# ========================================================================= #
#  检索策略链（固定加载，无开关）—— 实现唯一控制点在
#  os_mem/core/retrieval_strategies.py 的 STRATEGY_CHAIN：
#  VerbatimNoiseFilter → StructuredKeyDedup → RedundantVerbatimFilter
#  → VerbatimQuota → StructuredQuota → 终装配（结构化在前、verbatim 补位）。
#  验证（2026-09-07，layer1 struct/assert/top_k=15）：
#  v1 区分准入 11/20 → 14/20（run_c887cb12 → run_c087f9ee），覆盖漏 30→18。
#  基线对比不再靠运行时开关：用旧版本代码跑同 run，或对 run 落库结果对照。
# ========================================================================= #
