"""종목별 투자자 매매동향 확정 일별값 수집 DAG.

`kis_investor_estimate_intraday`가 장중 **추정치**를 받는다면 이 DAG는 장 마감 뒤의 **확정값**을
받는다. 추정은 하루 다섯 회차뿐이고 개인이 없지만, 확정값은 12개 분류가 전부 있고 외국인이
등록·미등록으로 갈리며 대금 단위까지 확정돼 있다(백만원).

수집 규칙은 `modules/collectors/market/kis_investor_flow.py`에 있다.

## 한 번 부르면 30 거래일이 온다

`FID_INPUT_DATE_1`은 구간의 **끝**이고 응답은 그날부터 과거로 30 거래일을 담는다(실측).
그래서 하루치를 위해 부르는 호출이 이미 지난 달까지 채운다. 매일 도는 것만으로 30 거래일이
겹쳐 들어와 실패한 날이 저절로 메워진다.

## 백필은 날짜를 뒤로 건다

`end_date`를 주면 그날을 끝으로 하는 구간을 받는다. `pages`를 함께 주면 30 거래일씩 더 과거로
걸으며 그만큼 더 받는다. 달력이 아니라 응답이 준 가장 이른 거래일의 하루 전을 다음 끝 날짜로
쓴다. 우리가 거래일을 세면 휴장일에서 어긋난다.

    airflow dags trigger kis_investor_trade_daily \\
      --conf '{"end_date": "2026-07-01", "pages": 6}'

**`pages`를 크게 줘도 `BACKFILL_START_DATE`(2018-12-10) 앞으로는 가지 않는다.** 그 앞의
응답은 투자자 항등식이 깨져 있다(그 상수의 주석에 실측이 있다). 전 구간을 채울 때는 장 수를
계산하지 말고 넉넉히 준다.

    airflow dags trigger kis_investor_trade_daily \\
      --conf '{"end_date": "2026-08-25", "pages": 70}'

장 사이에 `PAGE_DELAY_SECONDS`만큼 쉰다. 없으면 백필이 초당 거래건수 제한에 걸린다.

## params

| 이름 | 기본값 | 뜻 |
| --- | --- | --- |
| `end_date` | `null` | 구간의 끝(YYYY-MM-DD). 비우면 실행일(KST) |
| `pages` | `1` | 30 거래일씩 몇 구간을 뒤로 걸을지 |

## 조회 가능 시각

**당일을 구간 끝으로 주면 KST 15:40 전에는 거절당한다.** `rt_cd=2`에
`TIME LIMIT 00:00 ~ 15:40`이 온다. 마감 확정이 그 시각에 나오기 때문이고, 스케줄이 18:10인
이유다. 이 응답은 `KisTimeWindowError`로 갈라져 재시도 없이 즉시 실패한다 — 그 시각이 되기
전에는 재시도가 같은 답을 받는다.

**과거 구간은 시각 제한이 없다.** 2026-08-25 실측: 13:36에 `end_date=2025-01-14` 조회가
성공했고, 같은 날 14:11의 `end_date=2026-08-25` 조회는 거절당했으며, 15:51에 다시 부른
당일 조회는 성공했다. 그래서 이력 백필은 아무 때나 돌려도 되고, `end_date`를 지난 거래일로
주면 장중에도 채울 수 있다.

## 수정주가 소급 조정

KIS는 수정주가를 준다. 액면분할·병합·증자가 있으면 **과거 전체가 새 기준으로 다시 쓰인다**
(2026-08-26 실측: 2018-05-04 분할 뒤 2010-01-04 종가가 809,000이 아니라 16,180으로 온다).
`FID_ORG_ADJ_PRC`로 원주가를 고를 수 없다 — 빈 값·0·1이 전부 같은 답이다.

그대로 두면 겹치는 30 거래일만 새 기준이 되고 그 앞은 옛 기준으로 남아, 한 종목 안에 두
기준이 섞인 채로 SMA60이 계산된다. 그래서 **저장 전에** 같은 거래일의 기존 종가와 대조한다
(`close_conflicts`). 급락을 보는 것이 아니다 — 과거는 변하지 않으므로 **한 날짜의 종가가
둘일 수 없고**, 어긋났다면 소급 조정 말고는 설명이 없다.

어긋나면 그 종목의 `BACKFILL_START_DATE`부터 전 구간을 다시 받아 덮는다. 어긋난 날짜까지만
받으면 경계가 뒤로 밀릴 뿐이다. **실행당 한 번뿐이고**, 다 덮은 뒤에도 어긋나면 저장이
우리가 믿는 대로 굴러가지 않은 것이라 태스크를 죽인다.

봉이 바뀌었으므로 `technical_signal_daily`를 `scan_bars`를 넓혀 다시 돌려야 한다.
설계는 docs/analysis/market-thesis/10-base-rate.md 3절이다.

## 실패와 재시도

- **한 종목이 실패해도 다른 종목은 저장한다.** 호출 하나가 트랜잭션 하나다.
- HTTP 400/403/404: 설정 오류라 즉시 실패한다.
- 응답의 네 항등식이 깨지면 저장하지 않는다. 필드 뜻이 바뀐 것이다.
- 0행은 정상이다. 상장 전 구간을 요청하면 비어 있다.

## 필요한 환경

- `KIS_APP_KEY`, `KIS_APP_SECRET`. Airflow가 읽는 건 `compose/local/airflow/.env`다.
- `CONNECTION_ID`가 가리키는 Airflow 연결. 접속 정보는 `AIRFLOW_CONN_FINANCE`가 갖는다.

토큰은 다른 KIS DAG와 같은 Airflow Variable 캐시를 공유한다.
"""

