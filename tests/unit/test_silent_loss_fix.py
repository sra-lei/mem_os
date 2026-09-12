"""静默丢失修复单测（Fix B 判据对齐 + Fix C 可观测）。

背景：2026-09-12 审计出 layer1 有 17 条「兜底句被 R1 剪掉、但其 token 在最终
落库事实里也找不到」。根因是 **R1 的覆盖判据用「LLM 原始输出」而非「实际会
落库的事实集」**——被批内同签名收敛丢弃的事实，其 token 仍被算作"已覆盖"，
连带把唯一的兜底句剪掉，结构化与兜底双保险同时失效。

本文件锁定：R1 判据必须对齐「收敛后」的视图（Fix B），以及收敛丢弃可观测（Fix C）。
"""
from __future__ import annotations

from datetime import datetime

from os_mem.core.services.memory_versioning import (
    IncomingFact,
    collapse_same_signature,
    plan_versioning,
)
from os_mem.extractor.regular_extractor import RegularExtractor
from os_mem.extractor.utils.normalize import normalize_key
from os_mem.models.mem_models import MemoryFact


def _mf(key: str, fact: str, value: str = "", category: str = "contact") -> MemoryFact:
    return MemoryFact(category=category, key=key, fact=fact, value=value, confidence=0.9)


# --------------------------------------------------------------------------- #
#  真实场景复现：case02 的两个电话号同签名（D4-2 也分不开——纯版本冲突）
# --------------------------------------------------------------------------- #
DIALOG = (
    "We have your number on file.\n"
    "She'll call you at the number we have on file - is 916-555-2234 still correct?\n"
    "Yes, that's right.\n"
    "It's 916-555-8899.\n"
    "Patricia will call you at 916-555-8899.\n"
)


def _llm_facts_both_phones() -> list[MemoryFact]:
    return [
        _mf("phone_number", "User's phone number is 916-555-2234", "916-555-2234"),
        _mf("phone_number", "User's phone number is 916-555-8899", "916-555-8899"),
    ]


def test_r1_with_raw_llm_output_loses_the_dropped_fact():
    """旧行为（判据=LLM 原始输出）：被收敛丢弃的 8899 的兜底句被误剪 → 信息消失。"""
    fallbacks = RegularExtractor.fallback_numeric_facts(DIALOG)
    assert any("8899" in f.fact for f in fallbacks), "兜底应产出含 8899 的句子"

    kept = RegularExtractor.prune_redundant_verbatim(fallbacks, _llm_facts_both_phones())
    assert not any("8899" in f.fact for f in kept), (
        "这是旧的错误行为：8899 的兜底句被当成冗余剪掉，而承载它的事实随后被批内收敛丢弃"
    )


def test_fix_b_keeps_fallback_when_structured_fact_will_not_persist():
    """Fix B（判据=收敛后会落库的事实集）：8899 未被覆盖 → 兜底句保留，信息不丢。"""
    fallbacks = RegularExtractor.fallback_numeric_facts(DIALOG)
    persisted = collapse_same_signature(_llm_facts_both_phones())
    assert len(persisted) == 1, "同签名应收敛为一条"

    kept = RegularExtractor.prune_redundant_verbatim(fallbacks, persisted)
    survivor_value = persisted[0].value
    dropped_value = "916-555-8899" if survivor_value == "916-555-2234" else "916-555-2234"

    # 被收敛丢弃那条的兜底句必须留下——它的信息已无结构化载体
    assert any(dropped_value in f.fact for f in kept), (
        f"被收敛丢弃的 {dropped_value} 必须有兜底句保底"
    )
    # 存活那条由结构化事实承载 → 其兜底句被剪属正常，信息不丢
    assert not any(survivor_value in f.fact for f in kept)


# --------------------------------------------------------------------------- #
#  collapse_same_signature 与 plan_versioning 批内规则的**行为契约**
# --------------------------------------------------------------------------- #
def test_collapse_view_matches_plan_versioning_batch_rule():
    """单批（同 source_started_at）下，显式视图的存活者 == 版本裁决的批内存活者。"""
    facts = _llm_facts_both_phones() + [
        _mf("account_number", "User's account number is VEL-89923476", "VEL-89923476", "finance"),
        _mf("account_number", "User's checking account number is 8829447651", "8829447651", "finance"),
    ]
    explicit = collapse_same_signature(facts)

    t = datetime(2024, 11, 1, 9, 0, 0)  # 同一批共享同一会话时间戳
    incoming = [
        IncomingFact(
            fact=f.fact, category=f.category, key=f.key, value=f.value,
            confidence=f.confidence,
            nk=normalize_key(f.category, f.key, fact=f.fact, value=f.value),
            source_conversation_id="c1", source_started_at=t,
        )
        for f in facts
    ]
    plan = plan_versioning(incoming, {})  # 空库：每条签名插入一条

    assert plan.batch_collapsed == len(facts) - len(explicit)
    assert [nv.fact.fact for nv in plan.inserts] == [f.fact for f in explicit]


def test_collapse_keeps_distinct_entities_separate():
    """D4-2 已分开的实体不会被误收敛（编号前缀 vs 无前缀）。"""
    facts = [
        _mf("account_number", "User's new account number is VEL-89923476", "VEL-89923476", "finance"),
        _mf("account_number", "User's checking account number is 8829447651", "8829447651", "finance"),
    ]
    assert len(collapse_same_signature(facts)) == 2


# --------------------------------------------------------------------------- #
#  Fix C：收敛丢弃可观测
# --------------------------------------------------------------------------- #
def test_fix_c_reports_batch_collapsed_with_samples():
    t = datetime(2024, 11, 1, 9, 0, 0)
    incoming = [
        IncomingFact(
            fact=f.fact, category=f.category, key=f.key, value=f.value,
            confidence=f.confidence,
            nk=normalize_key(f.category, f.key, fact=f.fact, value=f.value),
            source_conversation_id="c1", source_started_at=t,
        )
        for f in _llm_facts_both_phones()
    ]
    plan = plan_versioning(incoming, {})
    assert plan.batch_collapsed == 1
    assert len(plan.collapsed_samples) == 1
    assert "916-555" in plan.collapsed_samples[0]


def test_fix_c_no_collapse_on_distinct_signatures():
    t = datetime(2024, 11, 1, 9, 0, 0)
    facts = [
        _mf("phone_number", "User's phone number is 916-555-2234", "916-555-2234"),
        _mf("email", "User's email is a@b.com", "a@b.com"),
    ]
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
    assert plan.batch_collapsed == 0
    assert plan.collapsed_samples == []
