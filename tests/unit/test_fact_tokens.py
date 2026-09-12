"""fact_tokens / norm_token 通用精确信息口径单测。

口径原则：通用存储/检索口径，不向 eval judge 的窄判分口径看齐
（百分比、时刻、日期都是有效用户信息，必须可表达、可覆盖比对）。
eval judge 在 tests/eval/judge 下持有自己的独立副本。
"""
from os_mem.extractor.utils.token_utils import fact_tokens, norm_token


class TestFactTokens:
    def test_amount_normalized(self) -> None:
        assert fact_tokens('Total is $1,430.00 today.') == {'1430.00'}

    def test_code_and_card(self) -> None:
        assert fact_tokens('Ref PAC-778K4M') == {'pac778k4m'}
        assert fact_tokens('Card 4532-8876-9901-3345') == {'4532887699013345'}

    def test_percentage_distinct_from_amount(self) -> None:
        # 3% 归一为 3pct，不与 $3 / 裸数字 3 混淆
        assert fact_tokens('We charge a 3% fee.') == {'3pct'}
        assert fact_tokens('Fee 1.2% APY') == {'1.2pct'}
        toks = fact_tokens('Fee is 3% or $12.')
        assert toks == {'3pct', '12'}

    def test_clock_time(self) -> None:
        assert fact_tokens('See you at 2:30 PM.') == {'2:30pm'}
        assert fact_tokens('Meeting at 14:35.') == {'14:35'}

    def test_dates_unified_month_day(self) -> None:
        # 三种写法统一为 M-D；年份不参与（少剪优于错剪）
        assert fact_tokens('Note it on 11/21/2024.') == {'11-21'}
        assert fact_tokens('Note it on 11/21.') == {'11-21'}
        assert fact_tokens('Thursday, November 21st at noon') == {'11-21'}
        assert fact_tokens('预约改到11月21日') == {'11-21'}

    def test_date_year_not_double_counted_as_num4(self) -> None:
        # 11/21/2024 的年份不得再被当成裸 4 位数字抽出
        assert '2024' not in fact_tokens('Happened on 11/21/2024.')

    def test_bare_year_still_num4(self) -> None:
        assert fact_tokens('We retire in 2045.') == {'2045'}

    def test_plain_text_empty(self) -> None:
        assert fact_tokens('A plain sentence with nothing precise.') == set()

    def test_coverage_semantics(self) -> None:
        # 同值不同表述应判为覆盖（R1/冗余过滤的语义基础）
        assert fact_tokens('手续费是 3%') <= fact_tokens('We charge a 3% fee.')
        assert fact_tokens('November 21st') == fact_tokens('on 11/21/2024')


def test_norm_token_basic() -> None:
    assert norm_token('$1,430') == '1430'
    assert norm_token('  PAC-778K4M ') == 'pac778k4m'
