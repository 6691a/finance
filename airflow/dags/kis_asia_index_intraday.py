"""KIS 아시아 지수 1분봉 장중 수집 DAG.

니케이225·상해종합·항셍·대만가권의 1분봉을 한국 장중에 5분마다 받아 `index_bar`에
`provider = 'kis'`로 쌓는다. `kis_quote_intraday`(국내 지수)와 짝이고 저장 테이블도 같다.

## 왜

2026-09-03 14:00~14:27 KST에 코스피가 3.2% 빠졌다 되돌렸다. 일본·중국도 같이 빠졌다는데 우리
데이터로는 가릴 수 없었다 — `index_bar`의 닛케이가 **하루 7봉**이었다. Yahoo `^N225`는 1분봉을
하루 390개 주지만 **15분 지연**이고 `yahoo_quote_intraday`의 `lookback_minutes`가 15라 정렬된
봉이 전부 `since` 앞으로 떨어져 버려졌다. 국내에서 받을 수 있으면 국내를 우선한다 — KIS
해외지수 분봉 API가 아시아 지수를 준다. 설계는 `docs/collection/kis-overseas-index-close.md` §13이다.

## 15분 지연

**KIS도 니케이는 15~16분 지연이다**(2026-09-04 실측: 10:03:54 KST 조회에 최신 봉 09:48). Yahoo와
같다. 그래서 `lookback_minutes` 기본값이 30이다 — 지연 15분 + 폴링 5분 + 여유. 상한은 102
(한 번에 오는 봉 수)다. 멱등 키 `(provider, symbol, bar_at)`가 겹치는 봉을 흡수한다.

## 시간 창

| 시장 | 현지 정규장 | KST | 마지막 봉이 보이는 KST |
| --- | --- | --- | --- |
| 도쿄 | 09:00~15:30 JST | 09:00~15:30 | 15:45 |
| 대만 | 09:00~13:30 | 10:00~14:30 | 14:45 |
| 상해 | 09:30~15:00 CST | 10:30~16:00 | 16:15 |
| 홍콩 | 09:30~16:00 HKT(정산 16:08) | 10:30~17:08 | 17:25 |

그래서 **평일 09:00~17:55 KST**에 돈다. 네 시장 모두 서머타임이 없다.

## 휴장

달력이 없다. 한국 휴일에 도쿄가 열고 그 반대도 있어 KRX 달력을 걸면 틀린다. **최근 구간에 새 봉이
0건인 것은 실패가 아니다** — 휴장·개장 전·마감 뒤가 그렇다. 응답 자체가 비면(`output2` 0건)
그 지수는 실패다. 모르는 코드에도 `rt_cd=0`에 0건으로 답하기 때문이다.

## 실패와 재시도

- **다음 run이 5분 뒤 같은 창을 다시 본다.** 그래서 지수 하나가 실패해도 나머지는 저장하고,
  **전부 실패했을 때만 죽인다.** 하나로 죽이면 경보만 늘고 고쳐지는 것은 없다.
- HTTP 400/403/404: 설정 오류라 `AirflowFailException`으로 즉시 실패한다.
- HTTP 401: 공유 토큰을 한 번 재발급하고 그 요청만 다시 시도한다.
- 그 밖의 HTTP 오류와 네트워크 오류: 그 지수만 실패로 모은다.
- 응답 계약 위반(`KisPayloadError`)·본문 오류(`KisResultError`): 그 지수만 실패로 모은다.

## params

| 이름 | 기본값 | 뜻 |
| --- | --- | --- |
| `lookback_minutes` | `30` | 이 run이 저장할 최근 구간. 상한은 102 |

## 필요한 환경

- `KIS_APP_KEY`, `KIS_APP_SECRET`. 토큰은 다른 KIS DAG와 같은 Airflow Variable 캐시를 공유한다.
- `CONNECTION_ID`가 가리키는 Airflow 연결.
"""

import logging
import os
from contextlib import closing
from datetime import UTC, datetime, timedelta
from typing import Any

import pendulum
from airflow.providers.postgres.hooks.postgres import PostgresHook
from airflow.sdk import Param, Variable, dag, get_current_context, task
from airflow.sdk.exceptions import AirflowFailException
from pydantic import SecretStr

from modules.collectors.kis import KisHTTPError, KisPayloadError, KisResultError, access_token
from modules.collectors.market.kis_overseas_index import (
    MAX_BARS_PER_REQUEST,
    AsiaIndex,
    AsiaIndexFetch,
    KisOverseasIndexCollector,
)
from modules.utility import CONNECTION_ID, KIS_UNRECOVERABLE_STATUSES, KST_TIMEZONE, atomic

logger = logging.getLogger(__name__)

# KST 평일 09:00~17:55 = UTC 평일 00:00~08:55. 도쿄 개장부터 항셍 정산 봉 + 15분 지연까지.
SCHEDULE = "*/5 9-17 * * 1-5"

# 지연 15분 + 폴링 5분 + 여유. 15로 두면 정렬된 봉이 전부 잘린다(Yahoo 수집이 그랬다).
LOOKBACK_MINUTES = 30
LOOKBACK_MINUTES_PARAM = "lookback_minutes"


