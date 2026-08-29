"""Cases API routes."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from sqlmodel import select

from src.db import get_session
from src.db.models import TestCaseDefinition, TestCaseResult, TestRun
from ..schemas import (
    CaseDefinition,
    CaseHistoryResponse,
    CaseHistoryEntry,
)

router = APIRouter(prefix="/api/cases", tags=["cases"])


@router.get("", response_model=list[CaseDefinition])
def list_cases(
    category: Optional[str] = Query(None),
    version: Optional[str] = Query(None),
):
    with get_session() as session:
        stmt = select(TestCaseDefinition).order_by(TestCaseDefinition.case_id)
        if category:
            stmt = stmt.where(TestCaseDefinition.category == category)
        if version:
            stmt = stmt.where(TestCaseDefinition.version_target == version)
        items = session.exec(stmt).all()
        return [CaseDefinition.model_validate(c) for c in items]


@router.get("/{case_id}", response_model=CaseDefinition)
def get_case(case_id: str):
    with get_session() as session:
        case = session.get(TestCaseDefinition, case_id)
        if case is None:
            raise HTTPException(status_code=404, detail="Case not found")
        return CaseDefinition.model_validate(case)


@router.get("/{case_id}/history", response_model=CaseHistoryResponse)
def get_case_history(case_id: str):
    with get_session() as session:
        case = session.get(TestCaseDefinition, case_id)
        if case is None:
            # Still return 404 if unknown case_id
            raise HTTPException(status_code=404, detail="Case not found")

        stmt = (
            select(TestCaseResult, TestRun)
            .join(TestRun, TestCaseResult.run_id == TestRun.id)
            .where(TestCaseResult.case_id == case_id)
            .order_by(TestRun.run_at.asc())
        )
        rows = session.exec(stmt).all()
        history = []
        for result, run in rows:
            history.append(CaseHistoryEntry(
                run_id=result.run_id,
                version=run.version,
                passed=bool(result.passed),
                score=result.score,
                run_at=run.run_at,
            ))
        return CaseHistoryResponse(
            case_id=case_id,
            name=case.name,
            history=history,
        )
