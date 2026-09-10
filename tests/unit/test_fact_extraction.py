"""FactExtractor（os_mem/extractor/fact_extractor.py）单元测试。

覆盖事实抽取工具类的全部确定性逻辑（不依赖真实 LLM / Milvus）：
  - ``validate_response``     ：LLM 返回清洗（markdown / 包装格式）与校验
    （分类白名单、confidence 边界、非法 JSON → []）
  - ``chunk_dialog``          ：长对话分段 + 段间冗余重叠，边界信息不切丢
  - ``extract_structured_facts``：单段提取 / 长对话并行 / 全失败降级
    （注入 fake complete）
  - ``dedup_facts``           ：按 (category, key, value) 跨段去重
  - ``fallback_numeric_facts``（RegularExtractor）：含金额/编号/日期/电话的原文
    句子 verbatim 兜底（测试调用见 regular_extractor.RegularExtractor）

用法:
    pytest tests/unit/test_fact_extraction.py
"""
from __future__ import annotations

import json

import pytest

from os_mem.extractor.common import dedup_facts
from os_mem.extractor.fact_extractor import FactExtractor
from os_mem.extractor.regular_extractor import RegularExtractor
from os_mem.models.mem_models import MemoryFact


def _fact(
    fact: str,
    category: str = "finance",
    key: str = "k",
    value: str = "v",
    confidence: float = 0.8,
) -> MemoryFact:
    return MemoryFact(fact=fact, category=category, key=key, value=value,
                      confidence=confidence)


# ------------------------------------------------------------------ #
#  validate_response
# ------------------------------------------------------------------ #
class TestValidateResponse:
    def test_valid_json_array(self) -> None:
        raw = ('[{"fact":"用户邮箱是 a@b.com","category":"contact",'
               '"key":"email","value":"a@b.com","confidence":0.9}]')
        facts = FactExtractor.validate_response(raw)
        assert len(facts) == 1
        assert facts[0].category == "contact"
        assert facts[0].value == "a@b.com"

    def test_dict_wrapper_format(self) -> None:
        raw = ('{"facts":[{"fact":"f","category":"personal","key":"name",'
               '"value":"x","confidence":0.7}]}')
        facts = FactExtractor.validate_response(raw)
        assert len(facts) == 1

    def test_markdown_code_fence_stripped(self) -> None:
        raw = (
            '```json\n'
            '[{"fact":"f","category":"preference","key":"k","value":"v"}]\n'
            '```'
        )
        facts = FactExtractor.validate_response(raw)
        assert len(facts) == 1

    def test_unknown_category_rejected(self) -> None:
        raw = ('[{"fact":"f","category":"not_allowed","key":"k",'
               '"value":"v","confidence":0.8}]')
        assert FactExtractor.validate_response(raw) == []

    def test_confidence_out_of_range_rejected(self) -> None:
        raw = json.dumps([{
            "fact": "f", "category": "personal",
            "key": "k", "value": "v", "confidence": 1.5,
        }])
        assert FactExtractor.validate_response(raw) == []

    def test_invalid_json_returns_empty(self) -> None:
        assert FactExtractor.validate_response("{not json") == []
        assert FactExtractor.validate_response("") == []


# ------------------------------------------------------------------ #
#  dedup_facts
# ------------------------------------------------------------------ #
class TestDedupFacts:
    def test_dedup_by_category_key_value(self) -> None:
        a = _fact("事实", "finance", "account", "4429853327")
        b = _fact("事实", "finance", "account", "4429853327")  # 与 a 完全相同
        c = _fact("事实", "finance", "account", "8847293001")  # 同 key 不同 value 保留
        out = dedup_facts([a, b, c])
        assert len(out) == 2

    def test_keeps_different_category_same_value(self) -> None:
        a = _fact("事实", "finance", "amount", "$2,400")
        b = _fact("事实", "contact", "amount", "$2,400")
        assert len(dedup_facts([a, b])) == 2


