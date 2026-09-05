"""A 批投影收敛单测：同 key 收敛（confidence 高者优先）+ delete_memories filter 语义。

覆盖 docs/方案-记忆更新收敛与Milvus投影一致性.md：
- §4.2/§9-3：``_converge_by_key`` 同 (category, key) 多条保留 confidence 最高一条；
  confidence 相同保留原序最后一条。
- §4.2/§9-1：``delete_memories`` 按 (user_id, category, keys) 构造批量 filter，
  filter 只作用于该 user + 该 category + key in (...)，不会波及其它 user/key。

不依赖真实 Milvus / DashScope / LLM：用伪 client 验证 filter 构造与日志参数，
用纯函数验证收敛语义。
"""
from __future__ import annotations

from typing import Any

import pytest

from os_mem.models.mem_models import MemoryFact


def _make_fact(
    fact: str,
    category: str,
    key: str,
    confidence: float = 0.8,
) -> MemoryFact:
    return MemoryFact(
        fact=fact, category=category, key=key, value=fact, confidence=confidence,
    )


# ------------------------------------------------------------------ #
#  _converge_by_key：同 key 收敛
# ------------------------------------------------------------------ #
def test_converge_keeps_highest_confidence() -> None:
    from os_mem.core.services.struc_mem_service import StructuredMemService

    facts = [
        _make_fact('旧版 48 小时', 'other', 'contact_time', confidence=0.6),
        _make_fact('新版 24-48 小时', 'other', 'contact_time', confidence=0.9),
    ]
    out = StructuredMemService._converge_by_key(facts)
    assert len(out) == 1
    assert out[0].fact == '新版 24-48 小时'


def test_converge_same_confidence_keeps_last() -> None:
    from os_mem.core.services.struc_mem_service import StructuredMemService

    facts = [
        _make_fact('版本 A', 'other', 'k', confidence=0.8),
        _make_fact('版本 B', 'other', 'k', confidence=0.8),
    ]
    out = StructuredMemService._converge_by_key(facts)
    assert len(out) == 1
    assert out[0].fact == '版本 B'


def test_converge_lower_confidence_does_not_override() -> None:
    from os_mem.core.services.struc_mem_service import StructuredMemService

    facts = [
        _make_fact('高置信', 'finance', 'balance', confidence=0.95),
        _make_fact('低置信', 'finance', 'balance', confidence=0.5),
    ]
    out = StructuredMemService._converge_by_key(facts)
    assert len(out) == 1
    assert out[0].fact == '高置信'


def test_converge_distinct_keys_kept() -> None:
    from os_mem.core.services.struc_mem_service import StructuredMemService

    facts = [
        _make_fact('电话 A', 'contact', 'phone', confidence=0.8),
        _make_fact('地址 A', 'contact', 'address', confidence=0.7),
        _make_fact('电话 B(新)', 'contact', 'phone', confidence=0.9),
    ]
    out = StructuredMemService._converge_by_key(facts)
    assert len(out) == 2
    by_key = {f.key: f.fact for f in out}
    assert by_key['phone'] == '电话 B(新)'
    assert by_key['address'] == '地址 A'


def test_converge_empty_and_single() -> None:
    from os_mem.core.services.struc_mem_service import StructuredMemService

    assert StructuredMemService._converge_by_key([]) == []
    one = [_make_fact('x', 'other', 'k', 0.8)]
    assert StructuredMemService._converge_by_key(one) == one


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
    # key in (...) 批量
    assert 'key in (' in expr
    assert 'claims_adjuster' in expr
    assert 'adjuster_contact_schedule' in expr


def test_delete_memories_keys_escaped() -> None:
    store, fake = _store_with_fake_client()

    # key 含引号/括号（LLM 生成 key 格式不可控）→ 拼接不破坏 filter
    tricky = ['odd"key', "it's"]
    store.delete_memories(user_id='u1', category='c', keys=tricky)
    expr = fake.calls[0]['filter']
    # 转义后仍是一个合法 in 列表（粗查：包含 key 片段即可，重点是不抛异常）
    assert 'key in (' in expr
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
