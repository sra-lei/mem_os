"""检索注入单测（装配在 os_mem/core/retrieve/strategies_retriever.py）。

2026-09-12 简化后：**链已清空**（`STRATEGY_CHAIN == []`）—— 检索侧只做
宽窗取回（`RETRIEVAL_WIDE_FETCH_K`）+ 终装配（结构化在前、verbatim 补位）
+ 字符预算截断（`INJECTION_CHAR_BUDGET`）。

1/2/3/4/5 号已全部移出链：2/3 号实测 no-op、4/5 号配额失去对象、1 号在 5 轮
端到端里未显示收益（带闸门 16/20 落在零闸门 16~19 波动区间内，依据是"无证据
支持 + 更简单"）。各策略类与组件单测暂留 `strategies/` 包，待更多轮次或 layer2
验证后随模块删除。见 docs/方案/方案-检索注入简化-宽窗替代策略链.md §六-续。

覆盖：
- 组件级：1/2/3/4/5 号各自单一职责仍成立（噪声判定/去重/冗余剔除/双配额）
- 链级（apply_retrieval_strategies）：零闸门 + 结构化优先 + 预算内全入 + 预算截断

不依赖真实 Milvus / LLM / 存储 —— 纯函数输入输出。
"""
from __future__ import annotations

from os_mem.core.retrieve import (
    STRATEGY_CHAIN,
    apply_retrieval_strategies,
)
from os_mem.core.retrieve.strategies import (
    RedundantVerbatimFilter,
    StructuredKeyDedup,
    StructuredQuota,
    VerbatimNoiseFilter,
    VerbatimQuota,
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
#  注入链语义（2026-09-12 简化后：单一噪声闸门 + 预算内全入）
# ------------------------------------------------------------------ #
class TestStrategyChain:
    def test_chain_is_empty(self) -> None:
        """链已清空（2026-09-12 A/B/C 端到端结论：宽窗 + 零闸门最优）。

        2/3/4/5 号实测 no-op / 配额失去对象；1 号按形式删整句会连带删掉合法内容
        （B 16/20 < C 19/20）→ 一并移出。见方案 §六-续。
        """
        assert STRATEGY_CHAIN == []

    def test_same_key_rows_no_longer_merged_in_retrieval(self) -> None:
        """同 (category, key) 行不再在检索侧合并——身份由入库侧签名保证。

        实测：投影键空间的 (category, 投影键) 重复组 = 0（签名唯一性不变量 +
        投影按 D4 收敛键删旧插新），故该层去重已无对象。
        """
        hits = [
            _hit('电话 916-555-2234', 'contact', 'phone'),
            _hit('电话 916-555-8899', 'contact', 'phone'),
            _hit('地址 Maple St', 'contact', 'address'),
        ]
        out = apply_retrieval_strategies('query', hits, top_k=3)
        assert len(out) == 3
        assert out[0]['key'] == 'phone'

    def test_structured_first_then_verbatim(self) -> None:
        """终装配顺序不变：结构化在前、verbatim 补位；无 token 句不再被检索侧剔除
        （那是入库侧 R1 的职责）。"""
        hits = (
            [_vb(f'English verbatim {i}') for i in range(6)]
            + [_hit('用户返程座位 14C', 'travel', 'return_seat')]
            + [_hit('用户去程座位 12C', 'travel', 'outbound_seat')]
            + [_hit('确认号 PAC-778K4M', 'travel', 'confirmation')]
        )
        out = apply_retrieval_strategies('q', hits, top_k=5)
        structured = [h for h in out if not h['key'].startswith('verbatim_')]
        verbatim = [h for h in out if h['key'].startswith('verbatim_')]
        assert len(structured) == 3
        assert len(verbatim) == 6
        assert all(not h['key'].startswith('verbatim_') for h in out[:3])

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

    def test_redundant_verbatim_no_longer_dropped_in_retrieval(self) -> None:
        """同值 verbatim 不再在检索侧剔除：入库侧 R1 已前置（实测残留 0/157）。"""
        hits = [
            _hit('用户 IRA 余额为 $248,500', 'finance', 'balance'),
            _vb('The rollover IRA has $248,500.'),
        ]
        out = apply_retrieval_strategies('q', hits, top_k=3)
        assert len(out) == 2

    def test_noise_sentence_no_longer_dropped_by_chain(self) -> None:
        """链不再删整句：比较句原样入窗（判定本身仍由组件级 TestNoiseFilter 覆盖）。

        端到端实测：C 组把 18 条形式干扰句全部放进上下文，20 例无一因此变差；
        而 B 组删句导致 15/16/04 掉分 → 故删句动作从链上移除。
        """
        hits = [
            _hit('用户每周学费为 $617.50', 'finance', 'weekly_tuition'),
            _vb('$308.75 instead of $617.50 for that week.'),
        ]
        out = apply_retrieval_strategies('q', hits, top_k=3)
        texts = [h['fact'] for h in out]
        assert len(out) == 2
        assert any('308.75' in t for t in texts)
        # 组件级判定仍成立（保留作为"标注/降权"将来复用）
        assert len(VerbatimNoiseFilter().apply('q', hits, top_k=3)) == 1

    def test_carriers_fill_when_structured_scarce(self) -> None:
        """结构化稀缺时 carrier 全部入窗（case 18 修复点）。"""
        hits = [_hit('用户 IRA 余额为 $248,500', 'finance', 'balance')] + [
            _vb(f'Account {i} balance is $1{i}0.')
            for i in range(1, 9)
        ]
        out = apply_retrieval_strategies('q', hits, top_k=15)
        verbatim = [h for h in out if h['key'].startswith('verbatim_')]
        assert len(verbatim) == 8
        assert len(out) == 9

    def test_no_reserved_slots_no_truncation(self) -> None:
        """结构化充足时不再预留槽位、不再按条数截断（配额机制已移除）。"""
        structured = [_hit(f'条目{i}: $1{i}0', 'finance', f'key{i}') for i in range(15)]
        carriers = [_vb(f'Confirmation REF-9{i}0.') for i in range(10)]
        out = apply_retrieval_strategies('q', structured + carriers, top_k=15)
        verbatim = [h for h in out if h['key'].startswith('verbatim_')]
        structured_out = [h for h in out if not h['key'].startswith('verbatim_')]
        assert len(structured_out) == 15
        assert len(verbatim) == 10
        assert all(not h['key'].startswith('verbatim_') for h in out[:15])

    def test_char_budget_trims_only_when_exceeded(self) -> None:
        """预算只在超出时生效；结构化在前，先保住结构化事实。"""
        hits = [
            _hit('A' * 100, 'finance', 'k1'),
            _hit('B' * 100, 'finance', 'k2'),
            _vb('C' * 100),
        ]
        # 预算 250：结构化两条（fact+value 各 200 字符）→ 第二条即超预算
        out = apply_retrieval_strategies('q', hits, top_k=3, budget_chars=250)
        assert len(out) == 1
        assert out[0]['key'] == 'k1'
        # 预算充裕：全入
        out2 = apply_retrieval_strategies('q', hits, top_k=3, budget_chars=10_000)
        assert len(out2) == 3


# ------------------------------------------------------------------ #
#  检索执行器接线（回归：Retrieval.retrieve 必须"宽窗取回 + 装配 + 预算"）
# ------------------------------------------------------------------ #
class TestRetrievalWiring:
    """2026-09-12 重构曾把 `apply_retrieval_strategies` 从检索链路里漏掉
    （`Retrieval.retrieve` 只返回原始 hits）→ 结构化优先与预算护栏双双失效。
    本用例用假 vectorizer/store 锁住接线。"""

    def test_retrieve_uses_wide_fetch_and_assembles(self) -> None:
        from os_mem.core.retrieve import RETRIEVAL_WIDE_FETCH_K, Retrieval

        class _FakeVectorizer:
            def embed(self, text: str) -> list[float]:
                return [0.0] * 4

        class _FakeStore:
            def __init__(self) -> None:
                self.fetch_calls: list[int] = []

            def search(self, vec, query_text=None, top_k=None, user_id=None):
                self.fetch_calls.append(top_k)
                # 故意把 verbatim 排在前面，检验装配是否把结构化提到前面
                return [
                    _vb('Traditional IRA has $127,845 in Fidelity.'),
                    _hit('用户地址 Maple St', 'contact', 'address'),
                ]

        store = _FakeStore()
        out = Retrieval(_FakeVectorizer(), store).retrieve('q', 3, 'u1')

        assert store.fetch_calls == [RETRIEVAL_WIDE_FETCH_K]  # 宽窗，而非 top_k=3
        assert len(out) == 2
        assert out[0]['key'] == 'address'  # 结构化在前
        assert out[1]['key'].startswith('verbatim_')

    def test_retrieve_trims_by_budget(self) -> None:
        from os_mem.core.retrieve import Retrieval, trim_to_budget

        class _FakeVectorizer:
            def embed(self, text: str) -> list[float]:
                return [0.0] * 4

        class _FakeStore:
            def search(self, vec, query_text=None, top_k=None, user_id=None):
                return [_hit('X' * 200, 'finance', f'k{i}') for i in range(3)]

        out = Retrieval(_FakeVectorizer(), _FakeStore()).retrieve('q', 3, 'u1')
        assert len(out) == 3  # 默认预算（12k 字符）下全入
        assert len(trim_to_budget(out, budget_chars=250)) == 1  # 预算生效

