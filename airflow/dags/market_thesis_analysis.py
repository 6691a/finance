"""시장 추론을 만들고 채점하고 되돌아본 뒤 Slack에 보낸다.

`docs/market-thesis/`의 3·5단계다. 분봉·공시·평가된 문서·매크로 시세가 전부 쌓이고 있지만
그것들을 놓고 "그래서 왜 움직였나 / 오늘 어떻게 움직일 것 같나"를 말하는 층이 없었다.

**맞고 틀림이 목적이 아니다.** 정답은 시간이 지나야 알고 맞추기도 어렵다. 목적은 "어떤
정보를 근거로 어떤 결론을 냈다"가 기록으로 남는 것이다. 채점은 그 위에 나중에 얹힌다.

## 두 슬롯이 곧 두 종류다

- **장전 08:35** — 오늘 어느 방향으로 갈 것 같은지(forecast). 이것만 채점 대상이다.
- **장후 20:30** — 오늘 왜 그렇게 움직였는지(review). 이미 일어난 일의 해석이라 예측이 아니다.

시각은 앞단 DAG의 데이터가 준비되는 때에 맞춘다. 장전은 문서 평가(매시 25분)가 끝난 뒤라야
밤사이 기사가 전부 근거 후보에 든다. 장후는 확정 종가(18:10)와 지수 마감 봉(16:00) 뒤이고,
선행 DAG 재시도 여유를 두어 20:30이다.

**시각은 전제이지 보장이 아니다.** 두 선행 DAG 모두 재시도가 있어 그 시각을 넘길 수 있다.
그래서 `build_thesis` 맨 앞에 readiness guard가 있다.

## 첫 성공본은 불변이다

같은 (날짜, 슬롯, 대상)에 추론 행이 이미 있으면 **LLM을 다시 부르지 않는다.** LLM은 재호출
마다 답이 달라서 덮어쓰면 최초 판단이 사라진다. 재실행은 기존 행을 읽어 다음 태스크로
넘길 뿐이다. 채점과 해설도 같다 — 이미 매긴 점수와 이미 쓴 해설을 SQL의 `WHERE`가 지킨다.

## 태스크 넷

    build_thesis >> grade_followups >> narrate_followups >> notify_slack

- `build_thesis` — 관측 상태(SQL) → LLM 추론 → 저장. 슬롯 무관하게 돈다.
- `grade_followups` — 지평 T+0·1·3·5의 미채점 예측을 채점한다. **LLM 없음.** 장후만.
- `narrate_followups` — T+1·3·5에 사후 해설과 판정을 붙인다. 장후만. 지평마다 호출 하나다.
- `notify_slack` — 이번 슬롯의 추론을 보낸다. LLM을 다시 부르지 않는다.

**채점·해설 지표는 이 메시지에 없다.** 읽는 사람이 달라 `slack_ops_briefing`이 OPS 채널로 낸다.

`notify_slack`을 마지막으로 뺀 이유: LangGraph 재추론(비용 큼)과 발송 실패를 분리한다.
Slack이 잠깐 죽어도 앞의 세 태스크를 다시 돌리지 않는다.

## 실패 판정

- `LlmError`·`ThesisError` → `AirflowFailException`(재시도해도 같다)
- `RetryableLlmError`·`ConnectionError`·`ThesisNotReady` → 그대로 올려 Airflow가 재시도
- `SlackError` → `AirflowFailException`. **발송은 at-least-once다** — `slack.py`가 응답 없는
  실패를 `ConnectionError`로 올리는데, 서버가 수락한 뒤 응답만 끊긴 경우도 여기 들어가
  재시도가 같은 메시지를 한 번 더 보낼 수 있다. 중복이 실제로 문제가 되면 그때
  `client_msg_id`를 넣는다. 지금은 문서화로 끝낸다.

## params

| 이름 | 기본값 | 뜻 |
| --- | --- | --- |
| `run_date` | `null` | 대상 세션 날짜(YYYY-MM-DD). 비우면 logical time의 KST 날짜 |

## 필요한 환경

- `XAI_API_KEY`. 어떤 모델을 부를지는 `modules/llm.py`의 `thesis_model()`이 코드로 정하고
  키는 그 LangChain 클래스가 자기 이름으로 읽는다. **운영 값이 무효였던 적이 있다**
  (2026-08-20 실측). 키가 무효면 이 DAG는 매 슬롯 실패한다.
- `SLACK_BOT_TOKEN`, `SLACK_CHANNEL_MARKET` — 시장 브리핑과 같은 채널을 재사용한다.
- `CONNECTION_ID`가 가리키는 Airflow 연결.
- `LANGSMITH_TRACING`을 켜면 프롬프트와 툴 결과(문서 제목·공시명)가 외부로 나간다.
"""

