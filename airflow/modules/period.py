"""run 파라미터와 run 시각에서 조회 구간을 정한다.

구간을 정하는 건 파싱·계산이므로 `modules/`에 두고, `dags/`에는 그 결과를 어떤 예외로
올릴지만 남긴다. **DAG마다 복사하지 않는다.**

규칙이 둘이다. 파라미터 이름과 기본 구간이 다르기 때문이다.

- **관측 구간**(`resolve_observation_period`) — `observation_start`/`observation_end`와
  되돌아보기 일수. 지표 수집기(`fred_treasury_daily`, `ecos_market_rate_daily`,
  `mof_jgb_daily`)가 쓴다. 제공처의 발표 지연을 되돌아보기로 흡수한다.
- **고정 창 구간**(`calendar_day`·`span_start`·`fetch_windows`) — `start_date`/`end_date`와
  200달력일 고정 창. 시세 일봉 수집기(`kis_index_daily`, `kis_future_daily`,
  `kis_overseas_index_daily`)가 쓴다. 매일 같은 창을 다시 받아 실패한 날이 저절로 메워진다.

여기서 Airflow를 import하지 않는다. 이 모듈을 import하는 것만으로 Airflow 설정이
초기화되면 수집기 테스트가 배포 환경 없이 돌지 않는다. 그래서 실패는 `PeriodError`로
올리고 `AirflowFailException`으로 바꾸는 일은 DAG가 한다.

날짜 경계는 KST 기준이다. `data_interval_end`와 `run_after`는 aware 값이므로 KST로 바꾼
뒤 날짜를 뽑는다. 시간대는 여기서 날짜 경계를 정할 때만 쓰고, 저장하는 시각은 UTC다.
"""

import re
from collections.abc import Mapping
from datetime import date, timedelta
from typing import Any

from modules.utility import KST_TIMEZONE

# 조회 구간을 직접 지정하는 run 파라미터. 비어 있으면 run 시각에서 계산한다.
OBSERVATION_START_PARAM = "observation_start"
OBSERVATION_END_PARAM = "observation_end"
LOOKBACK_DAYS_PARAM = "lookback_days"

# 휴장일과 발표 지연을 별도 캘린더 없이 흡수한다. 재조회는 멱등 키로 흡수된다.
LOOKBACK_DAYS = 7

# 고정 창 수집의 파라미터. 관측 구간 쪽과 이름이 다른 것은 뜻이 다르기 때문이다 —
# 저기는 "언제 발표된 값을 볼까"이고 여기는 "어느 거래일 구간의 봉을 받을까"다.
START_DATE_PARAM = "start_date"
END_DATE_PARAM = "end_date"

# 달력 하루만 받는다. ISO 주 표기(2026-W34)와 기본형(20260821)을 걸러 내는 그물이다.
# `date.fromisoformat`은 `2026-W34`도 받아 그 주의 월요일로 바꾸므로 모양을 먼저 본다.
CALENDAR_DAY_PATTERN = re.compile(r"\d{4}-\d{2}-\d{2}")

# SMA60과 EMA 안정화에 필요한 120거래일을 연휴 포함 구간에서도 확보하는 고정 창이다
# (docs/analysis/market-technical-indicators.md 4.4절). 백필의 창 크기이기도 하다 —
# 200달력일이 제공처의 페이지 상한 안에 들어오는 것이 일상 실행에서 이미 보장되므로
# 백필용 크기를 따로 정할 이유가 없다.
SPAN_CALENDAR_DAYS = 200


class PeriodError(ValueError):
    """run 파라미터로 구간을 만들지 못했다. 파라미터를 고치기 전에는 재시도해도 같다."""


def _parse_param_date(name: str, value: object) -> date:
    try:
        return date.fromisoformat(str(value))
    except ValueError as error:
        raise PeriodError(f"{name} must be an ISO date (YYYY-MM-DD)") from error


def resolve_observation_period(
    context: Mapping[str, Any],
    default_lookback_days: int = LOOKBACK_DAYS,
) -> tuple[date, date]:
    """이 run이 조회할 관측 구간.

    파라미터가 있으면 그 값을 그대로 쓰고, 없으면 run 시각에서 계산한다. 계산 기준은
    `data_interval_end`이고 수동 run에는 그 값이 없으므로 `dag_run.run_after`로 물러선다.
    두 값 모두 aware라 KST로 바꿔 날짜를 뽑는다.
    """
    params = context.get("params") or {}

    end_override = params.get(OBSERVATION_END_PARAM)
    if end_override:
        observation_end = _parse_param_date(OBSERVATION_END_PARAM, end_override)
    else:
        reference = context.get("data_interval_end") or getattr(context.get("dag_run"), "run_after", None)
        if reference is None:
            raise PeriodError(f"No run time to derive the observation period from; pass {OBSERVATION_END_PARAM}")
        observation_end = reference.astimezone(KST_TIMEZONE).date()

    start_override = params.get(OBSERVATION_START_PARAM)
    if start_override:
        observation_start = _parse_param_date(OBSERVATION_START_PARAM, start_override)
    else:
        lookback_days = int(params.get(LOOKBACK_DAYS_PARAM) or default_lookback_days)
        observation_start = observation_end - timedelta(days=lookback_days - 1)

    if observation_start > observation_end:
        raise PeriodError(
            f"{OBSERVATION_START_PARAM} ({observation_start}) is after {OBSERVATION_END_PARAM} ({observation_end})"
        )
    return observation_start, observation_end


# ---------------------------------------------------------------------------
# 고정 창 구간
# ---------------------------------------------------------------------------


def calendar_day(given: object, name: str) -> date:
    """`YYYY-MM-DD` 하나를 읽는다. 모양을 먼저 본다.

    `date.fromisoformat`이 ISO 주 표기(`2026-W34`)도 받아 그 주의 월요일로 바꾼다. 그것을
    그대로 두면 사용자가 적은 것과 다른 구간을 조용히 조회한다.
    """
    text = str(given).strip()
    if not CALENDAR_DAY_PATTERN.fullmatch(text):
        raise PeriodError(f"{name} must be YYYY-MM-DD, got {given!r}")
    try:
        return date.fromisoformat(text)
    except ValueError:
        raise PeriodError(f"{name} must be YYYY-MM-DD, got {given!r}") from None


def span_start(end_date: date, days: int = SPAN_CALENDAR_DAYS) -> date:
    """고정 창의 시작. 끝에서 `days`만큼 앞이다."""
    return end_date - timedelta(days=days)


def fetch_windows(
    start_date: date,
    end_date: date,
    days: int = SPAN_CALENDAR_DAYS,
) -> list[tuple[date, date]]:
    """조회 구간을 `days`씩 끊는다. 오래된 창이 먼저다.

    한 심볼의 페이지 상한을 넘지 않으려고 나눈다. 일상 실행은 구간이 정확히 `days`라
    창 하나가 나오고 동작이 바뀌지 않는다.

    **창의 끝은 포함이다.** 커서를 `days`씩 밀고 끝을 포함으로 잡으므로 한 창이 `days + 1`
    달력일을 덮는다. "최대 `days`일"이 아니라 "`days`일 간격"이다.
    """
    windows: list[tuple[date, date]] = []
    cursor = start_date
    while cursor <= end_date:
        window_end = min(cursor + timedelta(days=days), end_date)
        windows.append((cursor, window_end))
        cursor = window_end + timedelta(days=1)
    return windows
