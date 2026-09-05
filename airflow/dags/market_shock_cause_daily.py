"""급변의 원인을 사후에 찾는다 — 최대 3영업일, 매일 아침 한 번.

설계는 `docs/analysis/market-shock-capture.md` §6~§7이다. 포착(`market_shock_intraday`)이
남긴 행 중 원인이 빈 것을 집는다.

## 왜 장중이 아닌가

**그 시각에 우리가 가진 문서로는 답이 안 나온다.** 2026-09-03 사건에서 장중 수집 문서
96건 중 그 급락을 설명한 것이 0건이었고, 기사 발행에서 우리가 평가를 마치기까지 중앙값
58분이 걸린다. 물으면 모델이 관계없는 같은 날 기사를 붙여 지어낸다.

## 왜 3영업일인가

대개 그날 마감~다음날 아침에 나오지만 **안 나올 수도 있다.**

    D    포착
    D+1  08:00  1차 시도 — 전날 마감 시황 + 밤사이 기사 + 검색
    D+2  08:00  2차 시도 — 그 사이 새로 들어온 것이 더해진다
    D+3  08:00  3차 시도 — 마지막
    이후        unknown으로 닫는다

**날짜를 우리가 세지 않는다.** `market_session.effective_open_day`가 판정의 주인이다.

## 문서 창의 하한이 포착 시각이다

재료는 대개 며칠 전부터 있다(2026-09-03이면 09-02의 우에다 발언). 그 이전 문서를 함께
주면 모델이 **그것을 그날 그 시각의 방아쇠로 지목한다** — 그럴듯하지만 틀린 답이다.
조회가 이미 그 창이라 검증에서 다시 보지 않는다.

## 검색은 툴이 아니라 목록이다

코드가 먼저 Tavily에 묻고 결과를 프롬프트에 싣는다. 모델은 **준 목록의 번호로만** 인용할
수 있어 문서와 같은 검증이 그대로 산다. 받은 결과는 전부 `market_shock_search_hit`에
남는다 — 밖의 페이지는 바뀌고 사라지므로 우리가 본 스냅샷이 근거의 원본이다.

## 태스크 둘

    resolve_causes >> close_expired

닫는 일을 나눈 이유는 실패의 성격이 다르기 때문이다. 원인 찾기는 모델과 검색을 부르고,
닫기는 SQL이라 언제나 된다. 한 태스크로 묶으면 모델이 죽은 날 기한 지난 것도 안 닫힌다.

## 실패와 재시도

**항목별 실패 수집이다.** 이벤트 하나가 실패해도 나머지를 처리하고, **하나라도 실패하면
태스크를 죽인다** — 다음 run이 24시간 뒤라 그때까지 기다릴 수 없고, 시도 기회가 셋뿐이라
한 번을 조용히 잃으면 전체의 3분의 1이다.

- `LlmError`·`SearchError` → `AirflowFailException`
- `RetryableLlmError`·`ConnectionError`·`URLError` → 그대로 올려 Airflow가 재시도
- 대상 0건 → 정상 성공. 급변이 없는 날이 대부분이다
- 달력이 안 채워져 기한을 못 구함 → 그 이벤트는 건너뛰고 다음 실행이 집는다.
  **실패로 세지 않는다**
- `found=false` → 정상 결과다. `pending`을 유지하고 다음날이 다시 본다

## params

| 이름 | 기본값 | 뜻 |
| --- | --- | --- |
| `max_events` | `20` | 한 실행이 처리할 이벤트 수. 폭주만 막는다 |
| `search` | `true` | 외부 검색. 끄면 우리 문서만 본다 |
| `notify` | `true` | Slack 발송. 끄면 저장까지만 한다 |

## 필요한 환경

- `OPENAI_API_KEY` — 어떤 모델을 부를지는 `modules/llm.py`의 `shock_model()`이 코드로
  정하고 키는 그 LangChain 클래스가 자기 이름으로 읽는다.
- `TAVILY_API_KEY` — 없으면 검색 없이 우리 문서만 본다(경고 뒤 계속).
- `SLACK_BOT_TOKEN`, `SLACK_CHANNEL_MARKET`.
- `CONNECTION_ID`가 가리키는 Airflow 연결.
- `LANGSMITH_TRACING`을 켜면 프롬프트와 문서 본문이 LangSmith로 나간다.
"""

import logging
import os
from contextlib import closing
from datetime import UTC, datetime, timedelta
from urllib.error import URLError

import pendulum
from airflow.providers.postgres.hooks.postgres import PostgresHook
from airflow.sdk import Param, dag, get_current_context, task
from airflow.sdk.exceptions import AirflowFailException
from pydantic import SecretStr

from modules.llm import LlmError
from modules.shock.domain import (
    CAUSE_BUSINESS_DAYS,
    CAUSE_PROMPT_VERSION,
    MAX_DOCUMENTS,
    MAX_EVENTS_PER_RUN,
    MAX_SEARCH_RESULTS,
    CauseInput,
    SearchRow,
)
from modules.shock.search import SearchError, TavilySearch, build_queries, collect
from modules.shock.store import ShockStore
from modules.utility import CONNECTION_ID, KST_TIMEZONE, atomic