import logging
import os
import re
from contextlib import closing
from datetime import UTC, date, datetime, timedelta
from time import sleep as wait_seconds
from typing import Any

import pendulum
from airflow.exceptions import AirflowFailException, AirflowSkipException
from airflow.providers.postgres.hooks.postgres import PostgresHook
from airflow.sdk import Param, Variable, dag, get_current_context, task
from pydantic import BaseModel, ConfigDict, SecretStr

from modules.collectors.kis import (
    KisHTTPError,
    KisPayloadError,
    KisResultError,
    KisTimeWindowError,
    access_token,
)
from modules.collectors.market.kis_investor_flow import (
    InvestorFlowStock,
    KisInvestorFlowCollector,
    close_conflicts,
    missing_open_days,
)
from modules.db import Connection
from modules.market_session import krx_open_day
from modules.utility import CONNECTION_ID, KIS_UNRECOVERABLE_STATUSES, KST_TIMEZONE, atomic

logger = logging.getLogger(__name__)

# 달력 하루만 받는다. ISO 주 표기(2026-W32)와 기본형(20260701)을 걸러 내는 그물이다.
CALENDAR_DAY_PATTERN = re.compile(r"\d{4}-\d{2}-\d{2}")

END_DATE_PARAM = "end_date"
PAGES_PARAM = "pages"

# 걷기가 되돌아갈 수 있는 가장 이른 날. **제공처가 정한 값이지 우리 취향이 아니다.**
#
# 2026-08-26 실측: 2018-12-07까지의 응답은 투자자 항등식 셋이 전부 깨진다 — 기관 세부 합이
# 기관계와 다르고, 기타 세부 합이 `etc_ntby_qty`와 다르며, 시장 합계가 0으로 닫히지 않는다
# (005930·000660 둘 다 2018-08-28~12-07 전 거래일에서 깨졌고 2018-12-10부터 전부 성립한다).
# 종목이 달라도 경계가 같아 종목 특성이 아니라 제공처 쪽 집계 체제가 바뀐 날이다.
#
# 그래서 그 앞은 받지 않는다. 항등식을 완화해 받으면 못 믿는 세부 수급이 DB에 들어가고
# `stock_investor_flows` 툴과 브리핑이 그것을 읽는다. 지수(`index_daily`)는 이 응답과
# 무관해 2016-08-15 그대로다(docs/analysis/market-thesis/10-base-rate.md 2.5절).
BACKFILL_START_DATE = date(2018, 12, 10)

