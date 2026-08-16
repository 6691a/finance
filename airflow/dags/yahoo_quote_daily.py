"""Yahoo Finance에서 해외 지수·선물·환율의 일봉을 수집한다.

`yahoo_quote_intraday`와 대상 심볼은 같고 목적이 다르다. 저쪽은 장중 알림을 위한 1분봉이고
**제공처가 30일치만 보관한다.** 30일 표본으로는 두 시계열의 상관을 낼 수 없다. "환율이
오르면 반도체가 빠지더라" 같은 판단에는 몇 년치 일별 수익률이 필요하다.

일봉에는 그 보관 제한이 없다. 같은 chart 엔드포인트를 `interval=1d&range=10y`로 부르면
**심볼당 요청 한 번에 십수 년이 온다**(실측: `^SOX` 2,514행, `USDKRW=X` 2,611행). 그래서
이 DAG는 하루 한 번만 돌고, 매 실행이 10년 구간을 통째로 다시 받아 upsert한다. 증분만 받는
설계로 얻을 이득이 없다. 요청 수가 같기 때문이다.

## 거래일은 UTC 날짜가 아니다

응답의 `timestamp`는 그 시장이 문을 연 순간이다. 어느 달력 날짜에 속하는지는 시장의
시간대가 정한다. `USDKRW=X`의 `2016-08-14T23:00Z` 봉은 런던 기준 8월 15일이다. 판정은
`parse_daily_bars`가 응답의 `exchangeTimezoneName`을 IANA 시간대로 읽어서 한다. 응답이 주는
`gmtoffset`은 **응답을 받은 시점의 offset**이라 10년치에 그대로 쓰면 서머타임 구간이 하루씩
어긋난다.

## 국내 종목은 여기 없다

`stock_investor_trade_daily`가 이미 시가·고가·저가·종가를 수급과 함께 갖고 있다. 국내 지수와
선물은 KIS가 분봉으로 주지만 일봉 경로는 아직 없다. 필요해지면 별도 DAG로 붙인다.

## params

| 이름 | 기본값 | 뜻 |
| --- | --- | --- |
| `range` | `10y` | 받을 기간. Yahoo 표기(`1y`, `2y`, `5y`, `10y`, `max`) |

    airflow dags trigger yahoo_quote_daily --conf '{"range": "max"}'

## 실패와 재시도

- 심볼 하나가 실패해도 나머지는 저장한다. 사유는 `source_record.metadata`에 남는다.
- HTTP 400/401/403/404는 설정 오류라 즉시 실패한다. 429는 이 심볼만 건너뛴다.
- 전부 실패하면 재시도 가능한 오류로 올린다.

## 필요한 환경

- `CONNECTION_ID`가 가리키는 Airflow 연결. 접속 정보는 `AIRFLOW_CONN_FINANCE`가 갖는다.
- 인증은 없다. Yahoo v8 chart는 비공식 API다.
"""

import logging
from datetime import timedelta
from typing import Any

import pendulum
from airflow.exceptions import AirflowFailException
from airflow.providers.postgres.hooks.postgres import PostgresHook
from airflow.sdk import Param, dag, get_current_context, task

from modules.collectors.yahoo import (
    DAILY_RANGE,
    DAILY_RANGES,
    QuoteSymbol,
    SymbolOutcome,
    YahooHTTPError,
    fetch_daily_bars,
    store_daily_bars,
)
from modules.utility import CONNECTION_ID, KST_TIMEZONE

logger = logging.getLogger(__name__)

RANGE_PARAM = "range"

# 설정 오류라 재시도해도 같은 결과인 HTTP 상태.
UNRECOVERABLE_STATUSES = frozenset({400, 401, 403, 404})


def requested_range(params: dict[str, Any]) -> str:
    """받을 기간. 오타는 요청 전에 막는다.

    Yahoo는 잘못된 `range`에도 200에 빈 결과를 주므로, 응답만으로는 오타와 휴장을 가를 수
    없다. `ecos.py`가 항목코드를 Enum으로 좁히는 것과 같은 이유다.
    """
    given = params.get(RANGE_PARAM) or DAILY_RANGE
    if given not in DAILY_RANGES:
        raise AirflowFailException(f"{RANGE_PARAM} must be one of {sorted(DAILY_RANGES)}, got {given!r}")
    return str(given)