import logging
import os
import re
from contextlib import closing
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

import pendulum
from airflow.exceptions import AirflowFailException, AirflowSkipException
from airflow.providers.postgres.hooks.postgres import PostgresHook
from airflow.sdk import Param, dag, get_current_context, task
from airflow.timetables.trigger import MultipleCronTriggerTimetable
from pydantic import SecretStr

from modules.market_session import krx_open_day
from modules.slack import SlackError, post_message
from modules.utility import CONNECTION_ID, KST_TIMEZONE

logger = logging.getLogger(__name__)

# 08:35 장전 = 문서 평가(매시 25분)가 끝난 뒤. 08:20에 돌면 08:05 수집분이 아직 점수가 없어
# 근거 후보에서 빠진다.
# 20:30 장후 = 확정 종가(18:10)와 지수 마감 봉(16:00) 뒤. 선행 DAG 재시도 여유를 둔다.
SCHEDULE = MultipleCronTriggerTimetable(
    "35 8 * * 1-5",  # KST 평일 08:35 장전 = UTC 일~목 23:35
    "30 20 * * 1-5",  # KST 평일 20:30 장후 = UTC 월~금 11:30
    timezone=KST_TIMEZONE,
)

RUN_DATE_PARAM = "run_date"

# 달력 하루만 받는다. ISO 주 표기(2026-W32)와 기본형(20260821)을 걸러 내는 그물이다.
CALENDAR_DAY_PATTERN = re.compile(r"\d{4}-\d{2}-\d{2}")

# 슬롯이 정하는 기준 시각(KST). **벽시계를 쓰지 않는다** — 오후에 장전 슬롯을 clear해 다시
# 돌려도 장중 정보로 아침 예측을 덮지 않는다.
PRE_OPEN_TIME = time(8, 35)
CLOSE_TIME = time(15, 30)

# 장전 readiness guard가 보는 평가 지연 허용치.
ASSESSMENT_LAG = timedelta(minutes=20)


class ThesisNotReady(RuntimeError):
    """선행 DAG의 데이터가 아직 없다. 재시도하면 풀릴 수 있다."""


def _connection() -> Any:
    # 반환 타입은 provider 버전에 따라 psycopg2/psycopg3 래퍼로 갈린다. 어느 쪽이든 PEP 249다.
    return PostgresHook(postgres_conn_id=CONNECTION_ID).get_conn()


def _slack_settings() -> tuple[SecretStr, str]:
    token = os.environ.get("SLACK_BOT_TOKEN")
    channel = os.environ.get("SLACK_CHANNEL_MARKET")
    if not token or not channel:
        # 설정 누락이라 재시도해도 같다. 값 자체는 메시지에 넣지 않는다.
        raise AirflowFailException("SLACK_BOT_TOKEN and SLACK_CHANNEL_MARKET are required")
    return SecretStr(token), channel


def resolve_run_date(context: Any) -> date:
    """이 실행이 대상으로 삼는 세션 날짜(KST).

    **모양을 먼저 본다.** `date.fromisoformat`은 `2026-W32`도 받아 그 주의 월요일이 된다.
    운영자가 넣은 값과 다른 날을 조용히 추론하게 된다.
    """
    given = (context.get("params") or {}).get(RUN_DATE_PARAM)
    if given:
        text = str(given).strip()
        if not CALENDAR_DAY_PATTERN.fullmatch(text):
            raise AirflowFailException(f"{RUN_DATE_PARAM} must be YYYY-MM-DD, got {given!r}")
        return date.fromisoformat(text)
    logical = context.get("logical_date") or datetime.now(UTC)
    return logical.astimezone(KST_TIMEZONE).date()


