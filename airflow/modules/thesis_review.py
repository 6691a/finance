"""장후 리뷰(`post_close`)에만 쓰는 것. `market_thesis_review` DAG가 부른다.

리뷰는 **예측이 아니다.** 이미 일어난 일의 해석이라 채점 대상이 아니고, 대신 지난
예측을 채점하고 되돌아보는 일이 여기 붙는다. 장전과 공유하지 않는다 — 공유하면 다시
`if slot ==`이 생긴다. 슬롯을 모르는 것은 `thesis_common.py`에 있다.

## 기준 시각이 실행 시각이 아니다

이 DAG는 20:30에 돌지만 `as_of`는 **15:30 마감**이다. 확정 종가(18:10)를 기다리느라 늦게
돌 뿐, 모델이 보는 것은 장 마감까지다. 15:30 이후에 쌓인 저녁 기사는 일부러 뺀다 —
안 그러면 재실행할 때마다 근거가 달라져 기록이 흔들린다. 그 뒤에 알려진 것은 T+1·3·5
해설(`narrate_followups`)이 따로 붙인다.
"""

import logging
from contextlib import closing
from datetime import UTC, date, datetime, time
from typing import Any

from airflow.exceptions import AirflowFailException, AirflowSkipException
from airflow.sdk import get_current_context

from modules import thesis_common
from modules.market_session import krx_open_day
from modules.utility import KST_TIMEZONE

logger = logging.getLogger(__name__)

SLOT = "post_close"

# 매크로 창의 시작 = 당일 개장. "오늘 장중에 무엇이 움직였나"가 이 창이다.
SESSION_OPEN_TIME = time(9, 0)

# readiness guard가 확인하는 지수. 둘 다 마감 봉이 있어야 관측 상태가 선다.
GUARD_INDEX_SYMBOLS = ["KOSPI", "KOSDAQ"]


def as_of(run_date: date) -> datetime:
    """조회 창의 끝(UTC) = 그날 15:30 마감. **실행 시각이 아니다**(모듈 docstring)."""
    return thesis_common.close_at(run_date)


def check_ready(conn: Any, run_date: date, watched: list[str]) -> None:
    """확정 종가와 지수 마감 봉이 둘 다 들어왔는지 본다.

    확정 종가는 `kis_investor_trade_daily`가 18:10에, 지수 마감 봉은 `kis_quote_intraday`가
    16:00까지 채운다. 둘 다 없으면 채점도 관측 상태도 설 수 없다.
    """
    with conn.cursor() as cursor:
        cursor.execute(
            "SELECT count(DISTINCT stock_code) FROM stock_investor_trade_daily "
            "WHERE provider = 'kis' AND business_date = %s AND stock_code = ANY(%s)",
            (run_date, watched),
        )
        if cursor.fetchone()[0] < len(watched):
            raise thesis_common.ThesisNotReady(f"settled closes for {run_date} are not all in yet")
        cursor.execute(
            "SELECT count(*) FROM index_bar WHERE provider = 'kis' AND bar_at = %s AND symbol = ANY(%s)",
            (as_of(run_date), GUARD_INDEX_SYMBOLS),
        )
        if cursor.fetchone()[0] < len(GUARD_INDEX_SYMBOLS):
            raise thesis_common.ThesisNotReady(f"index closing bars for {run_date} are missing")


def macro_window_start(run_date: date) -> datetime:
    """매크로 창의 시작 = 당일 09:00. 장전의 창(전 개장일 마감부터)과 다르다.

    DB를 보지 않는다 — 오늘 장이 열렸다는 것은 readiness guard가 이미 확인했다.
    """
    return datetime.combine(run_date, SESSION_OPEN_TIME, tzinfo=KST_TIMEZONE).astimezone(UTC)


