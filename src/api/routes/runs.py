"""Runs API routes."""
from __future__ import annotations

import json
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from sqlmodel import select, func, orm

from src.db import get_session
from src.db.models import TestRun, TestCaseResult
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


@router.get("", response_model=RunListResponse)
def list_runs(
    version: Optional[str] = Query(None),
    phase: Optional[str] = Query(None),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
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
        return RunListResponse(
            runs=[RunSummary.model_validate(r) for r in items],
            total=total,
        )


@router.get("/{run_id}", response_model=RunDetailResponse)
def get_run(run_id: str):
    with get_session() as session:
        run = session.get(TestRun, run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="Run not found")

        results = session.exec(
            select(TestCaseResult).where(TestCaseResult.run_id == run_id)
        ).all()

        summary = RunSummary.model_validate(run).model_dump()
        detail = RunDetailResponse(
            **summary,
            results=[CaseResult.model_validate(r) for r in results],
        )
        return detail


@router.get("/{run_id}/progress", response_model=RunProgress)
def get_run_progress(run_id: str):
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
def get_run_chart(run_id: str):
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
def create_run(req: CreateRunRequest):
    """Create a run placeholder (status='running'); the runner fills results later."""
    from datetime import datetime
    from src.db.models import TestRun
    import uuid

    run = TestRun(
        id=uuid.uuid4().hex,
        version=req.version,
        phase=req.phase,
        run_at=datetime.utcnow(),
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
