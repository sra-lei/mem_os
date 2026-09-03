"""Pytest 配置：结果判定方式控制。

通过 `--judge` 命令行参数切换结果判定策略：
  - moonshot (默认): 使用 MoonshotJudgeProvider（LLM-as-Judge）对答案打分
  - assert        : 直接断言实际答案是否包含期望答案的关键信息

测试用例通过 ``judge_mode`` 获取当前判定方式，或直接用 ``verifier``
fixture 拿到一个统一判定函数 ``verifier(query, expected, actual) -> JudgeResult``，
两种模式返回结构一致，测试代码无需感知差异。

用法:
    pytest --judge moonshot     # 走 LLM 判分
    pytest --judge assert      # 走断言判定
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

# 让 `testing` / `os_mem` 在未安装时也可被 conftest 导入（与 scripts/*.py 一致）
ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--judge",
        default="moonshot",
        choices=["moonshot", "assert"],
        help="结果判定方式: moonshot (LLM-as-Judge, 默认) | assert (直接断言期望答案)",
    )


@pytest.fixture(scope="session")
def judge_mode(request: pytest.FixtureRequest) -> str:
    """当前结果判定方式：``moonshot`` | ``assert``。"""
    return request.config.getoption("--judge")


# ---- assert 判定辅助 ---------------------------------------------------------

_NUM_RE = re.compile(r"\b\d{4,}\b")
_WORD_RE = re.compile(r"[A-Za-z\u4e00-\u9fa5]{3,}")
_STOPWORDS = {"the", "and", "for", "with", "that", "this", "you", "are"}


def _key_numbers(text: str) -> list[str]:
    """从期望答案里提取关键数字（账号/路由/金额/编号等）。"""
    return _NUM_RE.findall(text or "")


def assert_evaluate(query: str, expected: str, actual: str):
    """直接断言判定：检查实际答案是否包含期望答案的关键信息。

    - 有数字时按数字命中率打分（账号/路由等场景最准）
    - 无数字时退化为关键词子串匹配，命中率 >= 0.7 视为通过
    - 期望答案无可校验信息时默认通过

    返回 ``testing.judge.JudgeResult``，与 moonshot 判定结果结构一致。
    """
    from testing.judge import JudgeResult  # 延迟导入，避免污染全局收集

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
            passed=score >= 0.7,
            reasoning=f"assert 判定: 关键词命中 {len(hit)}/{len(keys)}",
        )

    return JudgeResult(
        score=1.0,
        passed=True,
        reasoning="assert 判定: 期望答案无可校验关键信息，默认通过",
    )


@pytest.fixture(scope="session")
def verifier(judge_mode: str):
    """统一判定函数: ``verifier(query, expected, actual) -> JudgeResult``。

    - moonshot 模式调用 ``MoonshotJudgeProvider``（把 ``expected`` 当 rubric 注入）
    - assert 模式做关键信息断言

    session 级：判分器/节流状态在整个会话内复用。
    """

    if judge_mode == "moonshot":
        from testing.judge import build_judge

        judge = build_judge("moonshot")

        def _verify(query: str, expected: str, actual: str):
            return judge.evaluate(query=query, criteria=expected, actual=actual)

        return _verify

    return assert_evaluate