def _credentials() -> tuple[SecretStr, SecretStr]:
    app_key = os.environ.get("KIS_APP_KEY")
    app_secret = os.environ.get("KIS_APP_SECRET")
    if not app_key or not app_secret:
        raise AirflowFailException("KIS_APP_KEY and KIS_APP_SECRET are required")
    return SecretStr(app_key), SecretStr(app_secret)


def _cached_token(app_key: SecretStr, app_secret: SecretStr, force: bool = False) -> SecretStr:
    """`kis_quote_intraday`와 같은 캐시를 쓴다. 저장소를 고르는 일만 여기 있다."""
    return access_token(Variable, app_key, app_secret, force=force)


def _connection() -> Any:
    return PostgresHook(postgres_conn_id=CONNECTION_ID).get_conn()


def _fetch_with_retry(
    collector: KisOverseasIndexCollector,
    index: AsiaIndex,
    since: datetime,
    app_key: SecretStr,
    app_secret: SecretStr,
) -> AsiaIndexFetch:
    """401이면 토큰을 한 번만 재발급하고 다시 시도한다. 되돌릴 수 없는 HTTP 오류는 즉시 실패다."""
    try:
        return collector.fetch_since(index, since)
    except KisHTTPError as error:
        if error.status in KIS_UNRECOVERABLE_STATUSES:
            raise AirflowFailException(f"{index.value}: {error}") from error
        if error.status != 401:
            raise
        logger.warning("KIS returned 401; reissuing the token once")
        reissued = KisOverseasIndexCollector(_cached_token(app_key, app_secret, force=True), app_key, app_secret)
        return reissued.fetch_since(index, since)


@dag(
    dag_id="kis_asia_index_intraday",
    dag_display_name="🌏 아시아 지수 1분봉 (KIS)",
    description="한국 장중 5분마다 KIS 해외지수 분봉으로 니케이·상해·항셍·대만 1분봉을 index_bar에 저장한다.",
    schedule=SCHEDULE,
    start_date=pendulum.datetime(2026, 9, 5, tz=KST_TIMEZONE),  # KST 2026-09-05 00:00 = UTC 2026-09-04 15:00
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
            description=(
                f"이 run이 저장할 봉의 범위. 제공처가 15분 지연이라 기본 {LOOKBACK_MINUTES}분이고 "
                f"한 번에 {MAX_BARS_PER_REQUEST}봉만 오므로 그게 상한이다."
            ),
        ),
    },
    doc_md=__doc__,
    tags=["kis", "market", "intraday", "asia", "index"],
)
def kis_asia_index_intraday():
    @task(task_display_name="아시아 지수 1분봉 수집·저장")
    def collect() -> int:
        context = get_current_context()
        params = dict(context.get("params") or {})
        lookback_minutes = int(params.get(LOOKBACK_MINUTES_PARAM) or LOOKBACK_MINUTES)
        if not 1 <= lookback_minutes <= MAX_BARS_PER_REQUEST:
            raise AirflowFailException(f"{LOOKBACK_MINUTES_PARAM} must be between 1 and {MAX_BARS_PER_REQUEST}")
        since = datetime.now(UTC) - timedelta(minutes=lookback_minutes)

        app_key, app_secret = _credentials()
        collector = KisOverseasIndexCollector(_cached_token(app_key, app_secret), app_key, app_secret)

        fetches: list[AsiaIndexFetch] = []
        failures: list[str] = []
        for index in AsiaIndex:
            try:
                fetches.append(_fetch_with_retry(collector, index, since, app_key, app_secret))
            except KisHTTPError as error:
                logger.warning("%s failed with HTTP %s", index.value, error.status)
                failures.append(f"{index.value}({error})")
            except (KisPayloadError, KisResultError) as error:
                logger.warning("%s failed: %s", index.value, error)
                failures.append(f"{index.value}({error})")
            except ConnectionError as error:
                logger.warning("%s failed to connect: %s", index.value, error)
                failures.append(f"{index.value}({error})")

        # 5분 뒤 같은 창을 다시 본다. 전부 실패했을 때만 죽인다.
        if not fetches:
            raise AirflowFailException(f"Every Asian index failed: {'; '.join(failures)}")

        with closing(_connection()) as connection, atomic(connection):
            stored = [collector.store(connection, fetch) for fetch in fetches]

        # 봉 0건은 정상이다. 휴장·개장 전·마감 뒤면 모든 지수가 0건이고 그 run도 성공이다.
        for fetch, count in zip(fetches, stored, strict=True):
            logger.info(
                "Stored %s bars for %s (%s) since %s; latest bar %s",
                count,
                fetch.index.value,
                fetch.name,
                since.isoformat(),
                fetch.latest_bar_at.isoformat() if fetch.latest_bar_at else None,
            )
        if failures:
            logger.warning("Some Asian indexes failed this poll: %s", "; ".join(failures))
        return sum(stored)

    collect()


kis_asia_index_intraday = kis_asia_index_intraday()
