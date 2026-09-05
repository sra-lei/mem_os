"""事实抽取工具类（FactExtractor）—— 结构化记忆提取链路的确定性内聚封装。

从 StrucMemService 中抽出的全部"事实抽取"逻辑，职责单一、无存储/网络副作用
（LLM 通过注入的 ``complete(text) -> raw_json`` 回调使用，便于单测与替换）：

- ``validate_response``   ：LLM 原始输出清洗（markdown 围栏/包装格式）与校验
  （分类白名单、confidence 边界、非法 JSON → 空列表触发重试）
- ``chunk_dialog``        ：长对话按消息分段 + 段间冗余重叠（边界信息不切丢）
- ``extract_chunk``       ：单段提取（重试 + 校验）
- ``extract_structured_facts``：分段编排（短对话单次 / 长对话并行）+ 全失败降级
- ``dedup_facts``         ：按 (category, key, value) 跨段去重
- ``fallback_numeric_facts``：正则兜底 —— 含金额/编号/日期/百分比等精确 token 的
  原文句子原样入库（layer1 精确回忆防线：结构化提取改写会丢数字）

设计说明：本类放 ``os_mem.utils``（非 core/infra）—— 它是纯数据变换工具，
不编排业务流程（编排在 core/services/struc_mem_service），也不直接触碰
存储/网络（LLM 回调注入）。常量与默认值来自 memory_settings 或显式参数。
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed

from pydantic import ValidationError

from os_mem.configs.mem_settings import memory_settings
from os_mem.infra.logger import get_logger
from os_mem.models.mem_models import MemoryFact, MemoryFacts

_logger = get_logger('os_mem.utils.fact_extraction')

ALLOWED_CATEGORIES = [
    'personal',
    'contact',
    'preference',
    'health',
    'travel',
    'work',
    'finance',
    'family',
    'education',
    'other',
]

# 精确信息兜底：即便 LLM 提取遗漏，也要把含金额/编号/日期/百分比的原文句子捞进库。
# 这些 token 正是 layer1 精确回忆类问题的答案核心（金额、编号、时间等）。
_NUMERIC_TOKENS = re.compile(
    r'\$\s?\d[\d,]*(?:\.\d+)?|'  # $2,400 / $1,017.50
    r'\d{1,2}%|'  # 20%
    r'\b\d{1,2}/\d{1,2}/\d{2,4}\b|'  # 11/21/2024
    r'\b\d{1,2}[:：]\d{2}\s*[APap]\.?[Mm]\.?|'  # 2:30 PM
    r'\b\d{1,2}[:：]\d{2}\b|'  # 14:35
    r'\b[A-Z]{2,}-\d{2,}[A-Z0-9-]*\b|'  # CLM-2024-894327 / PAC-778K4M
    r'\b\d{3}-\d{3}-\d{4}\b|'  # 电话 916-555-8899
    r'\b\d{4}-\d{4}-\d{4}-\d{4}\b'  # 卡号 4532-8876-9901-3345
)
MAX_FALLBACK_FACTS = 60


class FactExtractor:
    """事实抽取链路的内聚工具类（线程安全：除 LLM 回调外无共享可变状态）。"""

    def __init__(self, complete: Callable[[str], str] | None = None) -> None:
        """complete：``(dialog_text) -> raw_json`` 的 LLM 回调；
        也可在调用时按次传入。
        """
        self._complete = complete

    # ------------------------------------------------------------------ #
    #  LLM 输出清洗与校验
    # ------------------------------------------------------------------ #
    @staticmethod
    def validate_response(raw_json: str) -> list[MemoryFact]:
        """清洗并校验 LLM 返回；非法输入返回 []（触发上层重试/降级）。"""
        try:
            # 0. 清洗：去 markdown 代码块（```json ... ```）与首尾空白
            raw = (raw_json or '').strip()
            if raw.startswith('```'):
                raw = raw.strip('`').strip()
                if raw.lower().startswith('json'):
                    raw = raw[4:].strip()
            # 1. 解析 JSON
            data = json.loads(raw)
            _logger.debug(f'解析的 JSON 数据: {data}')
            # 2. Pydantic 校验结构：数组 [{fact,category,key,value,confidence},...]
            #    或 {"facts": [...]} dict 包装
            if isinstance(data, list):
                validated = MemoryFacts(facts=data)
            else:
                validated = MemoryFacts(**data)
            # 3. 业务规则：分类白名单 + confidence ∈ [0,1]
            for fact in validated.facts:
                if fact.category not in ALLOWED_CATEGORIES:
                    raise ValueError(f'Unknown category: {fact.category}')
                if not 0 <= fact.confidence <= 1:
                    raise ValueError(f'Confidence out of range: {fact.confidence}')
            return validated.facts
        except (json.JSONDecodeError, ValidationError, ValueError) as e:
            _logger.error(f'验证失败: {e}')
            _logger.debug(f'原始响应: {raw_json}')
            return []

    # ------------------------------------------------------------------ #
    #  长对话分段
    # ------------------------------------------------------------------ #
    @staticmethod
    def chunk_dialog(
        dialog_text: str,
        max_chars: int | None = None,
        overlap: int | None = None,
    ) -> list[str]:
        """按消息分段：每段 < max_chars 字符，段间保留 overlap 条消息冗余。

        冗余保证落在分段边界附近的信息不被切掉，两边都能提取到。
        """
        max_chars = max_chars or memory_settings.DEEPSEEK_EXTRACT_MAX_CHARS
        overlap = (
            overlap if overlap is not None else memory_settings.DEEPSEEK_EXTRACT_OVERLAP
        )
        if len(dialog_text) <= max_chars:
            return [dialog_text]
        msgs = dialog_text.split('\n')
        chunks: list[str] = []
        cur: list[str] = []
        cur_len = 0
        for m in msgs:
            if cur and cur_len + len(m) > max_chars:
                chunks.append('\n'.join(cur))
                # 冗余：保留本段末尾 overlap 条消息作为下一段开头
                keep = max(0, len(cur) - overlap)
                cur = cur[keep:]
                cur_len = sum(len(x) for x in cur)
            cur.append(m)
            cur_len += len(m)
        if cur:
            chunks.append('\n'.join(cur))
        return chunks

    # ------------------------------------------------------------------ #
    #  LLM 提取（单段 + 编排）
    # ------------------------------------------------------------------ #
    def _resolve_complete(
        self,
        complete: Callable[[str], str] | None = None,
    ) -> Callable[[str], str]:
        fn = complete or self._complete
        if fn is None:
            raise ValueError('FactExtractor 需要 LLM complete 回调（构造或调用时传入）')
        return fn

    def extract_chunk(
        self,
        text: str,
        retries: int = 3,
        complete: Callable[[str], str] | None = None,
    ) -> list[MemoryFact]:
        """对单个分段提取结构化事实（校验失败/异常按 retries 重试）。

        失败续写（方案 3）：若 LLM 返回了非空但解析失败的 JSON（典型为 max_tokens
        截断导致的 ``Unterminated string``），且回调具备 ``repair(partial_json)``
        能力，则先尝试让模型**修复/续写**该 JSON（秒级），修复仍失败才整段重提取。
        避免每次截断都触发 1-3 分钟的整段重新提取。
        """
        fn = self._resolve_complete(complete)
        repair_fn = getattr(fn, 'repair', None)
        for attempt in range(retries):
            try:
                raw_json = fn(text)
                facts = self.validate_response(raw_json)
                if facts:
                    return facts
                # 非空但解析失败：尝试 repair 续写（若回调支持）
                if raw_json and raw_json.strip() and repair_fn is not None:
                    try:
                        _logger.warning(
                            f'第 {attempt + 1} 次输出解析失败，尝试 repair 续写 '
                            f'（len={len(raw_json)}）...'
                        )
                        repaired = repair_fn(raw_json)
                        repaired_facts = self.validate_response(repaired)
                        if repaired_facts:
                            _logger.info(
                                f'repair 成功: {len(repaired_facts)} 条'
                                f'（原始 len={len(raw_json)}'
                                f' → 修复 len={len(repaired)}）'
                            )
                            return repaired_facts
                        _logger.warning('repair 输出仍解析失败，回退整段重试')
                    except Exception as e:
                        _logger.error(f'repair 调用失败，回退整段重试: {e}')
                else:
                    _logger.warning(f'第 {attempt + 1} 次提取验证失败，整段重试中...')
            except Exception as e:
                _logger.error(f'Attempt {attempt + 1} failed: {e}')
        return []

    def extract_structured_facts(
        self,
        dialog_text: str,
        retries: int = 3,
        complete: Callable[[str], str] | None = None,
    ) -> list[MemoryFact]:
        """对整段对话提取结构化事实（分段 + 并行 + 全失败降级）。

        返回提取结果（长对话已跨段去重）；全部失败时降级为
        ``raw_conversation`` 原始对话事实（confidence=0.1），保证不空手。
        """
        chunks = self.chunk_dialog(dialog_text)
        if len(chunks) <= 1:
            # 短对话：单次提取（原有重试 + 降级）
            facts = self.extract_chunk(dialog_text, retries=retries, complete=complete)
            if facts:
                return facts
            _logger.error('提取失败，降级存储原始对话')
            return self._degrade_fact(dialog_text)

        # 长对话：分段提取，每段独立调用 LLM（并行），结果合并去重
        all_facts: list[MemoryFact] = []
        _logger.info(f'分段提取开始: {len(chunks)} 段（并行 {min(4, len(chunks))} 路）')
        with ThreadPoolExecutor(max_workers=min(4, len(chunks))) as pool:
            futures = {
                pool.submit(self.extract_chunk, chunk, retries, complete): i
                for i, chunk in enumerate(chunks, 1)
            }
            for fut in as_completed(futures):
                i = futures[fut]
                _logger.info(f'提取分段 {i}/{len(chunks)} 完成')
                all_facts.extend(fut.result())
        deduped = self.dedup_facts(all_facts)
        if not deduped:
            _logger.error('全部分段提取失败，降级存储原始对话')
            return self._degrade_fact(dialog_text)
        _logger.info(f'分段提取完成: {len(all_facts)} 条（去重后 {len(deduped)} 条）')
        return deduped

    @staticmethod
    def _degrade_fact(dialog_text: str) -> list[MemoryFact]:
        return [
            MemoryFact(
                fact=f'原始对话: {dialog_text[:200]}...',
                category='other',
                key='raw_conversation',
                value=dialog_text,
                confidence=0.1,
            )
        ]

    # ------------------------------------------------------------------ #
    #  去重
    # ------------------------------------------------------------------ #
    @staticmethod
    def dedup_facts(facts: list[MemoryFact]) -> list[MemoryFact]:
        """按 (category, key, value) 去重（分段重叠会导致重复提取）。"""
        seen = set()
        result: list[MemoryFact] = []
        for f in facts:
            sig = (f.category, f.key, f.value)
            if sig in seen:
                continue
            seen.add(sig)
            result.append(f)
        return result

    # ------------------------------------------------------------------ #
    #  数字兜底（verbatim 事实）
    # ------------------------------------------------------------------ #
    @staticmethod
    def fallback_numeric_facts(
        dialog_text: str,
        max_facts: int = MAX_FALLBACK_FACTS,
    ) -> list[MemoryFact]:
        """把原文中带金额/编号/日期/百分比等精确信息的短句原样入库。

        结构化提取会把对话"翻译/压缩"成语义事实，金额、编号这类精确 token 容易被
        改写或省略（layer1 失败的主因）。这里用正则从原文把含关键 token 的句子
        捞出来作为 verbatim 事实，保证数字类信息不因提取遗漏而丢失。
        """
        facts: list[MemoryFact] = []
        seen: set = set()
        for line in dialog_text.split('\n'):
            line = line.strip()
            if not line:
                continue
            content = line
            try:
                obj = json.loads(line)
                if isinstance(obj, dict) and obj.get('content'):
                    content = obj['content']
                elif isinstance(obj, list):
                    content = ' '.join(
                        str(x.get('content', '')) for x in obj if isinstance(x, dict)
                    )
            except Exception:
                pass
            # 按句末标点拆句，逐句判断是否含关键数值 token
            sentences = re.split(r'(?<=[.!?。！？])\s+', content)
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
                    'finance'
                    if re.search(r'\$\s?\d|%|\b\d{4}-\d{4}-\d{4}-\d{4}\b', sent)
                    else 'other'
                )
                # key 用内容指纹而非聚合的 'verbatim_record'：多条兜底句共享一个
                # key 会在 (user, key) 冲突 upsert 时互相覆盖（库里只留最后一条，
                # 其余全部丢失——曾导致 13/17/20 的关键数字句在入库后被消灭）。
                # 内容指纹：同句重跑同 key（幂等覆盖），异句不同 key（互不踩踏）。
                digest = hashlib.sha1(sent.encode('utf-8')).hexdigest()[:12]
                facts.append(
                    MemoryFact(
                        fact=sent,
                        category=category,
                        key=f'verbatim_{digest}',
                        value=sent,
                        confidence=0.85,
                    )
                )
                if len(facts) >= max_facts:
                    return facts
        return facts
