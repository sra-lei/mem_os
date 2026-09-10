"""检索注入策略链单测（os_mem/core/retrieval_strategies.py，v2 单一职责策略链）。

策略链（固定顺序、全部默认加载、无 Enable 开关）：
  VerbatimNoiseFilter → StructuredKeyDedup → RedundantVerbatimFilter
  → VerbatimQuota → StructuredQuota → 终装配（结构化在前、verbatim 补位）

覆盖：
- 组件级：每个策略只做一件事（噪声剔除/结构化去重/verbatim 冗余剔除/双配额）
- 链级（apply_retrieval_strategies）：端到端语义 = v1 验证过的区分准入行为
  （见 docs/方案/方案-检索注入verbatim区分策略.md §八：11/20 → 14/20）

不依赖真实 Milvus / LLM / 存储 —— 纯函数输入输出。
"""
from __future__ import annotations

from os_mem.core.retrieval_strategies import (
    STRATEGY_CHAIN,
    RedundantVerbatimFilter,
    StructuredKeyDedup,
    StructuredQuota,
    VerbatimNoiseFilter,
    VerbatimQuota,
    apply_retrieval_strategies,
)


def _hit(fact: str, category: str = 'other', key: str = 'k') -> dict:
    return {
        'id': f'{category}:{key}:{fact[:10]}',
        'fact': fact,
        'category': category,
        'key': key,
        'value': fact,
        'user_id': 'u1',
        'updated_at': '2026-09-05T00:00:00',
    }


def _vb(fact: str, category: str = 'finance') -> dict:
    return _hit(fact, category, f'verbatim_{abs(hash(fact)):012x}')


# ------------------------------------------------------------------ #
#  1. VerbatimNoiseFilter —— 噪声剔除
# ------------------------------------------------------------------ #
class TestNoiseFilter:
    def test_filters_question_and_fragment(self) -> None:
        hits = [
            _vb('So it would be $30 instead of $35?'),
            _vb('okay'),
            _vb('Full-time is $325 per week per child.'),
            _hit('用户电话 916-555-8899', 'contact', 'phone'),
        ]
        out = VerbatimNoiseFilter().apply('q', hits, top_k=3)
        texts = [h['fact'] for h in out]
        assert 'So it would be' not in ' '.join(texts)
        assert 'okay' not in ' '.join(texts)
        assert any('Full-time is $325' in t for t in texts)  # 定论句保留
        assert any('phone' == h['key'] for h in out)  # 非 verbatim 不受影响

    def test_filters_adjust_markers(self) -> None:
        """比较/调整句（过程性中间值）剔除（case 20 场景）。"""
        hits = [
            _vb('$308.75 instead of $617.50 for that week.'),
            _vb('That is $100 rather than $120.'),
            _vb('The weekly tuition is $617.50 total.'),
        ]
        out = VerbatimNoiseFilter().apply('q', hits, top_k=3)
        texts = [h['fact'] for h in out]
        assert len(out) == 1
        assert '617.50 total' in texts[0]


# ------------------------------------------------------------------ #
#  2. StructuredKeyDedup —— 结构化同 key 去重
# ------------------------------------------------------------------ #
class TestStructuredDedup:
    def test_same_key_dedup_keeps_first(self) -> None:
        hits = [
            _hit('电话 916-555-2234', 'contact', 'phone'),
            _hit('电话 916-555-8899', 'contact', 'phone'),  # 同 key 第二版
            _hit('地址 Maple St', 'contact', 'address'),
            _vb('English verbatim stays'),
        ]
        out = StructuredKeyDedup().apply('query', hits, top_k=5)
        keys = [(h['category'], h['key']) for h in out]
        assert ('contact', 'phone') in keys
        assert ('contact', 'address') in keys
        assert out[0]['fact'] == '电话 916-555-2234'  # 首见（最相关版）
        assert any(h['key'].startswith('verbatim_') for h in out)  # verbatim 不动


