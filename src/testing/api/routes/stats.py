"""Stats / overview / dashboard API routes."""
from __future__ import annotations

from datetime import timedelta
from typing import Any, List, Optional

from fastapi import APIRouter, HTTPException, Query
from sqlmodel import Session, select, func, and_, case

from testing.db import get_session
from testing.db.models import TestRun, TestCaseResult, TestCaseDefinition, utcnow
from ..schemas import (
    OverviewStats,
    LatestRun,
    ByVersionStat,
    FailingCase,
    RunSummary,
    TrendPoint,
    CategoryStat,
    LongestPhaseStat,
    DashboardStats,
)

router = APIRouter(prefix="/api/stats", tags=["stats"])


# ---------------------------------------------------------------------------
# Shared tiny helpers (avoid cross-module circular imports; ~4 lines each)
# ---------------------------------------------------------------------------

def _fill_run_derived(s: RunSummary) -> RunSummary:
    """Fill derived display fields + legacy compat aliases used by every RunSummary card."""
    s.name = f"{s.version} · {s.phase}"
    passed_i = max(0, int(s.passed_count or 0))
    total_i = max(0, int(s.total_cases or 0))
    s.failed = max(0, total_i - passed_i)
    s.passed = passed_i
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


def _build_failing_cases(session: Session, limit: int = 20) -> List[FailingCase]:
    """Rich failing-case rows (category, latest run metadata, pass/fail totals)
    consumed by the React Dashboard's failing-cards grid."""

    # 1) Aggregate historical pass_count / fail_count per case_id
    agg_stmt = (
        select(
            TestCaseResult.case_id,
            func.count(TestCaseResult.id).label("total"),
            func.sum(TestCaseResult.passed).label("passed_sum"),
        ).group_by(TestCaseResult.case_id)
    )
    agg: dict[str, tuple[int, int]] = {}
    for row in session.exec(agg_stmt).all():
        cid, total, passed = row
        total_i = int(total or 0)
        passed_i = int(passed or 0)
        agg[cid] = (passed_i, max(0, total_i - passed_i))

    if not agg:
        return []

    case_ids = list(agg.keys())

    # 2) Latest result per case_id (max run_at). Using a scalar-subquery for
    #    correlation — SQLAlchemy + SQLite both support this and it keeps us
    #    away from 2-layer self-joins that break on empty DBs.
    latest_stmt = (
        select(
            TestCaseResult.case_id,
            TestCaseResult.case_name,
            TestCaseResult.passed,
            TestRun.id.label("run_id"),
            TestRun.version.label("version"),
            TestRun.run_at.label("run_at"),
        )
        .join(TestRun, TestCaseResult.run_id == TestRun.id)
        .where(TestCaseResult.case_id.in_(case_ids))
    )
    # (Second-order filter: iterate candidates and keep MAX run_at per case_id)
    # A pure-SQL filter via scalar subquery is elegant but the 3-line Python
    # dedupe is O(n) and perfectly readable at our scale (~<1k cases).
    latest_map: dict[str, tuple] = {}
    for row in session.exec(latest_stmt).all():
        cid, cname, passed, rid, v, rat = row
        prev = latest_map.get(cid)
        if prev is None or rat > prev[5]:
            latest_map[cid] = (cid, cname, passed, rid, v, rat)

    # 3) Category lookup from TestCaseDefinition (new field required by schema)
    defn_stmt = (
        select(TestCaseDefinition.case_id, TestCaseDefinition.category)
        .where(TestCaseDefinition.case_id.in_(case_ids))
    )
    cat_map: dict[str, str] = {
        row[0]: (row[1] or "unknown") for row in session.exec(defn_stmt).all()
    }

    # 4) Assemble rows & sort
    out: list[FailingCase] = []
    for cid, (passed_i, fail_i) in agg.items():
        latest = latest_map.get(cid)
        if latest is None:
            continue
        _, cname, lp, rid, v, rat = latest
        last_failed = (int(lp or 0) == 0)
        if not (fail_i > 0 or last_failed):
            continue
        out.append(FailingCase(
            case_id=cid,
            name=str(cname or cid),
            category=cat_map.get(cid, "unknown"),
            phase=cat_map.get(cid, "unknown"),  # compat alias for PhaseBadge UI cards
            last_result=("failed" if last_failed else "passed"),
            fail_count=fail_i,
            pass_count=passed_i,
            last_run_id=str(rid) if rid else None,
            last_run_version=str(v) if v else None,
            last_run_time=rat,
            last_passed=bool(lp) if lp is not None else None,
            # --- Legacy compat aliases for React ListCards / FailedCasesGrid ---
            case_name=str(cname or cid),                  # alias of name
            last_run_name=(str(v) if v else None),        # alias of last_run_version
        ))
    out.sort(key=lambda x: (-x.fail_count, -x.pass_count, x.case_id))
    return out[:max(1, limit)]


