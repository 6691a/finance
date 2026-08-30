"""주간 사후 인과 그래프 — 한 주가 끝나고 반응이 확정된 뒤에 그 주를 되짚는다.

설계는 `docs/analysis/market-causal-graph.md`다. `market_thesis_*`와 목적이 다르다 —
저쪽은 슬롯마다 확률을 남기고 채점받는 **예측**이고, 여기는 "무엇이 어떤 경로로 무엇에
닿았나"를 노드·엣지로 쪼개 **누적**한다.

## 왜 W+2 월요일인가

대상 주 `W`의 사건이 시장에 닿는 데 시간이 걸린다. `W+1` 한 주가 반응이 나오는 창이고
(T+1 … T+5), `W+2` 월요일이면 그 셋이 전부 확정돼 있다. 그래서 **채점 DAG이 따로 없다** —
`thesis_outcome`처럼 지평마다 나중에 채우는 것이 아니라 저장할 때 이미 다 안다.

기준 시각은 실행 시각이 아니라 **`W+1` 금요일 KST 15:40**이다(`causal.domain.window_for`).
주말에 들어온 문서를 그 주 판단에 싣지 않기 위해서다. 다만 **실현 등락에는 그 cutoff를
걸지 않는다** — 그것은 일부러 미래를 보는 값이고, 반응 주에 휴장이 있으면 T+5가 cutoff
뒤로 밀려 대상이 통째로 빠진다.

## 왜 태스크가 하나인가

후보 조립부터 저장까지 한 흐름이고, 재시도가 후보 조립을 다시 해도 `as_of_at`이 같아 같은
결과가 나온다. 태스크를 나누면 XCom으로 후보 수십 건을 넘기게 되는데 얻는 것이 없다.

## 실패와 재시도

**단일 요청 형태다**(저장소의 세 형태 중 하나). 대상 열한 개를 대화 하나가 한 번에 보므로
항목별로 나눌 것이 없고, 모듈이 올린 예외 종류를 여기서 가른다.

- `LlmError`·`PromptError`·`VocabularyDriftError`·`IncompleteReturnsError`는 다시 불러도
  같은 결과라 `AirflowFailException`이다. 어휘 드리프트는 **정규화가 깨졌다는 신호**라
  조용히 넘어가면 다음 주에 어휘가 두 배가 된다. 실현 등락 누락은 **아직 돌 때가 아니라는
  신호**다 — T+5 일봉이 들어온 뒤(KST 18:20 이후) 손으로 다시 돌린다.
- `ConnectionError`는 그대로 올려 Airflow가 재시도하게 둔다.

**그 주에 경로가 이미 있으면 skip이 아니라 성공이다.** 재실행이 정상 흐름이라 매번 노란
태스크를 만들 이유가 없다.

## Neo4j 투영

`sync_graph`가 그 주 몫을 Neo4j에 민다. **Postgres가 원본이고 Neo4j는 파생물이다** —
설계는 [4-graph.md](../../docs/analysis/market-thesis/4-graph.md)다.

- **`NEO4J_URI`가 비어 있으면 `AirflowSkipException`이다.** 인스턴스가 서기 전에도
  `build_causal_graph`는 정상이어야 하고, 설정 누락으로 매주 빨간 태스크를 만들면 진짜
  실패가 묻힌다. URI가 있는데 접속이 안 되는 것은 skip이 아니라 `ConnectionError` 재시도다.
- **두 스토어를 한 트랜잭션에 넣지 않는다.** Neo4j 쓰기가 실패해도 Postgres는 이미 커밋돼
  있다. Slack 발송 실패가 DB 쓰기를 되돌리지 않는 것과 같은 이유다.
- **`sync_only`를 주면 `build_causal_graph`를 건너뛰고 저장된 주 전부를 다시 민다.**
  초기 적재와 밀린 주 복구가 이것 하나다. MERGE라 몇 번을 돌려도 같은 그래프다.
"""

import os
from datetime import UTC, datetime, timedelta
from typing import Any

import pendulum
from airflow.sdk import Param, dag, task
from airflow.sdk.exceptions import AirflowFailException, AirflowSkipException

from modules.utility import KST_TIMEZONE

DEFAULT_ARGS: dict[str, Any] = {"retries": 3, "retry_delay": timedelta(minutes=10)}

# 대화 하나가 대상 열한 개를 한 번에 본다. 교정이 붙으면 왕복이 둘이다.
BUILD_TIMEOUT = timedelta(minutes=20)

# 주 하나가 경로 수십 개다. 왕복은 트랜잭션 하나뿐이라 오래 걸릴 것이 없다.
SYNC_TIMEOUT = timedelta(minutes=10)

WEEK_START_PARAM = "week_start"
SYNC_ONLY_PARAM = "sync_only"


