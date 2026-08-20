"""Yahoo 지수·선물 1분봉 장중 수집 DAG.

`fred_treasury_daily`·`ecos_market_rate_daily`가 하루 한 번 도는 리포트용 수집이라면
이 DAG는 **실시간 알림**을 위한 수집이다. 미국 반도체 선물이 빠지면 한국 반도체 종목도
곧 빠질 수 있다는 신호를 한국 장중을 포함해 하루 종일 받는 것이 목적이다.

수집 대상은 `modules.collectors.yahoo.QuoteSymbol`이 정한다(현재 S&P500 선물, 나스닥100
선물, VIX, 필라델피아 반도체, 코스피). 심볼을 늘려도 이 파일은 바뀌지 않는다.

## 왜 24시간 도는가

한국 정규장 시간(09:00~15:30 KST)에는 **미국 정규장이 닫혀 있다**(미국 정규장은
KST 22:30~05:00). 그래서 그 시간대에 살아 있는 미국 신호는 선물뿐이고, 미국 장 시간에만
도는 스케줄로는 "한국 시간 14시에 나스닥 선물이 빠졌다"를 놓친다.

| 구간(KST) | ES=F / NQ=F | ^SOX / ^VIX | ^KS11 |
| --- | --- | --- | --- |
| 09:00~15:30 한국 정규장 | 거래 중 | 멈춤 | 거래 중 |
| 15:30~22:30 | 거래 중 | 멈춤 | 멈춤 |
| 22:30~05:00 미국 정규장 | 거래 중 | 거래 중 | 멈춤 |
| 06:00~07:00 | CME 정비 휴장 | 멈춤 | 멈춤 |

주말은 선물도 쉰다(토 06:00 ~ 월 07:00 KST). 서머타임에는 한 시간씩 밀린다.

시간 창으로 스케줄을 좁히지 않는다. 요청 5개는 싸고, 창 조건은 서머타임 전환과 임시
휴장마다 틀리기 시작한다. 조용한 구간은 수집기가 0건으로 흡수한다.

## 5분마다 도는데 왜 1분 데이터가 쌓이는가

Yahoo chart는 요청 한 번에 하루치 1분봉을 통째로 돌려준다. 그래서 폴링 주기와 저장
그레인이 분리된다. 5분마다 호출해도 저장은 1분 단위다. `lookback_minutes`가 그중 최근
몇 분을 쓸지 정하고, 멱등 키 `(provider, symbol, bar_at)`가 겹치는 봉을 흡수한다.

## params

| 이름 | 기본값 | 뜻 |
| --- | --- | --- |
| `lookback_minutes` | `15` | 이 run이 저장할 최근 구간. 폴링 주기보다 넉넉히 잡아 한두 번 걸러도 구멍이 안 나게 한다 |
| `backfill_start` | `null` | 백필 시작일(YYYY-MM-DD). 주면 `lookback_minutes`를 무시하고 과거 구간을 받는다 |
| `backfill_end` | `null` | 백필 종료일(YYYY-MM-DD, 포함). `backfill_start`와 함께 준다 |

재시작하거나 스케줄러가 잠깐 멈춰 구멍이 생겼으면 `lookback_minutes`를 키워 수동 run을
돌린다. `range=1d`가 하루치를 주므로 `1440`까지는 한 번에 메울 수 있다.

    airflow dags trigger yahoo_quote_intraday --conf '{"lookback_minutes": 240}'

## 과거 구간 백필

하루보다 먼 과거는 `lookback_minutes`로 닿지 않는다. `range=1d`가 "지금 기준 하루치"라
과거 날짜를 가리킬 수 없기 때문이다. 그때는 날짜를 직접 준다.

    airflow dags trigger yahoo_quote_intraday \
      --conf '{"backfill_start": "2026-07-10", "backfill_end": "2026-07-31"}'

**Yahoo는 1분봉을 약 30일만 보관한다.** 그보다 과거는 데이터가 존재하지 않아서 요청해도
`1m data not available`이 온다. 실측으로 2026-08-08 기준 2026-07-10까지만 조회됐다.
보관 기간을 넘긴 요청은 태스크가 시작할 때 막는다(`modules.collectors.yahoo.BAR_RETENTION_DAYS`).

요청 한 번은 8일까지만 담을 수 있어서 구간을 그만큼씩 쪼개 여러 번 부른다. 심볼 5개 ×
창 개수만큼 요청이 나가고, 창마다 `source_record`가 1건씩 생긴다. 3주치면 창 3개, 요청
15회다.

## 실패와 재시도

- HTTP 400/401/403/404: 설정 오류라 재시도해도 같으므로 `AirflowFailException`으로 즉시 실패한다.
- HTTP 429: 그대로 올려 재시도한다. Yahoo는 비공식 API라 이건 정상 운영 범위다.
- 그 밖의 HTTP 오류와 네트워크 오류: 그대로 올려 재시도한다.
- 응답이 chart 계약을 어기면 그 심볼만 실패로 기록하고 나머지는 저장한다.
- **최근 구간에 새 봉이 0건인 것은 실패가 아니다.** 위 표의 휴장 구간이다. 주말에는 모든
  심볼이 0건이고 그 run도 성공이다. 이걸 실패로 다루면 매일 1시간, 주말 내내 DAG가
  빨갛게 되고 알림 시스템이 스스로 노이즈가 된다.
- 모든 심볼이 **오류로** 끝났을 때만 태스크를 실패시킨다.

## 필요한 환경

- API 키가 없다. Yahoo v8 chart는 인증을 요구하지 않는다.
- `CONNECTION_ID`가 가리키는 Airflow 연결. 접속 정보는 `AIRFLOW_CONN_FINANCE`가 갖는다.

봉은 `quote_bar`에, 수집 계보는 `source_record`에 저장한다. 폴링 1회가 `source_record`
1건이다. 테이블 정의의 원본은 백엔드의 `apps/models/market.py`이고, 이 DAG가 쓰는 SQL은
`airflow/sql/postgres/` 아래에 있다.
"""

