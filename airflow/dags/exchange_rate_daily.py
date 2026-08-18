"""하나은행 고시 환율 일별 수집 DAG.

`exchange_rate.py`(dag_id `exchange_rate_1.0.0`)를 대체한다. 옛 DAG는 남겨 두지만 둘을
동시에 켜면 같은 행을 두 번 쓴다. 이 DAG를 켤 때 옛 DAG는 pause한다.

통화마다 태스크를 하나씩 매핑한다. 한 통화가 실패해도 나머지는 저장되고, 재시도도 실패한
통화만 다시 호출한다. 옛 DAG는 `crawling` 태스크가 열 통화를 한꺼번에 asyncio로 모아
XCom에 실어 `bulk_insert`로 넘겼다. 그 구조에는 문제가 셋 있었다.

- 열 통화 중 하나만 실패해도 전체가 실패하고, 재시도하면 성공한 아홉 개까지 다시 긁는다.
- 회차 표는 통화마다 1500행 가까이 된다. 그걸 XCom(메타 DB의 한 행)에 넣으면 메타 DB가
  수집 데이터 저장소가 된다. XCom은 태스크 사이에 넘기는 작은 값을 위한 자리다.
- 수집과 저장이 다른 태스크라 부분 실패 시 어디까지 저장됐는지 알 수 없다.

여기서는 통화 하나의 수집과 저장을 한 태스크 안에서 끝낸다. 태스크 경계를 넘는 값이
없으므로 XCom에는 저장 건수(정수)만 남는다.

`catchup=True`라서 켜는 순간 `start_date`부터 오늘까지의 run이 하루에 하나씩 만들어진다.
그게 곧 백필 범위다. 고시일자 하나가 통화당 1500행 가까이 되므로 `start_date`를 과거로
옮기기 전에 행 수를 먼저 계산한다. 수집기의 `EARLIEST_QUOTATION_DATE`가 같은 날을 잡고
있어서, `start_date`만 실수로 옮기면 태스크가 검증에서 먼저 막힌다. 둘은 함께 바꾼다.

`max_active_runs=1`이라 밀린 run은 한 번에 하나씩 순서대로 돈다. 하나은행에 동시 요청이
쏟아지지 않는다.

## 수집 대상

통화는 `modules.collectors.hana.HanaCurrency`가 정한다. 현재 열 개다.
USD, JPY, CNY, EUR, HKD, TWD, GBP, AUD, CAD, RUB. 통화를 늘리려면 그 enum에만 추가한다.
매핑되는 태스크 수가 따라 늘어난다.

## params

| 이름 | 기본값 | 뜻 |
| --- | --- | --- |
| `quotation_date` | `null` | 수집할 KST 고시일자(YYYY-MM-DD). 비우면 data interval에서 계산한다 |

고시일자는 실행 시각의 전날이다. 수집기가 `EARLIEST_QUOTATION_DATE`(KST 2026-07-01)보다
이른 날짜와 KST 오늘보다 늦은 날짜를 검증에서 막는다.

## 실행 방법

1. 일별 스케줄. 손댈 것 없다. KST 매일 08:00에 전날 고시분을 가져온다.

2. 백필. 이 DAG는 `catchup=True`라서 켜는 순간 `start_date`부터의 run이 하루에 하나씩
   만들어진다. 그게 곧 백필 범위다. 구간을 따로 지정하려면 backfill을 쓴다. 고시일자는
   run마다 data interval에서 계산되므로 `--dag-run-conf`로 날짜를 주면 안 된다.
   모든 run이 같은 날짜를 수집하게 된다.

       airflow backfill create --dag-id exchange_rate_daily \
         --from-date 2026-07-02 --to-date 2026-07-31

3. 하루만 확인. 고시일자를 계산에 맡기지 말고 직접 넘긴다.

       airflow dags test exchange_rate_daily --conf '{"quotation_date": "2026-08-05"}'

   크론 timetable의 수동 run은 넘긴 시각 **이전의 마지막 완결 구간**을 data interval로
   잡는다. 그래서 `dags test`에 날짜만 주면 고시일자가 이틀 밀린다. 스케줄 run은 정상이다.

한 run이 통화당 1500행 가까이 쓴다. 열 통화면 하루치가 1만 5천 행이다. 백필 범위를
넓히기 전에 행 수를 먼저 계산한다.

## 실패와 재시도

- HTTP 400/401/403/404: 설정 오류라 재시도해도 같으므로 즉시 실패한다.
- 그 밖의 HTTP 오류와 네트워크 오류: 그대로 올려서 재시도한다(2회, 30분 간격).
- 표 구조가 바뀌어 파싱이 깨지면 즉시 실패한다. 칸 수 검사가 먼저 걸리므로 값이 옆 칸으로
  밀린 채 저장되지 않는다.
- 휴일이라 표가 비어 있으면 실패가 아니다. 0을 반환하고 끝낸다.

## 필요한 환경

`CONNECTION_ID`가 가리키는 Airflow 연결 하나뿐이다. 접속 정보는 `AIRFLOW_CONN_FINANCE`가 갖고,
로컬에서는 `compose/local/airflow/.env`에 있다. 값을 바꾸면 `docker compose up -d`로
컨테이너를 다시 만들어야 반영된다. 외부 API 키는 쓰지 않는다.

## 배포 시 저장 위치를 바꾸는 곳

지금은 로컬 finance DB에 넣는다. 실 배포에서 다른 DB로 보내려면 아래 네 군데를 본다.
코드에서 저장 위치를 정하는 건 **연결 ID 하나뿐**이고 나머지는 환경·스키마 쪽이다.
각 자리에 `[배포]` 주석을 달아 뒀으니 `grep -rn "배포. 저장 위치"`로 한 번에 찾는다.

1. `CONNECTION_ID` (`modules/utility.py`). 어느 Airflow 연결로 쓸지. 모든 DAG가 이 한 값을
   공유한다. **DB를 바꾸려면 보통 여기까지 안 온다.**
   운영에서 같은 이름의 연결이 이미 운영 DB를 가리키면 코드는 그대로 두고 2번만 바꾼다.
2. `AIRFLOW_CONN_FINANCE` 환경 변수 (`compose/local/airflow/.env`). 실제 접속 정보.
   운영 Airflow는 Connection UI나 시크릿 백엔드에 같은 이름으로 등록한다.
3. `airflow/sql/postgres/exchange_rate/upsert.sql`. 테이블 이름과 스키마.
   운영에서 `public`이 아닌 스키마를 쓰면 여기서 `<스키마>.exchange_rate`로 수식한다.
4. `apps/models/finance.py`의 `table_options(database=...)`와 `config.yaml`의 별칭 URL.
   테이블을 **만드는** 쪽이다. DAG가 쓰는 DB와 마이그레이션이 만드는 DB는 같아야 한다.
1번과 4번이 어긋나면 DAG가 없는 테이블에 INSERT를 시도해 런타임에야 드러난다.
배포 전에 대상 DB에서 `SELECT to_regclass('exchange_rate')`로 존재를 먼저 확인한다.
"""

