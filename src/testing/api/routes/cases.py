"""Cases API routes."""

from __future__ import annotations

import json
import re

from fastapi import APIRouter, HTTPException, Query
from sqlmodel import func, or_, select

from testing.db import get_session
from testing.db.models import TestCaseDefinition, TestCaseResult, TestRun

from ..schemas import (
    CaseDefinition,
    CaseHistoryEntry,
    CaseHistoryResponse,
)

router = APIRouter(prefix='/api/cases', tags=['cases'])


# ---------- helpers ----------


def _attach_case_aggs(session, items: list[CaseDefinition]) -> None:
    """Fill CaseDefinition.total_runs / pass_count / fail_count in one query."""
    if not items:
        return
    ids = [c.case_id for c in items]
    stmt = (
        select(
            TestCaseResult.case_id,
            func.count(TestCaseResult.id).label('total'),
            func.sum(TestCaseResult.passed).label('passed_sum'),
        )
        .where(TestCaseResult.case_id.in_(ids))
        .group_by(TestCaseResult.case_id)
    )
    agg_map: dict[str, tuple[int, int]] = {}
    for row in session.exec(stmt).all():
        cid, total, passed_sum = row
        agg_map[cid] = (int(total or 0), int(passed_sum or 0))
    for c in items:
        total, passed = agg_map.get(c.case_id, (0, 0))
        c.total_runs = total
        c.pass_count = passed
        c.fail_count = total - passed


_LAYER_RE = re.compile(r'layer[1-3]')


def _layer_from_source(sp: str | None) -> str | None:
    if not sp:
        return None
    m = _LAYER_RE.search(sp)
    return m.group(0) if m else None


def _apply_case_filters(stmt, *, category, version_target, source_layer, search, tag):
    """Build a filter WHERE clause for list_cases() using real DB-col valid values.

    Returns early for any empty filter (avoids useless predicates).
    """
    if category:
        stmt = stmt.where(TestCaseDefinition.category == category)
    if version_target:
        stmt = stmt.where(TestCaseDefinition.version_target == version_target)
    if source_layer:
        # source_layer comes from DB-distinct values: layer1 | layer2 | layer3
        stmt = stmt.where(TestCaseDefinition.source_path.like(f'%{source_layer}%'))
    if search:
        kw = f'%{search}%'
        stmt = stmt.where(
            or_(
                TestCaseDefinition.case_id.like(kw),
                TestCaseDefinition.name.like(kw),
                TestCaseDefinition.description.like(kw),
                TestCaseDefinition.query.like(kw),
            )
        )
    if tag:
        # tags column is a JSON string of array, e.g. ["bank","account"]
        # SQLite JSON: use json_each is overkill; LIKE on JSON string works
        # reliably here because our tag values don't contain quotes/commas.
        stmt = stmt.where(TestCaseDefinition.tags.like(f'%"{tag}"%'))
    return stmt


# ---------- routes ----------


@router.get('', response_model=list[CaseDefinition])
def list_cases(
    category: str | None = Query(
        None, description='Filter by phase category: base | multi_session | proactive'
    ),
    version_target: str | None = Query(
        None, description='Filter by target version, e.g. v0.1'
    ),
    source_layer: str | None = Query(
        None, description='Filter by test layer: layer1 | layer2 | layer3'
    ),
    search: str | None = Query(
        None, description='Fuzzy search on case_id/name/description/query'
    ),
    tag: str | None = Query(
        None, description='Match one tag inside the tags JSON array'
    ),
):
    with get_session() as session:
        stmt = select(TestCaseDefinition).order_by(TestCaseDefinition.case_id)
        stmt = _apply_case_filters(
            stmt,
            category=category,
            version_target=version_target,
            source_layer=source_layer,
            search=search,
            tag=tag,
        )
        rows = session.exec(stmt).all()
        items = [CaseDefinition.model_validate(c) for c in rows]
        _attach_case_aggs(session, items)
        return items


@router.get('/tags', response_model=list[str])
def list_case_tags():
    """Return distinct tags across all case definitions for filter dropdowns."""
    with get_session() as session:
        stmt = select(TestCaseDefinition.tags).where(
            TestCaseDefinition.tags.is_not(None)
        )
        collected: set[str] = set()
        for raw in session.exec(stmt).all():
            try:
                arr = json.loads(raw)
            except (ValueError, TypeError):
                continue
            if not isinstance(arr, list):
                continue
            for t in arr:
                s = str(t).strip()
                if s:
                    collected.add(s)
        return sorted(collected)


@router.get('/versions', response_model=list[str])
def list_case_versions():
    """Return distinct version_target values ordered DESC (newest first)."""
    with get_session() as session:
        stmt = (
            select(TestCaseDefinition.version_target)
            .where(TestCaseDefinition.version_target.is_not(None))
            .distinct()
            .order_by(TestCaseDefinition.version_target.desc())
        )
        return [v for v in session.exec(stmt).all() if v]


@router.get('/layers', response_model=list[str])
def list_case_layers():
    """Return distinct layer values derived from source_path (layer1/2/3)."""
    with get_session() as session:
        stmt = (
            select(TestCaseDefinition.source_path)
            .where(TestCaseDefinition.source_path.is_not(None))
            .distinct()
        )
        layers: set[str] = set()
        for raw in session.exec(stmt).all():
            layer = _layer_from_source(raw)
            if layer:
                layers.add(layer)
        return sorted(layers)


@router.get('/{case_id}', response_model=CaseDefinition)
def get_case(case_id: str):
    with get_session() as session:
        case = session.get(TestCaseDefinition, case_id)
        if case is None:
            raise HTTPException(status_code=404, detail='Case not found')
        item = CaseDefinition.model_validate(case)
        _attach_case_aggs(session, [item])
        return item


@router.get('/{case_id}/history', response_model=CaseHistoryResponse)
def get_case_history(case_id: str):
    with get_session() as session:
        case = session.get(TestCaseDefinition, case_id)
        if case is None:
            # Still return 404 if unknown case_id
            raise HTTPException(status_code=404, detail='Case not found')

        stmt = (
            select(TestCaseResult, TestRun)
            .join(TestRun, TestCaseResult.run_id == TestRun.id)
            .where(TestCaseResult.case_id == case_id)
            .order_by(TestRun.run_at.asc())
        )
        rows = session.exec(stmt).all()
        history = []
        for result, run in rows:
            history.append(
                CaseHistoryEntry(
                    run_id=result.run_id,
                    version=run.version,
                    passed=bool(result.passed),
                    score=result.score,
                    run_at=run.run_at,
                    latency_ms=result.latency_ms,
                    expected_answer=result.expected_answer or case.expected_answer,
                    actual_answer=result.actual_answer,
                    error_message=result.error_message,
                    retrieved_memories=result.retrieved_memories,
                )
            )
        return CaseHistoryResponse(
            case_id=case_id,
            name=case.name,
            history=history,
        )
