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

from sqlmodel import select

from os_mem import Memory, MemoryProvider, build_memory_provider, temp_db_path
from testing.db import get_session, init_db
from testing.db.models import TestCaseDefinition, TestCaseResult, TestRun

from .judge import JudgeProvider, build_judge
from .llm import AnswerGenerator, LLMClient, build_llm_client

PHASE_TO_VERSION = {
    "base": "v0.1",
    "multi_session": "v0.2",
    "proactive": "v0.3",
}


def _load_cases(phase: str) -> list[TestCaseDefinition]:
    with get_session() as session:
        stmt = (
            select(TestCaseDefinition)
            .where(TestCaseDefinition.category == phase)
            .order_by(TestCaseDefinition.case_id)
        )
        return list(session.exec(stmt).all())


def _record_result(
    run_id: str,
    version: str,
    phase: str,
    case: TestCaseDefinition,
    *,
    passed: bool,
    score: Optional[float],
    actual: Optional[str],
    retrieved: list[Memory],
    error: Optional[str],
    latency_ms: int,
) -> None:
    with get_session() as session:
        session.add(TestCaseResult(
            id=f"res_{uuid.uuid4().hex[:10]}",
            run_id=run_id,
            case_id=case.case_id,
            case_name=case.name,
            category=phase,
            version=version,
            passed=1 if passed else 0,
            score=score,
            expected_answer=case.expected_answer,
            actual_answer=actual,
            retrieved_memories=json.dumps(
                [{"id": m.id, "fact": m.fact, "source_session_id": m.source_session_id}
                 for m in retrieved],
                ensure_ascii=False,
            ),
            error_message=error,
            latency_ms=latency_ms,
            created_at=datetime.utcnow(),
        ))
        session.commit()


def _update_progress(run_id: str, completed: int, total: int, status: str, **extra) -> None:
    with get_session() as session:
        run = session.get(TestRun, run_id)
        if run is None:
            return
        run.progress = round(completed / total, 4) if total else 1.0
        run.status = status
        for k, v in extra.items():
            setattr(run, k, v)
        session.commit()


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
    # memory_store: "tmp" -> throwaway memory db per run (evaluation default,
    # os_mem.db is never touched); "file" -> let the provider use its default
    # persistent path (production memory database).
    mem_store = str(config.get("memory_store", "tmp"))
    tmp_db: Optional[str] = None
    if mem_store == "tmp" and mem_name != "stub":
        tmp_db = str(temp_db_path())

    cases = _load_cases(phase)
    if limit:
        cases = cases[:limit]

    init_db()
    run_id = f"run_{uuid.uuid4().hex[:10]}"
    snapshot = json.dumps(config, ensure_ascii=False)
    # create run row
    with get_session() as session:
        session.add(TestRun(
            id=run_id,
            version=version,
            phase=phase,
            total_cases=len(cases),
            passed_count=0,
            pass_rate=0.0,
            config_snapshot=snapshot,
            notes=notes,
            triggered_by="manual",
            status="running",
            progress=0.0,
        ))
        session.commit()

    passed_total = 0
    t_run_start = time.monotonic()

    for idx, case in enumerate(cases, start=1):
        if case.query is None:
            if verbose:
                print(f"[{idx}/{len(cases)}] {case.case_id}: 跳过（无 query）")
            continue
        if verbose:
            print(f"\n[{idx}/{len(cases)}] {case.case_id} - {case.name[:60]}")
        t_case_start = time.monotonic()
        error: Optional[str] = None
        score: Optional[float] = None
        passed = False
        actual: Optional[str] = None
        retrieved: list[Memory] = []

        try:
            # Isolated provider per case (user_id = case_id) — no cross-case leakage
            provider: MemoryProvider = build_memory_provider(
                mem_name, user_id=case.case_id, db_path=tmp_db,
            )
            llm: LLMClient = build_llm_client(llm_name)
            judge: JudgeProvider = build_judge(judge_name, threshold=threshold)
            agent = AnswerGenerator(llm)

            histories = json.loads(case.conversation_histories_raw or "[]")
            if verbose:
                print(f"  会话数: {len(histories)}")
            for conv in histories:
                provider.ingest(conv)

            retrieved = provider.retrieve(case.query, top_k=top_k)
            if verbose:
                print(f"  检索到 {len(retrieved)} 条记忆")
            actual = agent.answer(query=case.query, memories=retrieved)
            if verbose:
                print(f"  DeepSeek 答案: {(actual or '')[:150]}")

            verdict = judge.evaluate(
                query=case.query,
                criteria=case.evaluation_criteria,
                actual=actual,
            )
            score = verdict.score
            passed = verdict.passed
            if verdict.error:
                error = verdict.error
            if verbose:
                print(f" Moonshot 判分: score={score} passed={passed}"
                      + (f"  (judge error: {verdict.error})" if verdict.error else ""))
        except Exception as exc:  # noqa: BLE001 — a failing case must not kill the run
            error = f"{type(exc).__name__}: {exc}"
            if verbose:
                print(f"  ✗ 用例异常: {error}")
                traceback.print_exc()

        latency_ms = int((time.monotonic() - t_case_start) * 1000)
        if passed:
            passed_total += 1

        _record_result(
            run_id, version, phase, case,
            passed=passed, score=score, actual=actual,
            retrieved=retrieved, error=error, latency_ms=latency_ms,
        )
        _update_progress(run_id, idx, len(cases), "running")

    duration = round(time.monotonic() - t_run_start, 3)
    _update_progress(
        run_id, len(cases), len(cases), "completed",
        passed_count=passed_total,
        pass_rate=round(passed_total / len(cases), 4) if cases else 0.0,
        duration_seconds=duration,
    )
    if tmp_db is not None:
        try:
            os.unlink(tmp_db)
        except OSError:
            pass  # provider may still hold the file open on Windows; ignore
    return run_id