@dag(
    dag_id="market_causal_weekly",
    dag_display_name="🕸️ 주간 인과 그래프 (LLM)",
    description="한 주가 끝나고 반응이 확정된 뒤 사건·전달 경로·대상을 그래프로 쪼개 누적한다.",
    schedule="0 7 * * 1",  # KST 월 07:00 = UTC 일 22:00
    start_date=pendulum.datetime(2026, 9, 1, tz=KST_TIMEZONE),  # KST 2026-09-01 00:00 = UTC 2026-08-31 15:00
    catchup=False,
    max_active_runs=1,
    default_args=DEFAULT_ARGS,
    params={
        WEEK_START_PARAM: Param(
            None,
            type=["null", "string"],
            format="date",
            title="대상 주의 월요일",
            description=(
                "YYYY-MM-DD이고 **반드시 월요일**이다. 비우면 실행 주의 2주 전 월요일. 지난 주를 다시 만들 때만 준다."
            ),
        ),
        SYNC_ONLY_PARAM: Param(
            False,
            type="boolean",
            title="Neo4j 재동기화만",
            description=(
                "켜면 LLM을 부르지 않고 **저장된 주 전부**를 Neo4j에 다시 민다. "
                "초기 적재와 밀린 주 복구용이다. MERGE라 몇 번을 돌려도 같은 그래프다."
            ),
        ),
    },
    doc_md=__doc__,
    tags=["causal", "llm", "market", "korea"],
)
def market_causal_weekly():
    @task(task_display_name="인과 그래프 생성", execution_timeout=BUILD_TIMEOUT)
    def build_causal_graph(**context: Any) -> dict[str, Any]:
        from modules.causal import run
        from modules.causal.run import IncompleteReturnsError
        from modules.causal.store import VocabularyDriftError
        from modules.llm import LlmError
        from modules.prompt import PromptError

        params = context.get("params") or {}
        if params.get(SYNC_ONLY_PARAM):
            # 재동기화만 하는 실행이다. 모델을 부르지 않는다.
            raise AirflowSkipException("sync_only run; skipping the model")

        # **`logical_date`는 스케줄된 실행에만 붙는다.** `airflow dags trigger`로 부르면
        # context에 아예 없어서 직접 인덱싱하면 태스크가 시작하자마자 죽는다. 없으면 벽시계를
        # 쓰고, 그 값은 Param이 있으면 어차피 안 본다(`domain.resolve_week`).
        logical_date = context.get("logical_date") or datetime.now(UTC)
        dag_run = context.get("dag_run")
        try:
            return run.build_weekly_graph(
                logical_date=logical_date,
                week_start_param=params.get(WEEK_START_PARAM),
                dag_run_id=getattr(dag_run, "run_id", ""),
            )
        except (
            LlmError,
            PromptError,
            VocabularyDriftError,
            IncompleteReturnsError,
            ValueError,
        ) as error:
            # 설정·프롬프트·정규화 문제다. 다시 불러도 같은 답이 온다.
            raise AirflowFailException(str(error)) from error

    @task(
        task_display_name="Neo4j 투영",
        execution_timeout=SYNC_TIMEOUT,
        # 기본값 `all_success`는 upstream이 skip이면 downstream도 skip이다. `sync_only`
        # 실행에서 `build_causal_graph`가 skip되어도 이 태스크는 돌아야 한다.
        trigger_rule="none_failed",
    )
    def sync_graph(summary: dict[str, Any] | None, **context: Any) -> dict[str, Any]:
        from contextlib import closing
        from datetime import date

        from modules import graph
        from modules.causal.run import connection

        uri = os.environ.get("NEO4J_URI")
        if not uri:
            # 인스턴스가 서기 전에도 앞 태스크는 정상이어야 한다. 설정 누락으로 매주
            # 빨간 태스크를 만들면 진짜 실패가 묻힌다.
            raise AirflowSkipException("NEO4J_URI is not set; skipping the projection")
        user = os.environ.get("NEO4J_USER")
        password = os.environ.get("NEO4J_PASSWORD")
        if not user or not password:
            raise AirflowFailException("NEO4J_USER and NEO4J_PASSWORD are required")

        sync_only = bool((context.get("params") or {}).get(SYNC_ONLY_PARAM))
        with closing(connection()) as conn:
            if sync_only:
                weeks = graph.stored_weeks(conn)
            elif summary and summary.get("week_start"):
                weeks = [date.fromisoformat(summary["week_start"])]
            else:
                # upstream이 요약을 안 남겼는데 `sync_only`도 아니다. 어느 주를 밀지 모르는
                # 채로 도는 것보다 죽는 편이 낫다.
                raise AirflowFailException("no week to project; pass sync_only to backfill")

            projected = 0
            for week in weeks:
                paths, steps = graph.read_week(conn, week)
                if not paths:
                    continue
                payload = graph.project(paths, steps)
                graph.write_graph(uri, (user, password), payload)
                projected += payload.edge_count

        return {"weeks": [week.isoformat() for week in weeks], "edges": projected}

    sync_graph(build_causal_graph())


market_causal_weekly = market_causal_weekly()