import logging
from contextlib import closing
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pendulum
from airflow.exceptions import AirflowFailException
from airflow.providers.postgres.hooks.postgres import PostgresHook
from airflow.sdk import Param, dag, get_current_context, task

from modules.collectors.yahoo import (
    BACKFILL_END_PARAM,
    BACKFILL_START_PARAM,
    US_EQUITY_SYMBOLS,
    QuoteSymbol,
    SymbolOutcome,
    YahooHTTPError,
    backfill_windows,
    fetch_bars,
    resolve_backfill_period,
    store_bars,
)
from modules.market_session import us_equity_open_day
from modules.utility import CONNECTION_ID, KST_TIMEZONE, UNRECOVERABLE_STATUSES, atomic

logger = logging.getLogger(__name__)

# 미국 현물장 개장 여부를 물을 때 쓰는 날짜의 기준 시간대.
NEW_YORK_TIMEZONE = ZoneInfo("America/New_York")

# 폴링 주기보다 넉넉히 잡는다. 한두 번 걸러도 다음 run이 구멍을 메운다.
LOOKBACK_MINUTES = 15

LOOKBACK_MINUTES_PARAM = "lookback_minutes"


def polling_symbols() -> tuple[QuoteSymbol, ...]:
    """이 폴링이 요청할 심볼.

    **판정 날짜는 지금 시각의 `America/New_York` 날짜다.** 이 DAG는 24시간 돌고 미국 정규장은
    KST로 전날 22:30에 시작해 당일 05:00에 끝나므로, KST 날짜로 물으면 세션의 절반이 엉뚱한
    날을 본다.

    확정 휴장(`False`)일 때만 미국 현물 심볼을 뺀다. 행이 없거나 아직 판정하지 않았으면
    전부 받는다. 백필은 과거 구간 자체가 대상이라 이 필터를 타지 않는다.
    """
    session_date = datetime.now(UTC).astimezone(NEW_YORK_TIMEZONE).date()
    connection: Any = PostgresHook(postgres_conn_id=CONNECTION_ID).get_conn()
    try:
        open_day = us_equity_open_day(connection, session_date)
    finally:
        connection.close()

    if open_day is not False:
        return tuple(QuoteSymbol)

    logger.info("US equities are closed on %s; skipping %s", session_date, sorted(US_EQUITY_SYMBOLS))
    return tuple(symbol for symbol in QuoteSymbol if symbol.value not in US_EQUITY_SYMBOLS)


def backfill_period(params: dict[str, Any]) -> tuple[datetime, datetime] | None:
    """`modules.collectors.yahoo.resolve_backfill_period`의 실패를 재시도 불가로 분류한다.

    잘못된 백필 파라미터는 다시 돌려도 같은 값이라 즉시 실패시킨다.
    """
    try:
        return resolve_backfill_period(params)
    except ValueError as error:
        raise AirflowFailException(str(error)) from error


