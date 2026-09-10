"""事实提取执行器（FactExtractor）—— 结构化记忆提取链路的确定性内聚封装。

归属：``os_mem.extractor`` 记忆提取域（2026-09-08 由 os_mem.utils 迁入
extraction/，2026-09-09 包更名 extraction→extractor 且 extractor.py 更名
fact_extractor.py，见 AGENTS.md 目录地图）。定位：被编排的**领域执行器**——不属 utils 小工具
（它是提取链路核心），也不属 core 业务编排（编排在 core/services/struc_mem_service，
存储/网络副作用为零，LLM 通过注入的 ``complete(text) -> raw_json`` 回调（旧路径，
测试/AB 脚本用）或 provider 自愈 caller（``extract(dialog_text, *, validate)``
干净契约，见 callers.py；恢复策略=provider 内部代码）使用，便于单测与替换）：

- ``validate_response``   ：LLM 原始输出清洗（markdown 围栏/包装格式）与校验
  （分类白名单、confidence 边界、非法 JSON → 空列表触发重试）
- ``chunk_dialog``        ：长对话按消息分段 + 段间冗余重叠（边界信息不切丢）
- ``extract_chunk``       ：单段提取（薄委托兼容层——repair 续写/截断切段/整段重试等
  恢复策略已收敛于 ``callers.ExtractionCore``，语义等价迁移见
  docs/方案-提取任务与LLM模型画像解耦.md）
- ``extract_structured_facts``：分段编排（短对话单次 / 长对话并行）+ 全失败降级
  （可注入 provider 自愈 caller：每段走 ``caller.extract(dialog_text, *, validate)``）
- ``dedup_facts``         ：按 (category, key, value) 跨段去重（实现收拢于
  ``os_mem.extractor.common.dedup_facts``，单一实现源）

不依赖 LLM 的正则提取（verbatim 数字句兜底 / R1 覆盖剪枝 / 数值 token 口径）
整合在 ``regular_extractor.py`` 的 ``RegularExtractor``（2026-09-10 自本类静态
方法与 tokens.py 收拢）。

命名规范：本包遵守"变量不用纯缩写"硬规则（snake_case 全称，如 signature /
tokens / kept_facts），新增代码保持同水准。常量与默认值来自 memory_settings 或
显式参数；与 caller 共享的纯函数/常量（split_text_midpoint / dedup_facts /
MAX_TRUNC_SPLIT_DEPTH / 统计 keys）见 ``os_mem.extractor.common``。
"""

from __future__ import annotations

import json
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from pydantic import ValidationError

from os_mem.configs.mem_settings import memory_settings
from os_mem.extractor.callers import ExtractionCore
from os_mem.extractor.common import (
    EXTRACTION_STATS_KEYS,
    MAX_TRUNC_SPLIT_DEPTH,
    dedup_facts as _dedup_facts_by_signature,
    split_text_midpoint as _split_text_midpoint,
)
from os_mem.extractor.models import ChunkCaps
from os_mem.infra.logger import get_logger
from os_mem.models.mem_models import MemoryFact, MemoryFacts

_logger = get_logger('os_mem.extractor.fact_extractor')


def _active_categories() -> frozenset[str]:
    """校验白名单：读 fact_category 词表 active 集（词表故障回退内置 10 类）。

    替代原硬编码 ALLOWED_CATEGORIES 常量 —— 词表化后停用/增补 category 即时生效，
    见 docs/方案-事实category与key词表管理.md。每次调用读表（本地 SQLite，廉价），
    不做缓存：管理窗口改词表后校验行为立即一致。
    """
    from os_mem.vocab import list_active_categories

    return frozenset(list_active_categories())


def _complete_to_generate(
    complete: Callable[[str], str],
) -> Callable[[str], tuple[str, str | None, tuple[int, int] | None]]:
    """把旧 complete 鸭子接口包成恢复核心需要的低层 generate 三元组
    (content, finish_reason, usage_tokens)。

    优先走 ``outcome()``（带 finish_reason，length 截断可路由）；usage_tokens
    恒 None——旧 complete 路径无 usage 口径，token 记账仅 caller 的
    chat_outcome 路径提供。纯 ``__call__`` 回调（无 outcome）→
    finish_reason=None（旧整段重试语义，见 FakeComplete）。
    """
    outcome_call = getattr(complete, 'outcome', None)
    if outcome_call is not None:

        def _generate_with_outcome(
            text: str,
        ) -> tuple[str, str | None, tuple[int, int] | None]:
            outcome = outcome_call(text)
            return outcome.content, outcome.finish_reason, None

        return _generate_with_outcome

    def _generate_plain(
        text: str,
    ) -> tuple[str, str | None, tuple[int, int] | None]:
        return complete(text), None, None

    return _generate_plain