logger = logging.getLogger(__name__)

MAX_EVENTS_PARAM = "max_events"
SEARCH_PARAM = "search"
NOTIFY_PARAM = "notify"


def _slack_settings() -> tuple[SecretStr, str]:
    token = os.environ.get("SLACK_BOT_TOKEN")
    channel = os.environ.get("SLACK_CHANNEL_MARKET")
    if not token or not channel:
        raise AirflowFailException("SLACK_BOT_TOKEN and SLACK_CHANNEL_MARKET are required")
    return SecretStr(token), channel


def _search_client(enabled: bool) -> TavilySearch | None:
    """키가 없으면 검색 없이 간다. **실패가 아니다** — 우리 문서만으로 푸는 날도 있다."""
    if not enabled:
        return None
    key = os.environ.get("TAVILY_API_KEY")
    if not key:
        logger.warning("TAVILY_API_KEY is not set; the cause analysis sees our documents only")
        return None
    return TavilySearch(SecretStr(key))


@dag(
    dag_id="market_shock_cause_daily",
    dag_display_name="🔎 급변 원인 분석 (LLM + 검색)",
    description="포착된 급변의 원인을 다음 영업일 아침부터 최대 3영업일 동안 문서와 검색으로 찾는다.",
    schedule="0 8 * * 1-5",  # KST 평일 08:00 = UTC 월~금 23:00(전날)
    start_date=pendulum.datetime(2026, 9, 5, tz=KST_TIMEZONE),  # KST 2026-09-05 00:00 = UTC 2026-09-04 15:00
    catchup=False,
    max_active_runs=1,
    default_args={"retries": 2, "retry_delay": timedelta(minutes=5)},
    params={
        MAX_EVENTS_PARAM: Param(
            MAX_EVENTS_PER_RUN,
            type="integer",
            minimum=1,
            maximum=100,
            title="한 실행이 볼 이벤트 수",
            description="급변이 한 달 8.6건이라 평소 0~2건이다. 이 값은 폭주만 막는다.",
        ),
        SEARCH_PARAM: Param(
            True,
            type="boolean",
            title="외부 검색",
            description="끄면 우리가 수집한 문서만 본다. 검색이 몇 %를 푸는지 견줄 때 끈다.",
        ),
        NOTIFY_PARAM: Param(
            True,
            type="boolean",
            title="Slack 발송",
            description="끄면 저장까지만 한다. 과거 이벤트를 손으로 채울 때 끈다.",
        ),
    },
    doc_md=__doc__,
    tags=["market", "shock", "llm", "slack"],
)
def market_shock_cause_daily():
    @task(task_display_name="원인 찾기")
    def resolve_causes() -> int:
        # 흐름 클래스를 모듈 수준에서 올리면 DagBag이 LangChain 무게를 문다.
        from modules.shock.cause import ShockCauseBuilder
        from modules.shock.render import render_cause_blocks, render_cause_text
        from modules.slack import SlackClient

        context = get_current_context()
        params = dict(context.get("params") or {})
        max_events = int(params.get(MAX_EVENTS_PARAM) or MAX_EVENTS_PER_RUN)
        notify = bool(params.get(NOTIFY_PARAM, True))
        search_client = _search_client(bool(params.get(SEARCH_PARAM, True)))

        now = datetime.now(UTC)
        today_kst = now.astimezone(KST_TIMEZONE).date()

        builder = ShockCauseBuilder()
        resolved = 0
        failures: list[str] = []

        with closing(PostgresHook(postgres_conn_id=CONNECTION_ID).get_conn()) as connection:
            store = ShockStore(connection)
            pending = store.pending_causes(today=today_kst, limit=max_events)
            if not pending:
                logger.info("No shock event is waiting for a cause")
                return 0

            for event in pending:
                try:
                    if _resolve_one(
                        store=store,
                        connection=connection,
                        builder=builder,
                        search_client=search_client,
                        event=event,
                        now=now,
                        notify=notify,
                        slack=SlackClient,
                        render_text=render_cause_text,
                        render_blocks=render_cause_blocks,
                    ):
                        resolved += 1
                except (LlmError, SearchError) as error:
                    # 되돌릴 수 없다 — 설정·키·계약 문제라 다시 불러도 같다.
                    failures.append(f"{event.id}({type(error).__name__}: {error})")
                except (ConnectionError, URLError):
                    # 재시도할 값어치가 있다. 그대로 올려 Airflow가 판단한다.
                    raise

        if failures:
            # 하루 한 번 도는 확정 작업이다. 다음 run이 24시간 뒤라 조용히 넘기지 않는다.
            raise AirflowFailException(f"{len(failures)} cause analysis failed; {'; '.join(failures)}")

        logger.info("Resolved %s of %s pending cause(s)", resolved, len(pending))
        return resolved

    @task(task_display_name="기한 지난 것 닫기")
    def close_expired() -> int:
        """3영업일을 다 쓴 것을 `unknown`으로 닫는다. **LLM이 없다.**

        원인 찾기와 나눈 이유는 실패의 성격이 다르기 때문이다 — 이쪽은 SQL이라 언제나 되고,
        한 태스크로 묶으면 모델이 죽은 날 기한 지난 것도 안 닫힌다.
        """
        from modules.shock.render import render_unknown_blocks
        from modules.slack import SlackClient

        context = get_current_context()
        params = dict(context.get("params") or {})
        notify = bool(params.get(NOTIFY_PARAM, True))

        now = datetime.now(UTC)
        today_kst = now.astimezone(KST_TIMEZONE).date()

        with closing(PostgresHook(postgres_conn_id=CONNECTION_ID).get_conn()) as connection:
            store = ShockStore(connection)
            with atomic(connection):
                closed = store.close_expired_causes(today=today_kst, resolved_at=now)

        if not closed:
            return 0

        logger.info("Closed %s expired cause(s) as unknown", len(closed))
        if notify:
            token, channel = _slack_settings()
            client = SlackClient(token)
            for event in closed:
                client.post_message(
                    channel,
                    text=f"🔎 {event.session_date} 코스피 급변의 원인 — 못 찾았다",
                    blocks=render_unknown_blocks(
                        detected_at=event.detected_at,
                        direction=event.direction,
                        attempts=event.attempts,
                        deadline=str(event.session_date),
                    ),
                )
        return len(closed)

    resolve_causes() >> close_expired()


