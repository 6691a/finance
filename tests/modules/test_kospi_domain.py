"""코스피 전망의 순수 함수 — 채점, 감쇠 가중치, 메모 만료.

**DB도 모델도 없다.** 판정이 전부 순수 함수라 경계값을 여기서 잰다. 그것이 이 함수들을
`observe()` 밖에 둔 이유다.
"""

from datetime import date
from decimal import Decimal

import pytest

from modules.kospi.domain import (
    MAX_STRENGTH,
    RELATION_WINDOW,
    SLOT_TIMES,
    Direction,
    Factor,
    FactorSource,
    Observation,
    ObservationSign,
    RunSlot,
    change_pct,
    decay_weight,
    grade_forecast,
    memory_expired,
    memory_key,
    normalize_text,
    relation_weight,
)


def observation(day: str, sign: ObservationSign, strength: int = 3) -> Observation:
    return Observation(observed_on=date.fromisoformat(day), sign=sign, strength=strength)


# --- 채점 -----------------------------------------------------------------


def test_change_pct_rounds_to_two_places():
    assert change_pct(Decimal("2650.00"), Decimal("2676.50")) == Decimal("1.00")
    assert change_pct(Decimal("2650.00"), Decimal("2623.50")) == Decimal("-1.00")


def test_change_pct_refuses_a_non_positive_base():
    # 기준가 0은 채점을 무한대로 만든다. 조용히 0을 돌려주면 그 날이 "안 움직였다"가 된다.
    with pytest.raises(ValueError, match="positive"):
        change_pct(Decimal(0), Decimal(2650))


def test_a_forecast_hits_when_the_realised_direction_matches():
    result = grade_forecast(
        direction=Direction.UP,
        expected_change_pct=Decimal("1.00"),
        band_pct=Decimal("0.50"),
        base_price=Decimal("2650.00"),
        close_price=Decimal("2676.50"),
    )
    assert result.actual_change_pct == Decimal("1.00")
    assert result.hit is True
    assert result.within_band is True


def test_a_flat_close_counts_as_a_miss():
    # 정확히 0이면 어느 방향도 아니다. "올랐다"고 부른 전망을 맞았다고 할 수 없다.
    result = grade_forecast(
        direction=Direction.UP,
        expected_change_pct=Decimal("1.00"),
        band_pct=Decimal("2.00"),
        base_price=Decimal("2650.00"),
        close_price=Decimal("2650.00"),
    )
    assert result.actual_change_pct == Decimal("0.00")
    assert result.hit is False
    # 방향이 틀려도 폭은 잰다. 둘은 다른 축이다.
    assert result.within_band is True


def test_the_band_is_measured_even_when_the_direction_is_wrong():
    result = grade_forecast(
        direction=Direction.UP,
        expected_change_pct=Decimal("1.00"),
        band_pct=Decimal("0.10"),
        base_price=Decimal("2650.00"),
        close_price=Decimal("2570.50"),
    )
    assert result.hit is False
    assert result.within_band is False


# --- 관계 가중치 ------------------------------------------------------------


def test_a_missing_factor_reports_zero_observations_not_zero_relation():
    weight = relation_weight(Factor.US10Y, [], as_of_date=date(2026, 9, 2))
    assert weight.n_obs == 0
    assert weight.weight == 0.0
    # 마지막 관측이 없으면 날짜도 없다. 프롬프트가 "관측 없음"으로 싣는 근거다.
    assert weight.last_date is None


def test_all_same_direction_observations_reach_the_maximum_weight():
    weight = relation_weight(
        Factor.FOREIGN_NET_BUY,
        [observation("2026-09-01", ObservationSign.SAME, MAX_STRENGTH)],
        as_of_date=date(2026, 9, 1),
    )
    assert weight.weight == 1.0


def test_inverse_observations_give_a_negative_weight():
    weight = relation_weight(
        Factor.US10Y,
        [observation("2026-09-01", ObservationSign.INVERSE, MAX_STRENGTH)],
        as_of_date=date(2026, 9, 1),
    )
    assert weight.weight == -1.0


def test_recent_observations_outweigh_older_ones():
    """**감쇠가 이 기능의 핵심이다.** 옛 관측을 남기되 최신이 무겁다."""
    flipping = relation_weight(
        Factor.FOREIGN_NET_BUY,
        [
            observation("2026-09-01", ObservationSign.INVERSE, 3),
            observation("2026-08-20", ObservationSign.SAME, 3),
            observation("2026-08-19", ObservationSign.SAME, 3),
            observation("2026-08-18", ObservationSign.SAME, 3),
        ],
        as_of_date=date(2026, 9, 1),
    )
    # 옛 관측 셋이 최근 하나를 이기지 못한다. 단순 평균이면 +0.5였다.
    assert flipping.weight < 0


