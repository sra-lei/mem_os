import json
import uuid

from sqlmodel import select

from testing.db import get_session, init_db
from testing.db.models import TestCaseDefinition, TestCaseResult, TestRun, utcnow

_db_off = False


class StoreService:
    def __init__(self) -> None:
        init_db()
        pass

    def load_cases(self, phase: str) -> list[TestCaseDefinition]:
        with get_session() as session:
            stmt = (
                select(TestCaseDefinition)
                .where(TestCaseDefinition.category == phase)
                .order_by(TestCaseDefinition.case_id)
            )
            return list(session.exec(stmt).all())

    def record_test_run(
        self,
        cases: list[TestCaseDefinition],
        run_id: str,
        version: str,
        phase: str,
        snapshot: str,
        notes: str | None,
    ) -> None:
        if _db_off:
            return
        # create run row
        with get_session() as session:
            session.add(
                TestRun(
                    id=run_id,
                    version=version,
                    phase=phase,
                    total_cases=len(cases),
                    passed_count=0,
                    pass_rate=0.0,
                    config_snapshot=snapshot,
                    notes=notes,
                    triggered_by='manual',
                    status='running',
                    progress=0.0,
                )
            )
            session.commit()

    def record_result(
        self,
        run_id: str,
        version: str,
        phase: str,
        case: TestCaseDefinition,
        *,
        passed: bool,
        score: float | None,
        actual: str | None,
        retrieved: str,
        error: str | None,
        latency_ms: int,
        tokens_input: int | None = None,
        tokens_output: int | None = None,
    ) -> None:
        if _db_off:
            return
        # retrieve 的返回在接口演进中：list[Memory]（stub）或注入文本 str（base）。
        # 落库统一为 TEXT：list 序列化成 JSON，str 原样。
        retrieved_json = (
            json.dumps(retrieved, ensure_ascii=False, default=str)
            if isinstance(retrieved, (list, tuple))
            else (retrieved or '')
        )
        with get_session() as session:
            session.add(
                TestCaseResult(
                    id=f'res_{uuid.uuid4().hex[:10]}',
                    run_id=run_id,
                    case_id=case.case_id,
                    case_name=case.name,
                    category=phase,
                    version=version,
                    passed=1 if passed else 0,
                    score=score,
                    expected_answer=case.expected_answer,
                    actual_answer=actual,
                    retrieved_memories=retrieved_json,
                    error_message=error,
                    latency_ms=latency_ms,
                    tokens_input=tokens_input,
                    tokens_output=tokens_output,
                    created_at=utcnow(),
                )
            )
            session.commit()

    def update_progress(
        self, run_id: str, completed: int, total: int, status: str, **extra
    ) -> None:
        if _db_off:
            return
        with get_session() as session:
            run = session.get(TestRun, run_id)
            if run is None:
                return
            run.progress = round(completed / total, 4) if total else 1.0
            run.status = status
            for k, v in extra.items():
                setattr(run, k, v)
            session.commit()


def get_store_service() -> StoreService:
    global _store_service
    if '_store_service' not in globals():
        _store_service = StoreService()
    return _store_service
