"""FactExtractor（os_mem/utils/fact_extraction.py）单元测试。

覆盖事实抽取工具类的全部确定性逻辑（不依赖真实 LLM / Milvus）：
  - ``validate_response``     ：LLM 返回清洗（markdown / 包装格式）与校验
    （分类白名单、confidence 边界、非法 JSON → []）
  - ``chunk_dialog``          ：长对话分段 + 段间冗余重叠，边界信息不切丢
  - ``extract_structured_facts``：单段提取 / 长对话并行 / 全失败降级（注入 fake complete）
  - ``dedup_facts``           ：按 (category, key, value) 跨段去重
  - ``fallback_numeric_facts``：含金额/编号/日期/电话的原文句子 verbatim 兜底

用法:
    pytest tests/test_fact_extraction.py
"""
from __future__ import annotations

import json

import pytest

from os_mem.models.mem_models import MemoryFact
from os_mem.utils.fact_extraction import FactExtractor


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
        out = FactExtractor.dedup_facts([a, b, c])
        assert len(out) == 2

    def test_keeps_different_category_same_value(self) -> None:
        a = _fact("事实", "finance", "amount", "$2,400")
        b = _fact("事实", "contact", "amount", "$2,400")
        assert len(FactExtractor.dedup_facts([a, b])) == 2


# ------------------------------------------------------------------ #
#  fallback_numeric_facts
# ------------------------------------------------------------------ #
class TestFallbackNumericFacts:
    def _run(self, text: str) -> list[MemoryFact]:
        return FactExtractor.fallback_numeric_facts(text)

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
        facts = FactExtractor.fallback_numeric_facts(text, max_facts=10)
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