# ------------------------------------------------------------------ #
#  fallback_numeric_facts
# ------------------------------------------------------------------ #
class TestFallbackNumericFacts:
    def _run(self, text: str) -> list[MemoryFact]:
        return RegularExtractor.fallback_numeric_facts(text)

    def test_picks_amount_phone_verbatim_sentences(self) -> None:
        text = (
            '{"role":"user","content":"我的支票账户每月自动扣款 $2,400。"}\n'
            '{"role":"user","content":"如有问题拨打 916-555-8899 联系客服。"}\n'
            '{"role":"assistant","content":"好的，已记录，没有其他需求。"}'
        )
        facts = self._run(text)
        assert len(facts) == 2
        assert all("$2,400" in f.fact or "916-555-8899" in f.fact for f in facts)

    def test_category_mapped_finance_for_amount(self) -> None:
        raw = json.dumps({"role": "user", "content": "我的卡号 4532-8876-9901-3345。"},
                         ensure_ascii=False)
        facts = self._run(raw)
        assert facts and facts[0].category == "finance"

    def test_plain_sentence_dropped(self) -> None:
        raw = json.dumps({"role": "user", "content": "我喜欢用这款产品，觉得很好。"},
                         ensure_ascii=False)
        facts = self._run(raw)
        assert facts == []

    def test_too_short_sentence_dropped(self) -> None:
        facts = self._run('{"role":"user","content":"$5 ok。"}')
        assert facts == []

    def test_max_facts_cap(self) -> None:
        import json as _json

        lines = [
            _json.dumps({"role": "user", "content": f"记住金额 ${i}00 即可。"},
                        ensure_ascii=False)
            for i in range(1, 80)
        ]
        text = "\n".join(lines)
        facts = RegularExtractor.fallback_numeric_facts(text, max_facts=10)
        assert len(facts) == 10


# ------------------------------------------------------------------ #
#  chunk_dialog
# ------------------------------------------------------------------ #
class TestChunkDialog:
    def test_short_dialog_single_chunk(self) -> None:
        assert len(FactExtractor.chunk_dialog("短文本")) == 1

    def test_long_dialog_split_with_overlap(self) -> None:
        lines = [f"第{i}条消息内容填充占位。".ljust(60, "啊") for i in range(1, 60)]
        chunks = FactExtractor.chunk_dialog("\n".join(lines), max_chars=300, overlap=5)
        assert len(chunks) >= 2
        # 相邻分段应保留 overlap 消息的冗余，边界信息不丢
        for prev, nxt in zip(chunks, chunks[1:]):
            for msg in prev.split("\n")[-5:]:
                assert msg in nxt

    def test_message_count_dimension_triggers_split(self) -> None:
        """字符数不超但消息条数超限 → 仍切分（双维 OR 语义，防消息密集短句漏网）。"""
        lines = [f"短消息{i}" for i in range(1, 60)]  # ~600 字符 < 默认 4500
        chunks = FactExtractor.chunk_dialog(
            "\n".join(lines), max_chars=10000, max_msgs=20, overlap=3
        )
        assert len(chunks) >= 3
        for prev, nxt in zip(chunks, chunks[1:]):
            for msg in prev.split("\n")[-3:]:
                assert msg in nxt

    def test_both_within_limits_single_chunk(self) -> None:
        lines = [f"短消息{i}" for i in range(1, 10)]
        chunks = FactExtractor.chunk_dialog(
            "\n".join(lines), max_chars=10000, max_msgs=50, overlap=3
        )
        assert chunks == ["\n".join(lines)]

    def test_default_settings_dual_dim(self) -> None:
        """默认参数（4500 字符 / 35 条消息）下：30 条短消息不分段、40 条即分段。"""
        small = "\n".join(f"第{i}条短消息" for i in range(1, 31))
        assert len(FactExtractor.chunk_dialog(small)) == 1
        big = "\n".join(f"第{i}条短消息" for i in range(1, 41))
        assert len(FactExtractor.chunk_dialog(big)) >= 2


# ------------------------------------------------------------------ #
#  extract_structured_facts（注入 fake complete，无真实 LLM）
# ------------------------------------------------------------------ #
class FakeComplete:
    """按文本长度返回不同结果，用于区分短对话单次 / 长对话并行调用。"""

    def __init__(
        self,
        payload_by_call: dict[int, str] | None = None,
        fail: bool = False,
    ) -> None:
        self.calls: list[str] = []
        self.payload_by_call = payload_by_call or {}
        self.fail = fail

    def __call__(self, text: str) -> str:
        self.calls.append(text)
        if self.fail:
            return "{bad json"
        payload = self.payload_by_call.get(len(self.calls), (
            '[{"fact":"用户账户 4429853327","category":"finance",'
            '"key":"account","value":"4429853327","confidence":0.95}]'
        ))
        return payload


