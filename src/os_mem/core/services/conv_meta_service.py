"""对话元数据服务（conv_meta 表）：会话元数据 + 不可逆处理状态推进。

定位：对话元数据表 —— 存会话元数据（来源、条数、起止时间），处理状态是顺带管理的
字段；会话原文(messages)不存本表，逐条消息走 conv_messages。

门禁语义（claim，见 docs/方案-会话处理状态机与原子入库.md §3.3）：
- 该会话尚不存在        → 插入一行（初始 PENDING + 元数据），本调用负责处理
- 已存在且 COMPLETED    → 跳过（唯一真正"已入库完成"的终止态）
- 已存在但非完成态      → 「接着处理」：
    · FAILED              → 立即重启（attempts+1、清空 last_error、回到 EXTRACTING）
    · PENDING/中途阶段    → 仅当行已「过期」（updated_at 早于租约窗口，判定为崩溃残留）
                            才接管重启；否则视为他人正在处理 → 本次跳过
并发安全：接管动作是单语句 CAS 条件更新（UPDATE … WHERE status=观测值），SQLite
单写者下天然只有一个执行者成功；唯一约束 (user_id, source_session_id) 兜底并发插入。

运行内阶段推进（mark）仍严格走状态机合法边，不可逆：
    PENDING → EXTRACTING → SAVING_SQLITE → SAVING_VECTOR → COMPLETED
    任一活跃态 → FAILED（异常终止，可经下次 claim 重启）
"""

from __future__ import annotations

import os
from datetime import datetime
from typing import Optional

from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlmodel import select

from os_mem.core.state_machine import IllegalTransitionError, LinearStateMachine
from os_mem.entries.mem_models import ConversationMeta
from os_mem.infra.logger import get_logger
from os_mem.infra.storage import get_session

_logger = get_logger("ConvMetaService")

# ---- 状态常量（单一事实源：与 docs/方案-会话处理状态机与原子入库.md §3.2 一致） ----
STATUS_PENDING = "PENDING"                # 初始：已登记，尚未进入任何阶段
STATUS_EXTRACTING = "EXTRACTING"          # 阶段1：LLM 结构化提取（含数字兜底）
STATUS_SAVING_SQLITE = "SAVING_SQLITE"    # 阶段2：struct_memories 落库
STATUS_SAVING_VECTOR = "SAVING_VECTOR"    # 阶段3：向量化 + Milvus 写入
STATUS_COMPLETED = "COMPLETED"            # 终止：全部完成（唯一跳过态）
STATUS_FAILED = "FAILED"                  # 异常终止（非完成态：下次 claim 重启）

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

# 租约窗口：PENDING/中途阶段超过该时长未推进 → 视为崩溃残留，允许接管重启。
# 环境变量 CONV_LEASE_SECONDS 可覆盖（默认 30 分钟，需大于单个最长阶段耗时）。
_LEASE_SECONDS = int(os.getenv("CONV_LEASE_SECONDS", "1800"))

_LAST_ERROR_MAX_LEN = 500


def process_state_machine() -> LinearStateMachine:
    """暴露状态机（单测 / 运维查询用）。"""
    return _process_machine


