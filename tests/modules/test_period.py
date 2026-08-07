from datetime import UTC, date, datetime
from types import SimpleNamespace

import pytest

from modules.period import LOOKBACK_DAYS, PeriodError, resolve_observation_period

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
