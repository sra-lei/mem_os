"""测试结构化事实提取链路中的确定性纯逻辑（不依赖 LLM / Milvus / 外部服务）。

覆盖 StructuredMemService 的四个环节：
  - ``_validate_response``：LLM 返回清洗（markdown / 包装格式）与校验
    （分类白名单、confidence 边界、非法 JSON）
  - ``_dedup_facts``       ：跨分段提取结果按 (category, key, value) 去重
  - ``_fallback_numeric_facts``：把含金额/编号/日期/电话等精确信息的原文句子
    兜底捞进库（layer1 精确回忆的关键，提取改写导致数字丢失的防线）
  - ``_chunk_dialog``      ：长对话分段 + 段间冗余重叠，保证边界信息不切丢

用法:
    pytest tests/test_struct_mem_extract.py
"""
from __future__ import annotations

import json

import pytest

from os_mem.core.services.struc_mem_service import StructuredMemService
from os_mem.models.mem_models import MemoryFact


@pytest.fixture()
def svc():
    """实例不触碰任何真实 client——被测环节均为确定性纯逻辑。"""
    return StructuredMemService(client=None, vectorizer=None, vector_store=None)


def _fact(fact, category="finance", key="k", value="v", confidence=0.8):
    return MemoryFact(fact=fact, category=category, key=key, value=value,
                      confidence=confidence)


# ------------------------------------------------------------------ #
#  _validate_response
# ------------------------------------------------------------------ #
class TestValidateResponse:
    def test_valid_json_array(self, svc):
        raw = ('[{"fact":"用户邮箱是 a@b.com","category":"contact",'
               '"key":"email","value":"a@b.com","confidence":0.9}]')
        facts = svc._validate_response(raw)
        assert len(facts) == 1
        assert facts[0].category == "contact"
        assert facts[0].value == "a@b.com"

    def test_dict_wrapper_format(self, svc):
        raw = ('{"facts":[{"fact":"f","category":"personal","key":"name",'
               '"value":"x","confidence":0.7}]}')
        facts = svc._validate_response(raw)
        assert len(facts) == 1

    def test_markdown_code_fence_stripped(self, svc):
        raw = (
            '```json\n'
            '[{"fact":"f","category":"preference","key":"k","value":"v"}]\n'
            '```'
        )
        facts = svc._validate_response(raw)
        assert len(facts) == 1

    def test_unknown_category_rejected(self, svc):
        raw = ('[{"fact":"f","category":"not_allowed","key":"k",'
               '"value":"v","confidence":0.8}]')
        assert svc._validate_response(raw) == []

    def test_confidence_out_of_range_rejected(self, svc):
        raw = json.dumps([{
            "fact": "f", "category": "personal",
            "key": "k", "value": "v", "confidence": 1.5,
        }])
        assert svc._validate_response(raw) == []

    def test_invalid_json_returns_empty(self, svc):
        assert svc._validate_response("{not json") == []
        assert svc._validate_response("") == []


# ------------------------------------------------------------------ #
#  _dedup_facts
# ------------------------------------------------------------------ #
class TestDedupFacts:
    def test_dedup_by_category_key_value(self):
        a = _fact("事实", "finance", "account", "4429853327")
        b = _fact("事实", "finance", "account", "4429853327")  # 与 a 完全相同
        c = _fact("事实", "finance", "account", "8847293001")  # 同 key 不同 value 保留
        out = StructuredMemService._dedup_facts([a, b, c])
        assert len(out) == 2

    def test_keeps_different_category_same_value(self):
        a = _fact("事实", "finance", "amount", "$2,400")
        b = _fact("事实", "contact", "amount", "$2,400")
        assert len(StructuredMemService._dedup_facts([a, b])) == 2


# ------------------------------------------------------------------ #
#  _fallback_numeric_facts
# ------------------------------------------------------------------ #
class TestFallbackNumericFacts:
    def _run(self, text):
        return StructuredMemService._fallback_numeric_facts(text)

    def test_picks_amount_phone_verbatim_sentences(self):
        text = (
            "{\"role\":\"user\",\"content\":\"我的支票账户每月自动扣款 $2,400。\"}\n"
            "{\"role\":\"user\",\"content\":\"如有问题拨打 916-555-8899 联系客服。\"}\n"
            "{\"role\":\"assistant\",\"content\":\"好的，已记录，没有其他需求。\"}"
        )
        facts = self._run(text)
        assert len(facts) == 2
        assert all("$2,400" in f.fact or "916-555-8899" in f.fact for f in facts)

    def test_category_mapped_finance_for_amount(self):
        raw = json.dumps({"role": "user", "content": "我的卡号 4532-8876-9901-3345。"},
                         ensure_ascii=False)
        facts = self._run(raw)
        assert facts and facts[0].category == "finance"

    def test_plain_sentence_dropped(self):
        raw = json.dumps({"role": "user", "content": "我喜欢用这款产品，觉得很好。"},
                         ensure_ascii=False)
        facts = self._run(raw)
        assert facts == []

    def test_too_short_sentence_dropped(self):
        facts = self._run("{\"role\":\"user\",\"content\":\"$5 ok。\"}")
        assert facts == []

    def test_max_facts_cap(self):
        import json as _json

        lines = [
            _json.dumps({"role": "user", "content": f"记住金额 ${i}00 即可。"},
                        ensure_ascii=False)
            for i in range(1, 80)
        ]
        text = "\n".join(lines)
        facts = StructuredMemService._fallback_numeric_facts(text, max_facts=10)
        assert len(facts) == 10


# ------------------------------------------------------------------ #
#  _chunk_dialog
# ------------------------------------------------------------------ #
class TestChunkDialog:
    def test_short_dialog_single_chunk(self, svc):
        assert len(svc._chunk_dialog("短文本")) == 1

    def test_long_dialog_split_with_overlap(self, svc):
        lines = [f"第{i}条消息内容填充占位。".ljust(60, "啊") for i in range(1, 60)]
        chunks = svc._chunk_dialog("\n".join(lines), max_chars=300, overlap=5)
        assert len(chunks) >= 2
        # 相邻分段应保留 overlap 消息的冗余，边界信息不丢
        for prev, nxt in zip(chunks, chunks[1:]):
            for msg in prev.split("\n")[-5:]:
                assert msg in nxt