def _resolve_one(
    *,
    store,
    connection,
    builder,
    search_client,
    event,
    now: datetime,
    notify: bool,
    slack,
    render_text,
    render_blocks,
) -> bool:
    """이벤트 하나. 원인을 찾아 저장했으면 `True`."""
    deadline = event.deadline or store.nth_open_day(event.session_date, CAUSE_BUSINESS_DAYS)
    if deadline is None:
        # 달력이 아직 그날까지 안 채워졌다. 없는 날짜를 지어내지 않는다.
        logger.info("event %s has no deadline yet; the calendar has not reached it", event.id)
        return False

    # **모델을 부르기 전에 커밋한다.** 죽은 실행이 안 세어지면 "안 돌았다"와 "돌다 죽었다"를
    # 못 가른다. 별도 원장 표를 안 두는 대신 이 칸이 그 일을 한다.
    with atomic(connection):
        attempt, deadline = store.start_attempt(event.id, deadline=deadline)

    documents = store.documents_after(event_at=event.detected_at, as_of_at=now, limit=MAX_DOCUMENTS)

    hits = []
    if search_client is not None:
        queries = build_queries(
            detected_at=event.detected_at,
            direction=event.direction,
            peers=list(event.peers),
        )
        hits = collect(search_client, queries, published_after=event.detected_at)[:MAX_SEARCH_RESULTS]
        if hits:
            with atomic(connection):
                store.save_search_hits(event.id, hits, attempt=attempt, retrieved_at=now)

    search_rows = tuple(
        SearchRow(
            index=index,
            title=hit.title,
            url=hit.url,
            publisher=hit.publisher,
            snippet=hit.snippet,
            published_at=hit.published_at,
        )
        for index, hit in enumerate(hits, start=1)
    )

    payload = CauseInput(
        shock_event_id=event.id,
        symbol=event.symbol,
        direction=event.direction,
        detected_at=event.detected_at,
        extreme_at=event.extreme_at,
        extreme_price=event.extreme_price,
        trigger_price=event.trigger_price,
        move_pct=event.move_pct,
        window_change_pct=event.window_change_pct,
        peers=event.peers,
        attempt=attempt,
        as_of_at=now,
        deadline=deadline,
        documents=tuple(documents),
        search_hits=search_rows,
    )

    answer, rejected = builder.run(payload)
    logger.info(
        "event %s attempt %s: found=%s docs=%s search=%s rejected=%s",
        event.id,
        attempt,
        answer.found,
        len(answer.document_ids),
        len(answer.search_indexes),
        len(rejected),
    )
    if not answer.found:
        # 정상 결과다. pending을 유지하고 다음날이 다시 본다.
        return False

    cited = tuple(row for row in search_rows if row.index in set(answer.search_indexes))
    with atomic(connection):
        written = store.resolve_cause(
            event.id,
            cause_text=answer.cause_text,
            cause_kind=answer.cause_kind.value,
            document_ids=list(answer.document_ids),
            search_used=bool(cited),
            weak=bool(rejected),
            prompt_version=CAUSE_PROMPT_VERSION,
            llm_model=builder.model_name,
            resolved_at=now,
        )
        if written and cited:
            store.mark_search_cited(event.id, [row.url for row in cited])

    if not written:
        # 다른 실행이 먼저 닫았다. 첫 성공본이 불변이라 덮어쓰지 않고 알림도 안 보낸다.
        logger.info("event %s was already resolved by another run", event.id)
        return False

    if notify:
        token, channel = _slack_settings()
        slack(token).post_message(
            channel,
            text=render_text(payload, answer),
            blocks=render_blocks(payload, answer, cited_search=cited, weak=bool(rejected)),
        )
    return True


market_shock_cause_daily = market_shock_cause_daily()
