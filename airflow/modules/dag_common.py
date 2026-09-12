"""DAG 파일이 공유하는 얇은 도우미 — Hook 사용, Param 묶음, 실패 분류.

DAG 파일마다 같은 함수가 복사돼 있던 것을 여기로 모았다(2026-09-12 실측 — `_connection`
26벌, `_credentials` 15벌, 관측 구간 Param 셋 16벌, `PeriodError` 변환 19벌). DAG 파일에는
스케줄·재시도·실패 판정만 남는다.

**Airflow를 import한다.** 그래서 부르는 쪽은 DAG 파일과 `modules/kospi/common.py`뿐이다.
수집기·흐름 모듈이 이것을 import하면 그 모듈의 테스트가 Airflow 설정 없이 돌지 않고,
DagBag이 매번 무는 무게가 는다(`tests/modules/test_import_weight.py`가 그 경계를 잰다).

여기 있는 것은 전부 상태 없는 함수다. **재시도할지의 판단은 그대로 DAG가 한다.** 이 모듈은
그 판단에 쓰는 예외 종류와 메시지를 한 벌로 맞출 뿐이다 — 설정·파라미터 오류처럼 되돌릴 수
없는 것은 `AirflowFailException`, 확정 휴장일은 `AirflowSkipException`, 그 밖은 그대로 올린다.
"""

import logging
import os
from collections.abc import Callable, Mapping
from datetime import date
from typing import Any

from airflow.providers.postgres.hooks.postgres import PostgresHook
from airflow.sdk import Param, Variable
from airflow.sdk.exceptions import AirflowFailException, AirflowSkipException
from pydantic import SecretStr

from modules.collectors.kis import KisHTTPError, access_token
from modules.market_session import krx_open_day, us_equity_open_day
from modules.period import (
    END_DATE_PARAM,
    LOOKBACK_DAYS,
    LOOKBACK_DAYS_PARAM,
    OBSERVATION_END_PARAM,
    OBSERVATION_START_PARAM,
    START_DATE_PARAM,
    PeriodError,
    calendar_day,
    resolve_observation_period,
    span_start,
)
from modules.utility import CONNECTION_ID, KIS_UNRECOVERABLE_STATUSES

logger = logging.getLogger(__name__)

# ECOS `RESULT.CODE` 중 재시도로 풀리지 않는 것. INFO-100은 인증키 오류, ERROR-1xx~4xx는
# 요청 형식·식별자 오류다. ERROR-5xx 이상은 제공처 쪽이라 재시도한다.
INVALID_KEY_CODE = "INFO-100"
UNRECOVERABLE_RESULT_PREFIXES = ("ERROR-1", "ERROR-2", "ERROR-3", "ERROR-4")

# 관측 구간 Param의 기본 문구. DAG마다 다른 것(제목의 날짜 종류, 되돌아보기의 이유)은 인자다.
OBSERVATION_START_HINT = "비우면 observation_end에서 lookback_days만큼 뺀 날. 주면 lookback_days를 무시한다."
OBSERVATION_END_HINT = "비우면 이 run 시각의 KST 날짜. 과거 구간을 한 번에 넣을 때 직접 넘긴다."


# ---------------------------------------------------------------------------
# Hook과 환경
# ---------------------------------------------------------------------------


def connection() -> Any:
    """`CONNECTION_ID`의 PEP 249 연결. 닫는 것은 부르는 쪽이 한다(`closing`).

    반환 타입은 provider 버전에 따라 psycopg2/psycopg3 래퍼로 갈린다. 어느 쪽이든
    commit·rollback을 갖는다.
    """
    return PostgresHook(postgres_conn_id=CONNECTION_ID).get_conn()


def require_env(name: str) -> str:
    """환경 변수 하나. 없으면 설정 누락이라 재시도해도 같으므로 즉시 실패다."""
    value = os.environ.get(name)
    if not value:
        raise AirflowFailException(f"{name} is required")
    return value


def kis_credentials() -> tuple[SecretStr, SecretStr]:
    """KIS 앱키·시크릿. 둘 중 하나라도 없으면 즉시 실패다. 값은 메시지에 넣지 않는다."""
    app_key = os.environ.get("KIS_APP_KEY")
    app_secret = os.environ.get("KIS_APP_SECRET")
    if not app_key or not app_secret:
        raise AirflowFailException("KIS_APP_KEY and KIS_APP_SECRET are required")
    return SecretStr(app_key), SecretStr(app_secret)


def slack_settings(channel_env: str = "SLACK_CHANNEL_MARKET") -> tuple[SecretStr, str]:
    """Slack 봇 토큰과 채널. 채널 변수 이름은 DAG가 정한다(시장·문서·운영).

    설정 누락이라 재시도해도 같다. 값 자체는 메시지에 넣지 않는다.
    """
    token = os.environ.get("SLACK_BOT_TOKEN")
    channel = os.environ.get(channel_env)
    if not token or not channel:
        raise AirflowFailException(f"SLACK_BOT_TOKEN and {channel_env} are required")
    return SecretStr(token), channel


# ---------------------------------------------------------------------------
# KIS 호출
# ---------------------------------------------------------------------------


