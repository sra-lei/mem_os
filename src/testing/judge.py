"""LLM-as-Judge evaluation.

IMPORTANT design point: grading uses `evaluation_criteria` (the multi-line
grading rubric in the test-case YAML), NOT `expected_answer` — 41/60 YAMLs have
no expected_behavior field, and the rubric is the authoritative scoring input.

Score convention: 0.0 ~ 1.0; passed = score >= threshold (default 0.7, matches
EvalView需求文档.md phase-4 design).
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Optional, Protocol

from openai import OpenAI

from testing.config import settings


@dataclass
class JudgeResult:
    score: float            # 0.0 ~ 1.0
    passed: bool
    reasoning: Optional[str] = None
    error: Optional[str] = None

class JudgeProvider(Protocol):
    """Grades one answer. Note: it receives the RUBRIC (evaluation_criteria),
    not a fixed expected answer string."""
    def evaluate(
        self,
        query: str,
        criteria: Optional[str],
        actual: str,
    ) -> JudgeResult:
        ...


# 普通字符串模板 + .format()（criteria/query/actual 在 evaluate 时注入）
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

# JSON Schema for structured output (score/passed/reasoning/error)
_JUDGE_SCHEMA = {
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

class MoonshotJudgeProvider(Protocol):
    """Grades one answer. Note: it receives the RUBRIC (evaluation_criteria),
    not a fixed expected answer string."""

    # 请求节流：读配置 MOONSHOT_MIN_INTERVAL（RPM=3 时代曾需 20s；
    # 充值/升配额后调小，如 RPM=60 → 1s）。空等时间由配置控制。
    _last_call: float = 0.0

    def __init__(self) -> None:
        self.client = OpenAI(
            api_key=settings.MOONSHOT_API_KEY,
            base_url=settings.MOONSHOT_BASE_URL,
            max_retries=3,  # openai SDK 会对 429/5xx 自动重试（指数退避）
        )

    @classmethod
    def _min_interval(cls) -> float:
        return getattr(settings, "MOONSHOT_MIN_INTERVAL", 1.0)

    @classmethod
    def _throttle(cls) -> None:
        """请求节流：保证调用间隔 >= 配置的最小间隔（防 429）。"""
        wait = cls._min_interval() - (time.monotonic() - cls._last_call)
        if wait > 0:
            time.sleep(wait)
        cls._last_call = time.monotonic()

    def evaluate(
        self,
        query: str,
        criteria: Optional[str],
        actual: str,
    ) -> JudgeResult:
        try:
            self._throttle()
            completion = self.client.chat.completions.create(
                model=settings.MOONSHOT_MODEL,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT.format(criteria=criteria, query=query, actual=actual)},
                ],
                response_format={
                    "type": "json_schema",
                    "json_schema": {
                        "name": "judge_result",
                        "schema": _JUDGE_SCHEMA,
                        "strict": True,
                    },
                }
            )
        except Exception as exc:  # noqa: BLE001 — 重试耗尽（含 429 持续超限）时兜底，不让 runner 崩
            return JudgeResult(
                score=0.0,
                passed=False,
                reasoning="moonshot judge 调用失败",
                error=f"{type(exc).__name__}: {exc}",
            )
        content = completion.choices[0].message.content
        print(f"[judge] {content}")
        try:
            obj = json.loads(content or "")
        except (json.JSONDecodeError, TypeError) as e:
            return JudgeResult(
                score=0.0,
                passed=False,
                reasoning=f"judge 输出不是合法 JSON: {(content or '')[:200]}",
                error=str(e),
            )
        return JudgeResult(
            score=float(obj.get("score", 0.0)),
            passed=bool(obj.get("passed", False)),
            reasoning=obj.get("reasoning"),
            error=obj.get("error"),
        )



class MockJudge:
    """Placeholder judge: fixed score, always fails above threshold 0.

    Replace with a real LLM-as-Judge by implementing JudgeProvider and
    registering it in build_judge(). A real judge prompt should instruct the
    model to check the answer against every requirement in `criteria`.
    """

    name = "mock"

    def __init__(self, fixed_score: float = 0.5):
        self._fixed_score = fixed_score

    def evaluate(
        self,
        query: str,
        criteria: Optional[str],
        actual: str,
    ) -> JudgeResult:
        return JudgeResult(
            score=self._fixed_score,
            passed=False,
            reasoning="mock judge: 固定分数，未接入真实判分模型",
        )


def build_judge(name: str, threshold: float = 0.7) -> JudgeProvider:
    """Factory used by the runner/CLI. Register your real judge here."""
    if name == "mock":
        return MockJudge()
    elif name == "moonshot":
        return MoonshotJudgeProvider()
    raise ValueError(f"unknown judge: {name!r} (available: mock)")
