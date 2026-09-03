"""코스피 장전 전망 — 오늘 종가가 전일 대비 어디로 갈 것 같은가.

설계는 `docs/analysis/kospi-forecast.md`다. 옛 시장 추론(`market_thesis_*`)을 대체하는
기능이고, 그쪽은 이 셋이 운영에서 돈 뒤 지운다.

## 무엇이 달라졌나

옛 추론은 84건 채점에서 무작위 찍기와 성적이 같았다(Brier 0.668, 균등 0.667). 기능이 많아
무엇이 문제인지 가를 수 없었다 — 대상 넷, 툴 열다섯, 확률 셋, 지평 넷, 슬롯 다섯.

여기는 코스피 하나, 툴 셋, 답은 **방향 + 기대 등락률 + ± 폭**이다. 확률을 안 낸다.
근거는 요인 고정 어휘의 관계 그래프(Neo4j)와 메모에서 오고, 그 둘은 장후 관찰이 쌓는다.

## 왜 08:35인가

문서 평가(`document_assessment_hourly`, 매시 25분)와 밤사이 매크로 수집(07:30~08:50) 뒤라야
밤사이 기사와 미국장 값이 근거에 든다. **시각은 전제이지 보장이 아니다** — 선행 DAG에
재시도가 있어 그 시각을 넘길 수 있다. 그래서 준비 검사가 있고 이 DAG도 재시도를 둘 갖는다.

`build_forecast`는 `execution_timeout` 15분이다. 요청 타임아웃은 모델 호출 하나만 막고 한
실행은 조사 왕복과 답변·교정까지 여러 번 부른다. 09:00 개장 전에 닿아야 해서 이 값이다.

## 태스크 둘

    build_forecast >> notify_slack

발송을 뗀 이유: LangGraph 재실행(비용이 크다)과 발송 실패를 분리한다. Slack이 잠깐 죽어도
전망을 다시 만들지 않는다.

## 첫 성공본은 불변이다

같은 (날짜, 슬롯)에 전망이 이미 있으면 **모델을 다시 부르지 않는다.** 재실행은 기존 행을
읽어 발송으로 넘길 뿐이다.

## 실패와 재시도

**단일 요청 형태다** — 전망 하나가 결과 전부라 예외를 그대로 올린다. 항목별 실패 수집이
필요한 자리가 없다.

- `LlmError`·`KospiError`·`GraphError` → `AirflowFailException`(재시도해도 같다)
- `RetryableLlmError`·`ConnectionError`·`KospiNotReady` → 그대로 올려 Airflow가 재시도
- `SlackError` → `AirflowFailException`. **발송은 at-least-once다** — 서버가 수락한 뒤 응답만
  끊긴 경우가 `ConnectionError`로 올라와 재시도가 같은 메시지를 한 번 더 보낼 수 있다

## params

| 이름 | 기본값 | 뜻 |
| --- | --- | --- |
| `run_date` | `null` | 대상 세션 날짜(YYYY-MM-DD). 비우면 logical time의 KST 날짜 |
| `notify` | `true` | Slack 발송. 끄면 저장까지만 한다 — **과거 날짜를 백필할 때 끈다** |

## 필요한 환경

- `XAI_API_KEY`. 어떤 모델을 부를지는 `modules/llm.py`의 `kospi_model()`이 코드로 정하고
  키는 그 LangChain 클래스가 자기 이름으로 읽는다.
- `NEO4J_URI`, `NEO4J_USER`, `NEO4J_PASSWORD` — **관계와 메모의 원본이다.** 없으면 죽는다.
- `SLACK_BOT_TOKEN`, `SLACK_CHANNEL_MARKET`.
- `CONNECTION_ID`가 가리키는 Airflow 연결.
- `LANGSMITH_TRACING`을 켜면 프롬프트와 툴 결과가 외부로 나간다.
"""

from typing import Any

import pendulum
from airflow.sdk import dag, task

from modules.kospi import common, forecast
from modules.utility import KST_TIMEZONE


@dag(
    dag_id="kospi_forecast_daily",
    dag_display_name="📈 코스피 전망 · 장전 (LLM)",
    description="장 열리기 전에 오늘 코스피의 방향과 기대 등락률을 근거와 함께 적고 Slack에 보낸다.",
    schedule="35 8 * * 1-5",  # KST 평일 08:35 = UTC 일~목 23:35
    start_date=pendulum.datetime(2026, 9, 3, tz=KST_TIMEZONE),  # KST 2026-09-03 00:00 = UTC 2026-09-02 15:00
    catchup=False,
    max_active_runs=1,
    default_args=common.DEFAULT_ARGS,
    params={**common.run_date_param(), **common.notify_param()},
    doc_md=__doc__,
    tags=["kospi", "llm", "market", "korea"],
)
def kospi_forecast_daily():
    @task(task_display_name="전망 생성", execution_timeout=common.BUILD_TIMEOUT)
    def build_forecast() -> dict[str, Any]:
        # XCom 경계다. 날짜와 슬롯만 넘기고 내용은 발송이 DB에서 다시 읽는다.
        return forecast.build()

    @task(task_display_name="Slack 발송")
    def notify_slack(built: dict[str, Any]) -> str:
        return common.notify_forecast(built)

    notify_slack(build_forecast())


kospi_forecast_daily = kospi_forecast_daily()
