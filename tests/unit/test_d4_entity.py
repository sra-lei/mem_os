"""D4-2 纯逻辑单测：确定性实体解析 + 投影收敛键（无 DB / LLM / Milvus）。

覆盖：
1. E1 编号前缀 / E4 产品课程码 两类形式化线索的切分；
2. 保守回退：无线索 / 兜底行 / 空文本 → SELF；
3. **红线回归**：版本演进（$85k→$100k→$95k）不被切成多实体，仍走 as-of 裁决；
4. 投影收敛键：SELF 与既有投影逐字节一致；非 SELF 带实体前缀、互不覆盖。
"""
from __future__ import annotations

from datetime import datetime

import pytest

from os_mem.core.services.memory_versioning import (
    ExistingVersion,
    IncomingFact,
    current_attribute_touches,
    plan_versioning,
)
from os_mem.extractor.utils.normalize import (
    SELF_ENTITY,
    normalize_key,
    projection_key,
    resolve_entity,
)
from os_mem.models.mem_models import MemoryFact


# --------------------------------------------------------------------------- #
#  E1 编号前缀
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "fact,value,expected",
    [
        # case1_05：Velocity 宽带账户号 vs Chase 银行账户号——必须切成两个实体
        ("User's new account number is VEL-89923476.", "VEL-89923476", "ID:VEL"),
        ("Account number VEL-89923476.", "VEL-89923476", "ID:VEL"),
        # case2 保险：租赁确认号
        ("Your rental confirmation number is ENT-7739482.", "ENT-7739482", "ID:ENT"),
        # 多段编号取前缀
        ("Claim CLM-2024-894327 was filed.", "CLM-2024-894327", "ID:CLM"),
    ],
)
def test_e1_id_prefix(fact, value, expected):
    assert resolve_entity(fact, "finance", "account_number", value) == expected


def test_e1_ignores_dates_and_phone_shapes():
    """日期 2024-09-15 / 电话 1-800-VELOCITY 不是编号实体线索（数字开头）。"""
    assert resolve_entity("Installation on 2024-09-15.", "other", "date", "") == SELF_ENTITY
    assert resolve_entity("Call 1-800-VELOCITY anytime.", "contact", "support_phone", "") == SELF_ENTITY


# --------------------------------------------------------------------------- #
#  E4 产品 / 课程码
# --------------------------------------------------------------------------- #
def test_e4_course_code_in_education_category():
    assert (
        resolve_entity(
            "Registered for MAT-151 Calculus I, section 01.", "education", "course", "MAT-151"
        )
        == "PROD:MAT-151"
    )


def test_e4_not_applied_outside_product_categories():
    """非产品类 category 下的同形态代码走 E1（ID 前缀），不做产品实体。"""
    assert resolve_entity("Ref MAT-151 attached.", "finance", "reference", "") == "ID:MAT"


# --------------------------------------------------------------------------- #
#  保守回退
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "fact,category,key,value",
    [
        ("User's checking account number is 8829447651", "finance", "account_number", "8829447651"),
        ("User's first three months are at 50% off services", "finance", "discount", ""),
        ("", "finance", "account_number", ""),
    ],
)
def test_falls_back_to_self_without_clue(fact, category, key, value):
    assert resolve_entity(fact, category, key, value) == SELF_ENTITY


@pytest.mark.parametrize("key", ["verbatim_d6adc85805ee", "raw_conversation", "raw_conversation_2"])
def test_verbatim_and_degrade_rows_stay_self(key):
    """兜底/降级行的 key 本身即内容哈希（唯一），不参与实体切分。"""
    assert (
        resolve_entity(
            "Your new account number is VEL-89923476.", "other", "", key, ""
        )
        == SELF_ENTITY
    )


def test_normalize_key_defaults_to_self_without_fact_text():
    """既有调用点（不传 fact/value）行为不变——实体恒 SELF。"""
    nk = normalize_key("finance", "wire_amount")
    assert nk.entity_ref == SELF_ENTITY


