"""D4-2 v2 单测：属性策略（单值/多值）+ P2 专名抽取 + P3 key 内实例名剥离。

核心不变量（安全阀）：
- **单值属性永不按值切分** → 版本演进（$85k→$100k→$95k）留在同一签名；
- 多值属性按实例切分 → 多实例并存；
- 抽不到实例名 / 属性不在白名单 → 回落 SELF（= 现状，绝不更差）。
"""
from __future__ import annotations

import pytest

from os_mem.core.services.memory_versioning import (
    IncomingFact,
    collapse_same_signature,
    plan_versioning,
)
from os_mem.extractor.utils.normalize import (
    POLICY_FUNCTIONAL,
    POLICY_MULTI_VALUED,
    SELF_ENTITY,
    attribute_policy,
    normalize_key,
    projection_key,
)
from os_mem.models.mem_models import MemoryFact


def _mf(category: str, key: str, fact: str, value: str = "") -> MemoryFact:
    return MemoryFact(category=category, key=key, fact=fact, value=value, confidence=0.9)


def _sig(category: str, key: str, fact: str, value: str = ""):
    return normalize_key(category, key, fact=fact, value=value)


# --------------------------------------------------------------------------- #
#  属性策略表
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "category,attribute,expected",
    [
        ("health", "medication", POLICY_MULTI_VALUED),
        ("education", "course_schedule", POLICY_MULTI_VALUED),
        ("finance", "credit_card", POLICY_MULTI_VALUED),
        # 单值：金额/日期/电话——绝不切
        ("finance", "wire_amount", POLICY_FUNCTIONAL),
        ("finance", "closing_date", POLICY_FUNCTIONAL),
        ("contact", "phone_number", POLICY_FUNCTIONAL),
        # 未登记属性一律 functional（缺省零回归）
        ("health", "height", POLICY_FUNCTIONAL),
    ],
)
def test_attribute_policy(category, attribute, expected):
    assert attribute_policy(category, attribute) == expected


# --------------------------------------------------------------------------- #
#  P3：key 内实例名剥离（中文场景的关键路径）
# --------------------------------------------------------------------------- #
def test_p3_strips_instance_from_key():
    nk = _sig("health", "medication_atorvastatin", "User takes atorvastatin 20mg at night")
    assert nk.entity_ref == "DRUG:atorvastatin"
    assert nk.attribute == "medication"  # 属性归到稳定的多值属性
    assert projection_key(nk.entity_ref, nk.attribute) == "DRUG:atorvastatin|medication"


def test_p3_works_for_chinese_instance():
    """中文无大小写线索，P2 失效——P3 是唯一可用路径。"""
    nk = _sig("health", "medication_甲氨蝶呤", "用户服用甲氨蝶呤 15 毫克")
    assert nk.entity_ref == "DRUG:甲氨蝶呤"
    assert nk.attribute == "medication"


def test_p3_longest_prefix_wins():
    """`course_schedule_x` 不能被 `course_` 先吞掉。"""
    nk = _sig("education", "course_schedule_psychology101", "User is registered for Psychology 101")
    assert nk.attribute == "course_schedule"
    assert nk.entity_ref.startswith("COURSE:")


def test_p3_interacts_with_lifecycle_prefix():
    """生命周期前缀先剥离，P3 再剥实例名；剩余段是生命周期词则不当作实例名。"""
    nk = _sig("health", "new_medication_lisinopril", "User takes lisinopril")
    assert nk.attribute == "medication"
    assert nk.entity_ref == "DRUG:lisinopril"
    assert nk.lifecycle == "current"

    # 裸 `<属性>_<生命周期词>` 不剥（防误吞）
    nk2 = _sig("health", "medication_current", "User takes lisinopril")
    assert nk2.entity_ref == SELF_ENTITY


def test_p3_bare_attribute_has_no_instance():
    nk = _sig("health", "medication", "User takes lisinopril for blood pressure")
    assert nk.attribute == "medication"
    # 无实例线索（P2 也抽不到唯一专名）→ SELF
    assert nk.entity_ref == SELF_ENTITY


# --------------------------------------------------------------------------- #
#  回归护栏：复合属性名不得被误当实例名（2026-09-12 三用例实测出的三处垃圾实体）
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "category,key,fact",
    [
        # 实测：曾产出 DRUG:supply
        ("health", "medication_supply", "User has about two weeks' worth of medication left"),
        # 实测：曾产出 ACCT:balance（P3a 也抽不到 → 应回落 SELF）
        ("finance", "investment_account_balance", "User has about $285,000 in a 401k"),
        # 实测：曾产出 COURSE:registration（`course_registration` 本身就在白名单里）
        (
            "education",
            "course_registration",
            "User is registered for Psychology 101 with Professor Williams, section 03",
        ),
    ],
)
def test_p3_compound_attribute_is_not_stripped(category, key, fact):
    """剩余段在事实文本里找不到 → 判定为复合属性名，不剥离（零词表护栏）。"""
    nk = _sig(category, key, fact)
    suffix = key.split("_", 1)[1]
    assert suffix not in nk.entity_ref.lower()


