"""会话处理账本服务（conv_process 表）：struct 入库原子化 + 不可逆状态推进。

入口语义（claim 门禁）：
    入库前先检查该会话是否已登记/已入库 —— 已有（任意状态）则跳过后续整个入库流程；
    没有则先插入会话信息（含原数据），状态为初始 PENDING。
之后每个流程阶段「开始前」先推进处理状态（mark），阶段顺序由状态机保证不可逆：
    PENDING → EXTRACTING → SAVING_SQLITE → SAVING_VECTOR → COMPLETED
    任一活跃态 → FAILED（异常终止，人工删行后可整体重跑）。

设计见 docs/方案-会话处理状态机与原子入库.md
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy.exc import IntegrityError
from sqlmodel import select

from os_mem.core.state_machine import IllegalTransitionError, LinearStateMachine
from os_mem.entries.mem_models import ConversationProcess
from os_mem.infra.logger import get_logger
from os_mem.infra.storage import get_session

_logger = get_logger("ConvProcessService")

# ---- 状态常量（单一事实源：与 docs/方案-会话处理状态机与原子入库.md §3.2 一致） ----
STATUS_PENDING = "PENDING"                # 初始：已登记，尚未进入任何阶段
STATUS_EXTRACTING = "EXTRACTING"          # 阶段1：LLM 结构化提取（含数字兜底）
STATUS_SAVING_SQLITE = "SAVING_SQLITE"    # 阶段2：struct_memories 落库
STATUS_SAVING_VECTOR = "SAVING_VECTOR"    # 阶段3：向量化 + Milvus 写入
STATUS_COMPLETED = "COMPLETED"            # 终止：全部完成
STATUS_FAILED = "FAILED"                  # 终止：异常（不可逆，人工裁定）

# struct 入库的阶段顺序（新增阶段时同步更新此处与方案文档）
_PROCESS_STAGES = [
    STATUS_EXTRACTING,
    STATUS_SAVING_SQLITE,
    STATUS_SAVING_VECTOR,
]

_process_machine = LinearStateMachine(
    initial=STATUS_PENDING,
    stages=_PROCESS_STAGES,
    complete=STATUS_COMPLETED,
    failed=STATUS_FAILED,
)

_LAST_ERROR_MAX_LEN = 500


def process_state_machine() -> LinearStateMachine:
    """暴露状态机（单测 / 运维查询用）。"""
    return _process_machine


class ConversationProcessService:
    # ------------------------------------------------------------------ #
    # 原子登记（claim 门禁）
    # ------------------------------------------------------------------ #
    def claim(
        self,
        user_id: str,
        source_session_id: str,
        raw_payload: str = "",
    ) -> Optional[ConversationProcess]:
        """同一事务内「查重 → 登记」：会话入库前的原子门禁。

        返回新登记的记录（status=PENDING，含原数据）；若该会话已存在
        （任意状态，含 COMPLETED/FAILED）返回 None —— 调用方应跳过整个入库流程。
        唯一约束 (user_id, source_session_id) 兜底并发竞争。
        """
        if not user_id or not source_session_id:
            raise ValueError("user_id 与 source_session_id 均不能为空")

        with get_session() as session:
            existing = session.exec(
                select(ConversationProcess).where(
                    ConversationProcess.user_id == user_id,
                    ConversationProcess.source_session_id == source_session_id,
                )
            ).first()
            if existing is not None:
                _logger.info(
                    f"会话已登记/已处理（status={existing.status}），跳过入库: "
                    f"user={user_id} session={source_session_id}"
                )
                return None

            row = ConversationProcess(
                user_id=user_id,
                source_session_id=source_session_id,
                status=STATUS_PENDING,
                raw_payload=raw_payload or "",
            )
            try:
                session.add(row)
                session.commit()
                session.refresh(row)
                _logger.info(
                    f"会话登记成功（PENDING）: user={user_id} session={source_session_id}"
                )
                return row
            except IntegrityError:
                # 并发竞争下唯一约束兜底：另一写入者已登记
                session.rollback()
                _logger.warning(
                    f"会话登记唯一约束冲突，视为已存在并跳过: user={user_id} session={source_session_id}"
                )
                return None

    # ------------------------------------------------------------------ #
    # 状态推进
    # ------------------------------------------------------------------ #
    def _load(self, process_id: str, session) -> ConversationProcess:
        row = session.exec(
            select(ConversationProcess).where(ConversationProcess.id == process_id)
        ).first()
        if row is None:
            raise KeyError(f"conv_process 记录不存在: {process_id}")
        return row

    def mark(self, process_id: str, target: str) -> ConversationProcess:
        """阶段开始前调用：把会话状态沿合法边推进到 target（非法转移抛异常）。"""
        with get_session() as session:
            row = self._load(process_id, session)
            _process_machine.validate(row.status, target)
            row.status = target
            row.updated_at = datetime.utcnow()
            session.add(row)
            session.commit()
            session.refresh(row)
            _logger.info(f"会话状态推进: {process_id} → {target}")
            return row

    def fail(self, process_id: str, error: Exception | str) -> ConversationProcess:
        """异常终止：任一活跃态 → FAILED，记录错误摘要后由调用方决定是否 re-raise。"""
        with get_session() as session:
            row = self._load(process_id, session)
            if row.status == STATUS_FAILED:
                # 已是失败态（如异常发生在 fail 自身途中）：幂等返回
                return row
            _process_machine.validate(row.status, STATUS_FAILED)
            row.status = STATUS_FAILED
            row.last_error = str(error)[:_LAST_ERROR_MAX_LEN]
            row.updated_at = datetime.utcnow()
            session.add(row)
            session.commit()
            session.refresh(row)
            _logger.error(f"会话处理失败（FAILED）: {process_id} — {row.last_error}")
            return row

    def get(self, process_id: str) -> Optional[ConversationProcess]:
        with get_session() as session:
            return self._load(process_id, session)


_conv_process_service: Optional[ConversationProcessService] = None


def get_conv_process_service() -> ConversationProcessService:
    global _conv_process_service
    if _conv_process_service is None:
        _conv_process_service = ConversationProcessService()
    return _conv_process_service


__all__ = [
    "STATUS_PENDING",
    "STATUS_EXTRACTING",
    "STATUS_SAVING_SQLITE",
    "STATUS_SAVING_VECTOR",
    "STATUS_COMPLETED",
    "STATUS_FAILED",
    "ConversationProcessService",
    "get_conv_process_service",
    "process_state_machine",
    "IllegalTransitionError",
]