# 걷기의 backstop. 2018-12-10까지 약 1,900 거래일이고 한 응답이 30 거래일이라 64장이면
# 닿는다. 실제로 멈추는 것은 `BACKFILL_START_DATE`이고 이 값은 그물이다.
RECOVERY_MAX_PAGES = 200

# 장 사이 대기. 없으면 백필이 초당 거래건수 제한에 걸린다(2026-08-26 실측: 무대기 백필이
# `EGW00201 초당 거래건수를 초과하였습니다`로 HTTP 500). 일상 실행은 종목당 한 장이라
# 이 대기를 타지 않는다. 값은 다른 KIS 수집기의 페이지 대기와 같다.
PAGE_DELAY_SECONDS = 0.5


def _credentials() -> tuple[SecretStr, SecretStr]:
    app_key = os.environ.get("KIS_APP_KEY")
    app_secret = os.environ.get("KIS_APP_SECRET")
    if not app_key or not app_secret:
        raise AirflowFailException("KIS_APP_KEY and KIS_APP_SECRET are required")
    return SecretStr(app_key), SecretStr(app_secret)


def _connection() -> Any:
    return PostgresHook(postgres_conn_id=CONNECTION_ID).get_conn()


def requested_end_date(now_kst: datetime, params: dict[str, Any]) -> date:
    """이 run 이 구간의 끝으로 쓸 날짜.

    **모양을 먼저 본다.** `date.fromisoformat`은 `20260701`과 `2026-W32`도 받는다. 주 표기는
    그 주의 월요일이 되어, 운영자가 넣은 값과 다른 구간을 조용히 받아 온다.
    """
    given = params.get(END_DATE_PARAM)
    if not given:
        return now_kst.date()
    text = str(given).strip()
    if not CALENDAR_DAY_PATTERN.fullmatch(text):
        raise AirflowFailException(f"{END_DATE_PARAM} must be YYYY-MM-DD, got {given!r}")
    try:
        return date.fromisoformat(text)
    except ValueError:
        raise AirflowFailException(f"{END_DATE_PARAM} must be YYYY-MM-DD, got {given!r}") from None


def requested_pages(params: dict[str, Any]) -> int:
    """몇 구간을 뒤로 걸을지.

    `or 1`로 기본값을 주지 않는다. 0이 falsy라 조용히 1이 되고, 운영자는 아무것도 받지 않기를
    바랐는데 하루치를 받게 된다.
    """
    given = params.get(PAGES_PARAM)
    pages = 1 if given is None else int(given)
    if pages < 1:
        raise AirflowFailException(f"{PAGES_PARAM} must be at least 1, got {pages}")
    return pages


class StockWalk(BaseModel):
    """한 종목을 뒤로 걸은 결과. 판정 재료라 dict로 두지 않는다."""

    model_config = ConfigDict(frozen=True)

    stored: int
    # 이번 걷기가 닿은 가장 이른 거래일. 구멍 검사의 시작이다.
    earliest: date
    # 저장된 값과 어긋난 거래일. 비어 있지 않으면 소급 조정이 일어난 것이다.
    conflicts: tuple[date, ...] = ()
    # 호출 실패 사유. 있으면 그 종목의 걷기는 거기서 멈췄다.
    failure: str | None = None