class ConversationMetaService:
    # ------------------------------------------------------------------ #
    # 原子登记（claim 门禁）
    # ------------------------------------------------------------------ #
    def claim(
        self,
        user_id: str,
        source_session_id: str,
        *,
        message_count: int = 0,
        started_at: Optional[datetime] = None,
        ended_at: Optional[datetime] = None,
    ) -> Optional[ConversationMeta]:
        """会话入库门禁：返回可处理记录，或 None（跳过）。

        - 返回记录：status 已处于 EXTRACTING —— 本调用负责继续处理；
          新建会话时为 PENDING（首个阶段开始标记会推进到 EXTRACTING）。
        - 返回 None：COMPLETED（已入库完成）或 他人正在处理（含未过期的中途状态），
          本次调用应整体跳过入库流程，不进入任何阶段。
        """
        if not user_id or not source_session_id:
            raise ValueError("user_id 与 source_session_id 均不能为空")

        with get_session() as session:
            row = session.exec(
                select(ConversationMeta).where(
                    ConversationMeta.user_id == user_id,
                    ConversationMeta.source_session_id == source_session_id,
                )
            ).first()

            # 1) 从未见过该会话：插入（初始 PENDING + 元数据），唯一约束兜底并发
            if row is None:
                row = ConversationMeta(
                    user_id=user_id,
                    source_session_id=source_session_id,
                    status=STATUS_PENDING,
                    message_count=message_count,
                    started_at=started_at,
                    ended_at=ended_at,
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
                    # 并发竞争：另一执行者已抢先插入 → 按已有行重新判定
                    session.rollback()
                    row = session.exec(
                        select(ConversationMeta).where(
                            ConversationMeta.user_id == user_id,
                            ConversationMeta.source_session_id == source_session_id,
                        )
                    ).first()

            # 2) 已存在：按状态判定是否「接着处理」
            now = datetime.utcnow()
            if row.status == STATUS_COMPLETED:
                _logger.info(f"会话已入库完成（COMPLETED），跳过: session={source_session_id}")
                return None

            restartable = row.status == STATUS_FAILED or self._is_stale(row, now)
            if not restartable:
                _logger.info(
                    f"会话正在处理中（status={row.status}，租约未过期），本次跳过: "
                    f"session={source_session_id}"
                )
                return None

            # 3) 接管重启：单语句 CAS（WHERE status=观测值）—— 并发下仅一个执行者成功
            taken = self._cas_take_over(session, row.id, observed_status=row.status)
            if not taken:
                _logger.warning(
                    f"会话接管竞争失败（他人已接管），本次跳过: session={source_session_id}"
                )
                return None
            _logger.info(
                f"会话接管重启（attempts+1 → EXTRACTING）: session={source_session_id}"
            )
            return session.exec(
                select(ConversationMeta).where(ConversationMeta.id == row.id)
            ).first()

    @staticmethod
    def _is_stale(row: ConversationMeta, now: datetime) -> bool:
        """PENDING/中途状态超过租约窗口未推进 → 崩溃残留，可接管。"""
        if row.updated_at is None:
            return True
        age = (now - row.updated_at).total_seconds()
        return age > _LEASE_SECONDS

    @staticmethod
    def _cas_take_over(session, row_id: str, observed_status: str) -> bool:
        """原子接管：仅当当前状态仍是观测值时置为 EXTRACTING 并 attempts+1。

        返回是否更新成功（1 行）。SQLite 单写者下，两个并发接管只有一个匹配。
        """
        result = session.exec(
            update(ConversationMeta)
            .where(
                ConversationMeta.id == row_id,
                ConversationMeta.status == observed_status,
            )
            .values(
                status=STATUS_EXTRACTING,
                attempts=ConversationMeta.attempts + 1,
                last_error="",
                updated_at=datetime.utcnow(),
            )
        )
        session.commit()
        return (result.rowcount or 0) == 1

    # ------------------------------------------------------------------ #
    # 运行内状态推进（严格状态机，不可逆）
    # ------------------------------------------------------------------ #
    def _load(self, meta_id: str, session) -> ConversationMeta:
        row = session.exec(
            select(ConversationMeta).where(ConversationMeta.id == meta_id)
        ).first()
        if row is None:
            raise KeyError(f"conv_meta 记录不存在: {meta_id}")
        return row

    def mark(self, meta_id: str, target: str) -> ConversationMeta:
        """阶段开始前调用：沿状态机合法边推进到 target（同态 no-op，非法转移抛异常）。"""
        with get_session() as session:
            row = self._load(meta_id, session)
            _process_machine.validate(row.status, target)
            row.status = target
            row.updated_at = datetime.utcnow()
            session.add(row)
            session.commit()
            session.refresh(row)
            _logger.info(f"会话状态推进: {meta_id} → {target}")
            return row

    def fail(self, meta_id: str, error: Exception | str) -> ConversationMeta:
        """异常终止：任一活跃态 → FAILED，记录错误摘要；重复 fail 幂等。"""
        with get_session() as session:
            row = self._load(meta_id, session)
            if row.status == STATUS_FAILED:
                return row
            _process_machine.validate(row.status, STATUS_FAILED)
            row.status = STATUS_FAILED
            row.last_error = str(error)[:_LAST_ERROR_MAX_LEN]
            row.updated_at = datetime.utcnow()
            session.add(row)
            session.commit()
            session.refresh(row)
            _logger.error(f"会话处理失败（FAILED）: {meta_id} — {row.last_error}")
            return row

    def get(self, meta_id: str) -> Optional[ConversationMeta]:
        with get_session() as session:
            return self._load(meta_id, session)


_conv_meta_service: Optional[ConversationMetaService] = None


def get_conversation_meta_service() -> ConversationMetaService:
    global _conv_meta_service
    if _conv_meta_service is None:
        _conv_meta_service = ConversationMetaService()
    return _conv_meta_service


__all__ = [
    "STATUS_PENDING",
    "STATUS_EXTRACTING",
    "STATUS_SAVING_SQLITE",
    "STATUS_SAVING_VECTOR",
    "STATUS_COMPLETED",
    "STATUS_FAILED",
    "ConversationMetaService",
    "get_conversation_meta_service",
    "process_state_machine",
    "IllegalTransitionError",
]
