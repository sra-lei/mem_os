"""assert 本地判分（eval.judge.assert_evaluate）离线单测。

覆盖信息点抽取形态：千分位金额 / 字母数字编号 / 座位 / ≥4 位账号 /
长数字卡号 / 中文日期回退；并锁定历史误伤回归（英文 rubric 词不参与比对、
日期年份不强制字面）。
"""

from __future__ import annotations

import pytest

from eval.judge import assert_evaluate


def _grade(expected: str, actual: str) -> tuple[float, bool]:
    result = assert_evaluate('', expected, actual)
    return result.score, result.passed


def test_money_with_commas_matches_chinese_answer() -> None:
    """千分位金额（12 gym 场景）应能命中中文回答，不再落英文词档判 0。"""
    expected = (
        'Original package: 24 sessions for $2,400. '
        '16/24 * $2,400 = $1,600. '
        'Administrative fee: 20% of $1,600 = $320. '
        'Net refund: $1,280.'
    )
    actual = (
        '套餐总价 $2,400，共 24 节；剩余 16 节价值 $1,600。'
        '若退款扣除 20% 手续费 $320，预计可退 $1,280。'
    )
    score, passed = _grade(expected, actual)
    assert passed
    assert score == pytest.approx(1.0)


def test_money_partial_hit_fails_below_threshold() -> None:
    expected = '$2,400 / $1,600 / $320 / $1,280'
    actual = '我记得到套餐总价是 $2,400。'
    score, passed = _grade(expected, actual)
    assert not passed
    assert score == pytest.approx(0.25)


def test_account_and_routing_numbers() -> None:
    """≥4 位纯数字账号独立成信息点；缺一项按比例扣分。"""
    expected = 'checking account 4429853327, routing 123006800'
    # 只答出账号、漏了路由 → 0.5，低于阈值不通过
    score, passed = _grade(expected, '我的支票账户是 4429853327。')
    assert not passed
    assert score == pytest.approx(0.5)
    # 全部答出 → 通过
    _, passed = _grade(expected, '账号 4429853327，路由号 123006800。')
    assert passed


def test_code_and_seat_tokens() -> None:
    """PAC-778K4M / 12C / 14C 应作为信息点，缺返程座位则按比例扣分。"""
    expected = (
        'provide confirmation number PAC-778K4M and specify seat 12C '
        'for the outbound flight and seat 14C for the return flight'
    )
    actual = '确认号 PAC-778K4M，去程座位 12C。'
    score, passed = _grade(expected, actual)
    assert not passed
    assert score == pytest.approx(2 / 3)
    # 补全 14C → 通过
    full = actual + '返程座位 14C。'
    _, passed = _grade(expected, full)
    assert passed


def test_hyphenated_card_number_matches_varied_format() -> None:
    """信用卡 5524-8897-2234-4001 应整体归一比较，容忍空格/连字符差异。"""
    expected = 'the credit card number 5524-8897-2234-4001'
    _, passed = _grade(expected, '您的信用卡号是 5524 8897 2234 4001。')
    assert passed
    _, passed = _grade(expected, '卡号 5524-8897-2234-4001。')
    assert passed
    score, passed = _grade(expected, '卡号是 5524-8897-xxxx-xxxx。')
    assert not passed
    assert score == pytest.approx(0.0)


def test_english_rubric_words_do_not_kill_chinese_answer() -> None:
    """英文 rubric 描述词不参与命中；回答只有中文也能按金额通过。"""
    expected = 'The agent should directly provide the total refund amount of $1,280.'
    _, passed = _grade(expected, '您预计可退的总额为 $1,280。')
    assert passed


def test_date_fallback_with_chinese_month_day() -> None:
    """期望仅含日期（03 预约场景）时按“月-日”组合回退判定。"""
    expected = (
        'the appointment is on Thursday, November 21st at 2:30 PM with Dr. Robert'
    )
    _, passed = _grade(expected, '预约在 11月21日（周四）下午 2:30，医生是 Robert。')
    assert passed
    score, passed = _grade(expected, '预约在下周四下午两点半。')
    assert not passed
    assert score == pytest.approx(0.0)


def test_date_year_letter_not_required() -> None:
    """December 23, 2024 不要求回答写出年份字面（20 daycare 场景）。"""
    expected = 'First week tuition due December 23, 2024'
    _, passed = _grade(expected, '第一周学费需在 12月23日 前支付。')
    assert passed


def test_amount_wins_over_date_hard_check() -> None:
    """金额信息足够时不再用日期硬判（避免 19/20 只差年份被误伤）。"""
    expected = 'Deposit: $5,000; balance due December 23, 2024: $18,400'
    actual = '定金 $5,000，尾款 $18,400 于12月23日支付。'
    score, passed = _grade(expected, actual)
    assert passed
    assert score == pytest.approx(1.0)


def test_empty_actual_fails() -> None:
    result = assert_evaluate('', 'account 4429853327', '  ')
    assert not result.passed
    assert result.score == 0.0
