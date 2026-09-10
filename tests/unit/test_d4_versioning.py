"""D4-1 纯逻辑单测：key 归一 + as-of 版本裁决（无 DB / LLM / Milvus）。

重演 case12 电汇金额三会话演进：$85,000(09-15) → $100,000(09-25) →
$95,000(09-26)，验证 latest-wins 版本链 + original_* 历史快照独立共存。
"""
from __future__ import annotations

from datetime import datetime

import pytest

from os_mem.core.services.memory_versioning import (
    ExistingVersion,
    IncomingFact,
    plan_versioning,
)
from os_mem.extractor.utils.normalize import (
    LIFECYCLE_CURRENT,
    LIFECYCLE_HISTORICAL,
    normalize_key,
)


# --------------------------------------------------------------------------- #
#  normalize_key
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "category,key,attr,life",
    [
        # case12 wire 同义簇全部收敛
        ("finance", "wire_amount", "wire_amount", LIFECYCLE_CURRENT),
        ("finance", "wire_transfer_amount", "wire_amount", LIFECYCLE_CURRENT),
        ("finance", "transfer_amount", "wire_amount", LIFECYCLE_CURRENT),
        ("finance", "final_transfer_amount", "wire_amount", LIFECYCLE_CURRENT),
        # 日期 / reference 簇
        ("finance", "wire_transfer_date", "wire_date", LIFECYCLE_CURRENT),
        ("finance", "wire_reference_number", "wire_reference", LIFECYCLE_CURRENT),
        ("finance", "transfer_reference_number", "wire_reference", LIFECYCLE_CURRENT),
        # 历史快照：去前缀归一本体，但 lifecycle=historical
        ("finance", "original_wire_amount", "wire_amount", LIFECYCLE_HISTORICAL),
        ("finance", "previous_balance", "balance", LIFECYCLE_HISTORICAL),
        ("finance", "old_policy_number", "policy_number", LIFECYCLE_HISTORICAL),
        # 当前值强化前缀剥离
        ("finance", "new_flight_cost", "flight_cost", LIFECYCLE_CURRENT),
        ("finance", "current_balance", "balance", LIFECYCLE_CURRENT),
        # last_ 不被判为历史（医疗 last_fill_date = 最近一次 = current）
        ("health", "last_fill_date", "last_fill_date", LIFECYCLE_CURRENT),
    ],
)
def test_normalize_key_alias_and_lifecycle(category, key, attr, life):
    nk = normalize_key(category, key)
    assert nk.attribute == attr
    assert nk.lifecycle == life
    assert nk.entity_ref == "SELF"


def test_normalize_key_unknown_passes_through_unchanged():
    """未登记的漂移 key 保持独立 = 旧行为（安全不误并）。"""
    nk = normalize_key("finance", "gift_amount")
    assert nk.attribute == "gift_amount"
    assert nk.lifecycle == LIFECYCLE_CURRENT


def test_historical_signature_differs_from_current():
    cur = normalize_key("finance", "wire_amount")
    hist = normalize_key("finance", "original_wire_amount")
    assert cur.attribute == hist.attribute == "wire_amount"
    assert cur.signature != hist.signature  # lifecycle 入签名 → 互不取代


# --------------------------------------------------------------------------- #
#  plan_versioning —— case12 重演
# --------------------------------------------------------------------------- #
DT = datetime


def _fact(key, value, ts, *, category="finance"):
    nk = normalize_key(category, key)
    return IncomingFact(
        fact=f"wire is {value}", category=category, key=key, value=value,
        confidence=0.9, nk=nk, source_conversation_id=f"conv-{ts.day}",
        source_started_at=ts,
    )