def test_p3_positive_guard_keeps_real_instance():
    """反面对照：实例名确实出现在文本中 → 正常剥离。"""
    nk = _sig("health", "medication_lisinopril", "User takes Lisinopril 10 milligrams once daily")
    assert nk.entity_ref == "DRUG:lisinopril"
    assert nk.attribute == "medication"


def test_p3_comes_before_p3a_when_suffix_is_a_real_instance():
    """`credit_card_balance` + "Amex … balance"：balance 是小写限定词 → P3 被挡，
    由 P3a 正确抽出 Amex（实测曾误产出 CARD:balance）。"""
    nk = _sig("finance", "credit_card_balance", "User's Amex has about $1,100 balance")
    assert nk.entity_ref == "CARD:amex"

    # 反面对照：同一限定词在小写文本里出现 → 不得成为实体
    nk2 = _sig("finance", "credit_card_balance", "User's balance is $1,100")
    assert nk2.entity_ref == SELF_ENTITY


def test_p3a_takes_over_when_p3_blocked():
    """P3 被护栏挡下后，P3a 专名抽取接管（11 房贷实测路径）。"""
    nk = _sig(
        "finance", "investment_account_balance",
        "User has a Vanguard brokerage account with about $125,000",
    )
    assert nk.entity_ref == "ACCT:vanguard"
    nk2 = _sig("finance", "credit_card_balance", "User has a Visa credit card with a $2,300 balance")
    assert nk2.entity_ref == "CARD:visa"


# --------------------------------------------------------------------------- #
#  P2：专名抽取（仅多值属性，恰好一个才采用）
# --------------------------------------------------------------------------- #
def test_p2_single_proper_noun_becomes_instance():
    nk = _sig("finance", "credit_card", "The Visa has a $2,300 balance")
    assert nk.entity_ref == "CARD:visa"


def test_p2_multiple_proper_nouns_falls_back_to_self():
    """一句多主体 → 抽到多个 → 保守回退 SELF（拆句另立项）。"""
    nk = _sig("finance", "credit_card", "The Visa has $2,300, MasterCard paid off, Amex $1,100")
    assert nk.entity_ref == SELF_ENTITY


def test_p2_stopwords_are_not_instances():
    nk = _sig("health", "medication", "The User takes the medicine on Monday")
    assert nk.entity_ref == SELF_ENTITY


def test_p2_not_applied_to_single_valued_attribute():
    """单值属性上即使出现专名也不切（安全阀）。"""
    nk = _sig("finance", "wire_amount", "User will send $95,000 via Chase wire")
    assert nk.entity_ref == SELF_ENTITY


# --------------------------------------------------------------------------- #
#  安全阀回归：多值切分不破坏版本演进（同一实例内仍走 latest-wins）
# --------------------------------------------------------------------------- #
def test_same_instance_still_version_chains():
    """同一药物的剂量变化：同签名 → 走 as-of 裁决，而非被切成两个实体。"""
    a = _sig("health", "medication_atorvastatin", "User takes atorvastatin 20mg")
    b = _sig("health", "medication_atorvastatin", "User's atorvastatin dose increased to 40mg")
    assert a.signature == b.signature == ("DRUG:atorvastatin", "medication", "current")


def test_single_valued_version_evolution_unchanged():
    """单值属性：$85k→$100k→$95k 仍同签名（v2 不得破坏 D4-1 行为）。"""
    sigs = {
        _sig("finance", "wire_amount", f"User will send {v} via wire").signature
        for v in ("$85,000", "$100,000", "$95,000")
    }
    assert sigs == {("SELF", "wire_amount", "current")}


def test_two_drugs_no_longer_collapse():
    """实证场景（03 医疗）：lisinopril 与 atorvastatin 并存，不再被批内收敛丢弃。"""
    facts = [
        _mf("health", "medication_lisinopril", "User takes lisinopril for blood pressure"),
        _mf("health", "medication_atorvastatin", "User takes atorvastatin 20mg at night"),
    ]
    assert len(collapse_same_signature(facts)) == 2


# --------------------------------------------------------------------------- #
#  行为契约：收敛视图仍与版本裁决一致（v2 后保持）
# --------------------------------------------------------------------------- #
def test_collapse_view_matches_plan_versioning_with_policy():
    from datetime import datetime

    facts = [
        _mf("health", "medication_lisinopril", "User takes lisinopril for blood pressure"),
        _mf("health", "medication_atorvastatin", "User takes atorvastatin 20mg at night"),
        _mf("health", "medication", "User takes lisinopril"),  # 无实例 → SELF，不与他条冲突
    ]
    explicit = collapse_same_signature(facts)
    t = datetime(2024, 11, 1, 9, 0, 0)
    incoming = [
        IncomingFact(
            fact=f.fact, category=f.category, key=f.key, value=f.value,
            confidence=f.confidence,
            nk=normalize_key(f.category, f.key, fact=f.fact, value=f.value),
            source_conversation_id="c1", source_started_at=t,
        )
        for f in facts
    ]
    plan = plan_versioning(incoming, {})
    assert [nv.fact.fact for nv in plan.inserts] == [f.fact for f in explicit]
    assert plan.batch_collapsed == len(facts) - len(explicit)