class TestExtractStructuredFacts:
    def test_short_dialog_single_llm_call(self) -> None:
        fx = FactExtractor()
        out = fx.extract_structured_facts("短对话内容", complete=FakeComplete())
        assert len(out) == 1
        assert out[0].key == "account"

    def test_retries_then_degrade_to_raw_conversation(self) -> None:
        """LLM 一直返回非法 JSON → 重试后降级为 raw_conversation 事实。"""
        fx = FactExtractor()
        out = fx.extract_structured_facts(
            "全失败对话", retries=2, complete=FakeComplete(fail=True),
        )
        assert len(out) == 1
        assert out[0].key == "raw_conversation"
        assert out[0].confidence == 0.1

    def test_retry_recovers_after_bad_output(self) -> None:
        """第 1 次非法、第 2 次合法 → 重试恢复，不降级。"""
        good = ('[{"fact":"用户邮箱 a@b.com","category":"contact",'
                '"key":"email","value":"a@b.com","confidence":0.9}]')
        fake = FakeComplete(payload_by_call={1: "{bad", 2: good})
        fx = FactExtractor()
        out = fx.extract_structured_facts("短对话", retries=3, complete=fake)
        assert len(out) == 1
        assert out[0].key == "email"

    def test_long_dialog_parallel_calls_and_cross_chunk_dedup(self) -> None:
        """长对话分段 → 多次 LLM 调用；跨段重复事实被去重。"""
        dedup_ok = ('[{"fact":"用户账户 4429853327","category":"finance",'
                    '"key":"account","value":"4429853327","confidence":0.95}]')
        fake = FakeComplete(payload_by_call={i: dedup_ok for i in range(1, 200)})
        fx = FactExtractor()
        # ~16k 字符 > 默认 8k 分段阈值 → 必然触发分段并行
        lines = [f"第{i}条消息填充占位。".ljust(40, "啊") for i in range(1, 400)]
        out = fx.extract_structured_facts(
            "\n".join(lines), retries=1, complete=fake,
        )
        # 分段发生 → 多次调用；跨段去重后只剩 1 条同键事实
        assert len(fake.calls) >= 2
        assert len(out) == 1
        assert out[0].key == "account"

    def test_requires_complete_callback(self) -> None:
        fx = FactExtractor()  # 未注入 complete
        with pytest.raises(ValueError, match="complete"):
            fx.extract_structured_facts("hi")


# ------------------------------------------------------------------ #
#  repair 续写（方案 3）：截断 JSON 优先修复而非整段重提取
# ------------------------------------------------------------------ #
class RepairComplete:
    """模拟带 repair 能力的回调：首次返回截断 JSON，repair 返回合法 JSON。"""

    def __init__(self) -> None:
        self.extract_calls = 0
        self.repair_calls = 0

    def __call__(self, text: str) -> str:
        self.extract_calls += 1
        # 第一次提取即"截断"：JSON 中途断掉
        return ('{"facts": [{"fact":"用户账户 4429853327","category":"finance",'
                '"key":"account","value":"4429853327","confidence":0.95},')

    def repair(self, partial_json: str) -> str:
        self.repair_calls += 1
        return ('{"facts": [{"fact":"用户账户 4429853327","category":"finance",'
                '"key":"account","value":"4429853327","confidence":0.95},'
                '{"fact":"用户邮箱 a@b.com","category":"contact","key":"email",'
                '"value":"a@b.com","confidence":0.9}]}')


class TestRepairFlow:
    def test_truncated_output_repaired_without_full_reextract(self) -> None:
        """截断 JSON → 走 repair 续写成功，不触发整段重提取。"""
        fx = FactExtractor()
        fake = RepairComplete()
        out = fx.extract_chunk("长对话内容", retries=3, complete=fake)
        assert len(out) == 2  # repair 补全后含 2 条 fact
        keys = {f.key for f in out}
        assert keys == {"account", "email"}
        assert fake.extract_calls == 1  # 只提取 1 次，未整段重试
        assert fake.repair_calls == 1

    def test_repair_fails_then_full_retry(self) -> None:
        """repair 也失败 → 回退整段重试（第 2 次提取成功）。"""
        good = ('[{"fact":"用户邮箱 a@b.com","category":"contact",'
                '"key":"email","value":"a@b.com","confidence":0.9}]')

        class FlakyRepair:
            def __init__(self) -> None:
                self.extract_calls = 0
                self.repair_calls = 0

            def __call__(self, text: str) -> str:
                self.extract_calls += 1
                return "{truncated" if self.extract_calls == 1 else good

            def repair(self, partial_json: str) -> str:
                self.repair_calls += 1
                return "{bad json again"

        fake = FlakyRepair()
        fx = FactExtractor()
        out = fx.extract_chunk("对话", retries=3, complete=fake)
        assert len(out) == 1
        assert out[0].key == "email"
        assert fake.repair_calls == 1  # repair 尝试过
        assert fake.extract_calls == 2  # 回退整段重试一次

    def test_plain_complete_without_repair_still_works(self) -> None:
        """无 repair 能力的普通回调 → 走原整段重试路径（兼容旧用法）。"""
        good = ('[{"fact":"用户邮箱 a@b.com","category":"contact",'
                '"key":"email","value":"a@b.com","confidence":0.9}]')
        fake = FakeComplete(payload_by_call={1: "{bad", 2: good})
        fx = FactExtractor()
        out = fx.extract_chunk("对话", retries=3, complete=fake)
        assert len(out) == 1
        assert out[0].key == "email"
        assert len(fake.calls) == 2  # 两次整段调用