def resolve_slot(context: Any) -> str:
    """logical time으로 슬롯을 판정한다. 정오 전이면 장전이다.

    벽시계가 아니라 스케줄된 시각을 보는 이유는 재실행이 늦어져도 슬롯이 안 바뀌게
    하기 위해서다. 수동 실행은 logical_date가 없을 수 있어 지금 시각으로 대신한다.
    """
    logical = context.get("logical_date") or datetime.now(UTC)
    return "pre_open" if logical.astimezone(KST_TIMEZONE).hour < 12 else "post_close"


def slot_as_of(run_date: date, slot: str) -> datetime:
    """그 슬롯의 기준 시각(UTC). 조회의 창 끝이 전부 이 값이다."""
    clock = PRE_OPEN_TIME if slot == "pre_open" else CLOSE_TIME
    return datetime.combine(run_date, clock, tzinfo=KST_TIMEZONE).astimezone(UTC)


def check_ready(connection: Any, run_date: date, slot: str, as_of_at: datetime, watched: list[str]) -> None:
    """선행 데이터가 있는지 본다. 없으면 `ThesisNotReady`로 Airflow 재시도에 맡긴다.

    DAG 간 센서보다 싸고, 기준이 "시각"이 아니라 "데이터"다.
    """
    with connection.cursor() as cursor:
        if slot == "post_close":
            # 확정 종가와 지수 마감 봉이 둘 다 있어야 채점과 관측 상태가 선다.
            cursor.execute(
                "SELECT count(DISTINCT stock_code) FROM stock_investor_trade_daily "
                "WHERE provider = 'kis' AND business_date = %s AND stock_code = ANY(%s)",
                (run_date, watched),
            )
            if cursor.fetchone()[0] < len(watched):
                raise ThesisNotReady(f"settled closes for {run_date} are not all in yet")
            cursor.execute(
                "SELECT count(*) FROM index_bar WHERE provider = 'kis' AND bar_at = %s AND symbol = ANY(%s)",
                (slot_as_of(run_date, "post_close"), ["KOSPI", "KOSDAQ"]),
            )
            if cursor.fetchone()[0] < 2:
                raise ThesisNotReady(f"index closing bars for {run_date} are missing")
            return

        # 장전은 문서 평가가 따라왔는지를 본다.
        cursor.execute("SELECT max(assessed_at) FROM document")
        latest = cursor.fetchone()[0]
        if latest is not None and latest >= as_of_at - ASSESSMENT_LAG:
            return
        # 평가할 것이 없었던 시간일 수 있다. 다만 **수집이 통째로 멈춘 것과 가려야 한다** —
        # "직전 1시간 0건"만 보면 며칠째 죽어 있어도 매번 통과한다.
        cursor.execute(
            "SELECT count(*) FILTER (WHERE detected_at >= %s), count(*) FILTER (WHERE detected_at >= %s) FROM document",
            (as_of_at - timedelta(hours=1), as_of_at - timedelta(hours=24)),
        )
        last_hour, last_day = cursor.fetchone()
        if last_hour == 0 and last_day > 0:
            logger.info("no documents arrived in the last hour; nothing was waiting for assessment")
            return
        raise ThesisNotReady(f"document assessment has not caught up to {as_of_at}")


