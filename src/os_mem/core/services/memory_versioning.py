"""D4：as-of 版本裁决（纯决策，不含 DB 写入）。

设计见 ``docs/方案/方案-D4-实体归属与as-of版本裁决.md`` §3.3。

同 ``(user_id, entity_ref, attribute, lifecycle)`` 的事实跨会话裁决：
- **current**：按「对话内时间」``source_started_at`` latest-wins。新事实更新 →
  旧 current 置 superseded（保留行），新版本 INSERT（version+1，supersedes_id 指旧）；
  更早 → 忽略（或归档为 superseded，默认忽略）；同值 → 幂等跳过。
- **historical**：独立生命周期，永不被覆盖（原始快照语义）。
- 裁决时间一律用对话时间而非入库时间，防迟到的旧会话后处理误覆盖。

本模块只产出 ``VersioningPlan``（要插入的新行 + 要置 superseded 的行 id + 忽略计数），
由持久化层在一个事务里执行——纯函数、密集单测、无 IO。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from os_mem.extractor.model.models import NormalizedKey
from os_mem.extractor.utils.normalize import (
    LIFECYCLE_CURRENT,
    LIFECYCLE_HISTORICAL,
    normalize_key,
    projection_key,
)
from os_mem.models.mem_models import MemoryFact


@dataclass(frozen=True)
class IncomingFact:
    """待裁决的一条入站事实（已归一）。"""

    fact: str
    category: str
    key: str
    value: str
    confidence: float
    nk: NormalizedKey
    source_conversation_id: str
    source_started_at: datetime | None


@dataclass(frozen=True)
class ExistingVersion:
    """库里同签名的 current 行裁决所需快照。"""

    id: str
    value: str
    version: int
    source_started_at: datetime | None


@dataclass
class NewVersion:
    """裁决结论：插入一条新 current 行。"""

    fact: IncomingFact
    version: int
    supersedes_id: str


@dataclass
class VersioningPlan:
    inserts: list[NewVersion] = field(default_factory=list)
    supersede_ids: list[str] = field(default_factory=list)
    ignored_older: int = 0
    skipped_same: int = 0
    historical_kept: int = 0


def _is_newer(incoming: datetime | None, existing: datetime | None) -> bool:
    """incoming 是否比 existing 更新。时间缺失时的安全策略：

    - 两者都有时间 → 严格比较对话时间；
    - incoming 有、existing 无 → 视为更新（旧库迁移行可能缺时间）；
    - incoming 无、existing 有 → 不更新（保护带时间的权威行）；
    - 都无 → 更新（退化为后入覆盖，等同旧 upsert 的 LWW 行为）。
    """
    if incoming is not None and existing is not None:
        return incoming > existing
    if incoming is not None and existing is None:
        return True
    if incoming is None and existing is not None:
        return False
    return True


def plan_versioning(
    incoming: list[IncomingFact],
    existing_by_sig: dict[tuple[str, str, str], list[ExistingVersion]],
) -> VersioningPlan:
    """对一批入站事实做版本裁决（纯函数）。

    ``existing_by_sig`` 的 key 是归一签名 (entity_ref, attribute, lifecycle)；
    值为该签名的 current 行列表（正常一条；历史脏数据可能多条，取时间最新者裁决）。

    批内同签名多条入站：先批内收敛（时间最新者代表本批），避免同批旧值误判。
    """
    plan = VersioningPlan()

    # 批内按签名收敛：同签名保留对话时间最新的一条（时间相同保留后一条）
    batch_latest: dict[tuple[str, str, str], IncomingFact] = {}
    batch_order: list[tuple[str, str, str]] = []
    for f in incoming:
        sig = f.nk.signature
        if sig not in batch_latest:
            batch_latest[sig] = f
            batch_order.append(sig)
            continue
        cur = batch_latest[sig]
        if _is_newer(f.source_started_at, cur.source_started_at):
            batch_latest[sig] = f

    for sig in batch_order:
        f = batch_latest[sig]

        # historical 生命周期：独立共存，永不取代也不被取代（同值去重即可）
        if f.nk.lifecycle == LIFECYCLE_HISTORICAL:
            existing = existing_by_sig.get(sig, [])
            if any(e.value == f.value for e in existing):
                plan.skipped_same += 1
            else:
                plan.inserts.append(NewVersion(fact=f, version=1, supersedes_id=""))
                plan.historical_kept += 1
            continue

        existing = existing_by_sig.get(sig, [])
        if not existing:
            plan.inserts.append(NewVersion(fact=f, version=1, supersedes_id=""))
            continue

        # current：取库里时间最新的 current 行作裁决基准
        def sort_key(e: ExistingVersion) -> datetime:
            return e.source_started_at or datetime.min

        latest = max(existing, key=sort_key)

        if latest.value == f.value:
            plan.skipped_same += 1
            continue

        if _is_newer(f.source_started_at, latest.source_started_at):
            plan.inserts.append(
                NewVersion(fact=f, version=latest.version + 1,
                           supersedes_id=latest.id)
            )
            plan.supersede_ids.append(latest.id)
        else:
            plan.ignored_older += 1

    return plan


# --------------------------------------------------------------------------- #
#  高层装配 helper：把归一（normalize_key）收在版本域内，编排层不直接碰归一细节
# --------------------------------------------------------------------------- #
def build_incoming_fact(
    fact: MemoryFact,
    *,
    source_conversation_id: str,
    source_started_at: datetime | None,
) -> IncomingFact:
    """把一条 MemoryFact 归一并装配成裁决输入 IncomingFact。

    归一签名（entity_ref/attribute/lifecycle）是版本域内部细节，调用方只需给
    原始 fact 与来源元数据；normalize_key 单点在此调用，防编排层各自调漂移。
    """
    return IncomingFact(
        fact=fact.fact,
        category=fact.category,
        key=fact.key,
        value=fact.value,
        confidence=fact.confidence,
        # D4-2：实体解析的线索要带上 fact/value（编号前缀/产品码形态）
        nk=normalize_key(fact.category, fact.key, fact=fact.fact, value=fact.value),
        source_conversation_id=source_conversation_id,
        source_started_at=source_started_at,
    )


def current_attribute_touches(
    facts: list[MemoryFact],
) -> dict[str, set[str]]:
    """统计本批事实触及的 current 收敛键：``{category: {projection_key, ...}}``。

    只含 lifecycle=current（historical 不投影）。返回的是**投影键**而非裸
    attribute（D4-2 起非 SELF 实体带 ``<entity>|<attribute>`` 前缀），供投影
    删旧插新圈定范围；归一签名经 normalize_key 单点计算，编排层不直接接触
    entity/attribute/lifecycle。
    """
    touched: dict[str, set[str]] = {}
    for f in facts:
        nk = normalize_key(f.category, f.key, fact=f.fact, value=f.value)
        if nk.lifecycle == LIFECYCLE_CURRENT:
            touched.setdefault(f.category, set()).add(
                projection_key(nk.entity_ref, nk.attribute)
            )
    return touched