@dag(
    dag_id="yahoo_quote_daily",
    dag_display_name="🌐 해외 지수·선물·환율 일봉 (Yahoo)",
    description="하루 한 번 Yahoo에서 해외 시세 일봉 10년치를 받아 저장한다. 상관 분석의 표본이다.",
    # KST 매일 07:30 = UTC 전날 22:30. 미국 정규장 마감(KST 05:00~06:00)보다 뒤라 전날
    # 종가가 확정된 뒤에 받는다. 24시간 거래되는 선물·환율은 어차피 경계가 없다.
    schedule="30 7 * * *",
    start_date=pendulum.datetime(2026, 8, 15, tz=KST_TIMEZONE),  # KST 2026-08-15 00:00 = UTC 2026-08-14 15:00
    catchup=False,
    max_active_runs=1,
    # 하루 한 번이라 다음 run이 멀다. 1시간 간격 2회는 `fred`와 같은 판단이다.
    default_args={"retries": 2, "retry_delay": timedelta(hours=1)},
    params={
        RANGE_PARAM: Param(
            DAILY_RANGE,
            type="string",
            enum=sorted(DAILY_RANGES),
            title="받을 기간",
            description="Yahoo range 표기. 기본값으로도 심볼당 10년이 온다.",
        ),
    },
    doc_md=__doc__,
    tags=["yahoo", "market", "daily"],
)
def yahoo_quote_daily():
    @task(task_display_name="일봉 수집·저장")
    def collect() -> int:
        """심볼 전부를 한 번 훑어 `source_record` 1건과 그에 딸린 일봉을 저장한다.

        심볼마다 태스크를 매핑하지 않는 이유는 `yahoo_quote_intraday`와 같다. 계보 레코드가
        수집 단위와 맞아야 하고, 심볼 하나의 실패는 예외로 잡아 나머지를 저장한다.

        **미국 휴장일 필터를 걸지 않는다.** 10년 구간을 통째로 다시 받는 수집이라 오늘 하루가
        휴장이어도 받을 값이 그대로 있다.
        """
        context = get_current_context()
        range_ = requested_range(dict(context.get("params") or {}))

        responses = []
        failures: list[SymbolOutcome] = []
        for symbol in QuoteSymbol:
            try:
                responses.append(fetch_daily_bars(symbol, range_))
            except YahooHTTPError as error:
                if error.status in UNRECOVERABLE_STATUSES:
                    # 심볼이 폐지됐거나 URL 규칙이 바뀐 것이다. 재시도해도 같다.
                    raise AirflowFailException(f"{symbol.value}: {error}") from error
                logger.warning("%s failed with HTTP %s", symbol.value, error.status)
                failures.append(
                    SymbolOutcome(
                        symbol=symbol.value,
                        yahoo_symbol=symbol.yahoo_symbol,
                        status=error.status,
                        error=str(error),
                    )
                )
            except ConnectionError as error:
                logger.warning("%s failed to connect: %s", symbol.value, error)
                failures.append(
                    SymbolOutcome(
                        symbol=symbol.value,
                        yahoo_symbol=symbol.yahoo_symbol,
                        error=str(error),
                    )
                )

        if not responses:
            # 하나도 못 받았으면 Yahoo 쪽 문제이거나 네트워크 문제다. 재시도할 값어치가 있다.
            raise ConnectionError("Every Yahoo request failed")

        # 반환 타입은 provider 버전에 따라 psycopg2/psycopg3 래퍼로 갈린다. 런타임 객체는
        # 어느 쪽이든 PEP 249 연결이라 commit·rollback을 갖는다.
        connection: Any = PostgresHook(postgres_conn_id=CONNECTION_ID).get_conn()
        try:
            bar_count, outcomes = store_daily_bars(connection, responses, range_, failures)
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

        succeeded = [outcome for outcome in outcomes if outcome.error is None]
        if not succeeded:
            # 계보 레코드는 남았으므로 실패로 끝낸다.
            raise AirflowFailException("Every symbol failed to parse; see source_record metadata")

        logger.info(
            "Stored %s daily bars over %s across %s symbols (%s failed)",
            bar_count,
            range_,
            len(succeeded),
            len(outcomes) - len(succeeded),
        )
        return bar_count

    collect()


yahoo_quote_daily = yahoo_quote_daily()
