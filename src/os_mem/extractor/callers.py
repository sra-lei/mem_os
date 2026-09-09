"""provider 自愈 extraction caller —— 「LLM 调用 + 恢复策略」收敛于此（架构搬迁·等价重构）。

背景（docs/方案-提取任务与LLM模型画像解耦.md §2 v2）：恢复策略（截断检测、
repair、对半切段、整段重试）是「怎么跟某个模型要到合法结果」的实现细节——每个
provider/model 各不同，收敛在 caller 内部，**不暴露给任务层**，也不进通用 schema。
任务层（FactExtractor）只认识干净契约 ``extract(dialog_text, *, validate) -> CallResult``
（facts|None = 合法结果或明确全败，stats = 遥测）；validate（schema 校验权：分类白名单/
confidence 边界）由任务注入。分段编排/去重/verbatim 兜底/降级仍是任务层语义。

- ``_ExtractionCore``：单一恢复循环。语义 = 迁出前的 ``FactExtractor.extract_chunk``
  现行实现逐行等价复刻（不优化不改行为，日志文案与旧实现逐字一致）；低层能力
  （generate / repair_fn / dedup_fn / split_fn / max_split_depth）由构造注入，
  供各 provider 内部策略拼装。
- ``DeepSeekExtractionCaller``：deepseek 提供方首版 caller——prompt 拼装复用
  ``os_mem.extractor.prompt`` 的 SYSTEM_PROMPT/REPAIR_PROMPT 渲染（词表/max_facts
  全部原样），generate 走 ``client.chat_outcome``（json_object 响应格式）。
- ``build_extraction_caller(client)``：工厂（现阶段 single provider；多路注册表留待
  方案 §3 扩展）。

兼容面：``DeepSeekExtractionCaller`` 保留 ``outcome()`` / ``__call__()`` / ``repair()``
鸭子接口（与迁出前的 ``prompt._ExtractComplete`` 同构）——AB 脚本 Recorder 依赖
``.outcome(...)`` 返回带 ``.usage`` 的 ChatOutcome，且逐字读 ``__call__ = outcome().content``、
``repair(partial)`` 走 ``client.chat``；旧调用方（build_extract_complete / complete 注入）
同样经此接口工作。

依赖方向（无环）：callers → prompt → （configs / infra.llm.base_client / utils.prompt_fp）；
callers / fact_extractor → profile → （configs.mem_settings，纯数据不反向依赖）；
callers / fact_extractor → common（共享纯函数/常量，common 不 import 包内其他模块）；
fact_extractor → callers；prompt 不反向 import fact_extractor/callers/profile（其兼容构造在
函数体内延迟 import，见 prompt.build_extract_complete）。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from os_mem.extractor.common import (
    EXTRACTION_STATS_KEYS,
    MAX_TRUNC_SPLIT_DEPTH,
    dedup_facts,
    split_text_midpoint,
)
from os_mem.extractor.profile import ModelProfile, build_default_profile
from os_mem.extractor.prompt import build_extract_messages, build_repair_messages
from os_mem.infra.llm.base_client import ChatClient, ChatOutcome
from os_mem.infra.logger import get_logger

_logger = get_logger('os_mem.extractor.callers')


# ------------------------------------------------------------------ #
#  契约
# ------------------------------------------------------------------ #
def _empty_stats() -> dict[str, int]:
    """恢复循环遥测计数（keys 与 FactExtractor 实例计数一致；degrade_rows 属任务层）。"""
    return {key: 0 for key in EXTRACTION_STATS_KEYS}


@dataclass
class CallResult:
    """单段提取结果：facts|None（None = 全败/干净失败），stats = 本次调用遥测。"""

    facts: list | None
    stats: dict[str, int] = field(default_factory=_empty_stats)


# ------------------------------------------------------------------ #
#  单一恢复循环
# ------------------------------------------------------------------ #
class _ExtractionCore:
    """单段提取的模型恢复循环（语义逐条等价于旧 FactExtractor.extract_chunk）。

    构造收低层能力（provider 内部策略拼装点）：
    - ``generate(text) -> (content, finish_reason, usage_tokens)``：单次 LLM 调用
      （含截断信号与 usage token 数）；usage_tokens = (in, out) | None——
      每次 generate 成功后累计进 stats['in_tokens']/['out_tokens']（None→0，
      截断空返回等一切路径同样计入，递归/重试自然累计，见观测增强 §4 步骤 4）；
    - ``repair_fn(partial_json) -> str``：修复/续写（可选；无则解析失败走整段重试）；
    - ``dedup_fn(facts) -> facts``：切段两半结果合并去重；
    - ``split_fn(text) -> (left, right) | None``：截断空返回的对半切段；
    - ``max_split_depth``：切段递归最大层数（默认 1）。

    恢复语义（对照旧 extract_chunk 逐行复刻）：
    - llm_calls 每次 generate +1（含递归切段调用）；
    - validate 注入调用；合法非空即返回；
    - 非空但解析失败：有 repair_fn → 尝试（repair_calls 在调用前 +1，成功 repair_ok +1）
      → 成功返回 / 失败回退整段重试（retries 循环）；
    - 空 content + finish_reason == 'length'：depth < max 且可切（split_fn 返回两半）
      → trunc_empties+1、split_recursions+1，两半各以 retries=1 递归 extract
      → dedup 合并 → 非空返回；两半仍全空 → 返回 None（不再整段重试，交上层降级）；
      不可切 → trunc_empties+1 返回 None；
    - 空 + 非 length（或无 finish 信息）→ 整段重试循环（warning 文案同旧实现）；
    - 日志 warning/info/error 文案与旧 extract_chunk 完全一致（loguru f-string）。
    """

    def __init__(
        self,
        generate: Callable[
            [str], tuple[str, str | None, tuple[int, int] | None]
        ],
        *,
        repair_fn: Callable[[str], str] | None = None,
        dedup_fn: Callable[[list], list] | None = None,
        split_fn: Callable[[str], tuple[str, str] | None] | None = None,
        max_split_depth: int = MAX_TRUNC_SPLIT_DEPTH,
    ) -> None:
        self._generate = generate
        self._repair_fn = repair_fn
        self._dedup_fn = dedup_fn
        self._split_fn = split_fn
        self._max_split_depth = max_split_depth

    def extract(
        self,
        text: str,
        validate: Callable[[str], list],
        retries: int = 2,
    ) -> tuple[list | None, dict[str, int]]:
        """对单段文本跑完整恢复循环；返回 (facts|None, 本次遥测计数)。"""
        stats = _empty_stats()
        facts = self._recover(text, validate, retries, depth=0, stats=stats)
        return facts, stats

    def _recover(
        self,
        text: str,
        validate: Callable[[str], list],
        retries: int,
        depth: int,
        stats: dict[str, int],
    ) -> list | None:
        """恢复循环（等价旧 extract_chunk 主体；stats 跨递归共享累计）。"""
        for attempt in range(retries):
            try:
                raw_json, finish_reason, usage_tokens = self._generate(text)
                stats['llm_calls'] += 1
                # usage token 记账：generate 一旦成功返回即累计（含截断空返回；
                # None → 0）。递归/重试全路径经本函数，自然计入同一 stats。
                if usage_tokens is not None:
                    input_tokens, output_tokens = usage_tokens
                    stats['in_tokens'] += input_tokens
                    stats['out_tokens'] += output_tokens
                facts = validate(raw_json)
                if facts:
                    return facts
                if not raw_json or not raw_json.strip():
                    if (
                        finish_reason == 'length'
                        and depth < self._max_split_depth
                        and self._split_fn is not None
                    ):
                        halves = self._split_fn(text)
                        if halves is not None:
                            stats['trunc_empties'] += 1
                            stats['split_recursions'] += 1
                            left, right = halves
                            # 两半各以 retries=1 递归（与旧实现一致：不再整段重试）
                            left_facts = (
                                self._recover(
                                    left, validate, retries=1, depth=depth + 1,
                                    stats=stats,
                                )
                                or []
                            )
                            right_facts = (
                                self._recover(
                                    right, validate, retries=1, depth=depth + 1,
                                    stats=stats,
                                )
                                or []
                            )
                            merged = left_facts + right_facts
                            if self._dedup_fn is not None:
                                merged = self._dedup_fn(merged)
                            if merged:
                                return merged
                            # 两半仍全空：截断为确定性失败，整段再试无信息增益，
                            # 直接放弃该段交给上层降级（不再走 retries 整段重试）
                            _logger.warning(
                                f'截断空返回，对半切段仍全空（depth={depth}），'
                                '放弃该段（降级）'
                            )
                            return None
                        stats['trunc_empties'] += 1
                        _logger.warning(
                            f'截断空返回且不可切段（depth={depth}），'
                            '放弃该段（降级）'
                        )
                        return None
                    # 真偶发空返回（无 finish 信息或非 length）
                    _logger.warning(
                        f'第 {attempt + 1} 次提取返回空'
                        f'（finish={finish_reason}），整段重试中...'
                    )
                    continue
                # 非空但解析失败：尝试 repair 续写（若注入）
                if self._repair_fn is not None:
                    try:
                        _logger.warning(
                            f'第 {attempt + 1} 次输出解析失败，尝试 repair 续写 '
                            f'（len={len(raw_json)}）...'
                        )
                        stats['repair_calls'] += 1
                        repaired_json = self._repair_fn(raw_json)
                        repaired_facts = validate(repaired_json)
                        if repaired_facts:
                            stats['repair_ok'] += 1
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
        return None


# ------------------------------------------------------------------ #
#  DeepSeek 提供方 caller
# ------------------------------------------------------------------ #
class DeepSeekExtractionCaller:
    """DeepSeek 自愈提取 caller：恢复策略=代码（prompt 数据仍来自 extractor.prompt）。

    - ``extract(dialog_text, *, validate, retries=2)``：任务侧唯一入口
      → CallResult{facts|None, stats}；validate 由任务注入；
    - ``outcome(dialog_text)`` / ``__call__(dialog_text)`` / ``repair(partial_json)``：
      旧鸭子接口保留（与迁出前 ``prompt._ExtractComplete`` 同构，供 AB 脚本 Recorder
      与旧 complete 调用方兼容）。
    """

    def __init__(
        self, client: ChatClient, profile: ModelProfile | None = None
    ) -> None:
        # 画像：不传 → settings 现值固化默认（行为与现状逐字节等价）；
        # system/repair prompt 字段为 None = 用 prompt.py 现行单源模板。
        self._profile = profile or build_default_profile()
        self._client = client
        self._response_format = {'type': 'json_object'}
        self._core = _ExtractionCore(
            generate=self._generate,
            repair_fn=self.repair,
            dedup_fn=dedup_facts,
            split_fn=split_text_midpoint,
            max_split_depth=MAX_TRUNC_SPLIT_DEPTH,
        )

    # ---- 任务侧干净契约 ------------------------------------------ #
    def extract(
        self,
        dialog_text: str,
        *,
        validate: Callable[[str], list],
        retries: int = 2,
    ) -> CallResult:
        facts, stats = self._core.extract(dialog_text, validate=validate, retries=retries)
        return CallResult(facts=facts, stats=stats)

    # ---- 低层能力（prompt 拼装复用 extractor.prompt，渲染逻辑零改动）---- #
    def _extract_messages(self, dialog_text: str) -> list[dict[str, str]]:
        """提取调用的 messages 拼装：{max_facts} 按本 caller 画像取值
        （默认画像 = settings 现值，与旧无参渲染逐字节一致）。"""
        return build_extract_messages(dialog_text, max_facts=self._profile.max_facts)

    def _generate(
        self, dialog_text: str
    ) -> tuple[str, str | None, tuple[int, int] | None]:
        chat_outcome = getattr(self._client, 'chat_outcome', None)
        if chat_outcome is not None:
            outcome = chat_outcome(
                self._extract_messages(dialog_text),
                response_format=self._response_format,
            )
            return (
                outcome.content,
                outcome.finish_reason,
                _usage_token_counts(outcome.usage),
            )
        # 无 chat_outcome 的 client：无截断信号，退化为旧整段重试语义
        return (
            self._client.chat(
                self._extract_messages(dialog_text),
                response_format=self._response_format,
            ),
            None,
            None,
        )

    # ---- 旧鸭子接口（AB Recorder / 旧调用方兼容） ------------------- #
    def outcome(self, dialog_text: str) -> ChatOutcome:
        """带 finish_reason 的提取调用（截断路由需要；兼容旧 _ExtractComplete）。

        client 支持 ``chat_outcome`` 时返回完整 outcome（含 finish_reason，length
        截断可由恢复循环识别）；否则退回 ``chat`` 包一层（无 finish 信息）。
        """
        chat_outcome = getattr(self._client, 'chat_outcome', None)
        if chat_outcome is not None:
            return chat_outcome(
                self._extract_messages(dialog_text),
                response_format=self._response_format,
            )
        return ChatOutcome(
            self._client.chat(
                self._extract_messages(dialog_text),
                response_format=self._response_format,
            )
        )

    def __call__(self, dialog_text: str) -> str:
        return self.outcome(dialog_text).content

    def repair(self, partial_json: str) -> str:
        return self._client.chat(
            build_repair_messages(partial_json, max_facts=self._profile.max_facts),
            response_format=self._response_format,
        )


def _usage_token_counts(usage: Any) -> tuple[int, int] | None:
    """从 chat outcome 的 usage 取 (input, output) token 数；usage 缺失 → None。

    与旧 ``_ExtractComplete``/AB Recorder 口径一致（getattr 容错，缺属性按 0）：
    恢复核心对每次 generate 累计 token 数（None → 0），见方案 §4 步骤 4。
    """
    if usage is None:
        return None
    input_tokens = getattr(usage, 'prompt_tokens', 0) or 0
    output_tokens = getattr(usage, 'completion_tokens', 0) or 0
    return input_tokens, output_tokens


def build_extraction_caller(
    client: ChatClient, profile: ModelProfile | None = None
) -> DeepSeekExtractionCaller:
    """构造 provider 自愈提取 caller（现阶段 single provider；注册表留待扩展）。

    ``profile`` 缺省 → settings 现值默认画像（行为与现状等价）；显式画像的
    max_facts 渲染进 prompt、chunk_caps 由任务层分段取用（见方案 §4 步骤 3）。
    ``build_extract_complete``（旧 client → complete 回调适配）与
    ``build_extraction_caller`` 现在返回同一类实例——旧回调用法（outcome/__call__/
    repair 鸭子）与任务侧新契约（extract）并存，见方案 §4 步骤 1-2。
    """
    return DeepSeekExtractionCaller(client, profile=profile)
