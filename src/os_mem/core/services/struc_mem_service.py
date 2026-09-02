import json
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import List

from openai import OpenAI
from pydantic import ValidationError

from os_mem.entries.mem_models import StructuredMemory
from os_mem.infra.logger import get_logger
from os_mem.infra.storage import MemoryVectorStore, Vectorizer, get_memory_vector_store, get_vectorizer
from os_mem.infra.llm.llm_client import get_llm_client, LLMClient
from os_mem.models import Conversation
from os_mem.models.mem_models import MemoryFact, MemoryFacts
from os_mem.configs import memory_settings


_logger = get_logger("StrucMemStoreService")
ALLOWED_CATEGORIES = ["personal", "contact", "preference", "health", "travel", "work", "finance", "family", "education", "other"]

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

    def _resolve_conflict(self, existing: StructuredMemory, new: StructuredMemory) -> StructuredMemory:
        """
        同一 (user_id, category, key) 出现不同 value：
        - 检查 timestamp：取最新的
        - 检查 confidence：取最高的
        - 保留历史：存到 previous_values 字段
        """
        memories = []
        if existing.user_id == new.user_id and existing.category == new.category and existing.key == new.key:
            if(existing.timestamp < new.timestamp or existing.confidence < new.confidence) :
                new.previous_fact = existing.fact
                memories.append(new)
        if memories:
            with get_session() as session:
                session.add_all(memories)
                session.commit()
                session.refresh(memories)

    def add_structured_memory(self, conversation: Conversation):
        conv_facts: List[MemoryFact] = self._extract_structured_facts(conversation)

        # 逐个 embed → 改为批量 embed（DashScope 单批上限 10，自动折半重试）
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
    