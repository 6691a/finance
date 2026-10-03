"""미국 빅테크·반도체 CDS 프리미엄 일별 수집 DAG(DTCC 공개 체결).

설계는 `docs/collection/us-tech-cds-spread.md`, 수집 규칙은
`modules.collectors.indicator.dtcc_cds`에 있다. 대상은 `CdsCompany` 열 곳 × `CdsTenor` 셋
(1·3·5년)이고 `indicator_observation`에 `provider = dtcc`로 쌓는다.

## 받을 날짜

DTCC는 UTC 하루치 체결을 **다음 날 00:15 UTC**쯤 파일 하나로 올린다. 이 DAG는 KST 화~토 10:00
(UTC 01:00)에 돌며 **UTC 기준 전날**, 즉 미국 월~금 하루치를 받는다. 주말 날짜는 받지 않는다
(토요일 파일은 20여 행이다).

날짜는 체결일(UTC)이다. `trade_start`·`trade_end`를 주면 그 구간의 평일을 하루씩 받고, 비우면
run 시각(`data_interval_end`, 수동 run이면 `run_after`)의 UTC 날짜에서 하루를 뺀 날 하나다.

## 실패와 재시도

**단일 요청**이다. 파일 하나가 그날 결과 전부라 수집기 예외를 그대로 올린다.

- HTTP 403·404·연결 오류는 재시도한다(3회, 30분 간격). S3는 "아직 안 올라왔다"와 "지워졌다"를
  같은 403으로 답한다 — 게시가 늦은 날을 살리려고 재시도하고, 그래도 없으면 실패한다.
- 헤더·zip 구성 변경, 모르는 이름 철자, 모르는 보험료 표기·행 종류는 `AirflowFailException`이다.
  재시도해도 같다. 새 철자면 `CdsCompany`의 허용 철자에 더한다.
- 대상 회사 체결이 0인 날은 실패가 아니다(미국 공휴일, 조용한 날). `source_record`만 남는다.
  **파일 행이 0이면** 실패다 — 조용한 날이 아니라 빈 파일이다.

구간을 주면 날짜마다 따로 커밋한다. 중간 날짜가 실패하면 앞 날짜는 남고 태스크는 실패한다.

## 백필

오래된 파일은 DTCC가 지운다(2026-10-03에 2024-09-03은 있고 2024-08-15는 없다). 배포 직후
구간을 주어 돌린다. 한 달씩 자르면 run 하나가 파일 20여 개를 받는다.

    airflow dags trigger dtcc_cds_daily \\
      --conf '{"trade_start": "2024-09-03", "trade_end": "2024-09-30"}'

## 필요한 환경

`AIRFLOW_CONN_FINANCE` 하나. 인증은 없다.
"""

import logging
from contextlib import closing
from datetime import UTC, date, timedelta
from typing import Any

import pendulum
from airflow.sdk import Param, dag, get_current_context, task
from airflow.sdk.exceptions import AirflowFailException

from modules import dag_common
from modules.collectors.indicator.dtcc_cds import DtccPayloadError, fetch_day, store_day
from modules.utility import KST_TIMEZONE, atomic

logger = logging.getLogger(__name__)

TRADE_START_PARAM = "trade_start"
TRADE_END_PARAM = "trade_end"

# 토·일. UTC 날짜 기준이다.
WEEKEND = frozenset({5, 6})


def resolve_trade_dates(context: Any) -> list[date]:
    """이 run이 받을 체결일(UTC) 목록. 주말은 뺀다."""
    params = context.get("params") or {}
    end_given = params.get(TRADE_END_PARAM)
    if end_given:
        trade_end = dag_common.calendar_day_or_fail(end_given, TRADE_END_PARAM)
    else:
        reference = context.get("data_interval_end") or getattr(context.get("dag_run"), "run_after", None)
        if reference is None:
            raise AirflowFailException(f"No run time to derive the trade date from; pass {TRADE_END_PARAM}")
        trade_end = reference.astimezone(UTC).date() - timedelta(days=1)

    start_given = params.get(TRADE_START_PARAM)
    trade_start = dag_common.calendar_day_or_fail(start_given, TRADE_START_PARAM) if start_given else trade_end
    if trade_start > trade_end:
        raise AirflowFailException(f"{TRADE_START_PARAM} {trade_start} must not be after {TRADE_END_PARAM} {trade_end}")

    days = (trade_end - trade_start).days + 1
    return [
        trade_start + timedelta(days=offset)
        for offset in range(days)
        if (trade_start + timedelta(days=offset)).weekday() not in WEEKEND
    ]


@dag(
    dag_id="dtcc_cds_daily",
    dag_display_name="📉 미국 빅테크 CDS 프리미엄 (DTCC)",
    description="DTCC 공개 체결 파일에서 미국 빅테크·반도체 열 곳의 1·3·5년 CDS 프리미엄을 매일 받아 indicator_observation에 쌓는다.",
    schedule="0 10 * * 2-6",  # KST 화~토 10:00 = UTC 화~토 01:00
    start_date=pendulum.datetime(2026, 10, 6, tz=KST_TIMEZONE),  # KST 2026-10-06 00:00 = UTC 2026-10-05 15:00
    catchup=False,
    max_active_runs=1,
    default_args={"retries": 3, "retry_delay": timedelta(minutes=30)},
    params={
        TRADE_START_PARAM: Param(
            None,
            type=["null", "string"],
            format="date",
            title="체결일 시작(UTC)",
            description="비우면 trade_end 하루만 받는다. 주말은 건너뛴다.",
        ),
        TRADE_END_PARAM: Param(
            None,
            type=["null", "string"],
            format="date",
            title="체결일 끝(UTC)",
            description="비우면 run 시각의 UTC 날짜에서 하루 뺀 날이다.",
        ),
    },
    doc_md=__doc__,
    tags=["dtcc", "macro", "daily"],
)
def dtcc_cds_daily():
    @task(task_display_name="CDS 체결 수집·저장")
    def collect() -> int:
        trade_dates = resolve_trade_dates(get_current_context())
        total = 0
        with closing(dag_common.connection()) as connection:
            for trade_date in trade_dates:
                # HTTP·연결 오류(`DtccHTTPError`·`ConnectionError`)는 게시 지연일 수 있어 그대로 올려
                # 재시도에 맡긴다. 지워진 날짜도 같은 403이라 재시도가 끝나면 실패한다.
                response = fetch_day(trade_date)
                try:
                    with atomic(connection):
                        count = store_day(connection, response)
                except DtccPayloadError as error:
                    raise AirflowFailException(f"{trade_date}: {error}") from error
                logger.info("Stored %s CDS observations for %s", count, trade_date)
                total += count
        return total

    collect()


dtcc_cds_daily = dtcc_cds_daily()
