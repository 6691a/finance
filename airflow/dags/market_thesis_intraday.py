"""장중 전망 — 지금 이 가격에서 마감까지 어느 방향으로 갈 것 같은가.

`docs/analysis/market-thesis/9-intraday.md`가 설계다. 장전 08:35 한 번으로는 09:00 개장 뒤에 나온
공시·기사·수급·가격 움직임이 그날 판단에 전혀 반영되지 않는다. 다음 판단이 다음 날 아침이라
"시장 변화를 따라잡지 못한다"가 이 DAG를 만든 이유다.

**목적은 정확도다 — 다만 개별 추론이 아니라 판(版)의 정확도다.** "어떤 정보를 근거로 어떤
결론을 냈다"를 먼저 남기고 채점이 쌓이면 model·prompt 판별로 비교한다. 채점하는 쪽은
`market_thesis_review`다.

## 장전과 무엇이 다른가

**기준가가 전일 종가가 아니라 지금 가격이다.** 10:35 슬롯은 "10:35 가격에서 마감까지"를
맞힌다. 이미 오른 만큼은 예측에 안 들어가고, 그래서 채점 조회도 갈린다
(`thesis_store.intraday_horizon_returns`). 관측 상태도 확정 종가가 아니라 봉에서 만든다 —
`stock_investor_trade_daily`는 18:10에 들어오고 KIS가 15:40 전 당일 조회를 거절한다.

**오늘 앞 슬롯을 되짚어 프롬프트에 싣는다.** 아침 예측이 지금 맞고 있는지가 다음 판단의
재료다. `thesis_outcome`에 저장하지는 않는다 — 이유는 `thesis_state.SameDayThesis`.

## 왜 10:35 / 12:35 / 14:35 / 15:00 인가

문서 평가(`document_assessment_hourly`, 매시 25분)가 끝난 뒤라야 직전 정시 수집분(:05)이
근거 후보에 든다. 장전이 08:35인 것과 같은 이유이고, 그래서 세 슬롯이 :35다.
15:00만 예외다 — 그 슬롯의 목적이 "마감 30분 전"이라 시각이 먼저 정해진다(문서는 14:25
평가분까지 본다).

**시각은 전제이지 보장이 아니다.** 그래서 `build_thesis` 안에 readiness guard가 있다.
봉이 아예 없으면 수집이 멈춘 것이고, 오래된 봉만 있으면 지연이다. 둘 다 `ThesisNotReady`로
올려 Airflow 재시도에 맡긴다.

## 왜 슬롯 넷이 DAG 하나인가

저장소 규칙은 "슬롯·모드로 갈리는 DAG는 나눈다"이고, 2026-08-21에 `market_thesis_analysis`를
장전·장후로 가른 것이 그 규칙의 출처다. **그때 문제는 시각이 여럿인 것이 아니라 앞단
데이터와 실패 성격이 다른 둘을 시계로 뭉뚱그린 것이었다.** 장중 넷은 같은 봉과 같은 문서
평가를 같은 이유로 기다린다 — `slack_kr_market_briefing`이
`MultipleCronTriggerTimetable` 하나로 남아 있는 것과 같은 경우다.

그때 실제로 사고를 낸 것("`logical_date`가 없는 수동 실행이 벽시계로 떨어져 조용히 다른
모드를 돈다")은 `thesis_intraday.resolve_slot`이 막는다. Param도 `logical_date`도 없으면
**실패시킨다.** 조용히 다른 슬롯을 도는 것보다 안 도는 편이 낫다.

## 태스크 둘

    build_thesis >> notify_slack

**채점·해설 태스크는 여기 없다.** 채점은 확정 종가가 필요해 18:10 전에는 설 수 없고,
사후 해설은 "그 이유가 이후 보도로 지지됐나"를 묻는데 두 시간으로는 새 문서가 몇 건뿐이라
`unresolved`가 거의 확정이다. 둘 다 `market_thesis_review`(20:30)가 하루 한 번에 몰아서
하고, 이 DAG가 만든 추론도 그때 대상에 든다.

## 실패와 재시도 — 장전·장후와 다르게 준다

공유 `DEFAULT_ARGS`(재시도 3 × 10분)에 `BUILD_TIMEOUT`(30분)이면 최악 두 시간이라
10:35 실행이 12:35 실행을 막는다(`max_active_runs=1`). 장중은 재시도 1 × 5분에
`execution_timeout` 15분으로 최악 40분에 묶는다.

근거는 수집 DAG의 판정과 같다 — **다음 슬롯이 두 시간 뒤에 같은 창을 다시 본다.**
실패한 슬롯을 오래 붙들 값어치가 없고, 그 슬롯은 없던 것으로 남는다(추론 재시도·재평가를
만들지 않는다는 원칙 그대로).

- `LlmError`·`ThesisError` → `AirflowFailException`(재시도해도 같다)
- `RetryableLlmError`·`ConnectionError`·`ThesisNotReady` → 그대로 올려 Airflow가 재시도
- 휴장일 → `AirflowSkipException`. 달력을 모르면 돌린다
- `SlackError` → `AirflowFailException`. **발송은 at-least-once다**(장전 DAG와 같다)

## 첫 성공본은 불변이다

같은 (날짜, 슬롯, 대상)에 추론 행이 이미 있으면 **LLM을 다시 부르지 않는다.** 재실행은
기존 행을 읽어 발송으로 넘길 뿐이다.

## params

| 이름 | 기본값 | 뜻 |
| --- | --- | --- |
| `run_date` | `null` | 대상 세션 날짜(YYYY-MM-DD). 비우면 logical time의 KST 날짜 |
| `run_slot` | `null` | 장중 슬롯 넷 중 하나. 비우면 스케줄된 시각으로 정한다. **수동 실행은 반드시 고른다** |

## 필요한 환경

- `XAI_API_KEY`. 어떤 모델을 부를지는 `modules/llm.py`의 `thesis_model()`이 코드로 정하고
  키는 그 LangChain 클래스가 자기 이름으로 읽는다.
- `SLACK_BOT_TOKEN`, `SLACK_CHANNEL_MARKET` — 시장 브리핑과 같은 채널을 재사용한다.
- `CONNECTION_ID`가 가리키는 Airflow 연결.
- `LANGSMITH_TRACING`을 켜면 프롬프트와 툴 결과(문서 제목·공시명)가 외부로 나간다.
"""

