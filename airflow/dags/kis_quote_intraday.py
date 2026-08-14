"""KIS 국내 지수·지수선물 1분봉 장중 수집 DAG.

`yahoo_quote_intraday`가 미국 지수·선물을 받는 것과 짝이다. 같은 `quote_bar` 테이블에
`provider = 'kis'`로 쌓는다.

**국내 시세는 국내 증권사에서 받는다.** Yahoo 에는 KOSPI200 선물이 없고(실측으로
`KS200.KS`·`101RC000.KS` 둘 다 Not Found) KIS 는 준다. 게다가 국내 시세는 무료다.
미국 선물을 막았던 CME API 시세료(월 USD 221.10)에 해당하지 않는다.

이 값이 중요한 이유는 이 수집의 가설이 "미국 신호 → 한국 종목" 이기 때문이다. KOSPI200
선물은 그 **한국 쪽 반응**을 실시간으로 보는 가장 직접적인 값이다.

## 왜 24시간 돌지 않는가

`yahoo_quote_intraday`는 미국 선물이 거의 24시간 거래돼서 시간 창 없이 돈다. 여기는 다르다.

| 구간(KST) | 코스피 | KOSPI200 선물 |
| --- | --- | --- |
| 09:00~15:30 | 거래 중 | 거래 중 |
| 15:30~15:45 | 멈춤 | 거래 중 |
| 그 밖 | **봉이 없다** | **봉이 없다** |

코스피 현물도 여기서 받는다. Yahoo 의 `^KS11` 분봉은 일중 변동이 5~10%로 나오는 날이 있어
신뢰할 수 없었다(문서 §8.4). **국내에서 받을 수 있는 것은 국내를 우선한다.**

야간장은 이 API 로 오지 않는다. 야간 시각을 넣어도 정규장 마감(15:45)으로 잘리고
`krx-ngt-*` REST 엔드포인트는 404 다(실측). 웹소켓만 되는 것으로 보이며 상주 프로세스가
필요해 이번 범위 밖이다.

그래서 **평일 08:00~16:59 KST 에만 돈다.** 개장 전후로 여유를 두되 밤새 빈 호출을 하지
않는다. 미국 쪽처럼 24시간 돌 이유가 없다.

## 태스크 둘

`collect`가 1분봉을, `collect_movement`가 코스피·코스닥의 상승·보합·하락 종목 수를 받는다.
둘을 나눈 이유는 분포 실패가 분봉 저장을 막지 않게 하려는 것이다. 휴장일 skip 판정도 각자
한다. 한쪽에만 걸면 다른 쪽이 휴장일에 그대로 돈다.

분포는 전 종목을 순회해 계산하지 않는다. 지수 현재가 응답(`FHPUP02100000`)이 이미 다섯
종목 수를 준다. **개장 전과 마감 뒤에는 그 다섯 값이 0으로 리셋되므로 저장하지 않는다**
(실측). 장중에는 상승·보합·하락의 합이 전 종목이라 all-zero가 나올 수 없어서, all-zero는
분포가 아니라 "장 밖"이라는 뜻이다.

## 5분마다 도는데 왜 1분 데이터가 쌓이는가

KIS 분봉은 한 번에 **102봉**을 준다. 1분봉이면 1시간 40분치라 5분 폴링에 넉넉하다.
`lookback_minutes`가 그중 최근 몇 분을 저장할지 정하고, 멱등 키 `(provider, symbol, bar_at)`가
겹치는 봉을 흡수한다.

Yahoo 가 하루치를 통째로 준 것과 다르므로 **`lookback_minutes` 상한이 102 다.**

## 월물

KOSPI200 선물은 분기물(3·6·9·12)이고 만기는 만기월 **두 번째 목요일**이다. 종목코드는
`A0` + 상품(1) + 연도 끝자리 + 만기월이라 `modules.collectors.kis.front_contract`가 날짜에서
계산한다. 코드를 하드코딩하지 않는다.

**미니가 아니라 정규 계약이다.** 미니(`A056`)는 계약 크기가 1/5이고 거래량도 적다
(실측: 같은 102봉 구간에서 정규 최근월물 16,393 대 차근월물 119).

실제 월물은 `quote_bar.contract_code`에 저장한다. 월물이 바뀌면 가격에 갭이 생기는데 그
값이 없으면 갭이 시장 급변인지 롤오버인지 구분할 수 없다.

## params

| 이름 | 기본값 | 뜻 |
| --- | --- | --- |
| `lookback_minutes` | `15` | 이 run 이 저장할 최근 구간. 상한은 102(한 번에 오는 봉 수) |

## 실패와 재시도

- HTTP 400/401/403: 설정 오류라 `AirflowFailException`으로 즉시 실패한다.
- 401 은 토큰 만료일 수 있으므로 **한 번 재발급하고 다시 시도한다.**
- 그 밖의 HTTP 오류와 네트워크 오류: 그대로 올려 재시도한다.
- 본문 `rt_cd` 오류: 종목코드나 권한 문제라 즉시 실패한다.
- **최근 구간에 새 봉이 0건인 것은 실패가 아니다.** 개장 전이나 마감 뒤가 그렇다.

## 필요한 환경

- `KIS_APP_KEY`, `KIS_APP_SECRET` 환경 변수. Airflow 가 읽는 건 `compose/local/airflow/.env`다.
- `CONNECTION_ID`가 가리키는 Airflow 연결. 접속 정보는 `AIRFLOW_CONN_NEWS`가 갖는다.

토큰은 **발급 횟수 제한이 있어** Airflow Variable 에 캐시한다. 폴링마다 발급하지 않는다.
"""

