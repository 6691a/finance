"""장전 전망(`pre_open`)에만 쓰는 것. `market_thesis_forecast` DAG가 부른다.

여기 있는 것은 전부 "장 열리기 전"이라서 그런 것이다. 장후와 공유하지 않는다 —
공유하면 다시 `if slot ==`이 생긴다. 슬롯을 모르는 것은 `thesis_common.py`에 있다.
"""

import logging
from contextlib import closing
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

from airflow.sdk import get_current_context

from modules import thesis_common
from modules.utility import KST_TIMEZONE

logger = logging.getLogger(__name__)

SLOT = "pre_open"

# 기준 시각(KST). **벽시계를 쓰지 않는다** — 오후에 이 DAG를 clear해 다시 돌려도
# 장중 정보로 아침 예측을 덮지 않는다.
PRE_OPEN_TIME = time(8, 35)

# readiness guard가 보는 문서 평가 지연 허용치. `document_assessment_hourly`가 매시 25분에
# 도는데 08:35까지 그 결과가 안 들어왔으면 밤사이 기사가 근거 후보에서 빠진다.
ASSESSMENT_LAG = timedelta(minutes=20)


def as_of(run_date: date) -> datetime:
    """조회 창의 끝(UTC). 모든 툴이 이 시각까지만 본다."""
    return datetime.combine(run_date, PRE_OPEN_TIME, tzinfo=KST_TIMEZONE).astimezone(UTC)


def check_ready(conn: Any, as_of_at: datetime) -> None:
    """문서 평가가 따라왔는지 본다. 아니면 `ThesisNotReady`로 Airflow 재시도에 맡긴다.

    DAG 간 센서보다 싸고, 기준이 "시각"이 아니라 "데이터"다.
    """
    with conn.cursor() as cursor:
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
    raise thesis_common.ThesisNotReady(f"document assessment has not caught up to {as_of_at}")


def macro_window_start(conn: Any, run_date: date) -> datetime:
    """매크로 창의 시작 = 전 개장일 마감.

    "밤사이 해외 시장이 얼마나 움직였나"가 이 창이다. 장후의 창(당일 09:00부터)과 다르다.
    """
    previous = thesis_common.previous_open_day(conn, run_date)
    return thesis_common.close_at(previous or run_date)


def build() -> dict[str, Any]:
    """오늘의 방향을 확률로 적는다. 관측 상태(SQL) → LLM 추론 → 저장."""
    from modules import thesis as market_thesis

    context = get_current_context()
    run_date = thesis_common.resolve_run_date(context)
    as_of_at = as_of(run_date)
    dag_run_id = str(context["dag_run"].run_id)

    with closing(thesis_common.connection()) as conn:
        thesis_common.skip_unless_open(conn, run_date)
        check_ready(conn, as_of_at)

        targets = market_thesis.subjects(conn)
        # 장전이 보는 세션은 **전 영업일**이다. 오늘 장은 아직 열리지 않았다.
        session = thesis_common.previous_open_day(conn, run_date)
        # 같은 대상의 지난 장전 추론과 채점·해설을 프롬프트에 미리 싣는다. 툴에 맡기면
        # 모델이 부를지도, 불렀는지도 우리 손 밖이다. 여기서 본 것이 `thesis_precedent`가 된다.
        past = {
            target.code: market_thesis.past_theses(
                conn,
                as_of_at=as_of_at,
                subject_code=target.code,
                n=market_thesis.PREFETCHED_PAST_THESES,
            )
            for target in targets
        }
        written = thesis_common.build_and_store(
            conn,
            run_slot=market_thesis.RunSlot.PRE_OPEN,
            run_date=run_date,
            as_of_at=as_of_at,
            macro_window_start=macro_window_start(conn, run_date),
            targets=targets,
            observed=thesis_common.observed_state(conn, market_thesis, session, targets, as_of_at=as_of_at),
            past=past,
            dag_run_id=dag_run_id,
        )
    return {"run_date": run_date.isoformat(), "slot": SLOT, "written": written}