# ------------------------------------------------------------------ #
#  截断空返回（finish_reason=length + content 空）→ 对半切段递归重提
#  （方案：事实提取鲁棒性与成本优化，2026-09-09）
# ------------------------------------------------------------------ #
class OutcomeComplete:
    """带 outcome() 的回调：按「被调用文本长度/内容」决定返回截断还是合法 JSON。

    outcome 返回 ``ChatOutcome(content, finish_reason)``，模拟真实 client
    的 chat_outcome 语义（length 截断时 content 为空）。
    """

    def __init__(self, truncate_prefix: str = "") -> None:
        # 文本以 truncate_prefix 开头时返回截断空返回；否则返回合法 JSON
        self.truncate_prefix = truncate_prefix
        self.calls: list[str] = []

    def outcome(self, text: str):
        from os_mem.infra.llm.base_client import ChatOutcome

        self.calls.append(text)
        if text.startswith(self.truncate_prefix):
            return ChatOutcome("", "length", None)
        payload = (
            '[{"fact":"User has card 4532-8876","category":"finance",'
            '"key":"card_number","value":"4532-8876","confidence":0.95}]'
        )
        return ChatOutcome(payload, "stop", None)

    def __call__(self, text: str) -> str:  # 兼容旧契约
        return self.outcome(text).content


class TestTruncationEmptyFlow:
    def test_length_empty_split_halves_and_recover(self) -> None:
        """整段截断空返回 → 对半切段，两半各自提取成功并去重合并。"""
        full = "\n".join(f"第{i}条消息" for i in range(1, 10))
        fx = FactExtractor()
        fake = OutcomeComplete(truncate_prefix=full)
        out = fx.extract_chunk(full, complete=fake)
        assert len(out) == 1
        assert out[0].key == "card_number"
        # 整段只被调 1 次（未盲目整段重试），其余调用是两半
        assert fake.calls[0] == full
        assert len(fake.calls) == 3  # 整段 + 左半 + 右半
        for half in fake.calls[1:]:
            assert half != full and len(half) < len(full)
        stats = fx.stats_delta({k: 0 for k in fx.stats_snapshot()})
        assert stats["split_recursions"] == 1

    def test_length_empty_halves_still_empty_degrades(self) -> None:
        """两半仍截断空返回 → 放弃（depth 达上限），返回空由上层降级。"""
        full = "\n".join(f"第{i}条消息" for i in range(1, 6))
        fx = FactExtractor()
        fake = OutcomeComplete(truncate_prefix="")  # 任何文本都截断空返回
        out = fx.extract_chunk(full, retries=2, complete=fake)
        assert out == []
        # 整段 1 次 + 两半各 1 次；不因 depth 上限后再盲目整段重试（calls==3）
        assert len(fake.calls) == 3

    def test_plain_complete_without_outcome_keeps_old_path(self) -> None:
        """无 outcome() 的普通回调（兼容旧用法）不受截断路由影响。"""
        good = ('[{"fact":"用户邮箱 a@b.com","category":"contact",'
                '"key":"email","value":"a@b.com","confidence":0.9}]')
        fake = FakeComplete(payload_by_call={1: "", 2: good})
        fx = FactExtractor()
        out = fx.extract_chunk("对话", retries=2, complete=fake)
        assert len(out) == 1
        assert out[0].key == "email"