import logging
import os
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pendulum
from airflow.exceptions import AirflowFailException, AirflowSkipException
from airflow.providers.postgres.hooks.postgres import PostgresHook
from airflow.sdk import Param, Variable, dag, get_current_context, task
from pydantic import SecretStr

from modules.collectors.kis import (
    MAX_BARS_PER_REQUEST,
    MOVEMENT_INDEXES,
    DomesticFuture,
    DomesticIndex,
    KisHTTPError,
    KisPayloadError,
    KisResultError,
    SymbolOutcome,
    access_token,
    fetch_bars,
    fetch_index_bars,
    fetch_index_price,
    front_contract,
    store_bars,
    store_market_movement,
)
from modules.market_session import krx_open_day
from modules.utility import KST_TIMEZONE

logger = logging.getLogger(__name__)

CONNECTION_ID = "news"

LOOKBACK_MINUTES = 15
LOOKBACK_MINUTES_PARAM = "lookback_minutes"

# 설정 오류라 재시도해도 같은 결과인 HTTP 상태.
UNRECOVERABLE_STATUSES = frozenset({400, 403, 404})


def _credentials() -> tuple[SecretStr, SecretStr]:
    app_key = os.environ.get("KIS_APP_KEY")
    app_secret = os.environ.get("KIS_APP_SECRET")
    if not app_key or not app_secret:
        raise AirflowFailException("KIS_APP_KEY and KIS_APP_SECRET are required")
    return SecretStr(app_key), SecretStr(app_secret)


def _closed_today(today_kst: date) -> bool:
    """확정 휴장일이면 `True`.

    행이 없거나 아직 판정하지 않았으면 `False`다. **모르면 수집을 계속한다.** 캘린더 수집이
    실패했다는 이유로 진짜 거래일 데이터를 잃는 것이 빈 요청 몇 번보다 나쁘다.

    **두 태스크가 함께 쓴다.** 한쪽에만 걸면 다른 쪽이 휴장일에 그대로 돈다.
    """
    connection: Any = PostgresHook(postgres_conn_id=CONNECTION_ID).get_conn()
    try:
        return krx_open_day(connection, today_kst) is False
    finally:
        connection.close()


def _skip_when_closed(today_kst: date) -> None:
    if _closed_today(today_kst):
        raise AirflowSkipException(f"KRX is closed on {today_kst}")