def test_two_account_numbers_get_distinct_signatures():
    """D4-2 核心收益：多实体并存不再撞同一签名（原静默覆盖点）。"""
    a = normalize_key("finance", "account_number", fact="User's new account number is VEL-89923476.", value="VEL-89923476")
    b = normalize_key("finance", "account_number", fact="User's checking account number is 8829447651", value="8829447651")
    assert a.entity_ref == "ID:VEL"
    assert b.entity_ref == SELF_ENTITY
    assert a.signature != b.signature


# --------------------------------------------------------------------------- #
#  红线回归：版本演进不被切成多实体
# --------------------------------------------------------------------------- #
def _incoming(value: str, fact: str, started_at: datetime) -> IncomingFact:
    mf = MemoryFact(category="finance", key="wire_amount", fact=fact, value=value, confidence=0.9)
    return IncomingFact(
        fact=mf.fact,
        category=mf.category,
        key=mf.key,
        value=mf.value,
        confidence=mf.confidence,
        nk=normalize_key(mf.category, mf.key, fact=mf.fact, value=mf.value),
        source_conversation_id="c1",
        source_started_at=started_at,
    )


def test_version_evolution_is_not_split_into_entities():
    """$85k→$100k→$95k 是同一实体的版本演进：全部 SELF 同签名，走 as-of 裁决。"""
    t1, t2, t3 = (datetime(2024, 9, d) for d in (15, 25, 26))
    f1 = _incoming("$85,000", "User will send $85,000 via wire", t1)
    f2 = _incoming("$100,000", "User will send $100,000 via wire", t2)
    f3 = _incoming("$95,000", "User will send $95,000 via wire", t3)
    assert {f1.nk.signature, f2.nk.signature, f3.nk.signature} == {("SELF", "wire_amount", "current")}

    # 重演：85k 落 current → 100k 取代 → 95k 取代；版本链完整
    plan1 = plan_versioning([f1], {})
    assert len(plan1.inserts) == 1 and plan1.inserts[0].version == 1

    existing = [ExistingVersion(id="v1", value="$85,000", version=1, source_started_at=t1)]
    plan2 = plan_versioning([f2], {("SELF", "wire_amount", "current"): existing})
    assert plan2.supersede_ids == ["v1"] and plan2.inserts[0].version == 2

    existing2 = [ExistingVersion(id="v2", value="$100,000", version=2, source_started_at=t2)]
    plan3 = plan_versioning([f3], {("SELF", "wire_amount", "current"): existing2})
    assert plan3.supersede_ids == ["v2"] and plan3.inserts[0].version == 3
    assert plan3.inserts[0].fact.value == "$95,000"


# --------------------------------------------------------------------------- #
#  投影收敛键
# --------------------------------------------------------------------------- #
def test_projection_key_self_is_byte_identical_to_old_behaviour():
    assert projection_key(SELF_ENTITY, "wire_amount") == "wire_amount"
    assert projection_key("", "wire_amount") == "wire_amount"


def test_projection_key_separates_entities():
    assert projection_key("ID:VEL", "account_number") == "ID:VEL|account_number"
    assert projection_key(SELF_ENTITY, "account_number") == "account_number"
    assert projection_key("ID:VEL", "account_number") != projection_key(SELF_ENTITY, "account_number")


def test_current_attribute_touches_returns_projection_keys():
    """投影删旧插新的圈定范围必须用收敛键：两实体的同名属性各占一格。"""
    facts = [
        MemoryFact(category="finance", key="account_number", fact="User's new account number is VEL-89923476.", value="VEL-89923476", confidence=0.9),
        MemoryFact(category="finance", key="account_number", fact="User's checking account number is 8829447651", value="8829447651", confidence=0.9),
        # historical 不进投影
        MemoryFact(category="finance", key="original_wire_amount", fact="Original wire was $85,000", value="$85,000", confidence=0.9),
    ]
    touched = current_attribute_touches(facts)
    assert touched == {"finance": {"ID:VEL|account_number", "account_number"}}
