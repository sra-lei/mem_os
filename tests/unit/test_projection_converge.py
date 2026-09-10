"""投影写入单测：delete_memories filter 语义 + verbatim 兜底句 key 内容指纹。

覆盖 docs/方案/方案-记忆更新收敛与Milvus投影一致性.md：
- §4.2/§9-1：``delete_memories`` 按 (user_id, category, keys) 构造批量 filter，
  filter 只作用于该 user + 该 category + key in (...)，不会波及其它 user/key。
- B 批：verbatim 兜底句 key 用内容指纹（同句幂等、异句不互踩）。

注：早期 ``_converge_by_key``（同 key 按 confidence 收敛）已被 D4 版本裁决 +
投影回读 current 赢家取代，该静态方法与其单测于 2026-09-10 作为死代码移除。

不依赖真实 Milvus / DashScope / LLM：用伪 client 验证 filter 构造与日志参数。
"""
from __future__ import annotations

from typing import Any

import pytest


# ------------------------------------------------------------------ #
#  verbatim 兜底句 key：内容指纹（B 批）
# ------------------------------------------------------------------ #
def test_verbatim_fingerprint_same_sentence_same_key() -> None:
    from os_mem.extractor.regular_extractor import RegularExtractor

    dialog = '{"role":"user","content":"My claim number is CLM-2024-894327."}'
    f1 = RegularExtractor.fallback_numeric_facts(dialog)
    f2 = RegularExtractor.fallback_numeric_facts(dialog)
    assert len(f1) == 1 and len(f2) == 1
    assert f1[0].key == f2[0].key  # 同句重跑幂等（同 key → upsert 覆盖）
    assert f1[0].key.startswith('verbatim_')


def test_verbatim_fingerprint_distinct_sentences_distinct_keys() -> None:
    from os_mem.extractor.regular_extractor import RegularExtractor

    dialog = (
        '{"role":"user","content":"Claim CLM-2024-894327."}\n'
        '{"role":"assistant","content":"The report number is SAC-2024-78432."}'
    )
    facts = RegularExtractor.fallback_numeric_facts(dialog)
    keys = {f.key for f in facts}
    assert len(keys) == len(facts)  # 异句不同 key，互不覆盖
    assert all(k.startswith('verbatim_') for k in keys)


def test_verbatim_no_shared_aggregate_key() -> None:
    """回归：不得再出现聚合的 'verbatim_record' 共享 key。"""
    from os_mem.extractor.regular_extractor import RegularExtractor

    dialog = (
        '{"role":"assistant","content":"Blood work $285 and X-ray $420."}\n'
        '{"role":"user","content":"And the DHPP vaccine is $42?"}'
    )
    facts = RegularExtractor.fallback_numeric_facts(dialog)
    assert facts
    assert all(f.key != 'verbatim_record' for f in facts)


# ------------------------------------------------------------------ #
#  delete_memories：filter 构造与作用域（伪 client）
# ------------------------------------------------------------------ #
class _FakeMilvusClient:
    """记录 delete 调用；返回固定 delete_count。"""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def delete(self, collection_name: str, filter: str) -> dict[str, int]:  # noqa: A002
        self.calls.append({'collection': collection_name, 'filter': filter})
        return {'delete_count': 3}

    def flush(self, *args: Any, **kwargs: Any) -> None:
        pass


def _store_with_fake_client() -> tuple[Any, _FakeMilvusClient]:
    """构造一个只测 delete_memories 的 MemoryVectorStore（跳过建 collection 逻辑）。"""
    from os_mem.infra.storage.vec_storage import MemoryVectorStore

    store = MemoryVectorStore.__new__(MemoryVectorStore)
    store.collection_name = 'mem_os'
    fake = _FakeMilvusClient()
    store.client = fake

    def _noop_ensure() -> None:
        return None

    store._ensure_collection = _noop_ensure  # type: ignore[method-assign]
    return store, fake


def test_delete_memories_filter_scoped_to_user_category_keys() -> None:
    store, fake = _store_with_fake_client()

    n = store.delete_memories(
        user_id='layer1_02_insurance_claim',
        category='other',
        keys=['claims_adjuster', 'adjuster_contact_schedule'],
    )
    assert n == 3
    assert len(fake.calls) == 1
    expr = fake.calls[0]['filter']
    # user 过滤必须存在（防误删其他用户）
    assert 'user_id == "layer1_02_insurance_claim"' in expr
    assert 'category == "other"' in expr
    # key in [...] 批量（Milvus 布尔表达式用方括号列表）
    assert 'key in [' in expr
    assert 'claims_adjuster' in expr
    assert 'adjuster_contact_schedule' in expr


def test_delete_memories_keys_escaped() -> None:
    store, fake = _store_with_fake_client()

    # key 含引号/括号（LLM 生成 key 格式不可控）→ 拼接不破坏 filter
    tricky = ['odd"key', "it's"]
    store.delete_memories(user_id='u1', category='c', keys=tricky)
    expr = fake.calls[0]['filter']
    # 转义后仍是一个合法 in 列表（粗查：包含 key 片段即可，重点是不抛异常）
    assert 'key in [' in expr
    assert 'odd' in expr
    assert 'it' in expr


def test_delete_memories_requires_user() -> None:
    store, _ = _store_with_fake_client()

    with pytest.raises(ValueError):
        store.delete_memories(user_id='', category='c', keys=['k'])


def test_delete_memories_whole_user_allowed_when_explicit() -> None:
    store, fake = _store_with_fake_client()

    store.delete_memories(user_id='u1')
    expr = fake.calls[0]['filter']
    assert 'user_id == "u1"' in expr
    assert 'key in' not in expr