def cached_access_token(app_key: SecretStr, app_secret: SecretStr, force: bool = False) -> SecretStr:
    """토큰 캐시에 Airflow `Variable`을 저장소로 물린다.

    캐시 판정과 재발급은 `modules.collectors.kis.access_token`이 한다. 여기 남는 것은
    저장소를 고르는 일뿐이다.
    """
    return access_token(Variable, app_key, app_secret, force=force)


@dag(
    dag_id="kis_quote_intraday",
    dag_display_name="📈 국내 지수·선물 1분봉 (KIS)",
    description="국내 정규장 동안 5분마다 KIS에서 지수·지수선물 1분봉을 받아 저장한다.",
    # KST 평일 08:00~16:59 = UTC 평일 23:00~07:59. 국내 정규장(09:00~15:45)을 앞뒤로 감싼다.
    # 야간장이 이 API 로 오지 않으므로 밤새 돌 이유가 없다.
    schedule="*/5 8-16 * * 1-5",
    start_date=pendulum.datetime(2026, 8, 8, tz=KST_TIMEZONE),  # KST 2026-08-08 00:00 = UTC 2026-08-07 15:00
    catchup=False,
    max_active_runs=1,
    default_args={"retries": 1, "retry_delay": timedelta(minutes=2)},
    params={
        LOOKBACK_MINUTES_PARAM: Param(
            LOOKBACK_MINUTES,
            type="integer",
            minimum=1,
            maximum=MAX_BARS_PER_REQUEST,
            title="저장할 최근 구간(분)",
            description=f"이 run 이 저장할 봉의 범위. 한 번에 {MAX_BARS_PER_REQUEST}봉만 오므로 그게 상한이다.",
        ),
    },
    doc_md=__doc__,
    tags=["kis", "market", "intraday", "korea"],
)
def kis_quote_intraday():
    @task(task_display_name="1분봉 수집·저장")
    def collect() -> int:
        context = get_current_context()
        params = dict(context.get("params") or {})
        lookback_minutes = int(params.get(LOOKBACK_MINUTES_PARAM) or LOOKBACK_MINUTES)
        if not 1 <= lookback_minutes <= MAX_BARS_PER_REQUEST:
            raise AirflowFailException(f"{LOOKBACK_MINUTES_PARAM} must be between 1 and {MAX_BARS_PER_REQUEST}")

        now = datetime.now(UTC)
        since = now - timedelta(minutes=lookback_minutes)
        today_kst = now.astimezone(KST_TIMEZONE).date()

        _skip_when_closed(today_kst)

        app_key, app_secret = _credentials()
        token = cached_access_token(app_key, app_secret)

        # 선물은 월물을 계산해 넣고, 지수는 업종코드로 바로 부른다. 엔드포인트가 다르다.
        jobs: list[tuple[str, str | None, object]] = [
            (future.value, front_contract(future, today_kst), future) for future in DomesticFuture
        ]
        jobs += [(index.value, None, index) for index in DomesticIndex]

        responses = []
        failures: list[SymbolOutcome] = []
        for symbol, contract, target in jobs:
            try:
                responses.append(_fetch(token, app_key, app_secret, target, contract, now))
            except KisHTTPError as error:
                if error.status in UNRECOVERABLE_STATUSES:
                    raise AirflowFailException(f"{symbol} ({contract or 'index'}): {error}") from error
                logger.warning("%s failed with HTTP %s", symbol, error.status)
                failures.append(
                    SymbolOutcome(
                        symbol=symbol,
                        contract_code=contract,
                        status=error.status,
                        error=str(error),
                    )
                )
            except ConnectionError as error:
                logger.warning("%s failed to connect: %s", symbol, error)
                failures.append(SymbolOutcome(symbol=symbol, contract_code=contract, error=str(error)))

        if not responses:
            raise ConnectionError("Every KIS request failed")

        connection: Any = PostgresHook(postgres_conn_id=CONNECTION_ID).get_conn()
        try:
            bar_count, outcomes = store_bars(connection, responses, since, failures)
            connection.commit()
        except (KisPayloadError, KisResultError) as error:
            connection.rollback()
            raise AirflowFailException(str(error)) from error
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

        succeeded = [outcome for outcome in outcomes if outcome.error is None]
        if not succeeded:
            raise AirflowFailException("Every contract failed to parse; see source_record metadata")

        # 봉 0건은 정상이다. 개장 전이나 마감 뒤면 모든 계약이 0건이고 그 run 도 성공이다.
        logger.info(
            "Stored %s bars over the last %s minutes across %s symbol(s): %s",
            bar_count,
            lookback_minutes,
            len(succeeded),
            ", ".join(f"{o.symbol}{f'={o.contract_code}' if o.contract_code else ''}" for o in succeeded),
        )
        return bar_count

    @task(task_display_name="상승·보합·하락 분포")
    def collect_movement() -> int:
        """코스피·코스닥의 종목 분포를 한 번 찍는다.

        가격 봉 태스크와 분리한다. 분포 실패가 분봉 저장을 막지 않게 하려는 것이고, 그래서
        휴장일 skip 판정도 여기서 따로 한다.
        """
        now = datetime.now(UTC)
        today_kst = now.astimezone(KST_TIMEZONE).date()
        _skip_when_closed(today_kst)

        app_key, app_secret = _credentials()
        token = cached_access_token(app_key, app_secret)

        responses = []
        failures: list[SymbolOutcome] = []
        for index in MOVEMENT_INDEXES:
            try:
                responses.append(_fetch(token, app_key, app_secret, index, None, now, price=True))
            except KisHTTPError as error:
                if error.status in UNRECOVERABLE_STATUSES:
                    raise AirflowFailException(f"{index.value}: {error}") from error
                logger.warning("%s movement failed with HTTP %s", index.value, error.status)
                failures.append(SymbolOutcome(symbol=index.value, status=error.status, error=str(error)))
            except ConnectionError as error:
                logger.warning("%s movement failed to connect: %s", index.value, error)
                failures.append(SymbolOutcome(symbol=index.value, error=str(error)))

        if not responses:
            raise ConnectionError("Every KIS index price request failed")

        # 이 조회에는 원천 시각이 없다. 응답을 받은 분으로 찍는다.
        observed_at = now.replace(second=0, microsecond=0)

        connection: Any = PostgresHook(postgres_conn_id=CONNECTION_ID).get_conn()
        try:
            stored, outcomes = store_market_movement(connection, responses, observed_at, failures)
            connection.commit()
        except (KisPayloadError, KisResultError) as error:
            connection.rollback()
            raise AirflowFailException(str(error)) from error
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

        if not [outcome for outcome in outcomes if outcome.error is None]:
            raise AirflowFailException("Every index failed to parse; see source_record metadata")

        # 0건은 정상이다. 개장 전과 마감 뒤에는 다섯 값이 모두 0으로 리셋돼 저장하지 않는다.
        logger.info("Stored %s movement snapshot(s) at %s", stored, observed_at.isoformat())
        return stored

    collect()
    collect_movement()


def _fetch(token, app_key, app_secret, target, contract, now, price: bool = False):
    """분봉을 받는다. 401 이면 토큰을 한 번만 재발급하고 다시 시도한다.

    선물이면 월물 코드로 선물 엔드포인트를, 지수면 업종 엔드포인트를 부른다.

    토큰은 24시간짜리라 폴링 중 만료되는 일이 드물지만, 만료됐을 때 run 하나를 통째로
    버리지 않으려면 여기서 한 번 흡수하는 편이 싸다.
    """

    def call(active):
        if price:
            return fetch_index_price(active, app_key, app_secret, target)
        if isinstance(target, DomesticIndex):
            return fetch_index_bars(active, app_key, app_secret, target)
        return fetch_bars(active, app_key, app_secret, target, contract, now)

    try:
        return call(token)
    except KisHTTPError as error:
        if error.status != 401:
            raise
        logger.warning("KIS returned 401; reissuing the token once")
        return call(cached_access_token(app_key, app_secret, force=True))


kis_quote_intraday = kis_quote_intraday()
