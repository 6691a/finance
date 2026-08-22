"""애프터마켓 리뷰 — 정규장이 닫힌 뒤 NXT에서 무엇이 움직였나.

`docs/market-thesis/7-nxt-review.md`의 구현이다. 한국 주식의 실제 하루는 KRX 15:30이 아니라
NXT 애프터마켓 20:00에 끝나는데, 장후 리뷰(`market_thesis_review`)의 기준 시각이 15:30이라
그 뒤 4시간 30분이 추론 기록에 없었다. 이 DAG가 그 구간을 채운다.

## 왜 슬롯을 나눴나

기존 장후 리뷰에 애프터 데이터를 얹으면 그 슬롯의 event-time cutoff(15:30)가 깨진다.
15:30 이후 정보를 일부러 빼는 것이 그 슬롯의 설계이고, 그래야 재실행마다 근거가 달라지지
않는다. 저장소 규칙 "슬롯·모드로 갈리는 DAG는 나눈다"가 이 경우다(2026-08-21 장전·장후
분리와 같은 판단). **DAG를 고르는 것이 곧 슬롯을 고르는 것이다.**

## 왜 21:00인가

`kis_stock_minute_bars_daily`가 20:05에 돌아 realtime WebSocket이 쌓은 잠정 봉
(`is_final=false`)을 REST 확정으로 바꾼다. 그 전에 추론하면 **첫 성공본 불변** 때문에
나중에 값이 바로잡혀도 잘못된 값 위의 추론이 영영 남는다. 재시도 여유를 두어 21:00이다.

**기준 시각은 실행 시각이 아니라 20:00 마감이다.**

## 대상이 종목뿐이다

**NXT에는 지수가 없다.** 지수를 대상에 넣으면 모델이 매번 "지수는 정규장 마감값이라
움직이지 않았다"를 쓰게 된다. 코스피·코스닥 정규장 등락률은 관측 상태에 맥락으로만 싣는다.

## 태스크 둘

    build_thesis >> notify_slack

채점도 사후 해설도 없다. 리뷰는 예측이 아니라 채점할 대상이 없고(기존 `post_close`와 같은
이유), 해설은 아직 붙이지 않았다 — 붙이려면 `NarrativeTarget`이 슬롯을 들고 슬롯마다
호출을 나눠야 한다. 새 슬롯이 그 루프에 조용히 들어가지 않도록
`thesis_outcome/select_pending_narratives.sql`과 `select_backlog.sql`이 슬롯을 열거한다.

## readiness guard

- 애프터 봉이 0개 → skip. 체결이 진짜 0인 날과 수집 실패를 응답만으로 가를 수 없다
- 봉이 전부 잠정 → `ThesisNotReady`. 20:05 백필이 아직 안 돌았다. 재시도가 기다린다
- 확정 종가 누락 → `ThesisNotReady`. 애프터 등락률의 분모다

## 실패 판정

- `LlmError`·`ThesisError` → `AirflowFailException`(재시도해도 같다)
- `RetryableLlmError`·`ConnectionError`·`ThesisNotReady` → 그대로 올려 Airflow가 재시도
- `SlackError` → `AirflowFailException`. 발송은 at-least-once다

## params

| 이름 | 기본값 | 뜻 |
| --- | --- | --- |
| `run_date` | `null` | 대상 세션 날짜(YYYY-MM-DD). 비우면 logical time의 KST 날짜 |

## 필요한 환경

- `XAI_API_KEY`. 어떤 모델을 부를지는 `modules/llm.py`의 `thesis_model()`이 코드로 정한다.
- `SLACK_BOT_TOKEN`, `SLACK_CHANNEL_MARKET`, `CONNECTION_ID`.
- `LANGSMITH_TRACING`을 켜면 프롬프트와 툴 결과가 외부로 나간다.
"""

from typing import Any

import pendulum
from airflow.sdk import dag, task

from modules import thesis_common, thesis_nxt_review
from modules.utility import KST_TIMEZONE


@dag(
    dag_id="market_thesis_nxt_review",
    dag_display_name="🧠 시장 추론 · 애프터마켓 리뷰 (LLM)",
    description="NXT 애프터마켓이 닫힌 뒤 정규장 이후의 종목 움직임을 해석해 Slack에 보낸다.",
    schedule="0 21 * * 1-5",  # KST 평일 21:00 = UTC 월~금 12:00
    start_date=pendulum.datetime(2026, 8, 25, tz=KST_TIMEZONE),  # KST 2026-08-25 00:00 = UTC 2026-08-24 15:00
    catchup=False,
    max_active_runs=1,
    default_args=thesis_common.DEFAULT_ARGS,
    params=thesis_common.run_date_param(),
    doc_md=__doc__,
    tags=["thesis", "llm", "market", "korea"],
)
def market_thesis_nxt_review():
    @task(task_display_name="추론 생성")
    def build_thesis() -> dict[str, Any]:
        return thesis_nxt_review.build()

    @task(task_display_name="Slack 발송")
    def notify_slack(built: dict[str, Any]) -> str:
        return thesis_common.notify_slack(built)

    notify_slack(build_thesis())


market_thesis_nxt_review = market_thesis_nxt_review()