@dag(
    dag_id="market_thesis_analysis",
    dag_display_name="🧠 시장 추론 기록 (LLM)",
    description="장전 전망과 장후 리뷰를 만들고 지평별로 채점·해설한 뒤 Slack에 보낸다.",
    schedule=SCHEDULE,
    start_date=pendulum.datetime(2026, 8, 21, tz=KST_TIMEZONE),  # KST 2026-08-21 00:00 = UTC 2026-08-20 15:00
    catchup=False,
    max_active_runs=1,
    # 재시도 셋은 readiness guard가 선행 DAG의 지연을 기다리는 수단이다.
    default_args={"retries": 3, "retry_delay": timedelta(minutes=10)},
    params={
        RUN_DATE_PARAM: Param(
            None,
            type=["null", "string"],
            format="date",
            title="대상 세션 날짜",
            description="YYYY-MM-DD. 비우면 스케줄된 시각의 KST 날짜. 지난 날을 다시 만들 때만 준다.",
        ),
    },
    doc_md=__doc__,
    tags=["thesis", "llm", "market", "korea"],
)
def market_thesis_analysis():
    @task(task_display_name="추론 생성")
    def build_thesis() -> dict[str, Any]:
        # LangChain·LangGraph import는 태스크 함수 안에서 한다(DagBag 30초 타임아웃).
        from modules import thesis as market_thesis
        from modules.llm import LlmError, model_name, thesis_model

        context = get_current_context()
        run_date = resolve_run_date(context)
        slot = resolve_slot(context)
        as_of_at = slot_as_of(run_date, slot)
        dag_run_id = str(context["dag_run"].run_id)

        with closing(_connection()) as connection:
            # 휴장 판정은 **모르면 돌린다.** 달력을 아직 못 채웠다는 이유로 진짜 거래일을
            # 빠뜨리는 것이 휴장일에 한 번 더 부르는 것보다 나쁘다.
            if krx_open_day(connection, run_date) is False:
                raise AirflowSkipException(f"KRX is closed on {run_date}")

            targets = market_thesis.subjects(connection)
            watched = [s.code for s in targets if s.kind is market_thesis.ThesisSubjectKind.STOCK]
            check_ready(connection, run_date, slot, as_of_at, watched)

            run_slot = market_thesis.RunSlot(slot)
            # **첫 성공본 불변.** 행이 있으면 모델을 부르지 않는다.
            stored = market_thesis.existing_theses(connection, run_date=run_date, run_slot=run_slot)
            if stored:
                logger.info("thesis for %s %s already exists; skipping the model", run_date, slot)
                return {"run_date": run_date.isoformat(), "slot": slot, "written": 0}

            state = _observed_state(connection, market_thesis, run_date, run_slot, targets)
            model = thesis_model()
            toolbox = market_thesis.ThesisToolbox(
                connection,
                as_of_at=as_of_at,
                macro_window_start=_macro_window_start(connection, market_thesis, run_date, run_slot),
                watched_codes=watched,
                subject_codes=[s.code for s in targets],
            )
            try:
                drafts, rounds = market_thesis.ThesisBuilder(model, toolbox).run(
                    run_slot=run_slot,
                    as_of_at=as_of_at,
                    subjects=targets,
                    observed_state=state,
                )
            except market_thesis.ThesisError as error:
                raise AirflowFailException(str(error)) from error
            except LlmError as error:
                # 재시도할 값어치가 있는 것은 그대로 올린다. 판단은 여기서 한다.
                if type(error).__name__ == "RetryableLlmError":
                    raise
                raise AirflowFailException(str(error)) from error

            rows = market_thesis.store_theses(
                connection,
                run_date=run_date,
                run_slot=run_slot,
                as_of_at=as_of_at,
                dag_run_id=dag_run_id,
                drafts=drafts,
                registry=toolbox.registry,
                observed_state=state,
                llm_model=model_name(model),
                tool_rounds=rounds,
            )

        logger.info("stored %s theses for %s %s (%s tool rounds)", len(rows), run_date, slot, rounds)
        return {"run_date": run_date.isoformat(), "slot": slot, "written": len(rows)}

    @task(task_display_name="지평별 채점")
    def grade_followups(built: dict[str, Any]) -> int:
        """미채점 예측을 지평마다 채점한다. **LLM 없음.**

        장후에만 돈다. 날짜 상한이 없어 장후가 실패했던 날의 것도 여기서 회수된다.
        """
        from modules import thesis as market_thesis

        if built["slot"] != "post_close":
            logger.info("grading runs after the close only")
            return 0

        # 대상은 미채점 조합 전부라 이 실행의 run_date로 좁히지 않는다. 장후가 실패했던
        # 날의 것도 여기서 회수된다.
        dag_run_id = str(get_current_context()["dag_run"].run_id)
        graded = 0
        with closing(_connection()) as connection:
            pending = market_thesis.pending_grades(connection)
            for item in pending:
                target_day = market_thesis.nth_open_day(connection, item.run_date, item.horizon_days)
                if target_day is None:
                    # 달력이 그날까지 안 채워졌다. 다음 실행이 다시 집는다.
                    continue
                returns = _horizon_returns(connection, market_thesis, item, target_day)
                value = returns.get(item.subject_code)
                if value is None:
                    # 종가가 없다. 0으로 꾸미지 않고 미채점으로 남긴다.
                    continue
                market_thesis.store_grade(
                    connection,
                    pending=item,
                    as_of_at=_close_at(target_day),
                    dag_run_id=dag_run_id,
                    return_pct=value,
                    evaluated_at=datetime.now(UTC),
                )
                connection.commit()
                graded += 1
        logger.info("graded %s of %s pending (thesis, horizon) pairs", graded, len(pending))
        return graded

    @task(task_display_name="사후 해설·판정")
    def narrate_followups(built: dict[str, Any]) -> int:
        """지평마다 해설과 판정을 붙인다. 장후에만 돌고 지평마다 호출 하나다."""
        from modules import thesis as market_thesis
        from modules.llm import LlmError, model_name, thesis_model

        if built["slot"] != "post_close":
            logger.info("narration runs after the close only")
            return 0

        run_date = date.fromisoformat(built["run_date"])
        dag_run_id = str(get_current_context()["dag_run"].run_id)
        model = thesis_model()
        written = 0
        with closing(_connection()) as connection:
            for horizon in market_thesis.NARRATED_HORIZON_DAYS:
                # 그 지평의 원 추론일을 거슬러 찾는다. 오늘이 T+N이면 추론일은 N영업일 전이다.
                origin = _origin_day(connection, run_date, horizon)
                if origin is None:
                    continue
                targets = market_thesis.pending_narratives(connection, run_date=origin, horizon_days=horizon)
                if not targets:
                    continue
                as_of_at = _close_at(run_date)
                toolbox = market_thesis.ThesisToolbox(
                    connection,
                    as_of_at=as_of_at,
                    macro_window_start=_close_at(origin),
                    watched_codes=[t.subject.code for t in targets],
                    subject_codes=[t.subject.code for t in targets],
                )
                narrator = market_thesis.FollowupNarrator(model, toolbox)
                try:
                    drafts = narrator.run(
                        run_date=origin,
                        run_slot=market_thesis.RunSlot.PRE_OPEN,
                        horizon_days=horizon,
                        as_of_at=as_of_at,
                        targets=targets,
                    )
                except market_thesis.ThesisError as error:
                    # 그 지평만 없던 것으로 남는다. 다음 실행이 다시 집는다.
                    logger.warning("T+%s narration failed for %s: %s", horizon, origin, error)
                    continue
                except LlmError as error:
                    if type(error).__name__ == "RetryableLlmError":
                        raise
                    raise AirflowFailException(str(error)) from error

                written += market_thesis.store_narratives(
                    connection,
                    horizon_days=horizon,
                    as_of_at=as_of_at,
                    dag_run_id=dag_run_id,
                    drafts=drafts,
                    registry=toolbox.registry,
                    llm_model=model_name(model),
                    prompt_revision=narrator.prompt_revision,
                )
        logger.info("wrote %s narratives", written)
        return written

    @task(task_display_name="Slack 발송")
    def notify_slack(built: dict[str, Any]) -> str:
        """이번 슬롯의 추론을 보낸다. LLM을 다시 부르지 않는다.

        **채점과 해설은 여기 싣지 않는다.** 읽는 사람이 다르다 — 이 메시지는 오늘 시장을
        보는 사람이 읽고, "우리 추론이 잘 맞고 있나"는 운영자가 본다. 지표는
        `slack_ops_briefing`이 OPS 채널로 낸다.
        """
        from modules import thesis as market_thesis

        token, channel = _slack_settings()
        run_date = date.fromisoformat(built["run_date"])
        run_slot = market_thesis.RunSlot(built["slot"])

        with closing(_connection()) as connection:
            theses = market_thesis.existing_theses(connection, run_date=run_date, run_slot=run_slot)
            ids = [thesis.id for thesis in theses]
            evidence = market_thesis.top_evidence(connection, ids)

        blocks = market_thesis.render_blocks(run_slot, run_date, theses, evidence)
        text = market_thesis.render_text(run_slot, run_date, theses)
        try:
            return post_message(token, channel, text=text, blocks=blocks)
        except SlackError as error:
            # 토큰·채널·블록이 틀렸다. 다시 보내도 같은 결과다.
            raise AirflowFailException(str(error)) from error

    built = build_thesis()
    built >> grade_followups(built) >> narrate_followups(built) >> notify_slack(built)


