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
from os_mem.core.services.memory_versioning import current_attribute_touches
from os_mem.entries.mem_models import StructuredMemory
from os_mem.extractor import FactExtractor, build_extraction_caller
from os_mem.extractor.common import dedup_facts
from os_mem.extractor.profile import build_default_profile
from os_mem.extractor.regular_extractor import RegularExtractor
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

# 事实提取执行器（校验/分段/去重/编排；数字兜底/R1 剪枝在 RegularExtractor）——
# 提取域见 os_mem/extractor/
_extractor = FactExtractor()


class StructuredMemService:
    def __init__(
        self,
        client: ChatClient,
        vectorizer: Vectorizer,
        vector_store: MemoryVectorStore,
    ) -> None:
        self.client = client
        # provider 自愈提取 caller（prompt/恢复策略见 os_mem/extractor/callers、
        # deepseek_caller；validate 由 FactExtractor 注入——任务侧只见干净 extract 契约）。
        # 画像：build_default_profile() 从 settings 现值固化（max_facts 渲染进
        # prompt、chunk_caps 供任务层分段）。
        self._profile = build_default_profile()
        self._caller = build_extraction_caller(client, profile=self._profile)
        self.vectorizer = vectorizer
        self.vector_store = vector_store

    @staticmethod
    def save_structured_memories_to_sqlite(
        user_id: str,
        source_conversation_id: str,
        facts: list[MemoryFact],
        started_at: datetime | None = None,
    ) -> int:
        """把结构化事实按 D4 版本裁决落库到 SQLite ``struct_memories`` 表。

        D4-1 起取代旧的同 ``(user_id, category, key)`` 原地 UPDATE：
        - key 经 :func:`normalize_key` 归一为 ``(entity_ref, attribute, lifecycle)``；
        - current 同签名按对话时间 ``started_at`` latest-wins：新版本 INSERT
          （version+1、supersedes_id 指旧版），旧 current 置 ``lifecycle=superseded``
          保留不删（旧值同时镜像到 previous_fact 保持既有审计习惯）；
        - historical（original_*/previous_* 快照）独立签名共存，永不被覆盖；
        - 同值幂等跳过；更早会话的迟到事实被忽略（不覆盖权威 current）。

        纯本地落库，与 Milvus / LLM / 向量化解耦。返回新插入的版本行数。
        """
        from os_mem.core.services.memory_versioning import (
            ExistingVersion,
            build_incoming_fact,
            plan_versioning,
        )

        if not facts:
            return 0
        now = datetime.utcnow()

        incoming = [
            build_incoming_fact(
                f,
                source_conversation_id=source_conversation_id,
                source_started_at=started_at,
            )
            for f in facts
        ]
        involved = {f.nk.signature for f in incoming}

        with get_session() as session:
            # 取相关签名的全部既有行（current + historical），按签名分桶
            rows = session.exec(
                select(StructuredMemory).where(
                    StructuredMemory.user_id == user_id
                )
            ).all()
            existing_by_sig: dict[tuple[str, str, str], list[ExistingVersion]] = {}
            row_by_id: dict[str, StructuredMemory] = {}
            for r in rows:
                sig = (r.entity_ref, r.attribute, r.lifecycle)
                row_by_id[r.id] = r
                # 裁决只以 current 行作基准；superseded 行不参与
                if r.lifecycle == "current" and sig in involved:
                    existing_by_sig.setdefault(sig, []).append(
                        ExistingVersion(
                            id=r.id,
                            value=r.value,
                            version=r.version,
                            source_started_at=r.source_started_at,
                        )
                    )
                # historical 槽位需要已有的 historical 行做同值去重
                if r.lifecycle == "historical":
                    hist_sig = (r.entity_ref, r.attribute, "historical")
                    if hist_sig in involved:
                        existing_by_sig.setdefault(hist_sig, []).append(
                            ExistingVersion(
                                id=r.id,
                                value=r.value,
                                version=r.version,
                                source_started_at=r.source_started_at,
                            )
                        )

            plan = plan_versioning(incoming, existing_by_sig)

            # 旧 current 置 superseded（保留行；旧 fact 镜像进新行 previous_fact）
            superseded_fact_by_id: dict[str, str] = {}
            for sid in plan.supersede_ids:
                old = row_by_id[sid]
                old.lifecycle = "superseded"
                old.updated_at = now
                superseded_fact_by_id[sid] = old.fact
                session.add(old)

            for nv in plan.inserts:
                f = nv.fact
                session.add(
                    StructuredMemory(
                        user_id=user_id,
                        fact=f.fact,
                        previous_fact=superseded_fact_by_id.get(nv.supersedes_id, ""),
                        category=f.category,
                        key=f.key,
                        value=f.value,
                        confidence=f.confidence,
                        source_conversation_id=source_conversation_id,
                        entity_ref=f.nk.entity_ref,
                        attribute=f.nk.attribute,
                        lifecycle=f.nk.lifecycle,
                        source_started_at=f.source_started_at,
                        version=nv.version,
                        supersedes_id=nv.supersedes_id,
                    )
                )
            session.commit()
        return len(plan.inserts)

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
        # LLM 结构化提取（分段/并行/降级，见 FactExtractor；调用经 provider 自愈
        # caller；分段上限取 profile.chunk_caps——默认 = settings 现值）
        stats_before = _extractor.stats_snapshot()
        llm_facts: list[MemoryFact] = _extractor.extract_structured_facts(
            dialog_text,
            caller=self._caller,
            chunk_caps=self._profile.chunk_caps,
        )
        t_extract = time.perf_counter()
        # 提取账（观测/校准/成本记账）：本次会话的调用·截断·repair·降级·token 统计
        extract_stats = _extractor.stats_delta(stats_before)
        _logger.info(
            f'  提取账: calls={extract_stats["llm_calls"]} '
            f'截断空={extract_stats["trunc_empties"]} '
            f'切段={extract_stats["split_recursions"]} '
            f'repair={extract_stats["repair_calls"]}'
            f'(成功 {extract_stats["repair_ok"]}) '
            f'降级行={extract_stats["degrade_rows"]} '
            f'in_tok={extract_stats["in_tokens"]} '
            f'out_tok={extract_stats["out_tokens"]} '
            f'{(t_extract - t0) * 1000:.0f}ms'
        )

        # LLM 提取兜底：从原文把含金额/编号/日期/百分比等精确 token 的句子原样入库，
        # 避免结构化提取改写/省略精确数值（如 $2,400、CLM-2024-894327、2:30 PM）。
        fallback_facts = RegularExtractor.fallback_numeric_facts(dialog_text)
        # R1 覆盖去重：精确信息 token 全被结构化覆盖的兜底句不存（只保唯一信息，
        # 无负收益——删的是重复；口径通用不看判分器，详见
        # RegularExtractor.prune_redundant_verbatim）。
        raw_fallback = len(fallback_facts)
        fallback_facts = RegularExtractor.prune_redundant_verbatim(
            fallback_facts, llm_facts
        )
        conv_facts = dedup_facts(llm_facts + fallback_facts)
        if raw_fallback:
            _logger.info(
                f'  数字兜底补充: {len(fallback_facts)} 条'
                f'（原始 {raw_fallback}，'
                f'R1 剪冗余 {raw_fallback - len(fallback_facts)}）'
                f'（LLM {len(llm_facts)} → 合并 {len(conv_facts)}）'
            )

        # SQLite 双写：结构化事实按 D4 版本裁决落库（审计/回溯 + 向量库重建兜底）。
        # 本地落库先于向量写入，保证即便 Milvus 写入失败，记忆仍持久化在 SQLite。
        if on_stage:
            on_stage(STATUS_SAVING_SQLITE)
        sqlite_written = self.save_structured_memories_to_sqlite(
            user_id=conversation.user_id,
            source_conversation_id=conversation.id
            or conversation.source_session_id
            or '',
            facts=conv_facts,
            started_at=conversation.started_at,
        )
        t_sqlite = time.perf_counter()
        _logger.info(f'  落库 SQLite struct_memories: 新版本 {sqlite_written} 条')

        # ---- D4 投影：Milvus 只镜像 SQLite 中 lifecycle=current 的行，
        #      投影 key=canonical attribute（每 (user, entity, attribute) 一条）；
        #      superseded / historical 行不投影。以 SQLite 回读为准（裁决后的赢家），
        #      而非直接投传入 facts——批内可能含被忽略的更旧事实。 ----
        if on_stage:
            on_stage(STATUS_SAVING_VECTOR)
        user_id = conversation.user_id

        # 本批触及的 current 签名（historical 不进投影）；归一收在版本域 helper
        touched_attrs = current_attribute_touches(conv_facts)

        # 从权威 SQLite 回读这些签名的 current 行
        projected: list[StructuredMemory] = []
        if touched_attrs:
            with get_session() as ro_session:
                all_rows = ro_session.exec(
                    select(StructuredMemory).where(
                        StructuredMemory.user_id == user_id,
                        StructuredMemory.lifecycle == 'current',
                    )
                ).all()
            wanted = {
                (cat, attr) for cat, attrs in touched_attrs.items() for attr in attrs
            }
            for r in all_rows:
                if (r.category, r.attribute) in wanted:
                    projected.append(r)

        # 删旧插新：按 category + canonical attribute 批量删旧向量（含被取代的旧版）
        for cat, attrs in touched_attrs.items():
            try:
                self.vector_store.delete_memories(
                    user_id=user_id, category=cat, keys=sorted(attrs)
                )
            except Exception as e:
                # 删除失败不阻断入库（SQLite 权威仍在，可重建）；
                # 记日志便于排查，后续重跑会再次删旧。
                _logger.error(
                    f'  投影删旧失败 user={user_id} category={cat} '
                    f'attrs_n={len(attrs)}: {e}'
                )

        records: list[dict] = []
        texts: list[str] = []
        for r in projected:
            records.append(
                {
                    # 投影行用新随机 id（与 SQLite id 无关，投影可整体重建）
                    'id': uuid.uuid4().hex,
                    'fact': r.fact,
                    'category': r.category,
                    # 投影 key=canonical attribute：保证同属性漂移 key 收敛为一条向量
                    'key': r.attribute,
                    'value': r.value,
                    'user_id': user_id,
                    'updated_at': datetime.utcnow().isoformat(),
                }
            )
            texts.append(r.fact)
        embeddings: list[list[float]] = (
            self.vectorizer.embed_batch(texts) if texts else []
        )

        if records:
            self.vector_store.add_structured_memories(records, embeddings)
        _logger.info(
            f'struct 入库完成 user={user_id} session={session_id} '
            f'新版本={sqlite_written} 投影current={len(records)} '
            f'提取={(t_extract - t0) * 1000:.0f}ms '
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
