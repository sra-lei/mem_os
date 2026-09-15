"""单一恢复循环（provider 无关；低层能力由具体 caller 注入）。

本模块只承载「怎么跟某个模型要到合法结果」的恢复循环骨架——具体能力
（generate / repair_fn / dedup_fn / split_fn / max_split_depth）由构造注入，
供各 provider 内部策略拼装（见 ``callers/base_caller.py`` 与
``callers/deepseek_caller.py``）。
"""

from __future__ import annotations

from collections.abc import Callable

from os_mem.core.extract.utils.extract_utils import (
    empty_extraction_stats,
)
from os_mem.infra.logger import get_logger

_logger = get_logger('os_mem.extractor.extraction_core')

MAX_TRUNC_SPLIT_DEPTH = 1

class ExtractionCore:
    """单段提取的模型恢复循环。

    构造收低层能力（provider 内部策略拼装点）：
    - ``generate(text) -> (content, finish_reason, usage_tokens)``：单次 LLM 调用
      （含截断信号与 usage token 数）；usage_tokens = (in, out) | None——
      每次 generate 成功后累计进 stats['in_tokens']/['out_tokens']（None→0，
      截断空返回等一切路径同样计入，递归/重试自然累计）；
    - ``repair_fn(partial_json) -> str``：修复/续写（可选；无则解析失败走整段重试）；
    - ``dedup_fn(facts) -> facts``：切段两半结果合并去重；
    - ``split_fn(text) -> (left, right) | None``：截断空返回的对半切段。

    切段递归最大层数为本模块常量 ``MAX_TRUNC_SPLIT_DEPTH``（固定 1：每层把段
    再切半，1 层已足够收敛输出预算）。

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

    实现分解（``_recover`` 只留「整段重试」骨架，单次尝试的分支下沉到
    ``_attempt_once`` 一族方法，避免多层嵌套）：
    ``_recover`` → ``_attempt_once`` → ``_recover_empty`` / ``_recover_malformed``
    →（截断可切段时）``_split_and_extract``。
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
    ) -> None:
        self._generate = generate
        self._repair_fn = repair_fn
        self._dedup_fn = dedup_fn
        self._split_fn = split_fn
        self._max_split_depth = MAX_TRUNC_SPLIT_DEPTH

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
        """恢复循环骨架（stats 跨递归共享累计）：每次尝试要么终局、要么再试一次。

        单次尝试内部的判定（截断切段 / repair 续写 / 偶发空返回）见
        ``_attempt_once``；抛错与「本次尝试无效」都只消耗一次 retry。
        """
        for attempt in range(retries):
            try:
                done, facts = self._attempt_once(
                    text, validate, attempt + 1, depth, stats
                )
            except Exception as error:
                _logger.error(f'Attempt {attempt + 1} failed: {error}')
                continue
            if done:
                return facts
        return None

    def _attempt_once(
        self,
        text: str,
        validate: Callable[[str], list],
        attempt_no: int,
        depth: int,
        stats: dict[str, int],
    ) -> tuple[bool, list | None]:
        """单次 generate + validate；返回 ``(是否终局, facts)``。

        - ``(True, facts)``：拿到终局结果（含「确定性失败、放弃该段」的 facts=None）；
        - ``(False, None)``：本次尝试无效，交 ``_recover`` 整段重试。
        """
        raw_json, finish_reason, usage_tokens = self._generate(text)
        stats['llm_calls'] += 1
        # usage token 记账：generate 一旦成功返回即累计（含截断空返回；None → 0）。
        # 递归/重试全路径经本方法，自然计入同一 stats。
        self._count_usage(stats, usage_tokens)
        facts = validate(raw_json)
        if facts:
            return True, facts
        if not raw_json or not raw_json.strip():
            return self._recover_empty(
                text, validate, attempt_no, depth, finish_reason, stats
            )
        return self._recover_malformed(raw_json, validate, attempt_no, stats)

    def _recover_empty(
        self,
        text: str,
        validate: Callable[[str], list],
        attempt_no: int,
        depth: int,
        finish_reason: str | None,
        stats: dict[str, int],
    ) -> tuple[bool, list | None]:
        """空返回：仅「截断(length) + 已注入切段能力 + 未到深度上限」走对半递归。

        三个条件任一不满足 → 与偶发空返回同路：整段重试（老实现的条件短路顺序
        即如此——未注入 split_fn、或递归到深度上限时的截断空返回都还会再试一次，
        不能当成确定性失败直接放弃）。切段能力存在但切不出两半（单行等）→
        ``trunc_empties`` 计数后放弃该段。
        """
        split_fn = self._split_fn
        if (
            finish_reason != 'length'
            or split_fn is None
            or depth >= self._max_split_depth
        ):
            _logger.warning(
                f'第 {attempt_no} 次提取返回空'
                f'（finish={finish_reason}），整段重试中...'
            )
            return False, None
        halves = split_fn(text)
        stats['trunc_empties'] += 1
        if halves is None:
            _logger.warning(
                f'截断空返回且不可切段（depth={depth}），'
                '放弃该段（降级）'
            )
            return True, None
        stats['split_recursions'] += 1
        merged = self._split_and_extract(halves, validate, depth, stats)
        if merged:
            return True, merged
        # 两半仍全空：截断为确定性失败，整段再试无信息增益，
        # 直接放弃该段交给上层降级（不再走 retries 整段重试）
        _logger.warning(
            f'截断空返回，对半切段仍全空（depth={depth}），'
            '放弃该段（降级）'
        )
        return True, None

    def _split_and_extract(
        self,
        halves: tuple[str, str],
        validate: Callable[[str], list],
        depth: int,
        stats: dict[str, int],
    ) -> list:
        """截断段的两个半段各以 retries=1 递归提取（不再整段重试）后合并去重。"""
        left, right = halves
        left_facts = (
            self._recover(
                left, validate, retries=1, depth=depth + 1, stats=stats,
            )
            or []
        )
        right_facts = (
            self._recover(
                right, validate, retries=1, depth=depth + 1, stats=stats,
            )
            or []
        )
        merged = left_facts + right_facts
        if self._dedup_fn is not None:
            merged = self._dedup_fn(merged)
        return merged

    def _recover_malformed(
        self,
        raw_json: str,
        validate: Callable[[str], list],
        attempt_no: int,
        stats: dict[str, int],
    ) -> tuple[bool, list | None]:
        """非空但解析失败：尝试 repair 续写（若注入）；否则回退整段重试。"""
        if self._repair_fn is None:
            _logger.warning(
                f'第 {attempt_no} 次提取验证失败，整段重试中...'
            )
            return False, None
        try:
            _logger.warning(
                f'第 {attempt_no} 次输出解析失败，尝试 repair 续写 '
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
                return True, repaired_facts
            _logger.warning('repair 输出仍解析失败，回退整段重试')
        except Exception as error:
            _logger.error(f'repair 调用失败，回退整段重试: {error}')
        return False, None

    @staticmethod
    def _count_usage(
        stats: dict[str, int],
        usage_tokens: tuple[int, int] | None,
    ) -> None:
        """usage token 记账（None → 0，不写 stats）。"""
        if usage_tokens is None:
            return
        input_tokens, output_tokens = usage_tokens
        stats['in_tokens'] += input_tokens
        stats['out_tokens'] += output_tokens
