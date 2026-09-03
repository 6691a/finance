"""코스피 장후 관찰 — 오늘 무엇이 움직였나, 그리고 메모를 정리한다.

설계는 `docs/analysis/kospi-forecast.md`다. **이 DAG가 관계 그래프를 쌓는 자리다** —
전망은 그것을 읽기만 한다.

## 하루의 마지막 태스크 셋

    grade_forecast >> observe_relations >> notify_slack

- `grade_forecast` — **LLM이 없다.** 오늘 확정 종가로 미채점 전망을 채점한다. 날짜 상한이
  없어 이 DAG가 며칠 죽어 있었으면 그 사이 전망도 여기서 회수된다.
- `observe_relations` — 오늘 무엇이 코스피를 움직였는지를 요인별 엣지로 Neo4j에 쓰고,
  새 메모를 남기고, 활성 메모를 하나씩 유지·삭제 판정한다. 셋이 한 LLM 호출이다.
- `notify_slack` — 채점 줄과 관찰·메모 변화를 보낸다.

**채점을 관찰과 나눈 이유는 실패의 성격이 다르기 때문이다.** 채점은 SQL이라 종가만 있으면
언제나 되고, 관찰은 모델을 부른다. 한 태스크로 묶으면 모델이 죽은 날 채점도 안 남는다.

## 메모의 수명

모델이 정하는 것은 `keep`/`drop` 하나뿐이다. 나머지 셋은 코드가 상한으로 정한다 —
나이 상한(20일), 두 번 연속 미검토, 활성 수 상한(20). 이 셋이 **"LLM 출력이 LLM 입력이 되어
스스로를 강화하는" 순환을 끊는 자리다.** 메모는 "요즘 볼 것"이지 규칙이 아니고, 규칙이
되려면 관계 엣지로 쌓여 가중치가 되어야 한다.

내린 메모는 노드를 지우지 않고 `retired_on`과 이유를 남긴다.

## 왜 19:00인가

`kis_index_daily`(18:20)가 오늘 확정 일봉을 넣고 투자자별 매매가 18:10에 확정된 뒤다.
둘 중 하나라도 밀리면 준비 검사가 막고 재시도가 기다린다.

## 실패와 재시도

**단일 요청 형태다** — 관찰 하나가 결과 전부라 예외를 그대로 올린다.

- 오늘 확정 종가 없음 → `AirflowSkipException`. 휴장이거나 수집 전이고 어느 쪽이든 관찰할
  것이 없다
- **오늘 |등락률| ≥ 0.5%인데 관찰이 0건** → `KospiError`로 죽인다. 움직인 날에 이유가 없는
  것은 답이 아니다(조용한 성공을 만들지 않는다). 0.5% 미만이면 0건을 받아들이고 원장에 남긴다
- `RetryableLlmError`·`ConnectionError` → 그대로 올려 Airflow가 재시도
- `GraphError` → `AirflowFailException`. 그래프가 거절한 것은 다시 불러도 같다

## params

| 이름 | 기본값 | 뜻 |
| --- | --- | --- |
| `run_date` | `null` | 대상 세션 날짜(YYYY-MM-DD). 비우면 logical time의 KST 날짜 |
| `notify` | `true` | Slack 발송. 끄면 저장까지만 한다 — **과거 날짜를 백필할 때 끈다** |

## 필요한 환경

`kospi_forecast_daily`와 같다. **`NEO4J_*`가 여기서 가장 중요하다** — 쓰기가 이 DAG에만 있다.
"""

from typing import Any

import pendulum
from airflow.sdk import dag, task

from modules.kospi import common, review
from modules.utility import KST_TIMEZONE


@dag(
    dag_id="kospi_review_daily",
    dag_display_name="📈 코스피 관찰 · 장후 (LLM)",
    description="오늘 전망을 채점하고, 무엇이 코스피를 움직였는지를 관계 그래프에 쌓고, 메모를 정리한다.",
    schedule="0 19 * * 1-5",  # KST 평일 19:00 = UTC 월~금 10:00
    start_date=pendulum.datetime(2026, 9, 3, tz=KST_TIMEZONE),  # KST 2026-09-03 00:00 = UTC 2026-09-02 15:00
    catchup=False,
    max_active_runs=1,
    default_args=common.DEFAULT_ARGS,
    params={**common.run_date_param(), **common.notify_param()},
    doc_md=__doc__,
    tags=["kospi", "llm", "market", "korea"],
)
def kospi_review_daily():
    @task(task_display_name="전망 채점")
    def grade_forecast() -> dict[str, Any]:
        return review.grade()

    @task(task_display_name="관계 관찰·메모 정리", execution_timeout=common.BUILD_TIMEOUT)
    def observe_relations(graded: dict[str, Any]) -> dict[str, Any]:
        # `graded`는 순서를 만드는 인자다. 채점이 끝난 뒤라야 관찰이 오늘 성적을 함께 본다.
        return review.observe()

    @task(task_display_name="Slack 발송")
    def notify_slack(observed: dict[str, Any]) -> str:
        return common.notify_review(observed)

    notify_slack(observe_relations(grade_forecast()))


kospi_review_daily = kospi_review_daily()
