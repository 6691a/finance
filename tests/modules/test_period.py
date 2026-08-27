from datetime import UTC, date, datetime, timedelta
from itertools import pairwise
from types import SimpleNamespace

import pytest

from modules.period import (
    LOOKBACK_DAYS,
    SPAN_CALENDAR_DAYS,
    PeriodError,
    calendar_day,
    fetch_windows,
    resolve_observation_period,
    span_start,
)

# KST 2026-08-07 08:20. UTC로는 하루 앞이라 날짜 경계가 어긋나는지 여기서 갈린다.
RUN_TIME = datetime(2026, 8, 6, 23, 20, tzinfo=UTC)


def context(params: dict | None = None, **overrides: object) -> dict:
    return {"params": params or {}, "data_interval_end": RUN_TIME} | overrides


def test_explicit_parameters_win_over_the_run_time():
    period = resolve_observation_period(
        context({"observation_start": "2000-01-01", "observation_end": "2024-12-31", "lookback_days": 7})
    )

    assert period == (date(2000, 1, 1), date(2024, 12, 31))


def test_the_period_ends_on_the_kst_date_of_the_run():
    # UTC 날짜로 계산하면 하루 앞선 구간을 조회한다.
    _, observation_end = resolve_observation_period(context())

    assert observation_end == date(2026, 8, 7)


def test_the_period_looks_back_the_requested_number_of_days_inclusive():
    observation_start, observation_end = resolve_observation_period(context({"lookback_days": 3}))

    assert (observation_start, observation_end) == (date(2026, 8, 5), date(2026, 8, 7))


def test_a_lookback_of_one_day_collects_only_that_run():
    assert resolve_observation_period(context({"lookback_days": 1})) == (date(2026, 8, 7), date(2026, 8, 7))


def test_the_default_lookback_applies_when_no_parameter_is_given():
    observation_start, observation_end = resolve_observation_period(context())

    assert (observation_end - observation_start).days == LOOKBACK_DAYS - 1


def test_a_manual_run_falls_back_to_run_after():
    # 수동 run에는 data interval이 없다. 그 run이 조용히 실패하면 안 된다.
    period = resolve_observation_period(
        context(data_interval_end=None, dag_run=SimpleNamespace(run_after=RUN_TIME), params={"lookback_days": 1})
    )

    assert period == (date(2026, 8, 7), date(2026, 8, 7))


def test_a_run_without_any_time_reference_fails():
    with pytest.raises(PeriodError, match="observation_end"):
        resolve_observation_period(context(data_interval_end=None, dag_run=None))


def test_a_reversed_period_fails():
    with pytest.raises(PeriodError, match="after"):
        resolve_observation_period(context({"observation_start": "2026-08-06", "observation_end": "2026-08-03"}))


# `20260801`은 뺀다. Python 3.11부터 `date.fromisoformat`이 구분자 없는 ISO 기본형도 읽는다.
@pytest.mark.parametrize("value", ["2026-13-01", "2026/08/01", "yesterday"])
def test_a_date_that_is_not_iso_fails(value):
    with pytest.raises(PeriodError, match="ISO date"):
        resolve_observation_period(context({"observation_end": value}))


# ---------------------------------------------------------------------------
# 고정 창 구간
# ---------------------------------------------------------------------------


def test_a_calendar_day_is_read_as_given():
    assert calendar_day("2026-08-21", "end_date") == date(2026, 8, 21)


@pytest.mark.parametrize("given", ["20260821", "2026-W34", "yesterday", "2026-8-21"])
def test_a_day_that_is_not_a_calendar_day_fails(given):
    """`date.fromisoformat`은 ISO 기본형과 주 표기도 받는다. 모양을 먼저 봐야 그것들이 걸린다."""
    with pytest.raises(PeriodError, match="must be YYYY-MM-DD"):
        calendar_day(given, "end_date")


def test_the_fixed_span_is_two_hundred_calendar_days():
    # 연휴가 끼어도 SMA60·EMA 안정화 120거래일을 확보하는 창이다.
    assert SPAN_CALENDAR_DAYS == 200
    assert span_start(date(2026, 8, 27)) == date(2026, 2, 8)


def test_a_span_of_exactly_the_window_is_one_window():
    """일상 실행의 동작이다. 여기가 둘로 갈리면 매일 요청이 하나 더 나간다."""
    end_date = date(2026, 8, 25)
    start_date = span_start(end_date)

    assert fetch_windows(start_date, end_date) == [(start_date, end_date)]


def test_a_backfill_span_is_cut_without_gaps_or_overlaps():
    start_date, end_date = date(2016, 8, 15), date(2026, 8, 25)

    windows = fetch_windows(start_date, end_date)

    assert windows[0][0] == start_date
    assert windows[-1][1] == end_date
    # 창의 끝이 포함이라 한 창이 201달력일을 덮는다. "최대 200"이 아니라 "200일 간격"이다.
    assert all(window_end - window_start <= timedelta(days=SPAN_CALENDAR_DAYS) for window_start, window_end in windows)
    # 창끼리 겹치거나 벌어지지 않는다. 벌어지면 지표 계산 창에 구멍이 남는다.
    for earlier, later in pairwise(windows):
        assert later[0] == earlier[1] + timedelta(days=1)


def test_a_single_day_span_is_one_window():
    day = date(2026, 8, 25)

    assert fetch_windows(day, day) == [(day, day)]
