"""评测 pytest 胶水层 —— 只做 pytest 集成，业务逻辑按职责在 src/ 两包：

- 评测运行库 → src/eval/       （cases 用例加载、harness 执行编排、llm、judge）
- 评测管理侧 → src/testing/    （db 模型、store_service 结果落库、EvalView api）

本文件职责：命令行参数、fixture 注入、动态参数化/打标 hook、可选 DB 上报
（--record-db 落 testing.services 管理的 memos.db）。

约定：test_eval_case 通过参数名自动注入 fixture（case_id/case_data 由
pytest_generate_tests 参数化，其余为下方 fixture）。
"""
from __future__ import annotations

import json
import time
from typing import Any

import pytest

from eval.cases import mark_name_for_case


# ------------------------------------------------------------------ #
#  会话级 run 状态（--record-db 上报用）
# ------------------------------------------------------------------ #
_SESSION_T0 = time.monotonic()
_RUN: dict[str, Any] = {
    "run_id": None,
    "phase": None,
    "version": None,
    "passed": 0,
    "recorded": 0,
}


# ------------------------------------------------------------------ #
#  命令行参数
# ------------------------------------------------------------------ #
def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup("memos-eval")
    group.addoption("--memory-provider", default="base",
                    choices=["base", "struct", "full"],
                    help="memory provider: base | struct | full (default: base)")
    group.addoption("--llm", default="deepseek",
                    help="answer llm client: deepseek (default: deepseek)")
    group.addoption("--top-k", type=int, default=5,
                    help="retrieval top-k (default: 5)")
    group.addoption("--judge", default="assert", choices=["assert", "moonshot"],
                    help="judge: assert (default) | moonshot (LLM judge)")
    group.addoption("--threshold", type=float, default=0.7,
                    help="judge pass threshold (default: 0.7)")
    group.addoption("--record-db", action="store_true",
                    help="评测结果写回 memos.db（EvalView Dashboard 可见）")


# ------------------------------------------------------------------ #
#  配置 fixture（直读命令行参数）
# ------------------------------------------------------------------ #
@pytest.fixture(scope="session")
def memory_provider_name(request: pytest.FixtureRequest) -> str:
    return request.config.getoption("--memory-provider")


@pytest.fixture(scope="session")
def llm_name(request: pytest.FixtureRequest) -> str:
    return request.config.getoption("--llm")


@pytest.fixture(scope="session")
def top_k(request: pytest.FixtureRequest) -> int:
    return request.config.getoption("--top-k")


@pytest.fixture(scope="session")
def judge_mode(request: pytest.FixtureRequest) -> str:
    return request.config.getoption("--judge")


@pytest.fixture(scope="session")
def threshold(request: pytest.FixtureRequest) -> float:
    return request.config.getoption("--threshold")


# ------------------------------------------------------------------ #
#  LLM 与判定 fixture
# ------------------------------------------------------------------ #
@pytest.fixture
def llm_client(llm_name: str):
    """延迟导入 — eval.llm 构建真实 client，避免收集期副作用。"""
    from eval.llm import build_llm_client

    return build_llm_client(llm_name)


@pytest.fixture
def answer_generator(llm_client):
    from eval.llm import AnswerGenerator

    return AnswerGenerator(llm_client)


@pytest.fixture(scope="session")
def verifier(judge_mode: str, threshold: float):
    """统一判定函数: ``verifier(query, expected, actual) -> JudgeResult``。

    - assert（默认）: 本地确定性数字/关键词命中判定（eval.judge.assert_evaluate）
    - moonshot       : 调 MoonshotJudgeProvider（把 expected 当 rubric 注入）
    """

    if judge_mode == "moonshot":
        from eval.judge import build_judge

        judge = build_judge("moonshot")

        def _verify(query: str, expected: str, actual: str):
            return judge.evaluate(query=query, criteria=expected, actual=actual)

        return _verify

    from eval.judge import assert_evaluate

    def _assert(query: str, expected: str, actual: str):
        return assert_evaluate(query, expected, actual, threshold=threshold)

    return _assert


# ------------------------------------------------------------------ #
#  动态参数化与打标
# ------------------------------------------------------------------ #
def _load_cases_for_pytest():
    """加载全部 YAML 用例；坏 YAML 抛 ValueError → 转 pytest.skip（与原语义一致：
    收集期遇到非法用例则跳过评测模块收集）。eval 包本身保持 pytest-free。"""
    from eval.cases import load_all_cases

    try:
        return load_all_cases()
    except ValueError as e:
        msg = str(e)
    pytest.skip(msg)
    return []  # unreachable（pytest.skip 抛出），仅为满足类型推断


