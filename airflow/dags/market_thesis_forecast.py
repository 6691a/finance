"""장전 전망 — 오늘 어느 방향으로 갈 것 같은가.

`docs/analysis/market-thesis/`의 3·5단계 중 예측 쪽이다. 분봉·공시·평가된 문서·매크로 시세가 전부
쌓이고 있지만 그것들을 놓고 "오늘 어떻게 움직일 것 같나"를 말하는 층이 없었다.

**목적은 정확도다 — 다만 개별 추론이 아니라 판(版)의 정확도다.** 정답은 시간이 지나야
알고 한 건의 적중은 운과 구분되지 않는다. 그래서 "어떤 정보를 근거로 어떤 결론을 냈다"를
먼저 남기고, 채점이 쌓이면 model·prompt 판별로 비교한다. 채점하는 쪽은
`market_thesis_review`다.

## 왜 08:35인가

문서 평가(`document_assessment_hourly`, 매시 25분)가 끝난 뒤라야 밤사이 기사가 전부 근거
후보에 든다. 08:20에 돌면 08:05 수집분이 아직 점수가 없어 빠진다.

**시각은 전제이지 보장이 아니다.** 선행 DAG에 재시도가 있어 그 시각을 넘길 수 있다.
그래서 `build_thesis` 안에 readiness guard가 있고 이 DAG도 재시도를 셋 갖는다.

`build_thesis`는 `execution_timeout` 30분(`thesis_common.BUILD_TIMEOUT`)이다. 요청 타임아웃은
모델 호출 하나만 막고 한 빌드는 모델을 여러 번 부르므로, 이것이 없으면 느린 실행이 개장을
한참 넘겨도 Airflow는 기다린다.

## 왜 리뷰와 DAG를 나눴나 (2026-08-21)

전에는 `market_thesis_analysis` 하나가 `logical_date`의 시각으로 슬롯을 판정했다
(`hour < 12`면 장전). **그러면 슬롯이 실행자의 의도가 아니라 시계에서 나온다.**
`logical_date`가 없는 수동 실행은 벽시계로 떨어져, 오후에 UI의 Trigger 버튼으로 장전을
다시 돌리려 하면 조용히 장후가 됐다. 이제 DAG를 고르는 것이 곧 슬롯을 고르는 것이다.

따라온 것: 장후 전용 태스크 둘이 장전 실행에서 빈 성공으로 보이던 것이 없어졌고,
두 슬롯을 따로 pause 할 수 있고, `max_active_runs`가 서로를 막지 않는다.

## 태스크 둘

    build_thesis >> notify_slack

`notify_slack`을 뺀 이유: LangGraph 재추론(비용 큼)과 발송 실패를 분리한다. Slack이 잠깐
죽어도 추론을 다시 돌리지 않는다.

**채점·판정 지표는 이 메시지에 없다.** 읽는 사람이 달라 `slack_ops_briefing`이 OPS 채널로 낸다.

## 첫 성공본은 불변이다

같은 (날짜, 슬롯, 대상)에 추론 행이 이미 있으면 **LLM을 다시 부르지 않는다.** LLM은 재호출
마다 답이 달라서 덮어쓰면 최초 판단이 사라진다. 재실행은 기존 행을 읽어 발송으로 넘길 뿐이다.

## 실패 판정

- `LlmError`·`ThesisError` → `AirflowFailException`(재시도해도 같다)
- `RetryableLlmError`·`ConnectionError`·`ThesisNotReady` → 그대로 올려 Airflow가 재시도
- `SlackError` → `AirflowFailException`. **발송은 at-least-once다** — `slack.py`가 응답 없는
  실패를 `ConnectionError`로 올리는데, 서버가 수락한 뒤 응답만 끊긴 경우도 여기 들어가
  재시도가 같은 메시지를 한 번 더 보낼 수 있다.

## params

| 이름 | 기본값 | 뜻 |
| --- | --- | --- |
| `run_date` | `null` | 대상 세션 날짜(YYYY-MM-DD). 비우면 logical time의 KST 날짜 |

## 필요한 환경

- `XAI_API_KEY`. 어떤 모델을 부를지는 `modules/llm.py`의 `thesis_model()`이 코드로 정하고
  키는 그 LangChain 클래스가 자기 이름으로 읽는다.
- `SLACK_BOT_TOKEN`, `SLACK_CHANNEL_MARKET` — 시장 브리핑과 같은 채널을 재사용한다.
- `CONNECTION_ID`가 가리키는 Airflow 연결.
- `LANGSMITH_TRACING`을 켜면 프롬프트와 툴 결과(문서 제목·공시명)가 외부로 나간다.
"""

from typing import Any

import pendulum
from airflow.sdk import dag, task

from modules import thesis_common, thesis_forecast
from modules.utility import KST_TIMEZONE


@dag(
    dag_id="market_thesis_forecast",
    dag_display_name="🧠 시장 추론 · 장전 전망 (LLM)",
    description="장 열리기 전에 오늘의 방향을 확률로 적고 근거와 함께 Slack에 보낸다.",
    schedule="35 8 * * 1-5",  # KST 평일 08:35 = UTC 일~목 23:35
    start_date=pendulum.datetime(2026, 8, 21, tz=KST_TIMEZONE),  # KST 2026-08-21 00:00 = UTC 2026-08-20 15:00
    catchup=False,
    max_active_runs=1,
    default_args=thesis_common.DEFAULT_ARGS,
    params=thesis_common.run_date_param(),
    doc_md=__doc__,
    tags=["thesis", "llm", "market", "korea"],
)
def market_thesis_forecast():
    @task(task_display_name="추론 생성", execution_timeout=thesis_common.BUILD_TIMEOUT)
    def build_thesis() -> dict[str, Any]:
        # XCom 경계다. Airflow가 Pydantic 모델을 어떻게 직렬화하는지에 기대지 않는다.
        return thesis_forecast.build().model_dump(mode="json")

    @task(task_display_name="Slack 발송")
    def notify_slack(built: dict[str, Any]) -> str:
        return thesis_common.notify_slack(built)

    notify_slack(build_thesis())


market_thesis_forecast = market_thesis_forecast()
