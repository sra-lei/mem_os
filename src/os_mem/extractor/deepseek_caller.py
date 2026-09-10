"""DeepSeek 提供方 extraction caller —— deepseek 专属逻辑全部内聚于此。

归属：``os_mem.extractor`` 记忆提取域。通用上层（provider 无关的恢复循环
``_ExtractionCore`` / 干净契约 / 工厂）在 ``callers.py``；本文件只承载
「怎么跟 deepseek 要到合法结果」的具体实现：

- prompt 拼装复用 ``os_mem.extractor.prompt`` 的 SYSTEM_PROMPT/REPAIR_PROMPT
  渲染（词表/max_facts 全部原样），generate 走 ``client.chat_outcome``
  （json_object 响应格式）；
- 恢复策略（截断检测、repair、对半切段、整段重试）不在本类重写——组合
  ``callers._ExtractionCore``，本文件只负责注入 deepseek 的低层能力
  （generate / repair_fn / dedup_fn / split_fn）；
- ``DeepSeekExtractionCaller`` 保留 ``outcome()`` / ``__call__()`` / ``repair()``
  鸭子接口（与迁出前的 ``prompt._ExtractComplete`` 同构）——AB 脚本 Recorder 依赖
  ``.outcome(...)`` 返回带 ``.usage`` 的 ChatOutcome，且逐字读
  ``__call__ = outcome().content``、``repair(partial)`` 走 ``client.chat``；
  旧调用方（build_extract_complete / complete 注入）同样经此接口工作。

依赖方向（无环）：deepseek_caller → callers（通用恢复循环）→ prompt →
（configs / infra.llm.base_client / utils.prompt_fp）；具体实现不被 callers
顶层 import——工厂按 ``profile.caller`` 在函数体内 lazy import 本模块。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from os_mem.extractor.callers import _ExtractionCore
from os_mem.extractor.common import (
    MAX_TRUNC_SPLIT_DEPTH,
    dedup_facts,
    split_text_midpoint,
)
from os_mem.extractor.models import CallResult, ModelProfile
from os_mem.extractor.profile import build_default_profile
from os_mem.extractor.prompt import build_extract_messages, build_repair_messages
from os_mem.infra.llm.base_client import ChatClient, ChatOutcome
from os_mem.infra.logger import get_logger

_logger = get_logger('os_mem.extractor.deepseek_caller')


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


def build_caller(client: ChatClient, profile: ModelProfile | None = None) -> DeepSeekExtractionCaller:
    """具体实现标准工厂（callers.build_extraction_caller 按 profile.caller 分发到此）。"""
    return DeepSeekExtractionCaller(client, profile=profile)
