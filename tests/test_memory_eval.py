"""pytest 全链路评测（替代 CLI run_eval.py 的入口）。

每个用例执行 ingest → retrieve → answer → judge，判定结果统一来自
``verifier`` fixture：
  - assert（默认）：本地确定性数字/关键词命中判定
  - moonshot      ：LLM-as-Judge（按 evaluation_criteria 打分）

评测为全链路真实链路，不做 mock 冒烟；单环节验证请走独立的
tests/test_struct_mem_*.py 单测。

用法:
    pytest tests/test_memory_eval.py -m layer1                 # base provider + assert
    pytest tests/test_memory_eval.py --memory-provider struct --top-k 15
    pytest tests/test_memory_eval.py --judge moonshot --record-db # LLM 判分 + 落库看板
    pytest tests/test_memory_eval.py -k bank_account              # 按名称过滤单条
    pytest tests/test_memory_eval.py --limit 1  # 过滤后只跑 1 条（快速验证）
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

import pytest

from eval.cases import expected_text_for_case, phase_version_for_case
from eval.harness import run_case_pipeline

if TYPE_CHECKING:
    from eval.judge import JudgeResult
    from eval.llm import AnswerGenerator


def test_eval_case(
    case_id: str,
    case_data: dict,
    memory_provider_name: str,
    answer_generator: AnswerGenerator,
    top_k: int,
    verifier: Callable[[str, str, str], JudgeResult],
    eval_case: dict[str, Any],
) -> None:
    """单个评测用例：ingest → retrieve → answer → judge。

    判定结果经 ``eval_case`` 现场在 teardown 由 conftest 统一上报 DB
    （仅 ``--record-db`` 时），断言失败与 provider 异常均会记录。
    """
    query = case_data.get('user_question')
    if not query:
        pytest.skip(f'{case_id}: 无 user_question')

    phase, version = phase_version_for_case(case_data)
    expected = expected_text_for_case(case_data)
    eval_case.update(
        case_id=case_id,
        case_name=str(case_data.get('title') or case_id),
        case_data=case_data,
        query=query,
        expected=expected,
        phase=phase,
        version=version,
    )

    t0 = time.monotonic()
    try:
        completion, retrieved = run_case_pipeline(
            case_data,
            memory_provider_name,
            answer_generator,
            top_k,
        )
        latency_ms = int((time.monotonic() - t0) * 1000)
        actual = completion.text
        result = verifier(query=query, expected=expected, actual=actual)
    except Exception as exc:  # noqa: BLE001 — 记录 error 后上抛，交给 pytest 报告
        eval_case.update(
            passed=False,
            error=f'{type(exc).__name__}: {exc}',
            latency_ms=int((time.monotonic() - t0) * 1000),
        )
        raise

    eval_case.update(
        actual=actual,
        retrieved=retrieved,
        score=result.score,
        passed=result.passed,
        error=result.error or None,
        latency_ms=latency_ms,
        tokens_input=getattr(completion, 'prompt_tokens', None),
        tokens_output=getattr(completion, 'completion_tokens', None),
    )
    assert result.passed, (
        f'{case_id}: 判定未通过\n'
        f'  判定: {result.reasoning}\n'
        f'  期望: {expected[:300]}\n'
        f'  实际回答: {actual[:500]}'
    )
