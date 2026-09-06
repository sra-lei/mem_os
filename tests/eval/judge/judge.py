"""LLM-as-Judge evaluation.

IMPORTANT design point: grading uses `evaluation_criteria` (the multi-line
grading rubric in the test-case YAML), NOT `expected_answer` — 41/60 YAMLs have
no expected_behavior field, and the rubric is the authoritative scoring input.

Score convention: 0.0 ~ 1.0; passed = score >= threshold (default 0.7, matches
EvalView需求文档.md phase-4 design).
"""
from __future__ import annotations

from typing import Protocol

from os_mem.infra.logger import get_logger

from .models import JudgeResult

_logger = get_logger('eval.judge')


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


def build_judge(name: str = "moonshot", threshold: float = 0.7) -> JudgeProvider:
    """Judge 工厂（真实链路，无 mock）。

    threshold 保留在签名里以兼容历史调用；Moonshot 判分的通过与否由模型
    按其输出决定，本地阈值仅 assert 判定使用。

    impl 延迟 import：impl 模块顶层继承本文件的 JudgeProvider，若在模块级
    互相 import 会构成 judge ↔ impl 循环导入，故具体实现放在首次构造加载。
    """
    if name == "assert":
        from .impl.assert_judger import AssertJudger

        return AssertJudger(threshold=threshold)
    if name == "moonshot":
        from .impl.moonshot_judger import MoonshotJudgeProvider

        return MoonshotJudgeProvider(threshold=threshold)
    raise ValueError(f"unknown judge: {name!r} (available: assert|moonshot)")


def assert_evaluate(
    query: str,
    expected: str,
    actual: str,
    threshold: float = 0.7,
) -> JudgeResult:
    """模块级 assert 判定入口（v1 签名兼容：expected 即判分 rubric）。

    eval/judge 单文件时期 assert_evaluate 是顶层函数；拆包后判分实现落在
    AssertJudger.evaluate，此处保留同名入口供 conftest 与单测沿用。
    """
    from .impl.assert_judger import AssertJudger

    return AssertJudger(threshold=threshold).evaluate(
        query=query,
        criteria=expected,
        actual=actual,
    )