@router.get("/overview", response_model=OverviewStats)
def overview_stats() -> OverviewStats:
    with get_session() as session:
        # 1) total_runs + latest run
        total_runs = session.exec(select(func.count(TestRun.id))).one()

        latest_run = session.exec(
            select(TestRun).order_by(TestRun.run_at.desc()).limit(1)
        ).first()
        latest: LatestRun | None = None
        if latest_run is not None:
            latest = LatestRun(
                version=latest_run.version,
                phase=latest_run.phase,
                pass_rate=latest_run.pass_rate,
                run_at=latest_run.run_at,
            )

        # 2) by_version: runs count and avg pass_rate
        by_version_stmt = (
            select(
                TestRun.version,
                func.count(TestRun.id),
                func.avg(TestRun.pass_rate),
            )
            .group_by(TestRun.version)
            .order_by(TestRun.version)
        )
        by_version_rows = session.exec(by_version_stmt).all()
        by_version = {}
        for row in by_version_rows:
            v = row[0]
            runs_count = int(row[1] or 0)
            avg = round(float(row[2] or 0.0), 4)
            by_version[v] = ByVersionStat(runs=runs_count, avg_pass_rate=avg)

        # 3) total_cases and case_categories distribution
        total_cases = session.exec(
            select(func.count(TestCaseDefinition.case_id))
        ).one()

        cat_stmt = (
            select(
                TestCaseDefinition.category,
                func.count(TestCaseDefinition.case_id),
            )
            .group_by(TestCaseDefinition.category)
        )
        cat_rows = session.exec(cat_stmt).all()
        case_categories = {row[0]: int(row[1] or 0) for row in cat_rows}

        # 4) failing cases (rich rows: category / latest run / total pass+fail)
        failing_cases = _build_failing_cases(session, limit=20)

        return OverviewStats(
            total_runs=int(total_runs or 0),
            total_cases=int(total_cases or 0),
            latest_run=latest,
            by_version=by_version,
            case_categories=case_categories,
            failing_cases=failing_cases,
        )


# ---------------------------------------------------------------------------
# Internal helpers (shared by dashboard / trend / by-category endpoints)
# ---------------------------------------------------------------------------

def _recent_runs(session: Session, limit: int) -> List[RunSummary]:
    """Return the most recent `limit` runs with derived name/failed filled."""
    stmt = select(TestRun).order_by(TestRun.run_at.desc()).limit(limit)
    return [
        _fill_run_derived(RunSummary.model_validate(r))
        for r in session.exec(stmt).all()
    ]


def _trend_points(session: Session, limit: int) -> List[TrendPoint]:
    """Return historical pass-rate trend points.

    Fetches the latest `limit` runs ordered by `run_at` DESC so the sample
    stays focused on the most relevant window, then reverses into ascending
    chronological order for left-to-right chart rendering.
    """
    stmt = (
        select(TestRun)
        .order_by(TestRun.run_at.desc())
        .limit(max(1, limit))
    )
    latest = list(session.exec(stmt).all())
    latest.reverse()  # oldest first for the x-axis

    points: List[TrendPoint] = []
    for r in latest:
        total = int(r.total_cases or 0)
        passed = int(r.passed_count or 0)
        failed = total - passed
        points.append(TrendPoint(
            run_id=str(r.id),
            name=f"{r.version} · {r.phase}",
            version=r.version,
            start_time=r.run_at,
            pass_rate=float(r.pass_rate or 0.0),
            total_cases=total,
            passed=passed,
            failed=failed,
            phase=r.phase,
        ))
    return points


