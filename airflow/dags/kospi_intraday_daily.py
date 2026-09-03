"""코스피 장중 전망 — 지금 가격에서 마감까지 어디로 갈 것 같은가.

설계는 `docs/analysis/kospi-forecast.md`다. 슬롯 둘(`midday` 11:35, `pre_close` 14:35)이
같은 DAG에서 돈다 — **같은 것을 같은 이유로 기다리기 때문이다**(분봉과 수급 스냅샷).
장전은 밤사이 매크로 수집을 기다려 DAG가 따로다.

## 예측 축이 장전과 다르다

장전은 전일 종가 대비 오늘 종가를 묻고, 여기는 **그 시각 현재가 대비** 마감까지를 묻는다.
정답(오늘 KRX 확정 종가)은 셋이 같고 분모만 다르다. 관측 상태에 "지금까지 등락"이 따로
실려서, 모델이 이미 일어난 것을 남은 것으로 다시 세지 않게 프롬프트가 둘을 가른다.

**슬롯끼리 성적을 직접 견주지 않는다.** 묻는 것이 다르다 — 마감 55분 전은 남은 움직임이
작아 폭이 좁다. 슬롯마다 자기 기준선과 견준다.

## 슬롯을 시계로 정하지 않는다

`resolve_slot`이 ① Param → ② `logical_date`의 KST 시·분이 슬롯 표와 **정확히** 일치 →
③ **실패** 순으로 정한다. 가까운 슬롯으로 반올림하지 않는다.

2026-08-21에 옛 추론이 그 반대였다 — `logical_date`가 없는 수동 실행이 벽시계로 떨어져
**UI의 Trigger 버튼이 조용히 다른 슬롯을 돌렸다.** 조용히 다른 슬롯을 도는 것보다 안 도는
편이 낫다는 것이 그때 얻은 교훈이다.

`SCHEDULE`의 cron과 `kospi.domain.SLOT_TIMES`는 **같은 커밋에서 만진다.** 테스트가 둘을
대조하고, 어긋나면 `resolve_slot`이 실행을 죽인다.

## 준비 검사

최신 `index_bar`가 기준 시각에서 `BAR_STALENESS`(15분)보다 오래됐으면 실행하지 않는다.
오래된 가격을 "지금"으로 읽고 답하는 것보다 안 도는 편이 낫다. 정상이면 `kis_quote_intraday`
가 5분마다 채운다.

## 태스크 둘

    build_forecast >> notify_slack

## 실패와 재시도

**단일 요청 형태다** — 전망 하나가 결과 전부라 예외를 그대로 올린다.

- `LlmError`·`KospiError`·`GraphError` → `AirflowFailException`
- `RetryableLlmError`·`ConnectionError`·`KospiNotReady` → 그대로 올려 Airflow가 재시도
- 슬롯을 못 정함 → `AirflowFailException`. **반올림하지 않는다**

재시도가 둘인 것은 앞 슬롯이 다음 슬롯을 막지 않게 하려는 값이다. 슬롯 간격이 세 시간이라
최악(2회 × 5분 + 15분 타임아웃 × 3)도 그 안에 들어온다.

## params

| 이름 | 기본값 | 뜻 |
| --- | --- | --- |
| `run_date` | `null` | 대상 세션 날짜(YYYY-MM-DD). 비우면 logical time의 KST 날짜 |
| `notify` | `true` | Slack 발송. 끄면 저장까지만 한다 — **과거 날짜를 백필할 때 끈다** |
| `run_slot` | `null` | `midday` 또는 `pre_close`. 비우면 스케줄된 시각으로 정한다. **수동 실행은 반드시 고른다** |

## 필요한 환경

`kospi_forecast_daily`와 같다.
"""

from typing import Any

import pendulum
from airflow.sdk import dag, task
from airflow.timetables.trigger import MultipleCronTriggerTimetable

from modules.kospi import common, intraday
from modules.utility import KST_TIMEZONE

# **`kospi.domain.SLOT_TIMES`와 같아야 한다.** 어긋나면 `resolve_slot`이 슬롯을 못 찾아
# 실행이 죽는다 — 조용히 다른 슬롯으로 떨어지는 것보다 낫다. 테스트가 둘을 대조한다.
SCHEDULE = MultipleCronTriggerTimetable(
    "35 11 * * 1-5",  # KST 평일 11:35 = UTC 월~금 02:35
    "35 14 * * 1-5",  # KST 평일 14:35 = UTC 월~금 05:35
    timezone=KST_TIMEZONE,
)


@dag(
    dag_id="kospi_intraday_daily",
    dag_display_name="📈 코스피 전망 · 장중 (LLM)",
    description="장중 두 번, 지금 가격에서 마감까지의 방향과 기대 등락률을 근거와 함께 Slack에 보낸다.",
    schedule=SCHEDULE,
    start_date=pendulum.datetime(2026, 9, 3, tz=KST_TIMEZONE),  # KST 2026-09-03 00:00 = UTC 2026-09-02 15:00
    catchup=False,
    max_active_runs=1,
    default_args=common.DEFAULT_ARGS,
    params={**common.run_date_param(), **intraday.run_slot_param(), **common.notify_param()},
    doc_md=__doc__,
    tags=["kospi", "llm", "market", "korea"],
)
def kospi_intraday_daily():
    @task(task_display_name="전망 생성", execution_timeout=common.BUILD_TIMEOUT)
    def build_forecast() -> dict[str, Any]:
        # XCom 경계다. 날짜와 슬롯만 넘기고 내용은 발송이 DB에서 다시 읽는다.
        return intraday.build()

    @task(task_display_name="Slack 발송")
    def notify_slack(built: dict[str, Any]) -> str:
        return common.notify_forecast(built)

    notify_slack(build_forecast())


kospi_intraday_daily = kospi_intraday_daily()