class FactExtractor:
    """事实抽取链路的内聚执行器（线程安全：除 LLM 回调外无共享可变状态）。"""

    def __init__(self, complete: Callable[[str], str] | None = None) -> None:
        """complete：``(dialog_text) -> raw_json`` 的 LLM 回调；
        也可在调用时按次传入。
        """
        self._complete = complete
        # 提取过程统计（线程安全；供编排方按「调用前后快照差」记账，见
        # StrucMemService 提取账日志 —— 校准分段参数/成本记账用）。
        # keys：恢复循环遥测共享自 os_mem.extractor.common.EXTRACTION_STATS_KEYS
        # （llm_calls/trunc_empties/split_recursions/repair_calls/repair_ok +
        # in_tokens/out_tokens token 记账），degrade_rows 属任务层语义由本实例追加
        self._stats_lock = threading.Lock()
        self._stats: dict[str, int] = {
            key: 0 for key in (*EXTRACTION_STATS_KEYS, 'degrade_rows')
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
        实现收拢于 ``os_mem.extractor.common.split_text_midpoint``（caller
        切段递归与任务侧共用同一实现，单一实现防漂移）。
        """
        return _split_text_midpoint(text)

    def extract_chunk(
        self,
        text: str,
        retries: int = 2,
        complete: Callable[[str], str] | None = None,
    ) -> list[MemoryFact]:
        """对单个分段提取结构化事实（薄委托兼容层——恢复策略已迁至 caller 核心）。

        恢复循环（repair 续写 / 截断对半切段 / 整段重试）自 2026-09-09 起收敛于
        ``os_mem.extractor.callers.ExtractionCore``（等价迁移：不优化不改行为，
        日志文案逐字一致，见 docs/方案-提取任务与LLM模型画像解耦.md §2 v2 / §4
        步骤 1-2）。本方法保留旧签名作为兼容层：把 ``complete`` 的鸭子能力
        （``outcome`` / ``__call__`` / ``repair``，缺哪个退哪个）包成低层
        ``generate`` 喂给核心，并把核心返回的遥测累加进实例计数
        （stats_snapshot/stats_delta 口径不变）。纯 ``__call__`` 无 outcome/repair
        的 complete → generate 返回 (content, None, None)、repair_fn=None
        → 整段重试语义。
        """
        complete_fn = self._resolve_complete(complete)
        core = ExtractionCore(
            generate=_complete_to_generate(complete_fn),
            repair_fn=getattr(complete_fn, 'repair', None),
            dedup_fn=self.dedup_facts,
            split_fn=self._split_text,
            max_split_depth=MAX_TRUNC_SPLIT_DEPTH,
        )
        facts, stats = core.extract(
            text, validate=self.validate_response, retries=retries
        )
        self._absorb_stats(stats)
        return facts or []

    def _absorb_stats(self, stats: dict[str, int]) -> None:
        """把一次 caller/核心调用的遥测累加进实例计数（快照差口径不变）。

        caller 模式（extract_structured_facts caller 路径）逐 chunk 回收；
        complete 委托路径（extract_chunk）在单段恢复循环结束后回收。
        """
        for name, count in stats.items():
            if count:
                self._bump(name, count)

    def _extract_chunk_via_caller(
        self,
        caller: Any,
        chunk_text: str,
        retries: int,
    ) -> list[MemoryFact]:
        """caller 模式单段提取（长对话并行 worker）：注入任务侧校验并回收遥测。

        caller 须满足 ``extract(dialog_text, *, validate, retries) -> CallResult``
        契约（见 models.CallResult）；facts 为 None = 全败（等效旧 extract_chunk
        返回 []，上层降级逻辑不变）。
        """
        result = caller.extract(
            chunk_text, validate=self.validate_response, retries=retries
        )
        self._absorb_stats(result.stats)
        return result.facts or []

    def extract_structured_facts(
        self,
        dialog_text: str,
        retries: int = 2,
        complete: Callable[[str], str] | None = None,
        caller: Any | None = None,
        chunk_caps: ChunkCaps | None = None,
    ) -> list[MemoryFact]:
        """对整段对话提取结构化事实（分段 + 并行 + 全失败降级）。

        返回提取结果（长对话已跨段去重）；全部失败时降级为
        ``raw_conversation`` 原始对话事实（confidence=0.1），保证不空手。
        ``retries`` 默认 2 = 初始 1 次 + 至多 1 次整段重试（截断确定性已证，
        更多整段重试无信息增益，见方案：事实提取鲁棒性与成本优化）。

        ``chunk_caps``：分段上限（字符/消息/overlap）——None → settings 现值
        （ChunkCaps.from_settings()，默认路径与现状逐字节等价）；显式传入时
        caller 与 complete 两条路径共用同一分段调用点（方案 §4 步骤 3：
        分段上限改由 profile.chunk_caps 供给任务层）。

        提取回调二选一（2026-09-09 起 caller 优先；恢复策略=provider 内部代码，
        见 docs/方案-提取任务与LLM模型画像解耦.md §2 v2）：
        - ``caller``：provider 自愈提取 caller（满足 ``extract(dialog_text, *,
          validate, retries) -> CallResult`` 契约）——每段走 caller.extract，
          validate 由本任务注入（= validate_response），并把每段返回的 stats
          累加进实例计数（stats_snapshot/delta 口径不变，含 in/out token 记账）；
        - ``complete``：旧回调路径（薄委托 extract_chunk，测试 / AB 脚本兼容）。
        """
        # 分段上限：入参优先，None → settings 现值（默认路径与现状逐字节等价）
        chunk_caps = chunk_caps or ChunkCaps.from_settings()
        chunks = self.chunk_dialog(
            dialog_text,
            max_chars=chunk_caps.max_chars,
            max_msgs=chunk_caps.max_msgs,
            overlap=chunk_caps.overlap,
        )
        if len(chunks) <= 1:
            # 短对话：单次提取（原有重试 + 降级）
            if caller is not None:
                result = caller.extract(
                    dialog_text, validate=self.validate_response, retries=retries
                )
                self._absorb_stats(result.stats)
                facts = result.facts or []
            else:
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
            if caller is not None:
                future_map = {
                    executor.submit(
                        self._extract_chunk_via_caller, caller, chunk, retries
                    ): index
                    for index, chunk in enumerate(chunks, 1)
                }
            else:
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
        """全败降级：原始对话按 ≤900 字符确定性切片为多条 fact。

        切片防线（方案：事实提取鲁棒性与成本优化 §3.3）：整段原文可到
        5.5-7.9KB，超过 Milvus ``value`` varchar(1024) 上限——降级是
        「LLM 全挂时保底写库」的路径，不能自己再炸向量写入。

        900 < 1024 留 schema 余量；固定窗口切分 → 同会话重跑产出同 key 集
        （raw_conversation / raw_conversation_2 / …），投影删旧插新幂等收敛。
        SQLite 侧无长度限制，原文完整仍可审计（此防线只保护向量投影）。
        """
        slice_size = 900
        if len(dialog_text) <= slice_size:
            return [
                MemoryFact(
                    fact=f'原始对话: {dialog_text[:200]}...',
                    category='other',
                    key='raw_conversation',
                    value=dialog_text,
                    confidence=0.1,
                )
            ]
        parts = [
            dialog_text[index:index + slice_size]
            for index in range(0, len(dialog_text), slice_size)
        ]
        facts: list[MemoryFact] = []
        for index, part in enumerate(parts, 1):
            key = 'raw_conversation' if index == 1 else f'raw_conversation_{index}'
            facts.append(
                MemoryFact(
                    fact=f'原始对话({index}/{len(parts)}): {part[:180]}...',
                    category='other',
                    key=key,
                    value=part,
                    confidence=0.1,
                )
            )
        return facts

    # ------------------------------------------------------------------ #
    #  去重
    # ------------------------------------------------------------------ #
    @staticmethod
    def dedup_facts(facts: list[MemoryFact]) -> list[MemoryFact]:
        """按 (category, key, value) 去重（分段重叠会导致重复提取）。

        实现收拢于 ``os_mem.extractor.common.dedup_facts``（caller 切段合并与
        任务侧跨段去重共用同一实现，单一实现防漂移）。
        """
        return _dedup_facts_by_signature(facts)