import logging
from collections.abc import Mapping
from datetime import date, timedelta
from typing import Any

import pendulum
from airflow.exceptions import AirflowFailException
from airflow.providers.postgres.hooks.postgres import PostgresHook
from airflow.sdk import Param, dag, get_current_context, task

from modules.collectors.hana import (
    HanaCurrency,
    HanaHTTPError,
    HanaPayloadError,
    HanaRateRequest,
    fetch_rates,
    parse_rates,
    quotation_date_for,
    store_rates,
)
from modules.utility import CONNECTION_ID, KST_TIMEZONE

logger = logging.getLogger(__name__)

# 설정 오류라 재시도해도 같은 결과인 HTTP 상태.
UNRECOVERABLE_STATUSES = frozenset({400, 401, 403, 404})

# 고시일자를 직접 지정하는 run 파라미터. 비어 있으면 data interval에서 계산한다.
QUOTATION_DATE_PARAM = "quotation_date"


def resolve_quotation_date(context: Mapping[str, Any]) -> date:
    """이 run이 수집할 KST 고시일자.

    `params.quotation_date`가 있으면 그 값을 그대로 쓴다. 없으면 data interval에서 계산한다.
    수동 run과 `dags test`에서 크론 timetable이 주는 구간이 직관과 이틀 어긋나므로,
    한 날짜만 확인할 때는 계산에 기대지 말고 파라미터로 넘긴다.
    """
    override = (context.get("params") or {}).get(QUOTATION_DATE_PARAM)
    if override:
        try:
            return date.fromisoformat(str(override))
        except ValueError as error:
            raise AirflowFailException(f"{QUOTATION_DATE_PARAM} must be an ISO date (YYYY-MM-DD)") from error

    data_interval_end = context.get("data_interval_end")
    if data_interval_end is None:
        raise AirflowFailException(
            f"data_interval_end is missing; pass {QUOTATION_DATE_PARAM} to choose the quotation date"
        )
    return quotation_date_for(data_interval_end)


