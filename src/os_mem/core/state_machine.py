"""不可逆线性状态机：会话处理状态的前进守卫。

语义（见 docs/方案-会话处理状态机与原子入库.md）：
- 状态只允许沿合法边前进：初始态 → 阶段1 → … → 阶段N → 完成态；
- 任一活跃态可因异常进入失败态；
- 无反向边 / 无跳级 / 终止态（完成、失败）无出边 —— 会话状态不可逆。

设计为无三方依赖的小类，便于单测；具体状态集合与顺序由调用方
（conv_process_service）按流水线阶段构造，本类不做领域假设。
"""

from __future__ import annotations


class IllegalTransitionError(Exception):
    """尝试执行状态机不允许的转移（回退 / 跳级 / 终止态外出）。"""


class LinearStateMachine:
    def __init__(
        self,
        initial: str,
        stages: list[str],
        complete: str = "COMPLETED",
        failed: str = "FAILED",
    ) -> None:
        """由有序阶段自动生成合法边。

        initial → stages[0] → stages[1] → … → stages[-1] → complete
        每个 {initial} ∪ stages 活跃态 → failed（异常终止）。
        stages 为空时：initial → complete。
        """
        if not stages:
            raise ValueError("stages 不能为空（至少需要一个阶段）")
        self.initial = initial
        self.complete = complete
        self.failed = failed
        self.stages = tuple(stages)
        self._active = (initial,) + self.stages
        self._terminal = (complete, failed)

        edges: dict[str, set[str]] = {s: set() for s in self._active}
        prev = initial
        for stage in self.stages:
            edges[prev].add(stage)
            prev = stage
        edges[prev].add(complete)
        for s in self._active:
            edges[s].add(failed)
        self._edges = edges

    @property
    def active_states(self) -> tuple[str, ...]:
        return self._active

    @property
    def terminal_states(self) -> tuple[str, ...]:
        return self._terminal

    def is_active(self, state: str) -> bool:
        return state in self._active

    def is_terminal(self, state: str) -> bool:
        return state in self._terminal

    def next_states(self, current: str) -> set[str]:
        return set(self._edges.get(current, set()))

    def can_transition(self, current: str, target: str) -> bool:
        return target in self.next_states(current)

    def validate(self, current: str, target: str) -> None:
        """非法转移（未知状态 / 回退 / 跳级 / 终止态外出）抛 IllegalTransitionError。

        同态（current == target）视为 no-op 通过：允许重复断言，也兼容
        「claim 已把状态推进到阶段 X，紧接着该阶段开始标记仍上报 X」的编排。
        （注意：FAILED/过期会话的整轮重启不走本机，由 claim 的 CAS 条件更新处理。）
        """
        if current == target:
            return
        if current not in self._edges:
            raise IllegalTransitionError(
                f"未知当前状态: {current!r}（合法: {sorted(self._edges)}）"
            )
        if not self.can_transition(current, target):
            raise IllegalTransitionError(
                f"非法状态转移: {current!r} → {target!r}（当前可去向: "
                f"{sorted(self.next_states(current))}）"
            )

    def transition(self, current: str, target: str) -> str:
        """校验并返回目标状态（不改外部存储，由调用方落库）。"""
        self.validate(current, target)
        return target