# ------------------------------------------------------------------ #
#  3. RedundantVerbatimFilter —— 冗余 verbatim 剔除
# ------------------------------------------------------------------ #
class TestRedundantFilter:
    def test_unique_carrier_kept(self) -> None:
        hits = [
            _hit('用户 IRA 余额为 $248,500', 'finance', 'balance'),
            _hit('用户地址 Maple St', 'contact', 'address'),
            _vb('Traditional IRA has $127,845 in Fidelity.'),
        ]
        out = RedundantVerbatimFilter().apply('q', hits, top_k=3)
        assert any('127,845' in h['fact'] for h in out)  # 新信息 carrier 保留

    def test_covered_verbatim_dropped(self) -> None:
        hits = [
            _hit('用户 IRA 余额为 $248,500', 'finance', 'balance'),
            _vb('The rollover IRA has $248,500.'),
        ]
        out = RedundantVerbatimFilter().apply('q', hits, top_k=3)
        assert len(out) == 1
        assert not out[0]['key'].startswith('verbatim_')

    def test_tokenless_verbatim_dropped(self) -> None:
        hits = [
            _hit('用户电话 916-555-8899', 'contact', 'phone'),
            _vb('English filler sentence without numbers.', 'other'),
        ]
        out = RedundantVerbatimFilter().apply('q', hits, top_k=3)
        assert len(out) == 1

    def test_duplicate_carrier_dropped(self) -> None:
        """carrier 间同值也只保留首见。"""
        hits = [
            _vb('Traditional IRA has $127,845 in Fidelity.'),
            _vb('The traditional IRA balance is $127,845.'),
        ]
        out = RedundantVerbatimFilter().apply('q', hits, top_k=3)
        assert len(out) == 1


# ------------------------------------------------------------------ #
#  4. VerbatimQuota —— verbatim 配额（按需让位）
# ------------------------------------------------------------------ #
class TestVerbatimQuota:
    def test_capped_at_third_when_structured_plenty(self) -> None:
        structured = [_hit(f'条目{i}: $1{i}0', 'finance', f'key{i}') for i in range(15)]
        carriers = [_vb(f'Confirmation REF-9{i}0.') for i in range(10)]
        out = VerbatimQuota().apply('q', structured + carriers, top_k=15)
        n_vb = sum(1 for h in out if h['key'].startswith('verbatim_'))
        assert n_vb == 5  # max(ceil(15/3), 15-15) = 5

    def test_fill_remaining_when_structured_scarce(self) -> None:
        structured = [_hit('用户 IRA 余额为 $248,500', 'finance', 'balance')]
        carriers = [_vb(f'Account {i} balance is $1{i}0.') for i in range(1, 9)]
        out = VerbatimQuota().apply('q', structured + carriers, top_k=15)
        n_vb = sum(1 for h in out if h['key'].startswith('verbatim_'))
        assert n_vb == 8  # max(5, 15-1) = 14 ≥ 8，放行填满


# ------------------------------------------------------------------ #
#  5. StructuredQuota —— 结构化配额（为 carrier 让位）
# ------------------------------------------------------------------ #
class TestStructuredQuota:
    def test_structured_capped_for_carriers(self) -> None:
        structured = [_hit(f'条目{i}: $1{i}0', 'finance', f'key{i}') for i in range(15)]
        carriers = [_vb(f'Confirmation REF-9{i}0.') for i in range(5)]
        out = StructuredQuota().apply('q', structured + carriers, top_k=15)
        n_struct = sum(1 for h in out if not h['key'].startswith('verbatim_'))
        n_vb = sum(1 for h in out if h['key'].startswith('verbatim_'))
        assert n_vb == 5
        assert n_struct == 10  # top_k - verbatim = 10