@dag(
    dag_id="exchange_rate_daily",
    dag_display_name="💱 하나은행 고시 환율",
    description="하나은행 고시 환율을 통화별로 매일 받아 exchange_rate에 회차 단위로 쌓는다.",
    schedule="0 8 * * *",  # KST 매일 08:00 = UTC 전날 23:00
    # 고시일자는 실행 시각의 전날이다. 첫 run이 `EARLIEST_QUOTATION_DATE`(KST 2026-07-01)를
    # 수집하려면 start_date가 그 다음 날 08:00이어야 한다. 하루 앞으로 당기면 첫 run이
    # 범위 밖 날짜를 요청해 검증에서 막힌다. 둘은 항상 함께 옮긴다.
    start_date=pendulum.datetime(2026, 7, 2, 8, 0, tz=KST_TIMEZONE),  # KST 2026-07-02 08:00 = UTC 2026-07-01 23:00
    catchup=True,
    max_active_runs=1,
    default_args={"retries": 2, "retry_delay": timedelta(minutes=30)},
    params={
        QUOTATION_DATE_PARAM: Param(
            None,
            type=["null", "string"],
            format="date",
            title="고시일자 (KST)",
            description="비우면 data interval에서 계산한다. 한 날짜만 확인할 때 YYYY-MM-DD로 넘긴다.",
        )
    },
    doc_md=__doc__,
    tags=["hana", "exchange-rate", "daily"],
)
def exchange_rate_daily():
    @task(task_display_name="통화별 고시 수집·저장")
    def collect(currency: str) -> int:
        context = get_current_context()
        quotation_date = resolve_quotation_date(context)
        request = HanaRateRequest(currency=HanaCurrency(currency), quotation_date=quotation_date)

        try:
            response = fetch_rates(request)
        except HanaHTTPError as error:
            if error.status in UNRECOVERABLE_STATUSES:
                raise AirflowFailException(str(error)) from error
            raise

        try:
            rates = parse_rates(response)
        except HanaPayloadError as error:
            # 표 구조가 바뀐 것이므로 재시도해도 같은 결과다.
            raise AirflowFailException(str(error)) from error

        if not rates:
            # 휴일에는 표가 비어 있다. 실패가 아니다.
            logger.info("No %s quotations published for %s", currency, quotation_date)
            return 0

        # 반환 타입은 provider 버전에 따라 psycopg2/psycopg3 래퍼로 갈린다. 런타임 객체는
        # 어느 쪽이든 PEP 249 연결이라 commit·rollback을 갖는다.
        connection: Any = PostgresHook(postgres_conn_id=CONNECTION_ID).get_conn()
        try:
            count = store_rates(connection, rates)
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

        logger.info(
            "Stored %s %s quotations for KST %s (rounds %s..%s)",
            count,
            currency,
            quotation_date,
            rates[0].round,
            rates[-1].round,
        )
        return count

    collect.expand(currency=[currency.value for currency in HanaCurrency])


exchange_rate_daily = exchange_rate_daily()