@dag(
    dag_id="yahoo_quote_intraday",
    dag_display_name="🌐 해외 지수·선물 1분봉 (Yahoo)",
    description="5분마다 Yahoo에서 해외 지수·선물 1분봉을 받아 저장한다. 한국 장중 미국 선물 변동 감시용이다.",
    # 시간 창을 두지 않는다. 한국 장중의 미국 선물 변동이 이 수집의 핵심이라
    # 미국 장 시간에만 도는 스케줄로는 목적을 못 이룬다.
    schedule="*/5 * * * *",
    start_date=pendulum.datetime(2026, 8, 8, tz=KST_TIMEZONE),  # KST 2026-08-08 00:00 = UTC 2026-08-07 15:00
    catchup=False,
    max_active_runs=1,
    # 5분 주기라 실패해도 다음 run이 곧 덮는다. FRED의 1시간 간격 2회 재시도는 여기 안 맞는다.
    default_args={"retries": 1, "retry_delay": timedelta(minutes=2)},
    params={
        LOOKBACK_MINUTES_PARAM: Param(
            LOOKBACK_MINUTES,
            type="integer",
            minimum=1,
            maximum=1440,
            title="저장할 최근 구간(분)",
            description="이 run이 저장할 봉의 범위. 구멍을 메울 때만 키운다. range=1d라 1440이 상한이다.",
        ),
        BACKFILL_START_PARAM: Param(
            None,
            type=["null", "string"],
            format="date",
            title="백필 시작일",
            description="주면 lookback_minutes를 무시하고 과거 구간을 받는다. Yahoo 보관 기간(약 30일) 안이어야 한다.",
        ),
        BACKFILL_END_PARAM: Param(
            None,
            type=["null", "string"],
            format="date",
            title="백필 종료일(포함)",
            description="backfill_start와 함께 준다. 이 날짜의 하루 끝까지 받는다.",
        ),
    },
    doc_md=__doc__,
    tags=["yahoo", "market", "intraday"],
)
def yahoo_quote_intraday():
    @task(task_display_name="1분봉 수집·저장")
    def collect() -> int:
        """심볼 전부를 한 번 훑어 `source_record` 1건과 그에 딸린 봉을 저장한다.

        `fred`·`ecos`처럼 심볼마다 태스크를 매핑하지 않는다. 이 DAG는 5분마다 영원히 돌기
        때문에 5개 매핑이면 하루 1440개, 단일 태스크면 288개의 task instance가 쌓인다.
        Airflow 메타데이터 DB에 그대로 남는 차이다. 대신 심볼마다 예외를 잡아 하나가
        실패해도 나머지를 저장한다.
        """
        context = get_current_context()
        params = dict(context.get("params") or {})

        backfill = backfill_period(params)
        if backfill is None:
            lookback_minutes = int(params.get(LOOKBACK_MINUTES_PARAM) or LOOKBACK_MINUTES)
            if lookback_minutes < 1:
                raise AirflowFailException(f"{LOOKBACK_MINUTES_PARAM} must be at least 1")
            # 폴링은 `range=1d`로 받고(요청 창 없음) "지금 기준 최근 N분"만 저장한다.
            # 상한이 없어 방금 만들어진 봉까지 들어간다.
            spans = ((datetime.now(UTC) - timedelta(minutes=lookback_minutes), None),)
            request_windows: tuple[tuple[datetime, datetime] | None, ...] = (None,)
            symbols = polling_symbols()
        else:
            # 요청 하나가 8일까지만 담으므로 구간을 쪼갠다. 창마다 요청과 저장이 한 번씩이다.
            windows = backfill_windows(*backfill)
            spans = windows
            request_windows = windows
            # 백필은 과거 구간 자체가 대상이라 오늘의 캘린더로 막지 않는다.
            symbols = tuple(QuoteSymbol)
            logger.info("Backfilling %s..%s in %s window(s)", backfill[0].date(), backfill[1].date(), len(windows))

        return sum(
            collect_window(since, until, request_window, symbols)
            for (since, until), request_window in zip(spans, request_windows, strict=True)
        )

    collect()


def collect_window(
    since: datetime,
    until: datetime | None,
    request_window: tuple[datetime, datetime] | None,
    symbols: tuple[QuoteSymbol, ...],
) -> int:
    """창 하나를 받아 저장한다. `source_record` 1건이 여기서 생긴다.

    `request_window`는 Yahoo에 넘길 구간이고 `since`/`until`은 그중 저장할 범위다. 폴링은
    앞이 `None`(=`range=1d`)이고 뒤만 쓰며, 백필은 둘이 같다.
    """
    responses = []
    failures: list[SymbolOutcome] = []
    for symbol in symbols:
        try:
            responses.append(fetch_bars(symbol, request_window))
        except YahooHTTPError as error:
            if error.status in UNRECOVERABLE_STATUSES:
                # 심볼이 폐지됐거나 URL 규칙이 바뀐 것이다. 재시도해도 같다.
                raise AirflowFailException(f"{symbol.value}: {error}") from error
            # 429를 포함한 나머지는 이 심볼만 실패로 기록하고 계속한다. Yahoo는 비공식
            # API라 간헐적인 차단이 정상 운영 범위다. 다음 run이 같은 구간을 다시 받는다.
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

    with closing(PostgresHook(postgres_conn_id=CONNECTION_ID).get_conn()) as connection, atomic(connection):
        bar_count, outcomes = store_bars(connection, responses, since, until, failures)

    succeeded = [outcome for outcome in outcomes if outcome.error is None]
    if not succeeded:
        # 저장할 응답이 하나도 파싱되지 않았다. 계보 레코드는 남았으므로 실패로 끝낸다.
        raise AirflowFailException("Every symbol failed to parse; see source_record metadata")

    # 봉 0건은 정상이다. 휴장 구간이면 모든 심볼이 0건이고 그 창도 성공이다.
    logger.info(
        "Stored %s bars for %s..%s across %s symbols (%s failed)",
        bar_count,
        since.isoformat(),
        until.isoformat() if until else "now",
        len(succeeded),
        len(outcomes) - len(succeeded),
    )
    return bar_count


yahoo_quote_intraday = yahoo_quote_intraday()
