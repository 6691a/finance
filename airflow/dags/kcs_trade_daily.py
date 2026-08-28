"""관세청 10일 단위 수출입 잠정치 수집 DAG.

데이터셋 넷을 받는다 — `{수출, 수입} × {품목별, 국가별}`이고
`modules.collectors.indicator.kcs.DATASETS`가 목록을 정한다. 계열은 42개다.

- 수출 품목: 전체와 반도체·철강제품·승용차·석유제품·무선통신기기·선박·자동차부품·
  컴퓨터 주변기기·정밀기기·가전제품
- 수입 품목: 전체와 반도체·원유·기계류·가스·반도체 제조용장비·정밀기기·석유제품·
  무선통신기기·승용차·석탄
- 수출 국가: 중국·미국·유럽연합·베트남·홍콩·일본·대만·인도·싱가포르·말레이시아
- 수입 국가: 중국·미국·유럽연합·일본·베트남·호주·대만·사우디아라비아·러시아연방·말레이시아

**국가별 데이터셋의 전체 금액은 저장하지 않는다.** 품목별이 이미 같은 값을 갖는다
(2026-07 1\\~10일 수출 전체가 두 데이터셋에서 29,827,757로 일치했다).

설계 배경은 [docs/collection/korea-trade-collection.md](../../docs/collection/korea-trade-collection.md)에 있다.

## 왜 이 값인가

국내 매크로에 금리와 환율은 있는데 **실물이 없었다.** 그중에서도 수출은 보유 종목
(`005930`·`000660`)의 실적에 직접 꽂히는 값이고, 관세청 10일 잠정치는 월간 발표보다 20일 빠르다.
수입 쪽은 반도체 제조용장비가 설비투자를, 원유·가스·석탄이 물가 압력을 말한다.

## 왜 매일 도나

발표는 월 세 번이다 — 1\\~10일은 11일에, 1\\~20일은 21일에, 1\\~말일은 익월 1일에 나온다.
그런데 **전월까지의 값이 신고 정정·취하를 반영해 계속 바뀐다.** 발표 달력을 따로 두고 맞추는
것보다 매일 한 번 묻는 편이 싸다. 요청은 하루 네 건이고 개발계정 한도는 데이터셋마다 10,000건이다.

멱등 키가 `(provider, series_id, observation_date)`라서 같은 구간을 며칠씩 다시 받아도 행이
늘지 않고 정정된 값으로 갱신된다.

주말에도 돈다. 11일·21일·1일이 주말일 수 있고, 그날 값을 다시 집는 실행이 없다.

## 조회 구간

수집 단위가 **월**이라(`strtYymm`/`endYymm`, `YYYYMM`) 날짜 구간을 그것이 걸친 월 구간으로
바꿔 부른다. 되돌아보기가 `LOOKBACK_DAYS_TRADE`(100일, 약 넉 달)인 것은 "전월까지 현행화"라는
제공처 규칙 때문이다. 두 달만 보면 분기 경계에서 정정을 놓친다.

한 요청이 받을 수 있는 구간은 **120개월**이다. 그보다 길면 제공처가 본문 오류로 답하고,
`KcsRequest`가 요청 전에 먼저 막는다. 백필은 그래서 두 번에 나눠 부른다(아래).

## 실패와 재시도

**태스크 매핑**이다(`.expand`). 데이터셋마다 요청 하나라 실패가 곧 그 태스크의 실패이고 따로
판정할 것이 없다. 재시도도 실패한 데이터셋만 다시 돈다. `fred_*`가 같은 형태다.

**데이터셋마다 활용신청이 따로다.** 하나만 승인이 빠지면 그 태스크만
`SERVICE_KEY_IS_NOT_REGISTERED_ERROR`(HTTP 403)로 죽고 나머지는 저장된다. 매핑을 고른 이유
중 하나가 이것이다.

제공처가 실패를 HTTP 상태가 아니라 본문(`resultCode`)으로도 알린다. 되돌릴 수 없는 것(인자 형식,
등록되지 않은 키)은 `AirflowFailException`으로 바꾸고, 한도 초과처럼 기다리면 풀리는 것은 그대로
올려 Airflow가 재시도하게 둔다.

## params

    airflow dags trigger kcs_trade_daily \\
      --conf '{"observation_start": "2016-01-01", "observation_end": "2025-12-31"}'
    airflow dags trigger kcs_trade_daily \\
      --conf '{"observation_start": "2026-01-01"}'

위 둘이 2016-01부터의 전체 백필이다. 제공처가 값을 주기 시작한 달이 **2016년 1월**이고
(2015-12는 0건으로 답한다), 120개월 상한 때문에 한 번에 못 받는다.

## 필요한 환경

- `KCS_SERVICE_KEY`. 공공데이터포털 서비스키의 **디코딩(원문) 값**이다. 인코딩된 값을 넣으면
  다시 인코딩되어 등록되지 않은 키로 거절된다. 질의 문자열에 들어가므로 예외 메시지와 로그에
  URL을 넣지 않는다.
- `CONNECTION_ID`가 가리키는 Airflow 연결. 접속 정보는 `AIRFLOW_CONN_FINANCE`가 갖는다.
"""

import logging
import os
from contextlib import closing
from datetime import timedelta

import pendulum
from airflow.providers.postgres.hooks.postgres import PostgresHook
from airflow.sdk import Param, dag, get_current_context, task
from airflow.sdk.exceptions import AirflowFailException
from pydantic import SecretStr, ValidationError