def test_case12_three_session_evolution_latest_wins():
    """$85k(15) → $100k(25) → $95k(26)：最终 current=$95k，版本链 v1→v2→v3。"""
    # 会话1：初始 $85,000（final_transfer_amount 与 wire_amount 同批同义→批内收敛）
    s1 = [_fact("final_transfer_amount", "$85,000", DT(2024, 9, 15))]
    plan1 = plan_versioning(s1, {})
    assert len(plan1.inserts) == 1
    assert plan1.inserts[0].version == 1
    assert plan1.inserts[0].fact.value == "$85,000"

    v1_id = "row-v1"
    existing = {
        ("SELF", "wire_amount", "current"): [
            ExistingVersion(v1_id, "$85,000", 1, DT(2024, 9, 15))
        ]
    }
    # 会话2：改成 $100,000（用 transfer_amount 漂移 key）
    s2 = [_fact("transfer_amount", "$100,000", DT(2024, 9, 25))]
    plan2 = plan_versioning(s2, existing)
    assert len(plan2.inserts) == 1
    assert plan2.inserts[0].fact.value == "$100,000"
    assert plan2.inserts[0].version == 2
    assert plan2.inserts[0].supersedes_id == v1_id
    assert plan2.supersede_ids == [v1_id]

    v2_id = "row-v2"
    existing["SELF", "wire_amount", "current"].append(
        # 库里 v1 已被置 superseded（裁决只看 current；这里模拟两条 current 脏数据取最新）
        ExistingVersion(v2_id, "$100,000", 2, DT(2024, 9, 25))
    )
    # 会话3：Patricia 改回 $95,000（wire_transfer_amount 漂移 key）
    s3 = [_fact("wire_transfer_amount", "$95,000", DT(2024, 9, 26))]
    plan3 = plan_versioning(s3, existing)
    assert len(plan3.inserts) == 1
    assert plan3.inserts[0].fact.value == "$95,000"
    assert plan3.inserts[0].version == 3
    assert plan3.inserts[0].supersedes_id == v2_id  # 链接到上一版 v2


def test_earlier_incoming_is_ignored():
    """迟到的旧会话（时间更早）不得覆盖 current。"""
    existing = {
        ("SELF", "wire_amount", "current"): [
            ExistingVersion("r1", "$95,000", 3, DT(2024, 9, 26))
        ]
    }
    plan = plan_versioning(
        [_fact("wire_amount", "$85,000", DT(2024, 9, 15))], existing
    )
    assert plan.inserts == []
    assert plan.ignored_older == 1


def test_same_value_is_idempotent():
    existing = {
        ("SELF", "wire_amount", "current"): [
            ExistingVersion("r1", "$95,000", 1, DT(2024, 9, 26))
        ]
    }
    plan = plan_versioning(
        [_fact("transfer_amount", "$95,000", DT(2024, 9, 27))], existing
    )
    assert plan.inserts == []
    assert plan.skipped_same == 1


def test_historical_snapshot_coexists_never_overrides():
    """original_wire_amount 与 current 是不同签名：各自独立，互不取代。"""
    existing = {
        ("SELF", "wire_amount", "current"): [
            ExistingVersion("r1", "$95,000", 3, DT(2024, 9, 26))
        ]
    }
    # 同批既来 current 更新又来 original 快照
    facts = [
        _fact("original_wire_amount", "$85,000", DT(2024, 9, 25)),
    ]
    plan = plan_versioning(facts, existing)
    # historical 不看 current 槽位 → 独立插入 v1，不动 current
    assert len(plan.inserts) == 1
    assert plan.inserts[0].fact.nk.lifecycle == LIFECYCLE_HISTORICAL
    assert plan.supersede_ids == []
    assert plan.historical_kept == 1


def test_batch_convergence_keeps_latest_within_batch():
    """同一会话同签名新旧两条（LLM 输出乱序）：批内只留时间最新者。"""
    facts = [
        _fact("wire_amount", "$85,000", DT(2024, 9, 15, 9)),
        _fact("transfer_amount", "$95,000", DT(2024, 9, 15, 16)),
    ]
    plan = plan_versioning(facts, {})
    assert len(plan.inserts) == 1
    assert plan.inserts[0].fact.value == "$95,000"


def test_null_time_fallback_does_not_protect_with_both_missing():
    """双方都缺时间 → 退化为后入覆盖（等同旧 LWW upsert）。"""
    existing = {
        ("SELF", "balance", "current"): [
            ExistingVersion("r1", "100", 1, None)
        ]
    }
    nk = normalize_key("finance", "balance")
    incoming = [IncomingFact(
        fact="b", category="finance", key="balance", value="200",
        confidence=0.9, nk=nk, source_conversation_id="c", source_started_at=None,
    )]
    plan = plan_versioning(incoming, existing)
    assert len(plan.inserts) == 1
    assert plan.inserts[0].fact.value == "200"


def test_incoming_missing_time_does_not_override_timestamped_current():
    """入站缺时间而 current 有时间 → 不覆盖（保护权威行）。"""
    existing = {
        ("SELF", "balance", "current"): [
            ExistingVersion("r1", "100", 1, DT(2024, 9, 26))
        ]
    }
    nk = normalize_key("finance", "balance")
    incoming = [IncomingFact(
        fact="b", category="finance", key="balance", value="200",
        confidence=0.9, nk=nk, source_conversation_id="c", source_started_at=None,
    )]
    plan = plan_versioning(incoming, existing)
    assert plan.inserts == []
    assert plan.ignored_older == 1
