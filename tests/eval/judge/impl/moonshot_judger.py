"""Moonshot (Kimi) LLM-as-Judge 判分实现。

判分 prompt 用普通字符串模板 + .format() 渲染
（criteria/query/actual 在 evaluate 调用时注入）。
"""

import json
import time
from typing import Any

from openai import OpenAI
from openai.types.shared_params.response_format_json_schema import (
    ResponseFormatJSONSchema,
)

from os_mem import get_logger
from os_mem.infra import mask_pii
from os_mem.utils.prompt_fp import fingerprint

from ...config import settings
from ..judge import JudgeProvider
from ..models import JudgeResult

_logger = get_logger('eval.judge.moonshot')

SYSTEM_PROMPT: str = '''
# 角色
你是 Kimi，由 Moonshot AI 提供的人工智能助手，你更擅长中文和英文的对话。
# 任务
你会为根据{criteria}，对于用户的问题{query}，以及给定的答案{actual}来输出一个评分。
# 规则
你会拒绝一切涉及恐怖主义，种族歧视，黄色暴力等问题的回答。
Moonshot AI 为专有名词，不可翻译成其他语言。
# 输出格式
输出 JudgeResult 格式的 JSON，包含分数、是否通过、评分理由和错误信息。
评分理由和错误信息用中文输出。
'''

# 判分 prompt 内容指纹（版本标识，见 os_mem.utils.prompt_fp）
SYSTEM_PROMPT_FINGERPRINT: str = fingerprint(SYSTEM_PROMPT)

# JSON Schema for structured output (score/passed/reasoning/error)
_JUDGE_SCHEMA  = {
    "type": "object",
    "properties": {
        "score": {"type": "number", "description": "分数 0.0 ~ 1.0"},
        "passed": {"type": "boolean"},
        "reasoning": {"type": ["string", "null"]},
        "error": {"type": ["string", "null"]},
    },
    "required": ["score", "passed"],
    "additionalProperties": False,
}

_RES_SCHEMA: ResponseFormatJSONSchema = {
    "type": "json_schema",
    "json_schema": {
        "name": "judge_result",
        "schema": _JUDGE_SCHEMA,  # pyright: ignore[reportAssignmentType]
        "strict": True,
    },
}

class MoonshotJudgeProvider(JudgeProvider):
    """Grades one answer. Note: it receives the RUBRIC (evaluation_criteria),
    not a fixed expected answer string."""

    # 请求节流：读配置 MOONSHOT_MIN_INTERVAL（RPM=3 时代曾需 20s；
    # 充值/升配额后调小，如 RPM=60 → 1s）。空等时间由配置控制。
    _last_call: float = 0.0

    def __init__(self, threshold: float = 0.7) -> None:
        self.client = OpenAI(
            api_key=settings.MOONSHOT_API_KEY,
            base_url=settings.MOONSHOT_BASE_URL,
            max_retries=3,  # openai SDK 会对 429/5xx 自动重试（指数退避）
        )

    @classmethod
    def _min_interval(cls) -> float:
        return getattr(settings, "MOONSHOT_MIN_INTERVAL", 1.0)

    @classmethod
    def _throttle(cls) -> float:
        """请求节流：保证调用间隔 >= 配置的最小间隔（防 429）。

        返回本次实际等待秒数（>=0），供调用观测日志记录节流开销。
        """
        wait = cls._min_interval() - (time.monotonic() - cls._last_call)
        wait = max(0.0, wait)
        if wait > 0:
            time.sleep(wait)
        cls._last_call = time.monotonic()
        return wait

    def evaluate(
        self,
        query: str,
        criteria: str | None,
        actual: str,
    ) -> JudgeResult:
        wait_ms = 0
        content = ""
        obj: dict[str, Any]= {}
        t0 = time.monotonic()
        try:
            wait_ms = int(self._throttle() * 1000)
            t0 = time.monotonic()  # 节流等待不计入请求耗时，单独观测
            prompt = SYSTEM_PROMPT.format(criteria=criteria, query=query, actual=actual)
            completion = self.client.chat.completions.create(
                model=settings.MOONSHOT_MODEL,
                messages=[{"role": "system", "content": prompt}],
                response_format=_RES_SCHEMA
            )
            usage = getattr(completion, 'usage', None)
            _logger.info(
                '[llm] chat ok role=judge provider=moonshot model=%s ms=%d '
                'throttle_ms=%d in_tok=%s out_tok=%s',
                settings.MOONSHOT_MODEL,
                int((time.monotonic() - t0) * 1000),
                wait_ms,
                getattr(usage, 'prompt_tokens', None),
                getattr(usage, 'completion_tokens', None),
            )
            content = completion.choices[0].message.content
            # 判分原始输出进日志（脱敏后；原 print 直接暴露 reasoning，可能含用户号码）
            _logger.info('[judge] 输出: %s', mask_pii(content or ''))

            obj: dict[str, Any]= json.loads(content or "")
            return JudgeResult(**obj)
        except (json.JSONDecodeError, TypeError) as e:
            return JudgeResult(
                score=0.0,
                passed=False,
                reasoning=f"judge 输出不是合法 JSON: {(content or '')[:200]}",
                error=str(e),
            )
        except Exception as exc:  # noqa: BLE001 — 重试耗尽（含 429 持续超限）时兜底，不让 runner 崩
            _logger.error(
                '[llm] chat failed role=judge provider=moonshot model=%s '
                'ms=%d throttle_ms=%d: %s',
                settings.MOONSHOT_MODEL,
                int((time.monotonic() - t0) * 1000),
                wait_ms,
                type(exc).__name__,
            )
            return JudgeResult(
                score=0.0,
                passed=False,
                reasoning="moonshot judge 调用失败",
                error=f"{type(exc).__name__}: {exc}",
            )

