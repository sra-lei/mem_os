import json
import re
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Callable, List, Optional

from openai import OpenAI
from pydantic import ValidationError
from sqlmodel import select

from os_mem.core.services.conv_meta_service import (
    STATUS_EXTRACTING,
    STATUS_SAVING_SQLITE,
    STATUS_SAVING_VECTOR,
)
from os_mem.entries.mem_models import StructuredMemory
from os_mem.infra.logger import get_logger
from os_mem.infra.storage import (
    MemoryVectorStore,
    Vectorizer,
    get_memory_vector_store,
    get_session,
    get_vectorizer,
)
from os_mem.infra.llm.llm_client import get_llm_client, LLMClient
from os_mem.models import Conversation
from os_mem.models.mem_models import MemoryFact, MemoryFacts
from os_mem.configs import memory_settings


_logger = get_logger("StrucMemStoreService")
ALLOWED_CATEGORIES = ["personal", "contact", "preference", "health", "travel", "work", "finance", "family", "education", "other"]

# 精确信息兜底：即便 LLM 提取遗漏，也要把含金额/编号/日期/百分比的原文句子捞进库。
# 这些 token 正是 layer1 精确回忆类问题的答案核心（金额、编号、时间等）。
_NUMERIC_TOKENS = re.compile(
    r"\$\s?\d[\d,]*(?:\.\d+)?|"          # $2,400 / $1,017.50
    r"\d{1,2}%|"                          # 20%
    r"\b\d{1,2}/\d{1,2}/\d{2,4}\b|"       # 11/21/2024
    r"\b\d{1,2}[:：]\d{2}\s*[APap]\.?[Mm]\.?|"  # 2:30 PM
    r"\b\d{1,2}[:：]\d{2}\b|"             # 14:35
    r"\b[A-Z]{2,}-\d{2,}[A-Z0-9-]*\b|"    # CLM-2024-894327 / PAC-778K4M / FID-8827439
    r"\b\d{3}-\d{3}-\d{4}\b|"             # 电话 916-555-8899
    r"\b\d{4}-\d{4}-\d{4}-\d{4}\b"        # 卡号 4532-8876-9901-3345
)
_MAX_FALLBACK_FACTS = 60

