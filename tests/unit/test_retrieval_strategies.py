"""可插拔检索策略层单测（os_mem/core/retrieval_strategies.py）。

覆盖：
- DiversityStrategy：同 (category, key) 去重保首见；不同 key 填满 top_k；
  放大候选可覆盖更多类（治扎堆）
- VerbatimGateStrategy：过滤疑问句/口语碎片 verbatim；保留含数字句
- apply_retrieval_strategies：全关 = 基线直出前 top_k（行为与无策略一致）

不依赖真实 Milvus / LLM / 存储 —— 纯函数输入输出。
"""
from __future__ import annotations

from os_mem.core.retrieval_strategies import (
    DiversityStrategy,
    VerbatimGateStrategy,
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


# ------------------------------------------------------------------ #
#  DiversityStrategy
# ------------------------------------------------------------------ #
class TestDiversity:
    def test_same_key_dedup_keeps_first(self) -> None:
        hits = [
            _hit('电话 916-555-2234', 'contact', 'phone'),
            _hit('电话 916-555-8899', 'contact', 'phone'),  # 同 key 第二版
            _hit('地址 Maple St', 'contact', 'address'),
        ]
        out = DiversityStrategy().apply('query', hits, top_k=3)
        assert len(out) == 2
        keys = [(h['category'], h['key']) for h in out]
        assert ('contact', 'phone') in keys
        assert ('contact', 'address') in keys
        # 同 key 保留首见（检索最相关版）
        assert out[0]['fact'] == '电话 916-555-2234'

    def test_fills_up_to_top_k_with_distinct_keys(self) -> None:
        hits = [_hit(f'fact{i}', 'finance', f'key{i}') for i in range(8)]
        out = DiversityStrategy().apply('q', hits, top_k=5)
        assert len(out) == 5  # 5 个不同 key
        assert len({(h['category'], h['key']) for h in out}) == 5

    def test_diversity_gains_coverage_over_plain_topk(self) -> None:
        """模拟扎堆场景：前 5 条全是 finance.balance 变体 → 直出只覆盖 1 类；
        diversity 放大候选后能覆盖多类。"""
        hits = (
            [_hit(f'余额变体{i}', 'finance', 'balance') for i in range(5)]
            + [_hit('账户号 4429', 'finance', 'account_number')]
            + [_hit('课程 MAT-151', 'education', 'course')]
            + [_hit('电话 8899', 'contact', 'phone')]
        )
        # 基线直出前 5：全 balance
        assert len({(h['category'], h['key']) for h in hits[:5]}) == 1
        # diversity 放大取回后覆盖 4 类
        out = DiversityStrategy().apply('q', hits, top_k=5)
        cats = {(h['category'], h['key']) for h in out}
        assert len(cats) == 4
        assert ('education', 'course') in cats
        assert ('contact', 'phone') in cats

    def test_structured_facts_beat_verbatim_before_fill(self) -> None:
        """回归（run_835b24aeb5 教训）：verbatim 英文句不得霸榜——
        结构化 fact 优先入选，verbatim 仅补位且受 1/3 上限约束。"""
        # 检索候选里 verbatim 句排最前（BM25 英文强），结构化 fact 靠后
        hits = (
            [
                _hit(f'English verbatim {i}', 'finance', f'verbatim_{i:012x}')
                for i in range(6)
            ]
            + [_hit('用户返程座位 14C', 'travel', 'return_seat')]
            + [_hit('用户去程座位 12C', 'travel', 'outbound_seat')]
            + [_hit('确认号 PAC-778K4M', 'travel', 'confirmation')]
        )
        out = DiversityStrategy().apply('q', hits, top_k=5)
        structured = [h for h in out if not h['key'].startswith('verbatim_')]
        verbatim = [h for h in out if h['key'].startswith('verbatim_')]
        # 结构化 fact 全入选（3 条 ≤ top_k）
        assert len(structured) == 3
        assert any('14C' in h['fact'] for h in structured)
        # verbatim 补位 ≤ top_k 的 1/3（15 条时 ≤5；此处 top_k=5 → ≤1 条）
        assert len(verbatim) <= 1

    def test_verbatim_capped_at_third_when_structured_scarce(self) -> None:
        """结构化很少时 verbatim 补位不超过 top_k 的 1/3。"""
        hits = (
            [_hit('唯一结构化事实', 'finance', 'balance')]
            + [
                _hit(f'verbatim filler {i}', 'finance', f'verbatim_{i:012x}')
                for i in range(20)
            ]
        )
        out = DiversityStrategy().apply('q', hits, top_k=15)
        structured = [h for h in out if not h['key'].startswith('verbatim_')]
        verbatim = [h for h in out if h['key'].startswith('verbatim_')]
        assert len(structured) == 1
        assert len(verbatim) <= 5  # 15 * 1/3 = 5


# ------------------------------------------------------------------ #
#  VerbatimGateStrategy
# ------------------------------------------------------------------ #
class TestVerbatimGate:
    def test_filters_question_noise_verbatim(self) -> None:
        hits = [
            _hit('So it would be $30 instead of $35?', 'finance', 'verbatim_abc'),
            _hit(
                'And weekly tuition of $617.50 after that?',
                'finance',
                'verbatim_def',
            ),
            _hit('Full-time is $325 per week per child.', 'finance', 'verbatim_123'),
        ]
        out = VerbatimGateStrategy().apply('q', hits, top_k=3)
        assert len(out) == 1
        assert out[0]['fact'] == 'Full-time is $325 per week per child.'

    def test_keeps_non_verbatim_facts(self) -> None:
        hits = [
            _hit('用户电话 916-555-8899', 'contact', 'phone'),  # 非 verbatim，保留
            _hit('So it would be $30?', 'finance', 'verbatim_x'),
        ]
        out = VerbatimGateStrategy().apply('q', hits, top_k=2)
        assert len(out) == 1
        assert out[0]['key'] == 'phone'


# ------------------------------------------------------------------ #
#  apply_retrieval_strategies（全关 = 基线）
# ------------------------------------------------------------------ #
class TestApplyAllOff:
    def test_all_off_returns_top_k_verbatim(self) -> None:
        """默认（两个开关都 False）行为 = 直出前 top_k，与无策略一致。"""
        hits = [_hit(f'f{i}', 'finance', f'k{i}') for i in range(10)]
        out = apply_retrieval_strategies('q', hits, top_k=3)
        assert [h['fact'] for h in out] == [f'f{i}' for i in range(3)]
