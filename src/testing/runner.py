"""Evaluation runner: orchestrates one test run end-to-end.

Pipeline per test case (matches the agreed design):
  1. ingest   — replay each conversation of the case into the memory provider
  2. retrieve — query the memory provider with user_question (top-k)
  3. answer   — agent generates the reply with retrieved memories injected
  4. judge    — grade the answer against evaluation_criteria (LLM-as-Judge)
  5. record   — write TestCaseResult; update TestRun progress

Isolation: each case gets its own MemoryProvider instance bound to
user_id=case_id, so memories never leak across cases. Evaluation memories go to
a throwaway temp database (os_mem.storage.temp_db_path) — memos.db (evaluation
records) and os_mem.db (production memories) are never touched by a run.

Evolution note: the runner is phase-agnostic (base / multi_session / proactive).
v0.1 runs with the stub provider will trivially fail — that is the expected
baseline until the real memory system is implemented (see provider.py).
"""
from __future__ import annotations

import json
import os
import time
import traceback
import uuid
from datetime import datetime
from typing import Any, Optional

from os_mem.provider import MemoryProvider, build_memory_provider
from os_mem.models import Conversation
from os_mem.infra.logger.logger import get_logger
from testing.services.store_service import get_store_service


def _parse_ts(value) -> datetime:
    """YAML timestamp -> datetime。YAML 会话用 `timestamp`（如 "2024-11-15 10:30:00"），
    Conversation 模型要求 datetime；缺失或解析失败时用当前时间兜底。"""
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
            try:
                return datetime.strptime(value, fmt)
            except ValueError:
                continue
    return datetime.utcnow()

from .judge import JudgeProvider, build_judge
from .llm import AnswerGenerator, Completion, LLMClient, build_llm_client

PHASE_TO_VERSION = {
    "base": "v0.1",
    "multi_session": "v0.2",
    "proactive": "v0.3",
}

_logger = get_logger("runner")  # singleton for the run
_storeService = get_store_service()  # singleton for the run

def run_test_suite(
    version: Optional[str] = None,
    phase: str = "base",
    config: Optional[dict[str, Any]] = None,
    notes: Optional[str] = None,
    limit: Optional[int] = None,
    verbose: bool = False,
) -> str:
    """Run one test phase; returns the new run_id."""
    config = dict(config or {})
    phase = phase.lower()
    version = version or PHASE_TO_VERSION.get(phase, "v0.1")
    top_k = int(config.get("top_k", 3))
    threshold = float(config.get("judge_threshold", 0.7))
    llm_name = str(config.get("llm_provider", "mock"))
    judge_name = str(config.get("judge_provider", "mock"))
    mem_name = str(config.get("memory_provider", "stub"))

    cases = _storeService.load_cases(phase)
    if limit:
        cases = cases[:limit]
    run_id = f"run_{uuid.uuid4().hex[:10]}"
    _logger.info(f"开始测试: run_id={run_id} phase={phase} version={version} "
                 f"cases={len(cases)} top_k={top_k} threshold={threshold} "
                 f"llm={llm_name} memory={mem_name} judge={judge_name} notes={notes}")
    snapshot = json.dumps(config, ensure_ascii=False)
    _storeService.record_test_run(cases, run_id, version, phase, snapshot, notes)

    passed_total = 0
    t_run_start = time.monotonic()

    _logger.info(f"  测试开始: {len(cases)} 用例")
    for idx, case in enumerate(cases, start=1):
        if case.query is None:
            _logger.info(f"[{idx}/{len(cases)}] {case.case_id}: 跳过（无 query）")
            continue
        
        _logger.info(f"\n[{idx}/{len(cases)}] {case.case_id} - {case.name[:60]}")
        t_case_start = time.monotonic()
        error: Optional[str] = None
        score: Optional[float] = None
        passed = False
        actual: Optional[str] = None
        retrievedMem: str = ""
        tokens_input: Optional[int] = None
        tokens_output: Optional[int] = None

        try:
            # Isolated provider per case (user_id = case_id) — no cross-case leakage
            provider: MemoryProvider = build_memory_provider(
                mem_name, user_id=case.case_id
            )
            llm: LLMClient = build_llm_client(llm_name)
            judge: JudgeProvider = build_judge(judge_name, threshold=threshold)
            agent = AnswerGenerator(llm)

            histories = json.loads(case.conversation_histories_raw or "[]")
            _logger.info(f"  会话数: {len(histories)}")
            for conv in histories:
                _logger.info(f"  会话: {conv.get('conversation_id')} "
                             f"({len(conv.get('messages', []))} 条消息)")
                conversation = Conversation(
                    id=conv.get("conversation_id"),
                    user_id=case.case_id,
                    summary="",
                    # Conversation.messages 是 list[str]（每条为 JSON 字符串），
                    # YAML 的 messages 是 [{role, content}] -> 序列化成 JSON 字符串
                    messages=[
                        json.dumps(m, ensure_ascii=False)
                        for m in conv.get("messages", [])
                    ],
                    source_session_id=conv.get("conversation_id"),
                    started_at=_parse_ts(conv.get("timestamp")),
                    ended_at=_parse_ts(conv.get("ended_at") or conv.get("timestamp")),
                    message_count=len(conv.get("messages", [])),
                )
                provider.ingest(conversation)

            retrievedMem = provider.retrieve(case.query, top_k=top_k)
            completion: Completion = agent.answer(query=case.query, memories=retrievedMem)
            actual = completion.text
            tokens_input = completion.prompt_tokens
            tokens_output = completion.completion_tokens
            _logger.info(f"  DeepSeek 答案: {(actual or '')[:150]}")

            verdict = judge.evaluate(
                query=case.query,
                criteria=case.evaluation_criteria,
                actual=actual,
            )
            score = verdict.score
            passed = verdict.passed
            if verdict.error:
                error = verdict.error
            _logger.info(f" Moonshot 判分: score={score} passed={passed}"
                      + (f"  (judge error: {verdict.error})" if verdict.error else ""))
        except Exception as exc:  # noqa: BLE001 — a failing case must not kill the run
            error = f"{type(exc).__name__}: {exc}"
            _logger.info(f"  ✗ 用例异常: {error}")
            traceback.print_exc()

        latency_ms = int((time.monotonic() - t_case_start) * 1000)
        if passed:
            passed_total += 1

        _storeService.record_result(
            run_id, version, phase, case,
            passed=passed, score=score, actual=actual,
            retrieved=retrievedMem, error=error, latency_ms=latency_ms,
            tokens_input=tokens_input, tokens_output=tokens_output,
        )
        _storeService.update_progress(run_id, idx, len(cases), "running")

    duration = round(time.monotonic() - t_run_start, 3)
    _storeService.update_progress(
        run_id, len(cases), len(cases), "completed",
        passed_count=passed_total,
        pass_rate=round(passed_total / len(cases), 4) if cases else 0.0,
        duration_seconds=duration,
    )
    return run_id
