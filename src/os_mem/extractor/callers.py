"""provider 无关的 extraction caller 上层 —— 干净契约 + 单一恢复循环 + 工厂。

背景（docs/方案-提取任务与LLM模型画像解耦.md §2 v2）：恢复策略（截断检测、
repair、对半切段、整段重试）是「怎么跟某个模型要到合法结果」的实现细节——每个
provider/model 各不同。本模块只承载 **provider 无关** 的部分，具体实现内聚在
各自模块（deepseek 专属逻辑见 ``deepseek_caller.py``），不与任何 provider 耦合：

- ``_ExtractionCore``：单一恢复循环。语义 = 迁出前的 ``FactExtractor.extract_chunk``
  现行实现逐行等价复刻（不优化不改行为，日志文案与旧实现逐字一致）；低层能力
  （generate / repair_fn / dedup_fn / split_fn / max_split_depth）由构造注入，
  供各 provider 内部策略拼装。
- ``ExtractionCaller``：任务侧 caller 协议（``extract(dialog_text, *, validate)
  -> CallResult``）。
- ``build_extraction_caller(client, profile=None)``：按 ``profile.caller`` 分发的
  工厂——具体 provider 模块在函数体内 lazy import（避免上层反向依赖具体实现、
  防环）；未注册 caller 标识回退 deepseek（v1 唯一实现，等价现状）。

任务层（FactExtractor）只认识干净契约 ``extract(dialog_text, *, validate)
-> CallResult``（facts|None = 合法结果或明确全败，stats = 遥测）；validate
（schema 校验权：分类白名单/confidence 边界）由任务注入。分段编排/去重/
verbatim 兜底/降级仍是任务层语义。

依赖方向（无环）：callers（本模块）→ prompt 不发生（本模块不拼 prompt）；
callers → common（共享纯函数/常量）/ models（契约数据类）；具体实现
（deepseek_caller 等）→ callers；工厂只在函数体内 lazy import 具体实现。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from os_mem.extractor.common import (
    MAX_TRUNC_SPLIT_DEPTH,
    empty_extraction_stats,
)
from os_mem.extractor.models import CallResult, ModelProfile
from os_mem.infra.llm.base_client import ChatClient
from os_mem.infra.logger import get_logger

_logger = get_logger('os_mem.extractor.callers')


# ------------------------------------------------------------------ #
#  caller 协议（任务侧只依赖此契约，不认识具体 provider 实现）
# ------------------------------------------------------------------ #
class ExtractionCaller(Protocol):
    """提取 caller 协议：facts|None = 合法结果或明确全败，stats = 遥测。"""

    def extract(
        self,
        dialog_text: str,
        *,
        validate: Callable[[str], list],
        retries: int = 2,
    ) -> CallResult: ...


# ------------------------------------------------------------------ #
#  单一恢复循环（provider 无关；低层能力由具体 caller 注入）
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
#  工厂：按 profile.caller 分发到具体 provider 实现（lazy import 防环）
# ------------------------------------------------------------------ #
# caller 标识 → 具体实现模块路径（新增 provider 在此登记，上层不动）。
_CALLER_IMPL_MODULES = {
    'deepseek': 'os_mem.extractor.deepseek_caller',
}


def build_extraction_caller(
    client: ChatClient,
    profile: ModelProfile | None = None,
) -> ExtractionCaller:
    """构造 provider 自愈提取 caller：按 ``profile.caller`` 分发具体实现。

    ``profile`` 缺省 → settings 现值默认画像（其 caller='deepseek'）；显式画像的
    caller 标识决定具体实现模块（注册表见 ``_CALLER_IMPL_MODULES``），未登记标识
    回退 deepseek 并告警（v1 唯一实现）。具体实现的 max_facts 渲染进 prompt、
    chunk_caps 由任务层分段取用（见方案 §4 步骤 1-4）。

    ``build_extract_complete``（旧 client → complete 回调适配）与本工厂现在返回
    同一实现实例——旧回调用法（outcome/__call__/repair 鸭子）与任务侧新契约
    （extract）并存。
    具体实现模块须暴露标准工厂 ``build_caller(client, profile)``；新增 provider
    只需写实现模块并在 ``_CALLER_IMPL_MODULES`` 登记，上层不动。
    """
    if profile is None:
        from os_mem.extractor.profile import build_default_profile

        profile = build_default_profile()
    caller_name = profile.caller or 'deepseek'
    module_path = _CALLER_IMPL_MODULES.get(caller_name)
    if module_path is None:
        _logger.warning(
            f'未登记的 caller 实现（{caller_name}），回退 deepseek caller'
        )
        module_path = _CALLER_IMPL_MODULES['deepseek']
    import importlib

    module = importlib.import_module(module_path)
    return module.build_caller(client, profile)