# ------------------------------------------------------------------ #
#  降级切片（_degrade_fact ≤900 字符确定性切片，防撞 Milvus 1024 上限）
# ------------------------------------------------------------------ #
class TestDegradeSlicing:
    def test_short_text_single_row(self) -> None:
        text = "短对话内容"
        out = FactExtractor._degrade_fact(text)
        assert len(out) == 1
        assert out[0].key == "raw_conversation"
        assert out[0].value == text

    def test_long_text_sliced_into_sequence(self) -> None:
        text = "原始对话内容。" * 300  # 2400 字符 > 900 → 3 片
        out = FactExtractor._degrade_fact(text)
        keys = [f.key for f in out]
        assert keys == ["raw_conversation", "raw_conversation_2", "raw_conversation_3"]
        assert all(len(f.value) <= 900 for f in out)
        assert "".join(f.value for f in out) == text
        # 确定性：同输入重跑产出相同 key/value 集（投影删旧插新幂等）
        again = FactExtractor._degrade_fact(text)
        assert [(f.key, f.value) for f in again] == [(f.key, f.value) for f in out]

    def test_boundary_900_exactly_single_row(self) -> None:
        text = "x" * 900
        out = FactExtractor._degrade_fact(text)
        assert len(out) == 1
        assert out[0].value == text


# ------------------------------------------------------------------ #
#  prune_redundant_verbatim（R1：兜底句与结构化事实做 token 覆盖去重）
# ------------------------------------------------------------------ #
class TestPruneRedundantVerbatim:
    def test_fully_covered_fallback_dropped(self) -> None:
        """数值全被结构化覆盖 → 兜底句不存（重复信息，无负收益）。"""
        llm = [_fact("用户 IRA 余额为 $248,500", key="balance", value="$248,500")]
        fallback = [
            _fact("The rollover IRA has $248,500.", key="verbatim_abc", value="x")
        ]
        out = RegularExtractor.prune_redundant_verbatim(fallback, llm)
        assert out == []

    def test_unique_carrier_kept(self) -> None:
        """结构化未覆盖的唯一数值 → 兜底句保留（保险语义）。"""
        llm = [_fact("用户 IRA 余额为 $248,500", key="balance", value="$248,500")]
        fallback = [
            _fact(
                "Traditional IRA has $127,845 in Fidelity.",
                key="verbatim_abc",
                value="x",
            )
        ]
        out = RegularExtractor.prune_redundant_verbatim(fallback, llm)
        assert len(out) == 1
        assert out[0].fact == fallback[0].fact

    def test_partially_covered_fallback_kept(self) -> None:
        """句中含结构化未覆盖的数值 → 保留整句（打包句里常有独立信息）。"""
        llm = [
            _fact("User purchased a laptop for $1,899", key="laptop", value="$1,899")
        ]
        fallback = [
            _fact(
                "Laptop $1,899, supplies $340, and insurance $1,200.",
                key="verbatim_abc",
                value="x",
            )
        ]
        out = RegularExtractor.prune_redundant_verbatim(fallback, llm)
        assert len(out) == 1  # 340/1200 未被结构化覆盖 → 整句保留

    def test_empty_llm_facts_keeps_all(self) -> None:
        fallback = [_fact("Balance is $127,845.", key="verbatim_abc", value="x")]
        out = RegularExtractor.prune_redundant_verbatim(fallback, [])
        assert len(out) == 1

    def test_degrade_raw_conversation_skips_prune(self) -> None:
        """LLM 整体失败降级为 raw_conversation（value=整段原文）→ 不剪枝，兜底照存。"""
        llm = [
            _fact(
                "原始对话: $248,500 and $127,845 ...",
                category="other",
                key="raw_conversation",
                value="整段对话含 $248,500 与 $127,845 ...",
            )
        ]
        fallback = [
            _fact("Balance is $127,845.", key="verbatim_abc", value="x"),
            _fact("IRA has $248,500.", key="verbatim_def", value="x"),
        ]
        out = RegularExtractor.prune_redundant_verbatim(fallback, llm)
        assert len(out) == 2


# ------------------------------------------------------------------ #
#  兜底正则盲区：裸年份/产品代号（Freedom 2045）补抓
# ------------------------------------------------------------------ #
class TestFallbackBlindSpots:
    def test_captures_product_year_code(self) -> None:
        """大写词 + 4 位代号（基金名 Freedom 2045）应被兜底捕获（case 18 真漏修复）。"""
        dialog = (
            '{"role":"assistant","content":"Your rollover IRA has $248,500. '
            'Currently invested in the Freedom 2045 target-date fund."}'
        )
        facts = RegularExtractor.fallback_numeric_facts(dialog)
        texts = [f.fact for f in facts]
        assert any("Freedom 2045" in text for text in texts)

    def test_bare_year_alone_not_captured(self) -> None:
        """裸 4 位年份（前词非大写词）不误收——避免把普通年份/计数当 verbatim 噪音。"""
        dialog = '{"role":"user","content":"We plan to retire sometime in 2045."}'
        facts = RegularExtractor.fallback_numeric_facts(dialog)
        assert facts == []
