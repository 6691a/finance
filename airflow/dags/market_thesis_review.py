"""장후 리뷰 — 오늘 왜 그렇게 움직였나. 그리고 지난 예측을 채점하고 되돌아본다.

`docs/analysis/market-thesis/`의 3·5단계 중 해석 쪽이다. **리뷰는 예측이 아니다** — 이미 일어난
일의 해석이라 Brier로 채점하지 않는다. 대신 채점의 주체가 여기다: 지난 장전 예측을
T+0·1·3·5로 채점하고, T+1·3·5에는 사후 해설과 판정을 붙인다.

## 왜 20:30인가

확정 종가(`kis_investor_trade_daily`, 18:10)와 지수 마감 봉(`kis_quote_intraday`, 16:00까지)
뒤여야 채점과 관측 상태가 선다. 선행 DAG 재시도 여유를 두어 20:30이다.

**기준 시각은 실행 시각이 아니라 15:30 마감이다.** 20:30에 돌든 재시도로 22:00에 돌든
모델이 보는 것은 장 마감까지다. 그 사이 저녁 기사는 일부러 뺀다 — 안 그러면 재실행할
때마다 근거가 달라져 기록이 흔들린다. **나중에 알려진 것은 T+1·3·5 해설이 따로 붙인다**
(`5-followup.md` 0절: "그날 시장이 왜 그렇게 움직였는지는 며칠 뒤에 알려진다").

## 왜 전망과 DAG를 나눴나 (2026-08-21)

전에는 `market_thesis_analysis` 하나가 `logical_date`의 시각으로 슬롯을 판정했다. 그러면
슬롯이 실행자의 의도가 아니라 시계에서 나오고, 수동 실행은 벽시계로 떨어졌다. 게다가
아래 두 태스크가 장전 실행에서는 아무 일도 안 하면서 성공으로 보였다. 상세는
`market_thesis_forecast`의 docstring에 있다.

## 태스크 넷

    build_thesis >> grade_followups >> narrate_followups >> notify_slack

- `build_thesis` — 오늘 세션을 해석한다. 관측 상태(SQL) → LLM → 저장
- `grade_followups` — 지평 T+0·1·3·5의 미채점 예측을 채점한다. **LLM 없음.**
  날짜 상한이 없어 리뷰가 실패했던 날의 것도 여기서 회수된다
- `narrate_followups` — T+1·3·5에 사후 해설과 판정을 붙인다. (지평, 원 추론의 슬롯)마다 호출 하나다
- `notify_slack` — 오늘 리뷰를 보낸다. LLM을 다시 부르지 않는다

`narrate_followups`가 실패해도 `grade_followups`의 채점은 이미 커밋돼 있다. 다음 실행이
`narrative IS NULL`로 회수한다.

**채점·판정 지표는 이 메시지에 없다.** 읽는 사람이 달라 `slack_ops_briefing`이 OPS 채널로 낸다.

## 첫 성공본은 불변이다

같은 (날짜, 슬롯, 대상)에 추론 행이 있으면 LLM을 다시 부르지 않는다. 이미 매긴 점수와
이미 쓴 해설도 SQL의 `WHERE`가 지킨다.

## 실패 판정

- `LlmError`·`ThesisError` → `AirflowFailException`(재시도해도 같다)
- `RetryableLlmError`·`ConnectionError`·`ThesisNotReady` → 그대로 올려 Airflow가 재시도
- `SlackError` → `AirflowFailException`. 발송은 at-least-once다

## params

| 이름 | 기본값 | 뜻 |
| --- | --- | --- |
| `run_date` | `null` | 대상 세션 날짜(YYYY-MM-DD). 비우면 logical time의 KST 날짜 |

## 필요한 환경

- `XAI_API_KEY`, `SLACK_BOT_TOKEN`, `SLACK_CHANNEL_MARKET`, `CONNECTION_ID`.
- `LANGSMITH_TRACING`을 켜면 프롬프트와 툴 결과가 외부로 나간다.
"""

from typing import Any

import pendulum
from airflow.sdk import dag, task

from modules.thesis import common, review
from modules.utility import KST_TIMEZONE


@dag(
    dag_id="market_thesis_review",
    dag_display_name="🧠 시장 추론 · 장후 리뷰와 채점 (LLM)",
    description="장 마감 뒤 오늘을 해석하고 지난 예측을 지평별로 채점·해설한 뒤 Slack에 보낸다.",
    schedule="30 20 * * 1-5",  # KST 평일 20:30 = UTC 월~금 11:30
    start_date=pendulum.datetime(2026, 8, 21, tz=KST_TIMEZONE),  # KST 2026-08-21 00:00 = UTC 2026-08-20 15:00
    catchup=False,
    max_active_runs=1,
    default_args=common.DEFAULT_ARGS,
    params=common.run_date_param(),
    doc_md=__doc__,
    tags=["thesis", "llm", "market", "korea"],
)
def market_thesis_review():
    @task(task_display_name="추론 생성", execution_timeout=common.BUILD_TIMEOUT)
    def build_thesis() -> dict[str, Any]:
        # XCom 경계다. Airflow가 Pydantic 모델을 어떻게 직렬화하는지에 기대지 않는다.
        return review.build().model_dump(mode="json")

    @task(task_display_name="지평별 채점")
    def grade_followups() -> int:
        return review.grade_followups()

    @task(task_display_name="사후 해설·판정")
    def narrate_followups(built: dict[str, Any]) -> int:
        return review.narrate_followups(built)

    @task(task_display_name="Slack 발송")
    def notify_slack(built: dict[str, Any]) -> str:
        return common.notify_slack(built)

    built = build_thesis()
    built >> grade_followups() >> narrate_followups(built) >> notify_slack(built)


market_thesis_review = market_thesis_review()