def walk_back(
    collector: KisInvestorFlowCollector,
    connection: Connection,
    stock: InvestorFlowStock,
    end_date: date,
    *,
    pages: int,
    until: date | None = BACKFILL_START_DATE,
    detect_conflicts: bool = True,
    sleep: float = PAGE_DELAY_SECONDS,
) -> StockWalk:
    """한 종목을 `end_date`부터 30 거래일씩 뒤로 걸으며 받아 저장한다.

    `until`보다 앞으로는 가지 않는다. 기본값이 `BACKFILL_START_DATE`라 `pages`를 크게 줘도
    항등식이 깨지는 구간까지 내려가지 않는다 — 운영자가 장 수를 계산할 일이 없다.

    장 사이에 `sleep`만큼 쉰다. 일상 실행은 한 장이라 이 대기를 타지 않는다.

    `detect_conflicts`가 참이면 **저장 전에** 기존 종가와 대조하고, 어긋나면 그 페이지를
    저장하지 않고 즉시 멈춘다. 어긋난 채로 얹으면 한 종목 안에 두 기준이 섞이기 때문이다.
    복구 걷기는 DB 전체가 옛 기준이라 매 페이지가 어긋나므로 이 검사를 끈다.
    """
    stored = 0
    earliest = end_date
    cursor_date = end_date

    for page in range(pages):
        if until is not None and cursor_date < until:
            break
        name = f"{stock.value}:{cursor_date.isoformat()}"
        try:
            fetch = collector.fetch_stock_trade_daily(stock, cursor_date)
        except KisHTTPError as error:
            if error.status in KIS_UNRECOVERABLE_STATUSES:
                raise AirflowFailException(f"{name}: {error}") from error
            logger.warning("%s failed with HTTP %s", name, error.status)
            return StockWalk(stored=stored, earliest=earliest, failure=f"{name}({error})")
        except KisTimeWindowError as error:
            # 조회를 받아 주지 않는 시각이다. 재시도는 같은 답을 받으며 예산만 태운다.
            # 사람이 시각을 맞춰 다시 트리거해야 하므로 즉시 죽인다.
            raise AirflowFailException(
                f"{name}: {error}. 당일치 확정은 KST 15:40 이후에만 나온다 — "
                "그 뒤에 다시 트리거하거나, 과거 구간만 채울 것이면 end_date를 "
                "지난 거래일로 준다(과거 구간은 시각 제한이 없다)."
            ) from error
        except (KisResultError, KisPayloadError) as error:
            logger.warning("%s failed: %s", name, error)
            return StockWalk(stored=stored, earliest=earliest, failure=f"{name}({error})")
        except ConnectionError as error:
            logger.warning("%s failed to connect: %s", name, error)
            return StockWalk(stored=stored, earliest=earliest, failure=f"{name}({error})")

        if not fetch.rows:
            logger.info("%s returned no rows; stopping this stock", name)
            break

        if detect_conflicts:
            conflicts = close_conflicts(connection, fetch)
            if conflicts:
                # 저장하지 않고 멈춘다. 판단은 부르는 쪽이 한다.
                return StockWalk(stored=stored, earliest=earliest, conflicts=conflicts)

        with atomic(connection):
            rows = collector.store_stock_trade_daily(connection, fetch)

        stored += rows
        logger.info("Stored %s rows for %s", rows, name)

        # 다음 구간의 끝은 이번 응답의 가장 이른 거래일 하루 전이다. 우리가 거래일을
        # 세면 휴장일에서 어긋난다.
        earliest = min(row.business_date for row in fetch.rows)
        cursor_date = earliest - timedelta(days=1)
        if page + 1 < pages and (until is None or cursor_date >= until):
            logger.info("Walking back to %s for %s", cursor_date, stock.value)
            wait_seconds(sleep)

    return StockWalk(stored=stored, earliest=earliest)


