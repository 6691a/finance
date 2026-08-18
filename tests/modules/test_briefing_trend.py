from datetime import date

import pytest

from modules.briefing.trend import ChangeKind, Trend, sign_streak, summarize


def points(*values: float, start: int = 1) -> list[tuple[date, float]]:
    return [(date(2026, 8, start + index), value) for index, value in enumerate(values)]


def test_a_flat_series_has_no_streak_and_sits_mid_range():
    trend = summarize(points(100, 100, 100, 100), ChangeKind.RELATIVE)

    assert trend.streak == 0
    assert trend.window_low == 100
    assert trend.window_high == 100


def test_the_streak_counts_consecutive_days_in_one_direction():
    up = summarize(points(100, 101, 102, 103), ChangeKind.RELATIVE)
    down = summarize(points(100, 101, 100, 99, 98), ChangeKind.RELATIVE)

    assert up.streak == 3
    # 부호가 방향이다. 3일 연속 하락.
    assert down.streak == -3


def test_an_unusually_large_move_ranks_near_the_top():
    """오늘 값이 큰지 작은지는 지난 변화들과 견줘야만 답할 수 있다. 이게 없으면 LLM은
    +0.82%가 큰 값인지 모른다."""
    quiet = [100.0 + index * 0.1 for index in range(20)]
    trend = summarize(points(*quiet, quiet[-1] * 1.05), ChangeKind.RELATIVE)

    assert trend.move_percentile > 90


def test_a_typical_move_ranks_in_the_middle():
    swings = [100, 101, 100, 101, 100, 101, 100, 101, 100, 101, 100]
    trend = summarize(points(*swings), ChangeKind.RELATIVE)

    assert 20 < trend.move_percentile < 80


def test_rates_are_measured_in_basis_points_not_percent():
    """금리에 퍼센트 변화를 씌우면 4.00→4.10과 0.40→0.50이 전혀 다른 크기가 된다.
    마이너스 금리 구간에서는 부호까지 뒤집힌다."""
    trend = summarize(points(4.00, 4.10), ChangeKind.ABSOLUTE)

    assert trend.change == pytest.approx(10.0)  # bp


def test_prices_are_measured_in_percent():
    trend = summarize(points(100.0, 102.0), ChangeKind.RELATIVE)

    assert trend.change == pytest.approx(2.0)


def test_a_short_sample_is_flagged_rather_than_hidden():
    """표본이 짧다는 사실이 결론의 일부다. 숫자를 감추지 않는다."""
    trend = summarize(points(100, 101, 102), ChangeKind.RELATIVE)

    assert trend.observations == 3
    assert trend.thin


def test_a_full_window_is_not_flagged():
    trend = summarize(points(*[100.0 + index for index in range(20)]), ChangeKind.RELATIVE)

    assert not trend.thin


def test_one_point_cannot_make_a_trend():
    assert summarize(points(100), ChangeKind.RELATIVE) is None
    assert summarize([], ChangeKind.RELATIVE) is None


def test_negative_rates_do_not_break_the_calculation():
    """유로 지역은 마이너스 금리 구간이 있다."""
    trend = summarize(points(-0.20, -0.10, 0.05), ChangeKind.ABSOLUTE)

    assert trend.change == pytest.approx(15.0)
    assert trend.streak == 2


def test_a_sign_streak_counts_the_value_not_its_direction():
    """ "외국인 5일 연속 순매도"는 금액이 계속 마이너스였다는 뜻이지, 금액이 매일
    줄었다는 뜻이 아니다. 변화 방향으로 세면 순매도가 잦아드는 날 흐름이 끊긴다."""
    fading_selling = [-500.0, -400.0, -300.0, -200.0, -100.0]

    assert sign_streak(fading_selling) == -5


def test_a_sign_streak_breaks_when_the_side_flips():
    assert sign_streak([-500.0, -400.0, 120.0, -300.0, -200.0]) == -2


def test_a_zero_day_ends_the_sign_streak():
    assert sign_streak([-500.0, 0.0, -300.0]) == -1
    assert sign_streak([-500.0, -300.0, 0.0]) == 0


def test_an_empty_sign_streak_is_zero():
    assert sign_streak([]) == 0


def test_the_trend_serializes_without_none_noise():
    trend = summarize(points(100, 101, 102), ChangeKind.RELATIVE)

    assert isinstance(trend, Trend)
    assert set(trend.model_dump()) == {
        "observations",
        "change",
        "move_percentile",
        "streak",
        "window_low",
        "window_high",
        "thin",
    }
