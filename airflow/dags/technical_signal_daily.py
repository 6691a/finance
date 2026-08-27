"""국내 기술적 매매 신호 검출 DAG.

확정 일봉의 SMA20/SMA60, MACD와 시그널 라인, RSI14에서 **교차 사건**을 찾아
`technical_signal`에 남긴다. 지표값 자체는 저장하지 않는다 — 원천 OHLCV에서 언제든 다시
계산되기 때문이다. 사건만 저장하는 이유는 "언제 교차했는지"가 값에서 되살려지지 않고,
그 뒤 실제로 어떻게 움직였는지를 채점하려면 사건이 행으로 남아야 하기 때문이다.
설계는 docs/analysis/market-technical-indicators.md 12절이다.

**신호는 판정이 아니다.** 골든크로스가 곧 매수가 아니고, 그 사건이 유효했는지는 문서
12.6절의 사후 수익률 SQL이 답한다. 이 DAG는 사건을 기록할 뿐 주문도 점수도 만들지 않는다.

## 앞단

- `kis_investor_trade_daily`(18:10)가 국내 종목 확정 일봉을 넣는다.
- `kis_index_daily`(18:20)가 KOSPI·KOSDAQ 확정 일봉을 넣는다.

둘 다 끝난 뒤 18:40에 돈다. 앞단이 하루 늦게 복구돼도 `scan_bars` 기본값(5)이 최근 며칠을
다시 훑어 사건이 빠지지 않는다. 저장이 upsert라 재검출은 무해하다.

## params

| 이름 | 기본값 | 뜻 |
| --- | --- | --- |
| `scan_bars` | `5` | 최근 몇 봉을 다시 볼지. 초기 백필은 120 |

## 실패와 재시도

- **표본이 60봉에 못 미치는 대상은 건너뛴다.** 이름을 로그에 남긴다.
- **대상 전부를 건너뛰면 실패한다.** 0건 저장은 "교차가 없었다"는 정상이지만, 볼 대상이
  없는 것은 앞단 수집이 비었다는 뜻이다. 그걸 성공으로 표시하면 다음 실행도 같은 자리에서
  조용히 지나간다.
- 자동 실행은 KRX 휴장일이면 skip한다. 수동 실행은 건너뛰지 않는다.
- DB 오류는 그대로 올라가 태스크를 죽인다.

## 필요한 환경

- `CONNECTION_ID`가 가리키는 Airflow 연결. 외부 API를 부르지 않아 KIS 자격 증명이 필요 없다.
"""

import logging
from contextlib import closing
from datetime import UTC, datetime, timedelta
from typing import Any

import pendulum
from airflow.providers.postgres.hooks.postgres import PostgresHook
from airflow.sdk import Param, dag, get_current_context, task
from airflow.sdk.exceptions import AirflowFailException, AirflowSkipException

from modules.market_session import krx_open_day
from modules.technical.indicators import SIGNAL_SCAN_BARS_MAX, TECHNICAL_LOOKBACK_BARS
from modules.technical.signals import TechnicalSignalError, detect_and_store
from modules.utility import CONNECTION_ID, KST_TIMEZONE, atomic

logger = logging.getLogger(__name__)

SCAN_BARS_PARAM = "scan_bars"

# 앞단이 하루 늦게 복구돼도 사건이 빠지지 않을 만큼만 되돌아본다. upsert라 재검출은 무해하다.
DEFAULT_SCAN_BARS = 5


def _connection() -> Any:
    return PostgresHook(postgres_conn_id=CONNECTION_ID).get_conn()


def requested_scan_bars(params: dict[str, Any]) -> int:
    """몇 봉을 다시 볼지.

    `or DEFAULT_SCAN_BARS`로 기본값을 주지 않는다. 0이 falsy라 조용히 기본값이 되고,
    운영자는 아무것도 검출하지 않기를 바랐는데 최근 며칠을 다시 훑게 된다.
    """
    given = params.get(SCAN_BARS_PARAM)
    scan_bars = DEFAULT_SCAN_BARS if given is None else int(given)
    if not 1 <= scan_bars <= SIGNAL_SCAN_BARS_MAX:
        raise AirflowFailException(f"{SCAN_BARS_PARAM} must be between 1 and {SIGNAL_SCAN_BARS_MAX}, got {scan_bars}")
    return scan_bars


@dag(
    dag_id="technical_signal_daily",
    dag_display_name="📐 국내 기술적 매매 신호 (계산)",
    description="확정 일봉의 이동평균·MACD·RSI 교차를 사건으로 검출해 사후 채점용으로 저장한다.",
    schedule="40 18 * * 1-5",  # KST 월~금 18:40 = UTC 월~금 09:40
    start_date=pendulum.datetime(2026, 8, 24, tz=KST_TIMEZONE),  # KST 2026-08-24 00:00 = UTC 2026-08-23 15:00
    catchup=False,
    max_active_runs=1,
    default_args={"retries": 2, "retry_delay": timedelta(minutes=10)},
    params={
        SCAN_BARS_PARAM: Param(
            DEFAULT_SCAN_BARS,
            type="integer",
            minimum=1,
            maximum=SIGNAL_SCAN_BARS_MAX,
            title="다시 볼 봉 수",
            description=(
                f"최근 몇 거래일을 다시 훑을지. 1~{SIGNAL_SCAN_BARS_MAX}. 초기 백필은 120, "
                "일봉을 2016년까지 백필한 뒤의 전 구간 재검출은 3000."
            ),
        ),
    },
    doc_md=__doc__,
    tags=["market", "daily", "korea", "technical", "signal"],
)
def technical_signal_daily():
    @task(task_display_name="신호 검출·저장")
    def detect() -> int:
        context = get_current_context()
        params = dict(context.get("params") or {})
        scan_bars = requested_scan_bars(params)

        now_kst = datetime.now(UTC).astimezone(KST_TIMEZONE)
        # 자동 실행만 휴장일을 건너뛴다. 수동 실행은 백필이라 막을 이유가 없다.
        if context.get("dag_run") is None or getattr(context["dag_run"], "run_type", "") != "manual":
            connection = _connection()
            try:
                closed = krx_open_day(connection, now_kst.date()) is False
            finally:
                connection.close()
            if closed:
                raise AirflowSkipException(f"KRX is closed on {now_kst.date()}")

        with closing(_connection()) as connection:
            try:
                with atomic(connection):
                    result = detect_and_store(
                        connection,
                        as_of_at=datetime.now(UTC),
                        scan_bars=scan_bars,
                        # 사건을 찾을 구간 앞에 지표 워밍업 봉을 붙여 읽는다. 이 여유가
                        # 없으면 구간의 앞머리에서 SMA60이 안 나와 사건이 통째로 빠진다.
                        lookback_bars=scan_bars + TECHNICAL_LOOKBACK_BARS,
                    )
            except TechnicalSignalError as error:
                raise AirflowFailException(str(error)) from error

        if result.skipped:
            logger.warning("Skipped subjects with too few bars: %s", ", ".join(result.skipped))
        logger.info("Stored %s signals across %s subjects", result.stored, len(result.subjects))
        return result.stored

    detect()


technical_signal_daily = technical_signal_daily()