from datetime import timedelta
from typing import Any

import pendulum
from airflow.sdk import dag, task
from airflow.timetables.trigger import MultipleCronTriggerTimetable

from modules import thesis_common, thesis_intraday
from modules.utility import KST_TIMEZONE

# **`thesis_state.INTRADAY_SLOT_TIMES`와 같아야 한다.** 어긋나면 `resolve_slot`이 슬롯을
# 못 찾아 실행이 죽는다 — 조용히 다른 슬롯으로 떨어지는 것보다 낫다. 테스트가 둘을 대조한다.
SCHEDULE = MultipleCronTriggerTimetable(
    "35 10 * * 1-5",  # KST 평일 10:35 = UTC 월~금 01:35
    "35 12 * * 1-5",  # KST 평일 12:35 = UTC 월~금 03:35
    "35 14 * * 1-5",  # KST 평일 14:35 = UTC 월~금 05:35
    "0 15 * * 1-5",  # KST 평일 15:00 = UTC 월~금 06:00
    timezone=KST_TIMEZONE,
)

# 장전·장후와 다르다. 근거는 모듈 docstring의 "실패와 재시도" 절에 있다.
DEFAULT_ARGS: dict[str, Any] = {"retries": 1, "retry_delay": timedelta(minutes=5)}
BUILD_TIMEOUT = timedelta(minutes=15)


@dag(
    dag_id="market_thesis_intraday",
    dag_display_name="🧠 시장 추론 · 장중 전망 (LLM)",
    description="장중 네 시각에 지금 가격 기준의 방향을 확률로 적고 근거와 함께 Slack에 보낸다.",
    schedule=SCHEDULE,
    start_date=pendulum.datetime(2026, 8, 26, tz=KST_TIMEZONE),  # KST 2026-08-26 00:00 = UTC 2026-08-25 15:00
    catchup=False,
    max_active_runs=1,
    default_args=DEFAULT_ARGS,
    params={**thesis_common.run_date_param(), **thesis_intraday.run_slot_param()},
    doc_md=__doc__,
    tags=["thesis", "llm", "market", "korea"],
)
def market_thesis_intraday():
    @task(task_display_name="추론 생성", execution_timeout=BUILD_TIMEOUT)
    def build_thesis() -> dict[str, Any]:
        # XCom 경계다. Airflow가 Pydantic 모델을 어떻게 직렬화하는지에 기대지 않는다.
        return thesis_intraday.build().model_dump(mode="json")

    @task(task_display_name="Slack 발송")
    def notify_slack(built: dict[str, Any]) -> str:
        return thesis_common.notify_slack(built)

    notify_slack(build_thesis())


market_thesis_intraday = market_thesis_intraday()
