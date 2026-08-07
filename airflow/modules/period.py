"""run 파라미터와 run 시각에서 조회 관측 구간을 정한다.

`fred_treasury_daily`, `ecos_market_rate_daily`, `mof_jgb_daily`가 같은 규칙을 쓴다.
구간을 정하는 건 파싱·검증이므로 `modules/`에 두고, `dags/`에는 그 결과를 어떤 예외로
올릴지만 남긴다.

여기서 Airflow를 import하지 않는다. 이 모듈을 import하는 것만으로 Airflow 설정이
초기화되면 수집기 테스트가 배포 환경 없이 돌지 않는다. 그래서 실패는 `PeriodError`로
올리고 `AirflowFailException`으로 바꾸는 일은 DAG가 한다.

날짜 경계는 KST 기준이다. `data_interval_end`와 `run_after`는 aware 값이므로 KST로 바꾼
뒤 날짜를 뽑는다. 시간대는 여기서 날짜 경계를 정할 때만 쓰고, 저장하는 시각은 UTC다.
"""

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