from modules.collectors.indicator.kcs import (
    DATASETS,
    KcsDataset,
    KcsHTTPError,
    KcsPayloadError,
    KcsRequest,
    KcsResultError,
    KcsTradeCollector,
)
from modules.period import (
    LOOKBACK_DAYS_PARAM,
    OBSERVATION_END_PARAM,
    OBSERVATION_START_PARAM,
    PeriodError,
    resolve_observation_period,
)
from modules.utility import CONNECTION_ID, KST_TIMEZONE, UNRECOVERABLE_STATUSES, atomic

logger = logging.getLogger(__name__)

# 100일이면 최근 넉 달이 걸린다. 제공처가 "전월까지" 정정하므로 그보다 넉넉해야 분기 경계를 덮는다.
LOOKBACK_DAYS_TRADE = 100

# 다시 불러도 같은 답이 오는 본문 오류. 인자 형식·구간 위반과 등록되지 않은 키가 여기 온다.
# 한도 초과(22·23)처럼 기다리면 풀리는 것은 여기 없어서 그대로 올라가고 Airflow가 재시도한다.
UNRECOVERABLE_RESULT_CODES = frozenset({"99", "12", "30", "31"})


@dag(
    dag_id="kcs_trade_daily",
    dag_display_name="🇰🇷 한국 수출입 10일 단위 잠정치 (관세청)",
    description="매일 관세청에서 수출·수입의 품목별·국가별 10일 단위 잠정치를 받아 저장한다.",
    # KST 매일 09:30 = UTC 00:30. 관세청은 한국 기관이라 발표가 KST 오전이다.
    schedule="30 9 * * *",
    start_date=pendulum.datetime(2026, 8, 28, tz=KST_TIMEZONE),  # KST 2026-08-28 00:00 = UTC 2026-08-27 15:00
    catchup=False,
    max_active_runs=1,
    default_args={"retries": 2, "retry_delay": timedelta(hours=1)},
    params={
        OBSERVATION_START_PARAM: Param(
            None,
            type=["null", "string"],
            format="date",
            title="조회 시작일",
            description="비우면 observation_end에서 lookback_days만큼 앞으로 잡는다. 걸친 달은 통째로 받는다.",
        ),
        OBSERVATION_END_PARAM: Param(
            None,
            type=["null", "string"],
            format="date",
            title="조회 종료일",
            description="비우면 이 run의 data_interval_end를 KST 날짜로 바꿔 쓴다.",
        ),
        LOOKBACK_DAYS_PARAM: Param(
            LOOKBACK_DAYS_TRADE,
            type="integer",
            minimum=1,
            title="되돌아볼 일수",
            description="제공처가 전월까지의 값을 정정으로 갱신한다. 짧게 잡으면 그 정정을 못 받는다.",
        ),
    },
    doc_md=__doc__,
    tags=["kcs", "macro", "daily"],
)
def kcs_trade_daily():
    @task(task_display_name="수출입 잠정치 수집·저장")
    def collect(dataset_key: str) -> int:
        """데이터셋 하나를 받아 저장한다.

        데이터셋마다 태스크를 매핑해 하나가 실패해도 나머지가 저장되게 한다. 재시도도 실패한
        데이터셋만 다시 호출한다. 응답 하나가 그 데이터셋의 계열을 전부 담고 있어 계열로는 더
        나누지 않는다.
        """
        context = get_current_context()
        try:
            observation_start, observation_end = resolve_observation_period(context, LOOKBACK_DAYS_TRADE)
        except PeriodError as error:
            raise AirflowFailException(str(error)) from error

        try:
            request = KcsRequest.from_dates(KcsDataset(dataset_key), observation_start, observation_end)
        except ValidationError as error:
            # 120개월을 넘는 구간이 여기 걸린다. 다시 불러도 같은 답이라 재시도하지 않는다.
            raise AirflowFailException(str(error)) from error

        service_key = os.environ.get("KCS_SERVICE_KEY")
        if not service_key:
            raise AirflowFailException("KCS_SERVICE_KEY is required")
        collector = KcsTradeCollector(SecretStr(service_key))

        try:
            response = collector.fetch_trade(request)
        except KcsHTTPError as error:
            # 활용신청이 안 된 데이터셋은 403으로 거절된다. 다시 불러도 같은 답이다.
            if error.status in UNRECOVERABLE_STATUSES:
                raise AirflowFailException(str(error)) from error
            if error.retry_after is not None:
                logger.warning("KCS asked to retry after %s seconds", error.retry_after)
            raise

        with closing(PostgresHook(postgres_conn_id=CONNECTION_ID).get_conn()) as connection:
            try:
                with atomic(connection):
                    count = collector.store_observations(connection, response)
            except KcsResultError as error:
                # 제공처가 실패를 HTTP가 아니라 본문으로 알린다. 코드로 재시도 여부를 가른다.
                if error.code in UNRECOVERABLE_RESULT_CODES:
                    raise AirflowFailException(str(error)) from error
                raise
            except KcsPayloadError as error:
                raise AirflowFailException(str(error)) from error

        logger.info(
            "Stored %s KCS %s observations for %s..%s",
            count,
            request.dataset.value,
            request.start_month,
            request.end_month,
        )
        return count

    collect.expand(dataset_key=list(DATASETS))


kcs_trade_daily = kcs_trade_daily()
