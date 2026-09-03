"""pytest 评测 harness：全链路真实链路（无 mock 冒烟）。

评测只保留真实能力：
  - memory-provider : base | struct | full（默认 base；真实存储/检索/LLM 提取）
  - llm             : deepseek（真实答案生成）
  - judge           : assert（本地确定性判定，默认）| moonshot（LLM-as-Judge）
  - record-db       : 可选；评测结果写回 memos.db，供 EvalView Dashboard

单环节验证（入库 / 事实提取等）不通过本评测动态配置 —— 用独立单测文件：
    pytest tests/test_struct_mem_extract.py      # 事实提取纯逻辑
    pytest tests/test_struct_mem_sqlite.py       # SQLite 双写入库

评测用例直接来自 tests/test_cases/**/*.yaml（不依赖 DB 导入），
替代原 CLI (scripts/run_eval.py + src/testing/runner.py) 的入口职责。

用法:
    pytest tests/test_memory_eval.py -m layer1                 # base provider + assert
    pytest tests/test_memory_eval.py -m layer2 --memory-provider struct --top-k 15
    pytest tests/test_memory_eval.py -m layer3 --judge moonshot --record-db
"""
from __future__ import annotations

import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
import yaml

# 手动真实验证脚本（非 pytest 用例）：需要在线 Milvus / 写 memos.db，
# 排除自动收集以免把真实环境副作用带进测试会话；用 `uv run python tests/<file>` 单独跑。
collect_ignore = [
    "test_vec_storage.py",        # 向量存储链路真实验证（写入 test_001~003）
    "test_hybrid_retrieval.py",   # 混合检索召回诊断（连真实 mem_os 库）
]

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

TEST_CASES_DIR = ROOT / "tests" / "test_cases"

# layer (YAML category) -> pytest mark name
LAYER_MARKS = {"layer1": "layer1", "layer2": "layer2", "layer3": "layer3"}

# layer -> (dashboard phase, version_target)
LAYER_PHASE_VERSION = {
    "layer1": ("base", "v0.1"),
    "layer2": ("multi_session", "v0.2"),
    "layer3": ("proactive", "v0.3"),
}

# 记录本次 session 的 run 汇总状态（--record-db 时使用）
_SESSION_T0 = time.monotonic()
_RUN: dict[str, Any] = {
    "run_id": None,
    "phase": None,
    "version": None,
    "passed": 0,
    "recorded": 0,
}


def _parse_ts(value: Any) -> datetime:
    """YAML timestamp -> datetime，缺失时用当前时间兜底。"""
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
            try:
                return datetime.strptime(value, fmt)
            except ValueError:
                continue
    return datetime.utcnow()


def load_yaml_case(path: Path) -> dict:
    """读取单个 YAML 用例，返回标准化 dict。"""
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not data.get("test_id"):
        pytest.skip(f"invalid yaml (missing test_id): {path}")
    return data


def load_all_cases() -> list[tuple[str, dict]]:
    """加载全部 YAML 用例，返回 [(case_id, data), ...]。"""
    cases = []
    for path in sorted(TEST_CASES_DIR.glob("**/*.yaml")):
        data = load_yaml_case(path)
        cases.append((data["test_id"], data))
    return cases


def phase_version_for_case(case_data: dict) -> tuple[str, str]:
    """layer (YAML category) -> (dashboard phase, version_target)。"""
    category = str(case_data.get("category", "")).lower()
    return LAYER_PHASE_VERSION.get(category, (category or "base", "v0.1"))


def build_provider(name: str, user_id: str):
    """延迟导入 — 避免 module-level 触发 Milvus / LLM 连接。"""
    from os_mem.provider import build_memory_provider

    return build_memory_provider(name, user_id=user_id)


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
#  核心流程 fixture — 延迟导入避免 module-level 副作用
# ------------------------------------------------------------------ #
@pytest.fixture
def llm_client(llm_name: str):
    from testing.llm import build_llm_client

    return build_llm_client(llm_name)


@pytest.fixture
def answer_generator(llm_client):
    from testing.llm import AnswerGenerator

    return AnswerGenerator(llm_client)


def pytest_generate_tests(metafunc: pytest.Metafunc) -> None:
    """为 test_eval_case 动态生成参数化用例。"""
    if "case_id" in metafunc.fixturenames and "case_data" in metafunc.fixturenames:
        cases = load_all_cases()
        ids = [cid for cid, _ in cases]
        metafunc.parametrize("case_id,case_data", cases, ids=ids)