def build() -> dict[str, Any]:
    """오늘 왜 그렇게 움직였는지를 적는다. 관측 상태(SQL) → LLM 추론 → 저장."""
    from modules import thesis as market_thesis

    context = get_current_context()
    run_date = thesis_common.resolve_run_date(context)
    as_of_at = as_of(run_date)
    dag_run_id = str(context["dag_run"].run_id)

    with closing(thesis_common.connection()) as conn:
        # 휴장 판정은 **모르면 돌린다.** 달력을 아직 못 채웠다는 이유로 진짜 거래일을
        # 빠뜨리는 것이 휴장일에 한 번 더 부르는 것보다 나쁘다.
        if krx_open_day(conn, run_date) is False:
            raise AirflowSkipException(f"KRX is closed on {run_date}")

        targets = market_thesis.subjects(conn)
        watched = [s.code for s in targets if s.kind is market_thesis.ThesisSubjectKind.STOCK]
        check_ready(conn, run_date, watched)

        # 장후가 보는 세션은 **당일**이다. 오늘 장이 이미 끝났다.
        written = thesis_common.build_and_store(
            conn,
            run_slot=market_thesis.RunSlot.POST_CLOSE,
            run_date=run_date,
            as_of_at=as_of_at,
            macro_window_start=macro_window_start(run_date),
            targets=targets,
            observed=thesis_common.observed_state(conn, market_thesis, run_date, targets),
            dag_run_id=dag_run_id,
        )
    return {"run_date": run_date.isoformat(), "slot": SLOT, "written": written}


def grade_followups() -> int:
    """미채점 예측을 지평마다 채점한다. **LLM 없음.**

    대상은 미채점 조합 전부라 이 실행의 `run_date`로 좁히지 않는다. 리뷰가 실패했던 날의
    것도 여기서 회수된다.
    """
    from modules import thesis as market_thesis

    dag_run_id = str(get_current_context()["dag_run"].run_id)
    graded = 0
    with closing(thesis_common.connection()) as conn:
        pending = market_thesis.pending_grades(conn)
        for item in pending:
            target_day = market_thesis.nth_open_day(conn, item.run_date, item.horizon_days)
            if target_day is None:
                # 달력이 그날까지 안 채워졌다. 다음 실행이 다시 집는다.
                continue
            value = _horizon_return(conn, market_thesis, item, target_day)
            if value is None:
                # 종가가 없다. 0으로 꾸미지 않고 미채점으로 남긴다.
                continue
            market_thesis.store_grade(
                conn,
                pending=item,
                as_of_at=thesis_common.close_at(target_day),
                dag_run_id=dag_run_id,
                return_pct=value,
                evaluated_at=datetime.now(UTC),
            )
            conn.commit()
            graded += 1
    logger.info("graded %s of %s pending (thesis, horizon) pairs", graded, len(pending))
    return graded


def narrate_followups(built: dict[str, Any]) -> int:
    """지평마다 해설과 판정을 붙인다. 지평마다 LLM 호출 하나다."""
    from modules import thesis as market_thesis
    from modules.llm import LlmError, model_name, thesis_model

    run_date = date.fromisoformat(built["run_date"])
    dag_run_id = str(get_current_context()["dag_run"].run_id)
    model = thesis_model()
    written = 0
    with closing(thesis_common.connection()) as conn:
        for horizon in market_thesis.NARRATED_HORIZON_DAYS:
            # 그 지평의 원 추론일을 거슬러 찾는다. 오늘이 T+N이면 추론일은 N영업일 전이다.
            origin = thesis_common.origin_day(conn, run_date, horizon)
            if origin is None:
                continue
            targets = market_thesis.pending_narratives(conn, run_date=origin, horizon_days=horizon)
            if not targets:
                continue
            as_of_at = thesis_common.close_at(run_date)
            toolbox = market_thesis.ThesisToolbox(
                conn,
                as_of_at=as_of_at,
                macro_window_start=thesis_common.close_at(origin),
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
                conn,
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


def _horizon_return(conn: Any, market_thesis: Any, item: Any, target_day: date) -> Any:
    """지평 하나의 누적 등락률. 종목은 확정 종가, 지수는 마감 봉을 본다."""
    if item.subject_kind is market_thesis.ThesisSubjectKind.STOCK:
        returns = market_thesis.horizon_returns(
            conn,
            subject_kind=item.subject_kind,
            run_date=item.run_date,
            target_date=target_day,
            codes=[item.subject_code],
        )
    else:
        returns = market_thesis.horizon_returns(
            conn,
            subject_kind=item.subject_kind,
            run_date=item.run_date,
            target_date=target_day,
            codes=[item.subject_code],
            base_bar_at=thesis_common.close_at(item.run_date),
            target_bar_at=thesis_common.close_at(target_day),
        )
    return returns.get(item.subject_code)