def _close_at(day: date) -> datetime:
    return datetime.combine(day, CLOSE_TIME, tzinfo=KST_TIMEZONE).astimezone(UTC)


def _macro_window_start(connection: Any, market_thesis: Any, run_date: date, run_slot: Any) -> datetime:
    """매크로 창의 시작. 장전은 전 개장일 마감, 장후는 당일 개장이다."""
    if run_slot is market_thesis.RunSlot.POST_CLOSE:
        return datetime.combine(run_date, time(9, 0), tzinfo=KST_TIMEZONE).astimezone(UTC)
    previous = _previous_open_day(connection, run_date)
    return _close_at(previous or run_date)


def _previous_open_day(connection: Any, day: date) -> date | None:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT session_date FROM market_session "
            "WHERE market_code = 'KRX' AND session_date < %s AND effective_open_day "
            "ORDER BY session_date DESC LIMIT 1",
            (day,),
        )
        row = cursor.fetchone()
    return row[0] if row else None


def _origin_day(connection: Any, target_day: date, horizon_days: int) -> date | None:
    """`target_day`가 T+N이 되는 추론일. 달력이 없으면 `None`."""
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT session_date FROM market_session "
            "WHERE market_code = 'KRX' AND session_date < %s AND effective_open_day "
            "ORDER BY session_date DESC OFFSET %s LIMIT 1",
            (target_day, horizon_days - 1),
        )
        row = cursor.fetchone()
    return row[0] if row else None