class StructuredMemService:
    def __init__(self, client: LLMClient, vectorizer: Vectorizer, vector_store: MemoryVectorStore):
        self.client = client
        self.vectorizer = vectorizer
        self.vector_store = vector_store

    def _validate_response(self, raw_json: str) -> List[MemoryFact]:
        """验证环节：在 API 响应后立即执行"""
        try:
            # 0. 清洗：去 markdown 代码块（```json ... ```）与首尾空白
            raw = (raw_json or "").strip()
            if raw.startswith("```"):
                raw = raw.strip("`").strip()
                if raw.lower().startswith("json"):
                    raw = raw[4:].strip()
            # 1. 解析 JSON
            data = json.loads(raw)
            _logger.debug(f"解析的 JSON 数据: {data}")
            # 2. 用 Pydantic 校验结构
            # LLM 按 prompt 输出 JSON 数组 [{fact, category, key, value, confidence}, ...]；
            # 也兼容 {"facts": [...]} 的 dict 包装。
            if isinstance(data, list):
                validated = MemoryFacts(facts=data)
            else:
                validated = MemoryFacts(**data)
            # 3. 额外业务规则验证
            for fact in validated.facts:
                # 分类必须在允许列表中
                if fact.category not in ALLOWED_CATEGORIES:
                    raise ValueError(f"Unknown category: {fact.category}")
                # confidence 必须在 0-1 之间
                if not 0 <= fact.confidence <= 1:
                    raise ValueError(f"Confidence out of range: {fact.confidence}")
            return validated.facts
        except (json.JSONDecodeError, ValidationError, ValueError) as e:
            # 验证失败，记录错误
            _logger.error(f"验证失败: {e}")
            _logger.debug(f"原始响应: {raw_json}")
            # 返回空列表，触发上层重试逻辑
            return []

    # 新增提取器
    def _chunk_dialog(self, dialog_text: str, max_chars: int = 8000, overlap: int = 5) -> List[str]:
        """把长对话按消息分段：每段 < max_chars 字符，段间保留 overlap 条消息冗余。

        冗余（重叠）保证落在分段边界附近的信息不被切掉，两边都能提取到。
        """
        if len(dialog_text) <= max_chars:
            return [dialog_text]
        msgs = dialog_text.split("\n")
        chunks: List[str] = []
        cur: List[str] = []
        cur_len = 0
        for m in msgs:
            if cur and cur_len + len(m) > max_chars:
                chunks.append("\n".join(cur))
                # 冗余：保留本段末尾 overlap 条消息作为下一段开头
                keep = max(0, len(cur) - overlap)
                cur = cur[keep:]
                cur_len = sum(len(x) for x in cur)
            cur.append(m)
            cur_len += len(m)
        if cur:
            chunks.append("\n".join(cur))
        return chunks

    def _extract_chunk(self, text: str, retries: int) -> List[MemoryFact]:
        """对单个分段提取结构化事实（complete 内已有空返回重试）。"""
        for attempt in range(retries):
            try:
                raw_json = self.client.complete(text)
                facts = self._validate_response(raw_json)
                if facts:
                    return facts
                _logger.warning(f"第 {attempt+1} 次提取验证失败，重试中...")
            except Exception as e:
                _logger.error(f"Attempt {attempt + 1} failed: {e}")
        return []

    @staticmethod
    def _dedup_facts(facts: List[MemoryFact]) -> List[MemoryFact]:
        """按 (category, key, value) 去重（分段重叠会导致重复提取）。"""
        seen = set()
        result: List[MemoryFact] = []
        for f in facts:
            sig = (f.category, f.key, f.value)
            if sig in seen:
                continue
            seen.add(sig)
            result.append(f)
        return result

    @staticmethod
    def _fallback_numeric_facts(dialog_text: str, max_facts: int = _MAX_FALLBACK_FACTS) -> List[MemoryFact]:
        """LLM 提取的兜底：把原文中带金额/编号/日期/百分比等精确信息的短句原样入库。

        结构化提取会把对话"翻译/压缩"成语义事实，金额、编号这类精确 token 容易被
        改写或省略（layer1 失败的主因）。这里直接用正则从原文把含关键 token 的句子
        捞出来作为 verbatim 事实，保证数字类信息不因提取遗漏而丢失。
        """
        facts: List[MemoryFact] = []
        seen: set = set()
        for line in dialog_text.split("\n"):
            line = line.strip()
            if not line:
                continue
            content = line
            try:
                obj = json.loads(line)
                if isinstance(obj, dict) and obj.get("content"):
                    content = obj["content"]
                elif isinstance(obj, list):
                    content = " ".join(
                        str(x.get("content", "")) for x in obj if isinstance(x, dict)
                    )
            except Exception:
                pass
            # 按句末标点拆句，逐句判断是否含关键数值 token
            sentences = re.split(r"(?<=[.!?。！？])\s+", content)
            for sent in sentences:
                sent = sent.strip()
                if len(sent) < 8 or len(sent) > 600:
                    continue
                if not _NUMERIC_TOKENS.search(sent):
                    continue
                sig = sent[:120]
                if sig in seen:
                    continue
                seen.add(sig)
                category = (
                    "finance"
                    if re.search(r"\$\s?\d|%|\b\d{4}-\d{4}-\d{4}-\d{4}\b", sent)
                    else "other"
                )
                facts.append(MemoryFact(
                    fact=sent,
                    category=category,
                    key="verbatim_record",
                    value=sent,
                    confidence=0.85,
                ))
                if len(facts) >= max_facts:
                    return facts
        return facts

    def _extract_structured_facts(self, conversation: Conversation, retries: int = 3) -> List[MemoryFact]:
        """
        调用 LLM 提取结构化事实（分段：每段 < 2048 字符，段间带冗余重叠）。
        {
            "fact": "用户支票账户号码是 4429853327",
            "category": "finance",
            "key": "checking_account_number",
            "value": "4429853327",
            "confidence": 0.95
        }
        """
        dialog_text = "\n".join([item for item in conversation.messages])
        chunks = self._chunk_dialog(
            dialog_text,
            max_chars=memory_settings.DEEPSEEK_EXTRACT_MAX_CHARS,
            overlap=memory_settings.DEEPSEEK_EXTRACT_OVERLAP,
        )
        if len(chunks) <= 1:
            # 短对话：单次提取（原有重试 + 降级）
            facts = self._extract_chunk(dialog_text, retries)
            if facts:
                return facts
            _logger.error("提取失败，降级存储原始对话")
            return [MemoryFact(
                fact=f"原始对话: {dialog_text[:200]}...",
                category="other",
                key="raw_conversation",
                value=dialog_text,
                confidence=0.1
            )]

        # 长对话：分段提取，每段独立调用 LLM（并行），结果合并去重
        all_facts: List[MemoryFact] = []
        _logger.info(f"分段提取开始: {len(chunks)} 段（并行 {min(4, len(chunks))} 路）")
        with ThreadPoolExecutor(max_workers=min(4, len(chunks))) as pool:
            futures = {pool.submit(self._extract_chunk, chunk, retries): i
                       for i, chunk in enumerate(chunks, 1)}
            for fut in as_completed(futures):
                i = futures[fut]
                _logger.info(f"提取分段 {i}/{len(chunks)} 完成")
                all_facts.extend(fut.result())
        deduped = self._dedup_facts(all_facts)
        if not deduped:
            _logger.error("全部分段提取失败，降级存储原始对话")
            return [MemoryFact(
                fact=f"原始对话: {dialog_text[:200]}...",
                category="other",
                key="raw_conversation",
                value=dialog_text,
                confidence=0.1
            )]
        _logger.info(f"分段提取完成: {len(all_facts)} 条（去重后 {len(deduped)} 条）")
        return deduped

    @staticmethod
    def save_structured_memories_to_sqlite(
        user_id: str,
        source_conversation_id: str,
        facts: List[MemoryFact],
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
                    session.add(StructuredMemory(
                        user_id=user_id,
                        fact=f.fact,
                        category=f.category,
                        key=f.key,
                        value=f.value,
                        confidence=f.confidence,
                        source_conversation_id=source_conversation_id,
                    ))
                written += 1
            session.commit()
        return written

    def add_structured_memory(
        self,
        conversation: Conversation,
        on_stage: Optional[Callable[[str], None]] = None,
    ):
        """把一段会话的结构化记忆写入 SQLite + 向量库。

        on_stage：可选阶段回调 —— 每个处理阶段「开始前」调用一次，参数为目标状态名
        （EXTRACTING / SAVING_SQLITE / SAVING_VECTOR），由调用方（StructProvider）
        接入会话处理状态机；为 None 时保持旧行为（不追踪）。
        """
        if on_stage:
            on_stage(STATUS_EXTRACTING)
        llm_facts: List[MemoryFact] = self._extract_structured_facts(conversation)

        # LLM 提取兜底：从原文把含金额/编号/日期/百分比等精确 token 的句子原样入库，
        # 避免结构化提取改写/省略精确数值（如 $2,400、CLM-2024-894327、2:30 PM）。
        dialog_text = "\n".join([item for item in conversation.messages])
        fallback_facts = self._fallback_numeric_facts(dialog_text)
        conv_facts = self._dedup_facts(llm_facts + fallback_facts)
        if len(fallback_facts):
            _logger.info(f"  数字兜底补充: {len(fallback_facts)} 条（LLM {len(llm_facts)} → 合并 {len(conv_facts)}）")

        # SQLite 双写：结构化事实同步落库（审计/回溯 + 向量库重建兜底）。
        # 本地落库先于向量写入，保证即便 Milvus 写入失败，记忆仍持久化在 SQLite。
        if on_stage:
            on_stage(STATUS_SAVING_SQLITE)
        sqlite_written = self.save_structured_memories_to_sqlite(
            user_id=conversation.user_id,
            source_conversation_id=conversation.id or conversation.source_session_id or "",
            facts=conv_facts,
        )
        _logger.info(f"  落库 SQLite struct_memories: {sqlite_written} 条")

        # 逐个 embed → 改为批量 embed（DashScope 单批上限 10，自动折半重试）
        if on_stage:
            on_stage(STATUS_SAVING_VECTOR)
        records: list[dict] = []
        texts: list[str] = []
        for conv_fact in conv_facts:
            records.append({
                "id": uuid.uuid4().hex,
                "fact": conv_fact.fact,
                "category": conv_fact.category,
                "key": conv_fact.key,
                "value": conv_fact.value,
                "user_id": conversation.user_id,
                "updated_at": datetime.utcnow().isoformat(),
            })
            texts.append(conv_fact.fact)
        embeddings: list[list[float]] = self.vectorizer.embed_batch(texts) if texts else []

        if records:
            self.vector_store.add_structured_memories(records, embeddings)
        _logger.info(f"  存储结构化记忆: {len(records)}（向量库 mem_os）")

    def get_structured_memories(self, user_id: str, query: str, top_k: int = 3) -> List[StructuredMemory]:
        """根据 query 检索结构化记忆（混合检索 + 元数据过滤）"""
        query_embedding: list[float] = []
        try:
            query_embedding = self.vectorizer.embed(query)
        except Exception as e:
            _logger.error(f"Error embedding for query '{query}': {e}")

        hits = self.vector_store.search(
            query_embedding, query_text=query, top_k=top_k, user_id=user_id,
        ) or []
        if not hits:
            _logger.warning(f"没有查询到相关记忆 for query '{query}'，返回空列表")

        _logger.info(f"  获取结构化记忆: {len(hits)} 条")
        memories: List[StructuredMemory] = []
        allowed = {"id", "fact", "category", "key", "value", "user_id", "updated_at"}
        for hit in hits:
            memories.append(StructuredMemory(**{k: hit[k] for k in allowed if k in hit}))
        return memories

_llm_client = get_llm_client()
_vectorizer = get_vectorizer()
_vector_store = get_memory_vector_store()
_structured_mem_service = None

def get_structured_mem_service():
    global _structured_mem_service
    if _structured_mem_service is None:
        _structured_mem_service = StructuredMemService(get_llm_client(), _vectorizer, _vector_store)
    return _structured_mem_service
    