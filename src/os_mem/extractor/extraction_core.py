"""单一恢复循环（provider 无关；低层能力由具体 caller 注入）。

本模块只承载「怎么跟某个模型要到合法结果」的恢复循环骨架——具体能力
（generate / repair_fn / dedup_fn / split_fn / max_split_depth）由构造注入，
供各 provider 内部策略拼装（见 ``callers/framework.py`` 与
``callers/deepseek_caller.py``）。
"""

from __future__ import annotations

from collections.abc import Callable

from os_mem.extractor.utils.extract_utils import (
    MAX_TRUNC_SPLIT_DEPTH,
    empty_extraction_stats,
)
from os_mem.infra.logger import get_logger

_logger = get_logger('os_mem.extractor.extraction_core')


class ExtractionCore:
    """单段提取的模型恢复循环。

    构造收低层能力（provider 内部策略拼装点）：
    - ``generate(text) -> (content, finish_reason, usage_tokens)``：单次 LLM 调用
      （含截断信号与 usage token 数）；usage_tokens = (in, out) | None——
      每次 generate 成功后累计进 stats['in_tokens']/['out_tokens']（None→0，
      截断空返回等一切路径同样计入，递归/重试自然累计）；
    - ``repair_fn(partial_json) -> str``：修复/续写（可选；无则解析失败走整段重试）；
    - ``dedup_fn(facts) -> facts``：切段两半结果合并去重；
    - ``split_fn(text) -> (left, right) | None``：截断空返回的对半切段；
    - ``max_split_depth``：切段递归最大层数（默认 1）。

    恢复语义：
    - llm_calls 每次 generate +1（含递归切段调用）；
    - validate 注入调用；合法非空即返回；
    - 非空但解析失败：有 repair_fn → 尝试（repair_calls 在调用前 +1，成功
      repair_ok +1）→ 成功返回 / 失败回退整段重试（retries 循环）；
    - 空 content + finish_reason == 'length'：depth < max 且可切（split_fn
      返回两半）→ trunc_empties+1、split_recursions+1，两半各以 retries=1
      递归 extract → dedup 合并 → 非空返回；两半仍全空 → 返回 None（不再整段
      重试，交上层降级）；不可切 → trunc_empties+1 返回 None；
    - 空 + 非 length（或无 finish 信息）→ 整段重试循环。
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
        stats = empty_extraction_stats()
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
        """恢复循环（stats 跨递归共享累计）。"""
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
                            # 两半各以 retries=1 递归（不再整段重试）
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