def test_the_window_caps_how_many_observations_count():
    many = [observation(f"2026-08-{day:02d}", ObservationSign.SAME) for day in range(1, 25)]
    weight = relation_weight(Factor.SP500, many, as_of_date=date(2026, 9, 1))
    assert weight.n_obs == RELATION_WINDOW


def test_the_weight_does_not_depend_on_the_order_rows_arrive_in():
    rows = [
        observation("2026-08-25", ObservationSign.SAME, 2),
        observation("2026-09-01", ObservationSign.INVERSE, 3),
        observation("2026-08-28", ObservationSign.SAME, 1),
    ]
    forward = relation_weight(Factor.VIX, rows, as_of_date=date(2026, 9, 1))
    backward = relation_weight(Factor.VIX, list(reversed(rows)), as_of_date=date(2026, 9, 1))
    assert forward.weight == backward.weight
    # 최근 부호는 감쇠 없이 그대로 보인다 — 가중치와 어긋나면 관계가 바뀌는 중이다.
    assert forward.recent_signs[0] is ObservationSign.INVERSE


def test_a_future_observation_keeps_full_weight_instead_of_growing():
    # 그런 행이 오면 조회가 잘못된 것이다. 조용히 키우면 그 결함이 숨는다.
    assert decay_weight(date(2026, 9, 5), date(2026, 9, 1)) == 1.0


def test_the_half_life_halves_the_weight():
    assert decay_weight(date(2026, 8, 27), date(2026, 9, 1)) == pytest.approx(0.5)


# --- 메모 -------------------------------------------------------------------


def test_a_memory_expires_strictly_after_the_age_limit():
    created = date(2026, 8, 1)
    assert memory_expired(created, date(2026, 8, 21)) is False
    assert memory_expired(created, date(2026, 8, 22)) is True


def test_memory_keys_ignore_spacing_and_punctuation():
    assert memory_key("목요일 밤 미국 CPI 발표!") == memory_key("목요일밤 미국 CPI 발표")


def test_normalize_text_folds_whitespace_and_truncates():
    assert normalize_text("  두   칸  ", 100) == "두 칸"
    assert normalize_text("가나다라", 2) == "가나"


# --- 어휘 -------------------------------------------------------------------


def test_document_factors_are_not_queryable_through_factor_history():
    """뉴스와 공시는 값이 아니라 글이다. 전용 툴이 따로 있다."""
    from modules.kospi.domain import FACTOR_SPECS, HISTORY_FACTORS

    document_factors = {code for code, spec in FACTOR_SPECS.items() if spec.source is FactorSource.DOCUMENT}
    assert document_factors == {Factor.NEWS, Factor.DISCLOSURE}
    assert document_factors.isdisjoint(HISTORY_FACTORS)


def test_every_factor_has_a_spec():
    from modules.kospi.domain import FACTOR_SPECS

    assert set(FACTOR_SPECS) == set(Factor)


def test_the_slot_table_covers_every_slot():
    # cron과 대조하는 테스트가 이 표를 원본으로 삼는다.
    assert set(SLOT_TIMES) == set(RunSlot)


# --- 크기 기준선 --------------------------------------------------------------


def test_the_move_baseline_window_is_much_longer_than_the_bar_window():
    """**봉 열다섯으로는 분위수를 못 뽑는다.** 그 열다섯이 조용한 구간이면 기준선이 통째로 낮아진다."""
    from modules.kospi.domain import BARS_WINDOW, MIN_MOVE_BASELINE_BARS, MOVE_BASELINE_BARS

    assert MOVE_BASELINE_BARS > BARS_WINDOW * 10
    # 최소 표본은 창보다 작아야 한다 — 아니면 언제나 비어 있다.
    assert MIN_MOVE_BASELINE_BARS < MOVE_BASELINE_BARS


def test_an_empty_baseline_reports_none_not_zero():
    """0으로 채우면 모델이 그 숫자를 쓴다. "재지 않았다"와 "0이다"는 다르다."""
    from modules.kospi.state import MoveBaseline

    empty = MoveBaseline()
    assert empty.observations == 0
    assert empty.abs_p50 is None
    assert empty.up_median is None
    assert empty.up_day_ratio is None
