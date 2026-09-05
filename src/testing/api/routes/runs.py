"""Runs API routes."""
from __future__ import annotations

import json
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import delete as sa_delete
from sqlmodel import select, func, orm

from testing.db import get_session
from testing.db.models import TestRun, TestCaseResult, TestCaseDefinition, utcnow
from ..schemas import (
    RunListResponse,
    RunSummary,
    RunDetailResponse,
    CaseResult,
    RunProgress,
    CreateRunRequest,
    CreateRunResponse,
)

router = APIRouter(prefix="/api/runs", tags=["runs"])


def _enrich_run_summary(run: TestRun) -> RunSummary:
    """Map ORM TestRun -> RunSummary and fill the 6 derived/compat fields used by UI cards."""
    s = RunSummary.model_validate(run)
    s.name = f"{s.version} · {s.phase}"
    passed_i = max(0, int(s.passed_count or 0))
    total_i = max(0, int(s.total_cases or 0))
    s.failed = max(0, total_i - passed_i)
    s.passed = passed_i
    # Backward-compat start_time / end_time (UI uses fmtDurationBetween on them)
    s.start_time = s.run_at
    if s.run_at is not None and s.duration_seconds is not None and s.duration_seconds >= 0:
        from datetime import timedelta as _td
        try:
            s.end_time = s.run_at + _td(seconds=float(s.duration_seconds))
        except (ValueError, TypeError, OverflowError):
            s.end_time = None
    else:
        s.end_time = None
    return s


