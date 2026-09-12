"""미국 현물지수 확정 일봉 수집 DAG.

마감 분봉(`kis_overseas_index_close`)은 브리핑용으로 마감 부근 102봉을 `index_bar`에 넣는다.
이 DAG는 상관 분석의 원천이 되는 **확정 일봉**을 `index_daily`에 넣는다. 설계는
docs/collection/kis-index-daily-collection.md 6절이다.

한 DAG으로 합치지 않는다. 마감 분봉은 그날 세션 하나가 결과 전부라 실패가 곧 태스크 실패지만,
일봉은 200달력일 창을 다시 받는 수집이라 백필 파라미터와 창 걷기가 붙는다. 앞단 데이터가
같아도 기다리는 성격과 실패 판정이 다르다.

## 왜 200달력일인가

국내 일봉과 같은 창이다. 매일 같은 창을 다시 받으므로 실패한 날이 저절로 메워지고,
`index_daily/upsert.sql`이 (provider, symbol, business_date)로 멱등 갱신한다. 계산은
`modules/period.py`에 한 벌 있다.

## 기준 날짜

**기대 세션은 벽시계가 아니라 이 run의 `data_interval_end`에서 나온다.** 마감 분봉 DAG과 같은
규칙이고 이유도 같다 — `datetime.now`를 쓰면 DAG을 켤 때 Airflow가 만드는 직전 인터벌 run이
그 인터벌 대신 지금 시각으로 세션을 잡는다.

거래일은 KIS가 준 `stck_bsop_date`를 그대로 쓴다. 뉴욕 거래일이라 KST 날짜와 하루 어긋난다.

## 페이지 이어받기

응답 헤더 `tr_cont`는 오지 않는다(2026-08-27 실측). 공식 예제가 연속조회를 구현해 두었는데도
빈 문자열이었다. 한 응답이 100행이라 200달력일 창은 두 장이고, 가장 오래된 날짜의 전날로
종료일을 옮겨 걷는다. 판단은 `KisOverseasIndexDailyCollector.fetch`가 한다.

## params

| 이름 | 기본값 | 뜻 |
| --- | --- | --- |
| `end_date` | `null` | 구간의 끝(YYYY-MM-DD). 비우면 이 run이 기대하는 뉴욕 세션 날짜 |
| `start_date` | `null` | 구간의 시작(YYYY-MM-DD). 비우면 `end_date`에서 200달력일 앞 |

## 이력 백필

```bash
airflow dags trigger kis_overseas_index_daily \
  --conf '{"start_date": "2016-08-15", "end_date": "2026-08-27"}'
```

## 실패와 재시도

- **한 지수가 실패해도 다른 지수는 저장한다.** 심볼 하나가 트랜잭션 하나다. 마지막에
  실패 목록이 있으면 태스크를 죽인다.
- **하루 한 번 도는 확정 수집이라 하나만 실패해도 죽인다.** 그날 값을 다시 집는 실행이
  없다. `kis_index_daily`와 같은 판단이다.
- HTTP 400/403/404: 설정 오류라 즉시 실패한다.
- HTTP 401: 공유 토큰을 한 번 재발급하고 그 요청만 다시 시도한다.
- 응답 계약 위반(`KisPayloadError`)·본문 오류(`KisResultError`): 재시도해도 같아 그 심볼만
  실패로 모은다.
- 자동 실행은 미국 확정 휴장일이면 skip한다. 캘린더가 없으면(`None`) 진행한다 — 빈 응답을
  실패로 만드는 수집기 검사가 잡는다.

## 필요한 환경

- `KIS_APP_KEY`, `KIS_APP_SECRET`. 토큰은 다른 KIS DAG와 같은 Airflow Variable 캐시를 공유한다.
- `CONNECTION_ID`가 가리키는 Airflow 연결.
"""

import logging
from contextlib import closing
from datetime import date, timedelta
from typing import Any

import pendulum
from airflow.sdk import Param, Variable, dag, get_current_context, task
from airflow.sdk.exceptions import AirflowFailException

from modules import dag_common
from modules.collectors.kis import (
    KisHTTPError,
    KisPayloadError,
    KisResultError,
    KisTimeWindowError,
    access_token,
)
from modules.collectors.market.kis_overseas_index import OverseasIndex, us_session_date
from modules.collectors.market.kis_overseas_index_daily import (
    KisOverseasIndexDailyCollector,
)
from modules.period import (
    END_DATE_PARAM,
    SPAN_CALENDAR_DAYS,
    START_DATE_PARAM,
    fetch_windows,
)
from modules.utility import KST_TIMEZONE, atomic

logger = logging.getLogger(__name__)

SCHEDULE = "35 7 * * 2-6"  # KST 화~토 07:35 = UTC 월~금 22:35


def _session_date() -> date:
    """이 run이 기대하는 미국 세션 날짜. 기준 시각은 벽시계가 아니라 이 run의 시각이다.

    `kis_overseas_index_close._session_date`와 같은 규칙이다. `datetime.now`를 쓰면 DAG을
    켤 때 Airflow가 만드는 직전 인터벌 run이 그 인터벌 대신 지금 시각으로 세션을 잡는다.
    """
    context = get_current_context()
    reference = context.get("data_interval_end") or context["dag_run"].run_after
    return us_session_date(reference)


