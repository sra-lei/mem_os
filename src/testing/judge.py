"""LLM-as-Judge evaluation.

IMPORTANT design point: grading uses `evaluation_criteria` (the multi-line
grading rubric in the test-case YAML), NOT `expected_answer` — 41/60 YAMLs have
no expected_behavior field, and the rubric is the authoritative scoring input.

Score convention: 0.0 ~ 1.0; passed = score >= threshold (default 0.7, matches
EvalView需求文档.md phase-4 design).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Protocol

from .llm import LLMClient


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
    raise ValueError(f"unknown judge: {name!r} (available: mock)")