def _observed_state(connection: Any, market_thesis: Any, run_date: date, run_slot: Any, targets: Any) -> dict[str, Any]:
    """프롬프트에 주는 관측 상태. **전부 SQL이 계산한다.**

    장후는 당일 세션 등락률, 장전은 전 영업일 것이다. 채점과 같은 원본을 본다.
    """
    session = run_date if run_slot is market_thesis.RunSlot.POST_CLOSE else _previous_open_day(connection, run_date)
    if session is None:
        return {"session": None, "index": {}, "stock": {}}

    state: dict[str, Any] = {"session": session.isoformat(), "index": {}, "stock": {}}
    index_codes = [s.code for s in targets if s.kind is market_thesis.ThesisSubjectKind.INDEX]
    stock_codes = [s.code for s in targets if s.kind is market_thesis.ThesisSubjectKind.STOCK]
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT symbol, close, previous_close FROM index_bar "
            "WHERE provider = 'kis' AND bar_at = %s AND symbol = ANY(%s)",
            (_close_at(session), index_codes),
        )
        for symbol, close, previous in cursor.fetchall():
            if previous:
                state["index"][symbol] = {
                    "close": float(close),
                    "return_pct": round(float((close - previous) / previous) * 100, 2),
                }
        cursor.execute(
            "SELECT stock_code, close_price FROM stock_investor_trade_daily "
            "WHERE provider = 'kis' AND business_date = %s AND stock_code = ANY(%s)",
            (session, stock_codes),
        )
        for code, close in cursor.fetchall():
            state["stock"][code] = {"close": float(close)}
    return state


def _horizon_returns(connection: Any, market_thesis: Any, item: Any, target_day: date) -> dict[str, Any]:
    if item.subject_kind is market_thesis.ThesisSubjectKind.STOCK:
        return market_thesis.horizon_returns(
            connection,
            subject_kind=item.subject_kind,
            run_date=item.run_date,
            target_date=target_day,
            codes=[item.subject_code],
        )
    return market_thesis.horizon_returns(
        connection,
        subject_kind=item.subject_kind,
        run_date=item.run_date,
        target_date=target_day,
        codes=[item.subject_code],
        base_bar_at=_close_at(item.run_date),
        target_bar_at=_close_at(target_day),
    )


market_thesis_analysis = market_thesis_analysis()