def call_with_token_reissue[Result](
    collector: Any,
    method: Callable[..., Result],
    *args: Any,
    app_key: SecretStr,
    app_secret: SecretStr,
    label: str = "",
) -> Result:
    """KIS 호출 하나 — `method(collector, *args)`. 401이면 토큰을 한 번만 재발급하고 다시 시도한다.

    `method`는 **바인딩되지 않은** 함수다(`KisQuoteCollector.fetch_index_price`처럼 클래스에서
    꺼낸 것, 또는 수집기를 첫 인자로 받는 모듈 함수). 재발급 뒤에는 새 객체로 같은 호출을
    반복해야 하므로 바인딩된 메서드로는 안 된다.

    되돌릴 수 없는 HTTP 오류(`KIS_UNRECOVERABLE_STATUSES`)는 즉시 `AirflowFailException`이고
    `label`이 있으면 메시지 앞에 붙는다. 그 밖의 HTTP 오류는 그대로 올려 DAG가 재시도 여부를
    정한다.

    토큰은 수집기 객체가 사는 동안 안 변하므로 재발급은 객체를 다시 만드는 것이다. 모든 KIS
    수집기가 `(token, app_key, app_secret)`을 받으므로 같은 클래스로 다시 만든다. 캐시는
    `kis_quote_intraday`와 같은 Airflow `Variable`이다 — 발급 횟수 제한이 있어 DAG마다 따로
    받지 않는다.
    """
    try:
        return method(collector, *args)
    except KisHTTPError as error:
        if error.status in KIS_UNRECOVERABLE_STATUSES:
            raise AirflowFailException(f"{label}: {error}" if label else str(error)) from error
        if error.status != 401:
            raise
        logger.warning("KIS returned 401; reissuing the token once")
        reissued = type(collector)(access_token(Variable, app_key, app_secret, force=True), app_key, app_secret)
        return method(reissued, *args)


# ---------------------------------------------------------------------------
# 조회 구간
# ---------------------------------------------------------------------------


def observation_period_params(
    *,
    lookback_hint: str,
    lookback_default: int = LOOKBACK_DAYS,
    start_title: str = "조회 시작 관측일",
    end_title: str = "조회 종료 관측일",
    start_hint: str = OBSERVATION_START_HINT,
    end_hint: str = OBSERVATION_END_HINT,
) -> dict[str, Param]:
    """관측 구간 Param 셋(`observation_start`·`observation_end`·`lookback_days`).

    `resolve_observation_period`가 읽는 이름과 짝이다. 문구는 DAG가 정한다 — 무엇의 날짜인지
    (관측일·기준일·거래일)와 왜 그만큼 되돌아보는지는 제공처마다 다르다.
    """
    return {
        OBSERVATION_START_PARAM: Param(
            None,
            type=["null", "string"],
            format="date",
            title=start_title,
            description=start_hint,
        ),
        OBSERVATION_END_PARAM: Param(
            None,
            type=["null", "string"],
            format="date",
            title=end_title,
            description=end_hint,
        ),
        LOOKBACK_DAYS_PARAM: Param(
            lookback_default,
            type="integer",
            minimum=1,
            title="되돌아볼 일수",
            description=lookback_hint,
        ),
    }


def resolve_period_or_fail(
    context: Mapping[str, Any],
    default_lookback_days: int = LOOKBACK_DAYS,
) -> tuple[date, date]:
    """이 run의 관측 구간. 파라미터가 틀린 것은 되돌릴 수 없어 즉시 실패다."""
    try:
        return resolve_observation_period(context, default_lookback_days)
    except PeriodError as error:
        raise AirflowFailException(str(error)) from error


def calendar_day_or_fail(given: Any, name: str) -> date:
    """`YYYY-MM-DD` 하나를 읽는다. 규칙은 `modules/period.py`에 한 벌 있다.

    여기 남는 것은 그 실패를 어떤 Airflow 예외로 올릴지뿐이다. 파라미터가 틀린 것은
    되돌릴 수 없어 재시도해도 같은 답이다.
    """
    try:
        return calendar_day(given, name)
    except PeriodError as error:
        raise AirflowFailException(str(error)) from None


def requested_start_date(end_date: date, params: Mapping[str, Any]) -> date:
    """고정 창 수집이 구간의 시작으로 쓸 날짜. 비우면 200달력일 앞이다.

    끝보다 뒤인 시작은 조용히 빈 구간이 되므로 막는다.
    """
    given = params.get(START_DATE_PARAM)
    if not given:
        return span_start(end_date)
    start_date = calendar_day_or_fail(given, START_DATE_PARAM)
    if start_date > end_date:
        raise AirflowFailException(f"{START_DATE_PARAM} {start_date} must not be after {END_DATE_PARAM} {end_date}")
    return start_date


# ---------------------------------------------------------------------------
# 휴장일과 실패 분류
# ---------------------------------------------------------------------------


def skip_unless_krx_open(connection: Any, day: date) -> None:
    """KRX 확정 휴장일이면 태스크를 건너뛴다. **모르면 계속한다.**

    행이 없거나 아직 판정하지 않았으면(`None`) 그대로 진행한다 — 캘린더 수집이 실패했다는
    이유로 진짜 거래일 데이터를 잃는 것이 빈 요청 몇 번보다 나쁘다.
    """
    if krx_open_day(connection, day) is False:
        raise AirflowSkipException(f"KRX is closed on {day}")


def skip_unless_us_open(connection: Any, day: date) -> None:
    """미국 확정 휴장일이면 건너뛴다. 모르면 계속한다. 날짜는 뉴욕 세션 날짜다."""
    if us_equity_open_day(connection, day) is False:
        raise AirflowSkipException(f"US equity market was closed on {day}")


def is_unrecoverable_result(code: str) -> bool:
    """이 ECOS `RESULT.CODE`가 재시도로 풀리지 않는 오류인지."""
    return code == INVALID_KEY_CODE or code.startswith(UNRECOVERABLE_RESULT_PREFIXES)