# ------------------------------------------------------------------ #
#  策略链语义（端到端 = v1 验证行为）
# ------------------------------------------------------------------ #
class TestStrategyChain:
    def test_chain_has_five_single_purpose_strategies(self) -> None:
        names = [type(s).__name__ for s in STRATEGY_CHAIN]
        assert names == [
            'VerbatimNoiseFilter',
            'StructuredKeyDedup',
            'RedundantVerbatimFilter',
            'VerbatimQuota',
            'StructuredQuota',
        ]

    def test_dedup_and_structured_first(self) -> None:
        hits = [
            _hit('电话 916-555-2234', 'contact', 'phone'),
            _hit('电话 916-555-8899', 'contact', 'phone'),
            _hit('地址 Maple St', 'contact', 'address'),
        ]
        out = apply_retrieval_strategies('query', hits, top_k=3)
        assert len(out) == 2
        assert out[0]['key'] == 'phone'

    def test_structured_beat_verbatim_dominance(self) -> None:
        """回归（run_835b24aeb5 教训）：verbatim 不得霸榜——噪声/无 token 句被剔除，
        结构化事实全部入选且排前。"""
        hits = (
            [_vb(f'English verbatim {i}') for i in range(6)]
            + [_hit('用户返程座位 14C', 'travel', 'return_seat')]
            + [_hit('用户去程座位 12C', 'travel', 'outbound_seat')]
            + [_hit('确认号 PAC-778K4M', 'travel', 'confirmation')]
        )
        out = apply_retrieval_strategies('q', hits, top_k=5)
        structured = [h for h in out if not h['key'].startswith('verbatim_')]
        verbatim = [h for h in out if h['key'].startswith('verbatim_')]
        assert len(structured) == 3  # 无 token 的 verbatim 全被剔除
        assert verbatim == []

    def test_unique_carrier_admitted(self) -> None:
        """余额只存在于 verbatim 句时应入窗（case 18 场景）。"""
        hits = [
            _hit('用户 IRA 余额为 $248,500', 'finance', 'balance'),
            _hit('用户地址 Maple St', 'contact', 'address'),
            _vb('Traditional IRA has $127,845 in Fidelity.'),
        ]
        out = apply_retrieval_strategies('q', hits, top_k=3)
        texts = [h['fact'] for h in out]
        assert any('127,845' in t for t in texts)
        assert out[0]['key'] == 'balance'
        assert out[1]['key'] == 'address'

    def test_redundant_verbatim_skipped(self) -> None:
        hits = [
            _hit('用户 IRA 余额为 $248,500', 'finance', 'balance'),
            _vb('The rollover IRA has $248,500.'),
        ]
        out = apply_retrieval_strategies('q', hits, top_k=3)
        assert len(out) == 1
        assert not out[0]['key'].startswith('verbatim_')

    def test_adjust_noise_filtered(self) -> None:
        """case 20 过程性中间值不入窗。"""
        hits = [
            _hit('用户每周学费为 $617.50', 'finance', 'weekly_tuition'),
            _vb('$308.75 instead of $617.50 for that week.'),
        ]
        out = apply_retrieval_strategies('q', hits, top_k=3)
        texts = [h['fact'] for h in out]
        assert len(out) == 1
        assert '308.75' not in ' '.join(texts)

    def test_carriers_fill_when_structured_scarce(self) -> None:
        """结构化稀缺时 carrier 突破 1/3 上限填满剩余（case 18 修复点）。"""
        hits = [_hit('用户 IRA 余额为 $248,500', 'finance', 'balance')] + [
            _vb(f'Account {i} balance is $1{i}0.')
            for i in range(1, 9)
        ]
        out = apply_retrieval_strategies('q', hits, top_k=15)
        verbatim = [h for h in out if h['key'].startswith('verbatim_')]
        assert len(verbatim) == 8
        assert len(out) == 9

    def test_reserved_slots_when_structured_plenty(self) -> None:
        """结构化充足时给 carrier 预留 1/3 槽位；结构化在前。"""
        structured = [_hit(f'条目{i}: $1{i}0', 'finance', f'key{i}') for i in range(15)]
        carriers = [_vb(f'Confirmation REF-9{i}0.') for i in range(10)]
        out = apply_retrieval_strategies('q', structured + carriers, top_k=15)
        verbatim = [h for h in out if h['key'].startswith('verbatim_')]
        structured_out = [h for h in out if not h['key'].startswith('verbatim_')]
        assert len(out) == 15
        assert len(structured_out) == 10
        assert len(verbatim) == 5
        assert all(not h['key'].startswith('verbatim_') for h in out[:10])
