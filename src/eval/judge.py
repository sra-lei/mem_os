"""LLM-as-Judge evaluation.

IMPORTANT design point: grading uses `evaluation_criteria` (the multi-line
grading rubric in the test-case YAML), NOT `expected_answer` — 41/60 YAMLs have
no expected_behavior field, and the rubric is the authoritative scoring input.

Score convention: 0.0 ~ 1.0; passed = score >= threshold (default 0.7, matches
EvalView需求文档.md phase-4 design).
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from typing import Protocol

from openai import OpenAI

from eval.config import settings


@dataclass
class JudgeResult:
    score: float            # 0.0 ~ 1.0
    passed: bool
    reasoning: str | None = None
    error: str | None = None

class JudgeProvider(Protocol):
    """Grades one answer. Note: it receives the RUBRIC (evaluation_criteria),
    not a fixed expected answer string."""
    def evaluate(
        self,
        query: str,
        criteria: str | None,
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
        criteria: str | None,
        actual: str,
    ) -> JudgeResult:
        try:
            self._throttle()
            prompt = SYSTEM_PROMPT.format(criteria=criteria, query=query, actual=actual)
            completion = self.client.chat.completions.create(
                model=settings.MOONSHOT_MODEL,
                messages=[{"role": "system", "content": prompt}],
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



def build_judge(name: str = "moonshot", threshold: float = 0.7) -> JudgeProvider:
    """Judge 工厂（真实链路，无 mock）。

    threshold 保留在签名里以兼容历史调用；Moonshot 判分的通过与否由模型
    按其输出决定，本地阈值仅 assert 判定使用。
    """
    if name == "moonshot":
        return MoonshotJudgeProvider()
    raise ValueError(f"unknown judge: {name!r} (available: moonshot)")


# ------------------------------------------------------------------ #
#  assert 判定（默认）：本地确定性数字/关键词命中，零成本、可复现。
#  与 moonshot 输出结构一致（JudgeResult），调用方无需感知差异。
# ------------------------------------------------------------------ #
_NUM_RE = re.compile(r"\b\d{4,}\b")
_WORD_RE = re.compile(r"[A-Za-z\u4e00-\u9fa5]{3,}")
_STOPWORDS = {"the", "and", "for", "with", "that", "this", "you", "are"}


def _key_numbers(text: str) -> list[str]:
    """从期望文本里提取关键数字（账号/路由/金额/编号等）。"""
    return _NUM_RE.findall(text or "")


def assert_evaluate(query: str, expected: str, actual: str, threshold: float = 0.7) -> JudgeResult:
    """本地确定性判定：检查实际答案是否包含期望答案的关键信息。

    - 有数字时按数字命中率打分（账号/路由等场景最准）
    - 无数字时退化为关键词子串匹配，命中率 >= threshold 视为通过
    - 期望答案无可校验信息时默认通过
    """
    if not actual:
        return JudgeResult(
            score=0.0,
            passed=False,
            reasoning="assert 判定: 实际答案为空",
        )

    nums = _key_numbers(expected)
    if nums:
        missing = [n for n in nums if n not in actual]
        passed = not missing
        score = (len(nums) - len(missing)) / len(nums)
        reasoning = (
            f"assert 判定: 关键数字命中 {len(nums) - len(missing)}/{len(nums)}"
            + (f"，缺失: {missing}" if missing else "")
        )
        return JudgeResult(score=score, passed=passed, reasoning=reasoning)

    keys = [
        w for w in _WORD_RE.findall((expected or "").lower())
        if w not in _STOPWORDS
    ]
    if keys:
        actual_lc = actual.lower()
        hit = [w for w in keys if w in actual_lc]
        score = len(hit) / len(keys)
        return JudgeResult(
            score=score,
            passed=score >= threshold,
            reasoning=f"assert 判定: 关键词命中 {len(hit)}/{len(keys)}",
        )

    return JudgeResult(
        score=1.0,
        passed=True,
        reasoning="assert 判定: 期望答案无可校验关键信息，默认通过",
    )