@router.get("", response_model=RunListResponse)
def list_runs(
    version: Optional[str] = Query(None),
    phase: Optional[str] = Query(None),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> RunListResponse:
    with get_session() as session:
        stmt = select(TestRun).order_by(TestRun.run_at.desc())
        if version:
            stmt = stmt.where(TestRun.version == version)
        if phase:
            stmt = stmt.where(TestRun.phase == phase)

        total = session.exec(
            select(func.count()).select_from(stmt.subquery())
        ).one()

        items = session.exec(stmt.offset(offset).limit(limit)).all()
        summaries = [_enrich_run_summary(r) for r in items]
        # Aggregate per-run answer-LLM token usage (input/output) for the list view
        if items:
            ids = [r.id for r in items]
            agg_rows = session.exec(
                select(
                    TestCaseResult.run_id,
                    func.sum(TestCaseResult.tokens_input),
                    func.sum(TestCaseResult.tokens_output),
                )
                .where(TestCaseResult.run_id.in_(ids))
                .group_by(TestCaseResult.run_id)
            ).all()
            token_map = {rid: (tin, tout) for rid, tin, tout in agg_rows}
            for s in summaries:
                tin, tout = token_map.get(s.id, (None, None))
                s.tokens_input = int(tin) if tin is not None else 0
                s.tokens_output = int(tout) if tout is not None else 0
        return RunListResponse(
            runs=summaries,
            total=total,
        )


@router.get("/versions", response_model=list[str])
def list_run_versions() -> list[str]:
    """Return distinct run versions for filter dropdowns (newest first)."""
    with get_session() as session:
        stmt = (
            select(TestRun.version)
            .where(TestRun.version.is_not(None))
            .distinct()
            .order_by(TestRun.version.desc())
        )
        return [v for v in session.exec(stmt).all() if v]


@router.get("/{run_id}", response_model=RunDetailResponse)
def get_run(run_id: str) -> RunDetailResponse:
    with get_session() as session:
        run = session.get(TestRun, run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="Run not found")

        # LEFT JOIN case definitions so each result carries setup / grading fields
        # used by the React run-detail accordion & compare modal.
        stmt = (
            select(TestCaseResult, TestCaseDefinition)
            .join(
                TestCaseDefinition,
                TestCaseResult.case_id == TestCaseDefinition.case_id,
                isouter=True,
            )
            .where(TestCaseResult.run_id == run_id)
            .order_by(TestCaseResult.category, TestCaseResult.case_id)
        )
        joined_rows = session.exec(stmt).all()

        results: list[CaseResult] = []
        for result, defn in joined_rows:
            payload = result.model_dump() if hasattr(result, "model_dump") else {
                c.name: getattr(result, c.name, None)
                for c in TestCaseResult.__table__.columns
            }
            if defn is not None:
                # Merge case-def columns as flat aliases used by CaseResult schema
                for col in ("query", "description", "tags",
                            "evaluation_criteria", "expected_behavior",
                            "conversation_histories_raw", "source_path"):
                    payload[col] = getattr(defn, col, None)
                # Override expected_answer to prefer the richer definition answer
                if payload.get("expected_answer") in (None, ""):
                    payload["expected_answer"] = getattr(defn, "expected_answer", None)
            results.append(CaseResult.model_validate(payload))

        summary = _enrich_run_summary(run)
        detail = RunDetailResponse(
            **summary.model_dump(),
            results=results,
        )
        return detail


@router.get("/{run_id}/progress", response_model=RunProgress)
def get_run_progress(run_id: str) -> RunProgress:
    with get_session() as session:
        run = session.get(TestRun, run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="Run not found")

        completed = session.exec(
            select(func.count(TestCaseResult.id)).where(
                TestCaseResult.run_id == run_id
            )
        ).one()

        total = run.total_cases or 0
        percent = 0.0
        if total > 0:
            percent = round(completed / total * 100, 1)
        return RunProgress(
            status=run.status or "unknown",
            completed=completed,
            total=total,
            percent=percent,
        )


@router.get("/{run_id}/chart")
def get_run_chart(run_id: str) -> dict[str, Any]:
    """Return chart-friendly data for a single run."""
    with get_session() as session:
        run = session.get(TestRun, run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="Run not found")

        by_cat_stmt = (
            select(
                TestCaseResult.category,
                func.count(TestCaseResult.id).label("total"),
                func.sum(TestCaseResult.passed).label("passed"),
            )
            .where(TestCaseResult.run_id == run_id)
            .group_by(TestCaseResult.category)
        )
        rows = session.exec(by_cat_stmt).all()
        categories = []
        for row in rows:
            cat = row[0]
            total = row[1] or 0
            passed = row[2] or 0
            categories.append({
                "category": cat,
                "total": total,
                "passed": passed,
                "pass_rate": round(passed / total, 3) if total else 0.0,
            })

        return {
            "run_id": run_id,
            "version": run.version,
            "phase": run.phase,
            "pass_rate": run.pass_rate,
            "total_cases": run.total_cases,
            "passed_count": run.passed_count,
            "failed_count": run.total_cases - run.passed_count,
            "by_category": categories,
        }


@router.post("", response_model=CreateRunResponse, status_code=201)
def create_run(req: CreateRunRequest) -> CreateRunResponse:
    """Create a run placeholder (status='running'); the runner fills results later."""
    import uuid

    run = TestRun(
        id=uuid.uuid4().hex,
        version=req.version,
        phase=req.phase,
        run_at=utcnow(),
        total_cases=0,
        passed_count=0,
        pass_rate=0.0,
        duration_seconds=None,
        config_snapshot=json.dumps(req.config or {}),
        notes=req.notes,
        triggered_by="manual",
        status="running",
        progress=0.0,
    )
    with get_session() as session:
        session.add(run)
        session.commit()
        session.refresh(run)
    return CreateRunResponse(run_id=run.id, status=run.status)


# ---------- delete ----------
# NOTE: concrete-path routes must be declared before "/{run_id}" wildcard routes.

@router.delete("")
def clear_all_runs() -> dict[str, int]:
    """Delete ALL runs together with their case results (irreversible).

    Refuses while any run is still 'running' to avoid the runner writing
    orphaned results after the parent run is gone.
    """
    with get_session() as session:
        running = session.exec(
            select(func.count()).select_from(TestRun).where(TestRun.status == "running")
        ).one()
        if running:
            raise HTTPException(
                status_code=409,
                detail=f"有 {running} 条运行中的记录，请等待完成后再清空",
            )
        try:
            res_results = session.execute(sa_delete(TestCaseResult))
            res_runs = session.execute(sa_delete(TestRun))
            session.commit()
        except Exception:
            session.rollback()
            raise
        return {
            "deleted_runs": int(res_runs.rowcount or 0),
            "deleted_results": int(res_results.rowcount or 0),
        }


@router.delete("/{run_id}")
def delete_run(run_id: str) -> dict[str, Any]:
    """Delete one run and all its case results (irreversible)."""
    with get_session() as session:
        run = session.get(TestRun, run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="Run not found")
        try:
            res = session.execute(
                sa_delete(TestCaseResult).where(TestCaseResult.run_id == run_id)
            )
            session.delete(run)
            session.commit()
        except Exception:
            session.rollback()
            raise
        return {"run_id": run_id, "deleted_results": int(res.rowcount or 0)}