@dag(
    dag_id="kis_investor_trade_daily",
    dag_display_name="🧾 종목 투자자 매매동향 확정 (KIS)",
    description="장 마감 뒤 종목별 투자자 매매동향 확정값을 30 거래일씩 받아 저장한다.",
    # KST 평일 18:10 = UTC 평일 09:10. 정규장과 시간외를 모두 지난 뒤다.
    schedule="10 18 * * 1-5",
    start_date=pendulum.datetime(2026, 8, 15, tz=KST_TIMEZONE),  # KST 2026-08-15 00:00 = UTC 2026-08-14 15:00
    catchup=False,
    max_active_runs=1,
    default_args={"retries": 2, "retry_delay": timedelta(minutes=10)},
    params={
        END_DATE_PARAM: Param(
            None,
            type=["null", "string"],
            title="구간의 끝",
            description="YYYY-MM-DD. 비우면 실행일(KST). 이 날짜부터 과거로 30 거래일이 온다.",
        ),
        PAGES_PARAM: Param(
            1,
            type="integer",
            minimum=1,
            title="구간 수",
            description="30 거래일씩 몇 구간을 뒤로 걸을지. 백필에만 쓴다.",
        ),
    },
    doc_md=__doc__,
    tags=["kis", "market", "daily", "korea", "investor"],
)
def kis_investor_trade_daily():
    @task(task_display_name="확정 수급 수집·저장")
    def collect() -> int:
        context = get_current_context()
        params = dict(context.get("params") or {})

        now_kst = datetime.now(UTC).astimezone(KST_TIMEZONE)
        end_date = requested_end_date(now_kst, params)
        pages = requested_pages(params)

        # 자동 실행만 휴장일을 건너뛴다. 백필은 끝 날짜가 휴장일이어도 그 앞 거래일들이
        # 응답에 담겨 오므로 막을 이유가 없다.
        if not params.get(END_DATE_PARAM):
            connection = _connection()
            try:
                closed = krx_open_day(connection, end_date) is False
            finally:
                connection.close()
            if closed:
                raise AirflowSkipException(f"KRX is closed on {end_date}")

        app_key, app_secret = _credentials()
        collector = KisInvestorFlowCollector(access_token(Variable, app_key, app_secret), app_key, app_secret)

        stored = 0
        failures: list[str] = []
        gaps: list[str] = []
        with closing(_connection()) as connection:
            for stock in InvestorFlowStock:
                walk = walk_back(collector, connection, stock, end_date, pages=pages)

                if walk.conflicts:
                    # 수정주가 소급 조정이다. 조정은 분할일 이전 **전 기간**에 걸리므로
                    # 어긋난 날짜까지만 다시 받으면 경계가 뒤로 밀릴 뿐 두 기준이 섞이는
                    # 것은 그대로다. 그 종목 전체를 다시 받아 덮는다.
                    logger.warning(
                        "%s: stored closes disagree on %s — refetching from %s (adjusted prices were rewritten)",
                        stock.value,
                        ", ".join(day.isoformat() for day in walk.conflicts),
                        BACKFILL_START_DATE,
                    )
                    # **실행당 한 번뿐이다.** 검사를 끄고 걷는다 — DB 전체가 옛 기준이라
                    # 매 페이지가 어긋난다. 다 덮은 뒤 한 번만 다시 확인한다.
                    walk = walk_back(
                        collector,
                        connection,
                        stock,
                        end_date,
                        pages=RECOVERY_MAX_PAGES,
                        detect_conflicts=False,
                    )
                    if walk.failure is None:
                        settled = walk_back(collector, connection, stock, end_date, pages=1)
                        if settled.conflicts:
                            # 다 덮었는데도 어긋난다. 저장이 우리가 믿는 대로 굴러가지
                            # 않은 것이고, 그대로 두면 매일 같은 재수집을 돈다.
                            raise AirflowFailException(
                                f"{stock.value}: closes still disagree after refetching from "
                                f"{BACKFILL_START_DATE} on "
                                f"{', '.join(day.isoformat() for day in settled.conflicts)}"
                            )

                stored += walk.stored
                if walk.failure is not None:
                    failures.append(walk.failure)

                # 받은 구간에 KRX 개장일이 빠져 있으면 그 구멍은 아무도 모르게 남는다.
                # 한 응답이 30 거래일을 담으므로 매일 도는 것만으로 메워져야 하고, 메워지지
                # 않았다면 응답이나 저장 어느 한쪽이 우리가 믿는 대로 굴러가지 않은 것이다.
                missing = missing_open_days(connection, stock.value, walk.earliest, end_date)
                if missing:
                    gaps.append(f"{stock.value}: {', '.join(day.isoformat() for day in missing)}")

        # 호출 실패가 먼저다. 실패하면 구멍은 그 결과라 원인을 두 번 말할 것이 없다.
        if failures:
            raise AirflowFailException(f"{len(failures)} KIS calls failed: {'; '.join(failures)}")

        if gaps:
            raise AirflowFailException(f"daily bars are missing on KRX open days — {'; '.join(gaps)}")

        logger.info("Stored %s daily investor trade rows ending %s", stored, end_date)
        return stored

    collect()


kis_investor_trade_daily = kis_investor_trade_daily()
