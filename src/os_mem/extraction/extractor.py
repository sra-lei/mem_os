"""事实提取执行器（FactExtractor）—— 结构化记忆提取链路的确定性内聚封装。

归属：``os_mem.extraction`` 记忆提取域（2026-09-08 由 os_mem.utils 迁入，
见 AGENTS.md 目录地图）。定位：被编排的**领域执行器**——不属 utils 小工具
（它是提取链路核心），也不属 core 业务编排（编排在 core/services/struc_mem_service，
存储/网络副作用为零，LLM 通过注入的 ``complete(text) -> raw_json`` 回调使用，
便于单测与替换）：

- ``validate_response``   ：LLM 原始输出清洗（markdown 围栏/包装格式）与校验
  （分类白名单、confidence 边界、非法 JSON → 空列表触发重试）
- ``chunk_dialog``        ：长对话按消息分段 + 段间冗余重叠（边界信息不切丢）
- ``extract_chunk``       ：单段提取（重试 + 修复续写）
- ``extract_structured_facts``：分段编排（短对话单次 / 长对话并行）+ 全失败降级
- ``dedup_facts``         ：按 (category, key, value) 跨段去重
- ``fallback_numeric_facts``：正则兜底 —— 含金额/编号/日期/百分比等精确 token 的
  原文句子原样入库（layer1 精确回忆防线：结构化提取改写会丢数字）
- ``prune_redundant_verbatim``：R1 覆盖去重 —— token 全被结构化覆盖的兜底句不存

命名规范：本包遵守"变量不用纯缩写"硬规则（snake_case 全称，如 signature /
tokens / kept_facts），新增代码保持同水准。常量与默认值来自 memory_settings 或
显式参数。
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed

from pydantic import ValidationError

from os_mem.configs.mem_settings import memory_settings
from os_mem.extraction.tokens import fact_tokens
from os_mem.infra.logger import get_logger
from os_mem.models.mem_models import MemoryFact, MemoryFacts

_logger = get_logger('os_mem.extraction.extractor')


def _active_categories() -> frozenset[str]:
    """校验白名单：读 fact_category 词表 active 集（词表故障回退内置 10 类）。

    替代原硬编码 ALLOWED_CATEGORIES 常量 —— 词表化后停用/增补 category 即时生效，
    见 docs/方案-事实category与key词表管理.md。每次调用读表（本地 SQLite，廉价），
    不做缓存：管理窗口改词表后校验行为立即一致。
    """
    from os_mem.vocab import list_active_categories

    return frozenset(list_active_categories())

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
    r'\b\d{4}-\d{4}-\d{4}-\d{4}\b|'  # 卡号 4532-8876-9901-3345
    # 产品名+年份/代号：Freedom 2045（裸 4 位易噪，仅收"大写词 4 位"形态）
    r'\b[A-Z][a-z]+ \d{4}\b'
)
MAX_FALLBACK_FACTS = 60
# 截断空返回的对半切段递归最大层数（每层把段再切半，≤1 层已足够收敛输出预算）
MAX_TRUNC_SPLIT_DEPTH = 1


class FactExtractor:
    """事实抽取链路的内聚执行器（线程安全：除 LLM 回调外无共享可变状态）。"""

    def __init__(self, complete: Callable[[str], str] | None = None) -> None:
        """complete：``(dialog_text) -> raw_json`` 的 LLM 回调；
        也可在调用时按次传入。
        """
        self._complete = complete
        # 提取过程统计（线程安全；供编排方按「调用前后快照差」记账，见
        # StrucMemService 提取账日志 —— 校准分段参数/成本记账用）
        self._stats_lock = threading.Lock()
        self._stats: dict[str, int] = {
            'llm_calls': 0,
            'trunc_empties': 0,
            'split_recursions': 0,
            'repair_calls': 0,
            'repair_ok': 0,
            'degrade_rows': 0,
        }

    # ------------------------------------------------------------------ #
    #  过程统计
    # ------------------------------------------------------------------ #
    def stats_snapshot(self) -> dict[str, int]:
        """当前累计计数快照（调用方自行做前后差 = 本次调用账目）。"""
        with self._stats_lock:
            return dict(self._stats)

    def stats_delta(self, base: dict[str, int]) -> dict[str, int]:
        """相对某次快照的增量（调用方在 extract_structured_facts 前后各取一次）。"""
        current = self.stats_snapshot()
        return {key: current[key] - base.get(key, 0) for key in current}

    def _bump(self, name: str, count: int = 1) -> None:
        with self._stats_lock:
            self._stats[name] = self._stats.get(name, 0) + count

    # ------------------------------------------------------------------ #
    #  LLM 输出清洗与校验
    # ------------------------------------------------------------------ #
    @staticmethod
    def validate_response(raw_json: str) -> list[MemoryFact]:
        """清洗并校验 LLM 返回；非法输入返回 []（触发上层重试/降级）。"""
        try:
            # 0. 清洗：去 markdown 代码块（```json ... ```）与首尾空白
            cleaned = (raw_json or '').strip()
            if cleaned.startswith('```'):
                cleaned = cleaned.strip('`').strip()
                if cleaned.lower().startswith('json'):
                    cleaned = cleaned[4:].strip()
            # 1. 解析 JSON
            payload = json.loads(cleaned)
            _logger.debug(f'解析的 JSON 数据: {payload}')
            # 2. Pydantic 校验结构：数组 [{fact,category,key,value,confidence},...]
            #    或 {"facts": [...]} dict 包装
            if isinstance(payload, list):
                validated = MemoryFacts(facts=payload)
            else:
                validated = MemoryFacts(**payload)
            # 3. 业务规则：分类白名单 + confidence ∈ [0,1]
            allowed = _active_categories()
            for fact in validated.facts:
                if fact.category not in allowed:
                    raise ValueError(f'Unknown category: {fact.category}')
                if not 0 <= fact.confidence <= 1:
                    raise ValueError(f'Confidence out of range: {fact.confidence}')
            return validated.facts
        except (json.JSONDecodeError, ValidationError, ValueError) as error:
            _logger.error(f'验证失败: {error}')
            _logger.debug(f'原始响应: {raw_json}')
            return []

    # ------------------------------------------------------------------ #
    #  长对话分段
    # ------------------------------------------------------------------ #
    @staticmethod
    def chunk_dialog(
        dialog_text: str,
        max_chars: int | None = None,
        max_msgs: int | None = None,
        overlap: int | None = None,
    ) -> list[str]:
        """按消息分段（双维：字符数 OR 消息数任一超限即切），段间保留冗余。

        冗余保证落在分段边界附近的信息不被切掉，两边都能提取到。
        双维依据（方案：事实提取鲁棒性与成本优化 §3.2）：输出预算 8192 tokens
        是硬约束，而产出需求由「事实条数 ≈ 消息数」决定——仅按字符切会漏掉
        「消息密集但每条短」的段（layer2 中长会话崩因），仅按消息切会漏掉
        「少数超长消息」；两维 OR 语义使两盲区互不穿透。
        """
        max_chars = max_chars or memory_settings.DEEPSEEK_EXTRACT_MAX_CHARS
        max_msgs = (
            max_msgs if max_msgs is not None else memory_settings.DEEPSEEK_EXTRACT_MAX_MSGS
        )
        overlap = (
            overlap if overlap is not None else memory_settings.DEEPSEEK_EXTRACT_OVERLAP
        )
        messages = dialog_text.split('\n')
        if len(dialog_text) <= max_chars and len(messages) <= max_msgs:
            return [dialog_text]
        chunks: list[str] = []
        current_chunk: list[str] = []
        current_length = 0
        for message in messages:
            if current_chunk and (
                current_length + len(message) > max_chars
                or len(current_chunk) >= max_msgs
            ):
                chunks.append('\n'.join(current_chunk))
                # 冗余：保留本段末尾 overlap 条消息作为下一段开头
                overlap_keep = max(0, len(current_chunk) - overlap)
                current_chunk = current_chunk[overlap_keep:]
                current_length = sum(len(line) for line in current_chunk)
            current_chunk.append(message)
            current_length += len(message)
        if current_chunk:
            chunks.append('\n'.join(current_chunk))
        return chunks

    # ------------------------------------------------------------------ #
    #  LLM 提取（单段 + 编排）
    # ------------------------------------------------------------------ #
    def _resolve_complete(
        self,
        complete: Callable[[str], str] | None = None,
    ) -> Callable[[str], str]:
        complete_fn = complete or self._complete
        if complete_fn is None:
            raise ValueError('FactExtractor 需要 LLM complete 回调（构造或调用时传入）')
        return complete_fn

    @staticmethod
    def _split_text(text: str) -> tuple[str, str] | None:
        """消息中点对半切（截断空返回的切段递归用）。

        少于 2 条消息或任一侧为空 → None（不可切）。
        """
        lines = text.split('\n')
        if len(lines) < 2:
            return None
        mid = len(lines) // 2
        left = '\n'.join(lines[:mid])
        right = '\n'.join(lines[mid:])
        if not left or not right:
            return None
        return left, right

    def extract_chunk(
        self,
        text: str,
        retries: int = 2,
        complete: Callable[[str], str] | None = None,
        _depth: int = 0,
    ) -> list[MemoryFact]:
        """对单个分段提取结构化事实（校验失败/异常按 retries 重试）。

        失败分流（方案：事实提取鲁棒性与成本优化，2026-09-09）：
        - 非空但解析失败（典型为 max_tokens 截断的 ``Unterminated string``），
          且回调具备 ``repair(partial_json)`` → 先让模型**修复/续写**（秒级）；
        - **空 content + finish_reason=length**（截断到 content 空，无法 repair）
          → 消息中点对半切段递归重提（≤ ``MAX_TRUNC_SPLIT_DEPTH`` 层）——
          用「更小的段」而非「同样的段再试」；
        - 其余失败按 retries 整段重试（截断确定性已证，默认仅 1 次整段重试）。
        """
        complete_fn = self._resolve_complete(complete)
        repair_callback = getattr(complete_fn, 'repair', None)
        outcome_call = getattr(complete_fn, 'outcome', None)
        for attempt in range(retries):
            try:
                if outcome_call is not None:
                    outcome = outcome_call(text)
                    raw_json = outcome.content
                    finish_reason = outcome.finish_reason
                else:
                    raw_json = complete_fn(text)
                    finish_reason = None
                self._bump('llm_calls')
                facts = self.validate_response(raw_json)
                if facts:
                    return facts
                if not raw_json or not raw_json.strip():
                    if finish_reason == 'length' and _depth < MAX_TRUNC_SPLIT_DEPTH:
                        halves = self._split_text(text)
                        if halves is not None:
                            self._bump('trunc_empties')
                            self._bump('split_recursions')
                            left, right = halves
                            merged = self.dedup_facts(
                                self.extract_chunk(
                                    left, retries=1, complete=complete_fn,
                                    _depth=_depth + 1,
                                )
                                + self.extract_chunk(
                                    right, retries=1, complete=complete_fn,
                                    _depth=_depth + 1,
                                )
                            )
                            if merged:
                                return merged
                            # 两半仍全空：截断为确定性失败，整段再试无信息增益，
                            # 直接放弃该段交给上层降级（不再走 retries 整段重试）
                            _logger.warning(
                                f'截断空返回，对半切段仍全空（depth={_depth}），'
                                '放弃该段（降级）'
                            )
                            return []
                        self._bump('trunc_empties')
                        _logger.warning(
                            f'截断空返回且不可切段（depth={_depth}），'
                            '放弃该段（降级）'
                        )
                        return []
                    # 真偶发空返回（无 finish 信息或非 length）
                    _logger.warning(
                        f'第 {attempt + 1} 次提取返回空'
                        f'（finish={finish_reason}），整段重试中...'
                    )
                    continue
                # 非空但解析失败：尝试 repair 续写（若回调支持）
                if repair_callback is not None:
                    try:
                        _logger.warning(
                            f'第 {attempt + 1} 次输出解析失败，尝试 repair 续写 '
                            f'（len={len(raw_json)}）...'
                        )
                        self._bump('repair_calls')
                        repaired_json = repair_callback(raw_json)
                        repaired_facts = self.validate_response(repaired_json)
                        if repaired_facts:
                            self._bump('repair_ok')
                            _logger.info(
                                f'repair 成功: {len(repaired_facts)} 条'
                                f'（原始 len={len(raw_json)}'
                                f' → 修复 len={len(repaired_json)}）'
                            )
                            return repaired_facts
                        _logger.warning('repair 输出仍解析失败，回退整段重试')
                    except Exception as error:
                        _logger.error(f'repair 调用失败，回退整段重试: {error}')
                else:
                    _logger.warning(
                        f'第 {attempt + 1} 次提取验证失败，整段重试中...'
                    )
            except Exception as error:
                _logger.error(f'Attempt {attempt + 1} failed: {error}')
        return []

    def extract_structured_facts(
        self,
        dialog_text: str,
        retries: int = 2,
        complete: Callable[[str], str] | None = None,
    ) -> list[MemoryFact]:
        """对整段对话提取结构化事实（分段 + 并行 + 全失败降级）。

        返回提取结果（长对话已跨段去重）；全部失败时降级为
        ``raw_conversation`` 原始对话事实（confidence=0.1），保证不空手。
        ``retries`` 默认 2 = 初始 1 次 + 至多 1 次整段重试（截断确定性已证，
        更多整段重试无信息增益，见方案：事实提取鲁棒性与成本优化）。
        """
        chunks = self.chunk_dialog(dialog_text)
        if len(chunks) <= 1:
            # 短对话：单次提取（原有重试 + 降级）
            facts = self.extract_chunk(dialog_text, retries=retries, complete=complete)
            if facts:
                return facts
            _logger.error('提取失败，降级存储原始对话')
            degraded = self._degrade_fact(dialog_text)
            self._bump('degrade_rows', len(degraded))
            return degraded

        # 长对话：分段提取，每段独立调用 LLM（并行），结果合并去重
        all_facts: list[MemoryFact] = []
        _logger.info(f'分段提取开始: {len(chunks)} 段（并行 {min(4, len(chunks))} 路）')
        with ThreadPoolExecutor(max_workers=min(4, len(chunks))) as executor:
            future_map = {
                executor.submit(self.extract_chunk, chunk, retries, complete): index
                for index, chunk in enumerate(chunks, 1)
            }
            for future in as_completed(future_map):
                chunk_index = future_map[future]
                _logger.info(f'提取分段 {chunk_index}/{len(chunks)} 完成')
                all_facts.extend(future.result())
        deduped = self.dedup_facts(all_facts)
        if not deduped:
            _logger.error('全部分段提取失败，降级存储原始对话')
            degraded = self._degrade_fact(dialog_text)
            self._bump('degrade_rows', len(degraded))
            return degraded
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
        seen_signatures: set[tuple[str, str, str]] = set()
        result: list[MemoryFact] = []
        for fact in facts:
            signature = (fact.category, fact.key, fact.value)
            if signature in seen_signatures:
                continue
            seen_signatures.add(signature)
            result.append(fact)
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
        seen_prefixes: set[str] = set()
        for line in dialog_text.split('\n'):
            line = line.strip()
            if not line:
                continue
            message_content = line
            try:
                message_obj = json.loads(line)
                if isinstance(message_obj, dict) and message_obj.get('content'):
                    message_content = message_obj['content']
                elif isinstance(message_obj, list):
                    message_content = ' '.join(
                        str(item.get('content', ''))
                        for item in message_obj
                        if isinstance(item, dict)
                    )
            except Exception:
                pass
            # 按句末标点拆句，逐句判断是否含关键数值 token
            sentences = re.split(r'(?<=[.!?。！？])\s+', message_content)
            for sentence in sentences:
                sentence = sentence.strip()
                if len(sentence) < 8 or len(sentence) > 600:
                    continue
                if not _NUMERIC_TOKENS.search(sentence):
                    continue
                signature = sentence[:120]
                if signature in seen_prefixes:
                    continue
                seen_prefixes.add(signature)
                category = (
                    'finance'
                    if re.search(
                        r'\$\s?\d|%|\b\d{4}-\d{4}-\d{4}-\d{4}\b', sentence
                    )
                    else 'other'
                )
                # key 用内容指纹而非聚合的 'verbatim_record'：多条兜底句共享一个
                # key 会在 (user, key) 冲突 upsert 时互相覆盖（库里只留最后一条，
                # 其余全部丢失——曾导致 13/17/20 的关键数字句在入库后被消灭）。
                # 内容指纹：同句重跑同 key（幂等覆盖），异句不同 key（互不踩踏）。
                digest = hashlib.sha1(sentence.encode('utf-8')).hexdigest()[:12]
                facts.append(
                    MemoryFact(
                        fact=sentence,
                        category=category,
                        key=f'verbatim_{digest}',
                        value=sentence,
                        confidence=0.85,
                    )
                )
                if len(facts) >= max_facts:
                    return facts
        return facts

    @staticmethod
    def prune_redundant_verbatim(
        fallback_facts: list[MemoryFact],
        llm_facts: list[MemoryFact],
    ) -> list[MemoryFact]:
        """R1 覆盖去重：verbatim 数值 token 全被 LLM 结构化事实覆盖 → 不存。

        兜底是"结构化漏提数字"的保险——只应保结构化**没覆盖**的信息。若一句兜底
        句里的数值全部已由结构化事实表达（同一 token 集合），该句不提供新信息，
        丢弃（安全：删的只是重复信息，唯一载体不受影响，无负收益）。

        降级保护：LLM 提取整体失败时 llm_facts 是 ``raw_conversation`` 原文降级
        （value=整段对话，token 覆盖一切）——此时不做剪枝，兜底照存（保险语义）。
        """
        if not llm_facts:
            return fallback_facts
        if any(fact.key == 'raw_conversation' for fact in llm_facts):
            return fallback_facts
        structured_tokens: set[str] = set()
        for fact in llm_facts:
            structured_tokens |= fact_tokens(f'{fact.fact} {fact.value or ""}')
        kept_facts: list[MemoryFact] = []
        for fact in fallback_facts:
            tokens = fact_tokens(f'{fact.fact} {fact.value or ""}')
            if tokens and tokens <= structured_tokens:
                continue
            kept_facts.append(fact)
        return kept_facts