def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item],
) -> None:
    """根据 YAML category 自动打 layer 标记；统计评测用例总数（供 --record-db）。"""
    config._eval_total = 0
    for item in items:
        if getattr(item, "originalname", None) == "test_eval_case":
            config._eval_total += 1
        if hasattr(item, "callspec"):
            case_data = item.callspec.params.get("case_data")
            if isinstance(case_data, dict):
                category = str(case_data.get("category", "")).lower()
                mark_name = LAYER_MARKS.get(category)
                if mark_name:
                    item.add_marker(getattr(pytest.mark, mark_name))


# ------------------------------------------------------------------ #
#  判定：assert（默认，本地确定性）| moonshot（LLM-as-Judge）
#  两者返回结构一致（JudgeResult），测试代码无需感知差异。
# ------------------------------------------------------------------ #

_NUM_RE = re.compile(r"\b\d{4,}\b")
_WORD_RE = re.compile(r"[A-Za-z\u4e00-\u9fa5]{3,}")
_STOPWORDS = {"the", "and", "for", "with", "that", "this", "you", "are"}


def _key_numbers(text: str) -> list[str]:
    """从期望文本里提取关键数字（账号/路由/金额/编号等）。"""
    return _NUM_RE.findall(text or "")


def assert_evaluate(query: str, expected: str, actual: str, threshold: float = 0.7):
    """本地确定性判定：检查实际答案是否包含期望答案的关键信息。

    - 有数字时按数字命中率打分（账号/路由等场景最准）
    - 无数字时退化为关键词子串匹配，命中率 >= threshold 视为通过
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
            passed=score >= threshold,
            reasoning=f"assert 判定: 关键词命中 {len(hit)}/{len(keys)}",
        )

    return JudgeResult(
        score=1.0,
        passed=True,
        reasoning="assert 判定: 期望答案无可校验关键信息，默认通过",
    )


@pytest.fixture(scope="session")
def verifier(judge_mode: str, threshold: float):
    """统一判定函数: ``verifier(query, expected, actual) -> JudgeResult``。

    - assert（默认）: 本地确定性数字/关键词命中判定
    - moonshot       : 调 ``MoonshotJudgeProvider``（把 expected 当 rubric 注入）
    """

    if judge_mode == "moonshot":
        from testing.judge import build_judge

        judge = build_judge("moonshot")

        def _verify(query: str, expected: str, actual: str):
            return judge.evaluate(query=query, criteria=expected, actual=actual)

        return _verify

    def _assert(query: str, expected: str, actual: str):
        return assert_evaluate(query, expected, actual, threshold=threshold)

    return _assert


# ------------------------------------------------------------------ #
#  用例执行流程
# ------------------------------------------------------------------ #
def run_case_pipeline(
    case_data: dict,
    memory_provider_name: str,
    answer_generator,
    top_k: int,
):
    """执行单个用例的 ingest → retrieve → answer 流程。

    返回 (completion, retrieved_memories)：completion 含文本与 token 消耗，
    retrieved_memories 为注入给 LLM 的记忆文本。
    """
    case_id = case_data["test_id"]
    provider = build_provider(memory_provider_name, user_id=case_id)

    histories = case_data.get("conversation_histories", [])
    for conv in histories:
        conversation = _build_conversation(conv, case_id)
        provider.ingest(conversation)

    query = case_data.get("user_question")
    retrieved = provider.retrieve(query, top_k=top_k)
    completion = answer_generator.answer(query=query, memories=retrieved)
    return completion, retrieved


def _build_conversation(conv: dict, case_id: str):
    """构造 Conversation 对象 — 延迟导入避免 module-level 副作用。"""
    from os_mem.models import Conversation

    return Conversation(
        id=conv.get("conversation_id"),
        user_id=case_id,
        summary="",
        messages=[
            json.dumps(m, ensure_ascii=False)
            for m in conv.get("messages", [])
        ],
        source_session_id=conv.get("conversation_id"),
        started_at=_parse_ts(conv.get("timestamp")),
        ended_at=_parse_ts(conv.get("ended_at") or conv.get("timestamp")),
        message_count=len(conv.get("messages", [])),
    )


def expected_text_for_case(case_data: dict) -> str:
    """判定/落库用的期望文本：优先 expected_behavior，缺省回退 rubric。"""
    return str(
        case_data.get("expected_behavior")
        or case_data.get("evaluation_criteria")
        or ""
    )


# ------------------------------------------------------------------ #
#  可选 DB 上报（--record-db）：结果写回 memos.db 供 EvalView。
#  每个评测用例把现场写进 holder（request.node._eval_case），
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
