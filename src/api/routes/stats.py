"""Stats / overview API routes."""
from __future__ import annotations

from fastapi import APIRouter
from sqlmodel import select, func, and_, case

from src.db import get_session
from src.db.models import TestRun, TestCaseResult, TestCaseDefinition
from ..schemas import OverviewStats, LatestRun, ByVersionStat, FailingCase

router = APIRouter(prefix="/api/stats", tags=["stats"])


@router.get("/overview", response_model=OverviewStats)
def overview_stats():
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

        # 4) failing cases:
        #    - First compute fail_count per case_id across all historical results
        #    - Then find the latest result per case_id
        #    - If the latest one is failed, include it
        fail_subq = (
            select(
                TestCaseResult.case_id,
                func.sum(
                    case(
                        (TestCaseResult.passed == 0, 1),
                        else_=0,
                    )
                ).label("fail_count"),
            )
            .group_by(TestCaseResult.case_id)
            .subquery()
        )
        latest_result_subq = (
            select(
                TestCaseResult.case_id,
                TestCaseResult.case_name,
                TestCaseResult.passed,
                func.max(TestRun.run_at).label("last_run"),
            )
            .join(TestRun, TestCaseResult.run_id == TestRun.id)
            .group_by(
                TestCaseResult.case_id,
                TestCaseResult.case_name,
                TestCaseResult.passed,
            )
            .subquery()
        )
        # Per case_id pick the row with max run_at
        latest2_subq = (
            select(
                latest_result_subq.c.case_id,
                func.max(latest_result_subq.c.last_run).label("last_run"),
            )
            .group_by(latest_result_subq.c.case_id)
            .subquery()
        )
        failing_stmt = (
            select(
                latest_result_subq.c.case_id,
                latest_result_subq.c.case_name,
                fail_subq.c.fail_count,
            )
            .select_from(latest_result_subq)
            .join(
                latest2_subq,
                and_(
                    latest_result_subq.c.case_id == latest2_subq.c.case_id,
                    latest_result_subq.c.last_run == latest2_subq.c.last_run,
                ),
            )
            .join(
                fail_subq,
                latest_result_subq.c.case_id == fail_subq.c.case_id,
                isouter=True,
            )
            .where(latest_result_subq.c.passed == 0)
            .order_by(fail_subq.c.fail_count.desc())
            .limit(20)
        )
        failing_rows = session.exec(failing_stmt).all()
        failing_cases = []
        for row in failing_rows:
            case_id = row[0]
            name = row[1] or case_id
            fail_count = int(row[2] or 0)
            failing_cases.append(FailingCase(
                case_id=case_id,
                name=name,
                last_result="failed",
                fail_count=fail_count,
            ))

        return OverviewStats(
            total_runs=int(total_runs or 0),
            total_cases=int(total_cases or 0),
            latest_run=latest,
            by_version=by_version,
            case_categories=case_categories,
            failing_cases=failing_cases,
        )