def _category_rows(session: Session, *, run_id: Optional[str] = None) -> list[Any]:
    """Return (category, total_count, passed_count, avg_latency_ms) tuples.

    Scoped to a specific run when `run_id` is provided; otherwise aggregated
    across every historical case result.
    """
    stmt = (
        select(
            TestCaseResult.category,
            func.count(TestCaseResult.id).label("total"),
            func.sum(TestCaseResult.passed).label("passed"),
            func.avg(TestCaseResult.latency_ms).label("avg_lat"),
        )
        .group_by(TestCaseResult.category)
        .order_by(TestCaseResult.category)
    )
    if run_id is not None:
        stmt = stmt.where(TestCaseResult.run_id == run_id)
    return session.exec(stmt).all()


def _build_category_stats(session: Session, *, run_id: Optional[str] = None) -> List[CategoryStat]:
    stats: List[CategoryStat] = []
    for row in _category_rows(session, run_id=run_id):
        category = row[0] or "unknown"
        total = int(row[1] or 0)
        passed = int(row[2] or 0)
        failed = total - passed
        pass_rate = round(passed / total, 4) if total else 0.0
        avg_lat = int(round(float(row[3] or 0.0)))
        stats.append(CategoryStat(
            phase=category,
            total=total,
            passed=passed,
            failed=failed,
            pass_rate=pass_rate,
            avg_latency_ms=avg_lat,
        ))
    return stats


# ---------------------------------------------------------------------------
# Explicit routes requested by the React stats API client
# ---------------------------------------------------------------------------

@router.get("/dashboard", response_model=DashboardStats)
def dashboard_stats() -> DashboardStats:
    """Single-shot aggregation used by the React Dashboard page."""
    with get_session() as session:
        overview = overview_stats()  # computed via the overview endpoint above

        # --- total_pass_rate (historical average across all case results) ---
        overall_avg = session.exec(
            select(func.avg(TestCaseResult.passed))
        ).one()
        total_pass_rate = round(float(overall_avg or 0.0), 4)

        # --- recent_7_days (case results executed in the last 7 days) ---
        cutoff = utcnow() - timedelta(days=7)
        recent_stmt = (
            select(func.count(TestCaseResult.id))
            .join(TestRun, TestCaseResult.run_id == TestRun.id)
            .where(TestRun.run_at >= cutoff)
        )
        recent_7_days = int(session.exec(recent_stmt).one() or 0)

        # --- longest_phase (category with highest avg latency) ---
        lp_stmt = (
            select(
                TestCaseResult.category,
                func.avg(TestCaseResult.latency_ms).label("avg_lat"),
            )
            .where(TestCaseResult.latency_ms.is_not(None))
            .group_by(TestCaseResult.category)
            .order_by(func.avg(TestCaseResult.latency_ms).desc())
            .limit(1)
        )
        lp_row = session.exec(lp_stmt).first()
        longest_phase: Optional[LongestPhaseStat] = None
        if lp_row is not None and lp_row[0] is not None:
            longest_phase = LongestPhaseStat(
                phase=str(lp_row[0]),
                avg_latency_ms=int(round(float(lp_row[1] or 0.0))),
            )

        return DashboardStats(
            total_runs=overview.total_runs,
            total_cases=overview.total_cases,
            total_pass_rate=total_pass_rate,
            recent_7_days=recent_7_days,
            longest_phase=longest_phase,
            failing_cases=overview.failing_cases,
            recent_runs=_recent_runs(session, 10),
            trend=_trend_points(session, 10),
            by_category=_build_category_stats(session),
        )


@router.get("/trend", response_model=List[TrendPoint])
def trend_stats(limit: int = Query(10, ge=1, le=100)) -> List[TrendPoint]:
    """Pass-rate trend points ordered chronologically (ascending run_at)."""
    with get_session() as session:
        return _trend_points(session, limit=limit)


@router.get("/by-category", response_model=List[CategoryStat])
def by_category_stats_global() -> List[CategoryStat]:
    """Per-category pass+latency stats aggregated across ALL historical runs."""
    with get_session() as session:
        return _build_category_stats(session)


@router.get("/by-category/{run_id}", response_model=List[CategoryStat])
def by_category_stats_for_run(run_id: str) -> List[CategoryStat]:
    """Per-category pass+latency stats scoped to one specific run."""
    with get_session() as session:
        # Validate run exists so 404 surfaces for garbage IDs
        if session.get(TestRun, run_id) is None:
            raise HTTPException(status_code=404, detail="Run not found")
        return _build_category_stats(session, run_id=run_id)