def pytest_generate_tests(metafunc: pytest.Metafunc) -> None:
    """把 tests/test_cases/**/*.yaml 参数化为 test_eval_case 用例。"""
    if "case_id" in metafunc.fixturenames and "case_data" in metafunc.fixturenames:
        cases = _load_cases_for_pytest()
        ids = [cid for cid, _ in cases]
        metafunc.parametrize("case_id,case_data", cases, ids=ids)


def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item],
) -> None:
    """按 YAML category 自动打 layer 标记（-m layer1 过滤用）；统计用例总数。"""
    config._eval_total = 0
    for item in items:
        if getattr(item, "originalname", None) == "test_eval_case":
            config._eval_total += 1
        if hasattr(item, "callspec"):
            case_data = item.callspec.params.get("case_data")
            if isinstance(case_data, dict):
                mark_name = mark_name_for_case(case_data)
                if mark_name:
                    item.add_marker(getattr(pytest.mark, mark_name))


# ------------------------------------------------------------------ #
#  可选 DB 上报（--record-db）：结果写回 memos.db 供 EvalView。
#  每个用例把现场写进 holder（request.node._eval_case），
#  teardown 阶段由 pytest_runtest_makereport 统一落库（含异常用例）。
# ------------------------------------------------------------------ #
@pytest.fixture
def eval_case(request: pytest.FixtureRequest) -> dict[str, Any]:
    """评测用例现场：test_eval_case 在此填充结果，teardown 统一上报。"""
    holder: dict[str, Any] = {
        "config": request.config,
        "case_id": None,
        "case_name": None,
        "case_data": None,
        "query": None,
        "expected": None,
        "phase": None,
        "version": None,
        "actual": None,
        "retrieved": None,
        "score": None,
        "passed": None,
        "error": None,
        "latency_ms": None,
        "tokens_input": None,
        "tokens_output": None,
    }
    request.node._eval_case = holder
    return holder


def _flush_eval_case(holder: dict[str, Any]) -> None:
    """把单个评测用例结果写入 memos.db（首次调用时创建 run 记录）。"""
    cfg = holder["config"]
    if not cfg.getoption("--record-db") or holder["case_id"] is None:
        return
    import uuid
    from types import SimpleNamespace

    from testing.services.store_service import get_store_service

    svc = get_store_service()
    if _RUN["run_id"] is None:
        _RUN.update(run_id=f"run_{uuid.uuid4().hex[:10]}",
                    phase=holder["phase"], version=holder["version"])
        snapshot = json.dumps({
            "memory_provider": cfg.getoption("--memory-provider"),
            "llm": cfg.getoption("--llm"),
            "judge": cfg.getoption("--judge"),
            "top_k": cfg.getoption("--top-k"),
            "threshold": cfg.getoption("--threshold"),
        }, ensure_ascii=False)
        total = max(int(getattr(cfg, "_eval_total", 0) or 0), 1)
        svc.record_test_run(
            [None] * total, _RUN["run_id"],
            _RUN["version"], _RUN["phase"], snapshot, notes=None,
        )

    case = SimpleNamespace(
        case_id=holder["case_id"],
        name=holder["case_name"] or holder["case_id"],
        expected_answer=holder["expected"] or "",
    )
    svc.record_result(
        _RUN["run_id"], holder["version"] or _RUN["version"],
        holder["phase"] or _RUN["phase"], case,
        passed=bool(holder["passed"]),
        score=holder["score"],
        actual=holder["actual"],
        retrieved=holder["retrieved"] or "",
        error=holder["error"],
        latency_ms=int(holder["latency_ms"] or 0),
        tokens_input=holder["tokens_input"],
        tokens_output=holder["tokens_output"],
    )
    _RUN["recorded"] += 1
    if holder["passed"]:
        _RUN["passed"] += 1


@pytest.hookimpl(trylast=True)
def pytest_runtest_makereport(item: pytest.Item, call: pytest.CallInfo) -> None:
    """评测用例 teardown 后统一上报（覆盖断言失败与异常路径）。"""
    if call.when == "teardown":
        holder = getattr(item, "_eval_case", None)
        if holder is not None and holder.get("case_id") is not None:
            _flush_eval_case(holder)


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    """--record-db 时收尾本次 run 记录（status/completed + 通过率）。"""
    if _RUN["run_id"] is None:
        return
    total = max(
        int(getattr(session.config, "_eval_total", 0) or 0),
        _RUN["recorded"],
        1,
    )
    from testing.services.store_service import get_store_service

    svc = get_store_service()
    svc.update_progress(
        _RUN["run_id"], _RUN["recorded"], total, "completed",
        passed_count=_RUN["passed"],
        pass_rate=round(_RUN["passed"] / max(_RUN["recorded"], 1), 4),
        duration_seconds=round(time.monotonic() - _SESSION_T0, 3),
    )