def requested_end_date(session_date: date, params: dict[str, Any]) -> date:
    """이 run이 구간의 끝으로 쓸 날짜. 비우면 이 run이 기대하는 뉴욕 세션 날짜다."""
    given = params.get(END_DATE_PARAM)
    if not given:
        return session_date
    return dag_common.calendar_day_or_fail(given, END_DATE_PARAM)


@dag(
    dag_id="kis_overseas_index_daily",
    dag_display_name="📅 미국 지수 확정 일봉 (KIS)",
    description="S&P500·나스닥 종합의 확정 일봉을 최근 200달력일 창으로 받아 index_daily에 저장한다.",
    schedule=SCHEDULE,
    start_date=pendulum.datetime(2026, 8, 28, tz=KST_TIMEZONE),  # KST 2026-08-28 00:00 = UTC 2026-08-27 15:00
    catchup=False,
    max_active_runs=1,
    default_args={"retries": 2, "retry_delay": timedelta(minutes=10)},
    params={
        END_DATE_PARAM: Param(
            None,
            type=["null", "string"],
            title="구간의 끝",
            description=(
                "YYYY-MM-DD. 비우면 이 run이 기대하는 뉴욕 세션 날짜. "
                "이 날짜를 끝으로 최근 200달력일을 받는다."
            ),
        ),
        START_DATE_PARAM: Param(
            None,
            type=["null", "string"],
            title="구간의 시작",
            description=(
                f"YYYY-MM-DD. 비우면 구간의 끝에서 {SPAN_CALENDAR_DAYS}달력일 앞. "
                f"이력 백필에 쓴다 — 구간을 {SPAN_CALENDAR_DAYS}달력일씩 끊어 반복 조회한다."
            ),
        ),
    },
    doc_md=__doc__,
    tags=["kis", "market", "daily", "us", "index"],
)
def kis_overseas_index_daily():
    @task(task_display_name="미국 지수 일봉 수집·저장")
    def collect() -> int:
        context = get_current_context()
        params = dict(context.get("params") or {})

        end_date = requested_end_date(_session_date(), params)
        start_date = dag_common.requested_start_date(end_date, params)
        windows = fetch_windows(start_date, end_date)

        # 자동 실행만 휴장일을 건너뛴다. 백필은 끝 날짜가 휴장일이어도 구간 안의 거래일이
        # 목적이므로 막을 이유가 없다. 캘린더가 없으면(`None`) 진행한다 — 빈 응답을 실패로
        # 만드는 수집기 검사가 잡는다.
        if not params.get(END_DATE_PARAM) and not params.get(START_DATE_PARAM):
            with closing(dag_common.connection()) as connection:
                dag_common.skip_unless_us_open(connection, end_date)

        app_key, app_secret = dag_common.kis_credentials()
        collector = KisOverseasIndexDailyCollector(access_token(Variable, app_key, app_secret), app_key, app_secret)

        stored = 0
        failures: list[str] = []
        with closing(dag_common.connection()) as connection:
            for index in OverseasIndex:
                # 창 하나가 실패하면 그 심볼의 남은 창은 건너뛴다. 구멍 난 구간 위에 나머지를
                # 얹어 봐야 수익률 계산이 그 구멍에서 멈춘다.
                for window_start, window_end in windows:
                    try:
                        fetch = dag_common.call_with_token_reissue(
                            collector,
                            KisOverseasIndexDailyCollector.fetch,
                            index,
                            window_start,
                            window_end,
                            app_key=app_key,
                            app_secret=app_secret,
                            label=index.value,
                        )
                    except KisHTTPError as error:
                        logger.warning("%s failed with HTTP %s", index.value, error.status)
                        failures.append(f"{index.value} {window_start}~{window_end}({error})")
                        break
                    except KisTimeWindowError as error:
                        # 제공처가 지금은 이 조회를 받지 않는다. 재시도는 같은 답을 받으며 예산만
                        # 태우므로 즉시 죽인다. 사람이 시각을 맞춰 다시 트리거한다.
                        raise AirflowFailException(
                            f"{index.value}: {error}. 제한 시각 뒤에 다시 트리거한다."
                        ) from error
                    except (KisResultError, KisPayloadError) as error:
                        logger.warning("%s %s~%s failed: %s", index.value, window_start, window_end, error)
                        failures.append(f"{index.value} {window_start}~{window_end}({error})")
                        break
                    except ConnectionError as error:
                        logger.warning("%s %s~%s failed to connect: %s", index.value, window_start, window_end, error)
                        failures.append(f"{index.value} {window_start}~{window_end}({error})")
                        break

                    with atomic(connection):
                        rows = collector.store(connection, fetch)
                    stored += rows
                    logger.info(
                        "Stored %s daily bars for %s (%s) %s~%s in %s pages",
                        rows,
                        index.value,
                        index.kis_code,
                        window_start,
                        window_end,
                        fetch.page_count,
                    )

        if failures:
            raise AirflowFailException(f"Overseas index daily collection failed for: {'; '.join(failures)}")
        return stored

    collect()


kis_overseas_index_daily = kis_overseas_index_daily()
