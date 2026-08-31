"""장전 전망(`pre_open`)에만 쓰는 것. `market_thesis_forecast` DAG가 부른다.

여기 있는 것은 전부 "장 열리기 전"이라서 그런 것이다. 장후와 공유하지 않는다 —
공유하면 다시 `if slot ==`이 생긴다. 슬롯을 모르는 것은 `thesis/common.py`에 있다.
"""

import logging
from collections.abc import Sequence
from contextlib import closing
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

from airflow.sdk import get_current_context

from modules.thesis import common
from modules.thesis.state import AfterHoursObservation, RunSlot, ThesisRunResult
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


class PreOpenForecast:
    """장전 전망 한 번. 연결과 세션 날짜를 들고 돈다.

    `thesis.nxt_review.NxtAfterHoursReview`와 같은 모양이다 — 슬롯을 아는 것은 이 클래스이고,
    슬롯을 모르는 조회·저장은 `common.ThesisRun`이 갖는다. 세 슬롯 중 하나만 클래스인
    상태를 없애는 것이 이 전환의 목적이다.

    **기준 시각 계산(`as_of`·`PRE_OPEN_TIME`)은 모듈 함수로 남는다.** 날짜 하나를 받아 시각
    하나를 주는 순수 계산이라 감쌀 상태가 없다.
    """

    def __init__(self, connection: Any, *, run_date: date) -> None:
        self._run = common.ThesisRun(connection, run_date=run_date, as_of_at=as_of(run_date))

    @property
    def connection(self) -> Any:
        return self._run.connection

    def check_ready(self) -> None:
        """문서 평가가 따라왔는지 본다. 아니면 `ThesisNotReady`로 Airflow 재시도에 맡긴다.

        DAG 간 센서보다 싸고, 기준이 "시각"이 아니라 "데이터"다.
        """
        as_of_at = self._run.as_of_at
        with self.connection.cursor() as cursor:
            cursor.execute("SELECT max(assessed_at) FROM document")
            latest = cursor.fetchone()[0]
            if latest is not None and latest >= as_of_at - ASSESSMENT_LAG:
                return
            # 평가할 것이 없었던 시간일 수 있다. 다만 **수집이 통째로 멈춘 것과 가려야 한다** —
            # "직전 1시간 0건"만 보면 며칠째 죽어 있어도 매번 통과한다.
            cursor.execute(
                "SELECT count(*) FILTER (WHERE detected_at >= %s), "
                "count(*) FILTER (WHERE detected_at >= %s) FROM document",
                (as_of_at - timedelta(hours=1), as_of_at - timedelta(hours=24)),
            )
            last_hour, last_day = cursor.fetchone()
        if last_hour == 0 and last_day > 0:
            logger.info("no documents arrived in the last hour; nothing was waiting for assessment")
            return
        raise common.ThesisNotReady(f"document assessment has not caught up to {as_of_at}")

    def after_hours_state(self, session: date, stock_codes: Sequence[str]) -> dict[str, AfterHoursObservation]:
        """그 세션 정규장이 닫힌 뒤 NXT 애프터마켓 마감가. **장전만 이것을 본다.**

        어젯밤 무엇이 움직였나가 오늘 아침 판단의 재료다. 리뷰 슬롯이 같은 창을 당일로 읽는
        것과 세션 날짜만 다르다(조회는 `common.ThesisRun.after_hours_bars` 한 벌).

        **어느 것도 재시도하지 않는다.** 장전은 08:35에 묶여 있고 이 값은 선택 입력이라
        없어도 전망이 선다 — 리뷰가 같은 상황에서 기다리는 것과 다른 판단이다
        (`18-nxt-precedent.md` 2.6절).

        - 봉이 0개면 빈 dict. 애프터마켓 무거래일과 수집 실패를 응답만으로 가를 수 없다.
        - 전부 잠정이면 빈 dict에 **경고**. 20:05 REST 백필이 열두 시간 전에 끝났어야 한다.
          잠정 값 위에 예측을 세우면 **첫 성공본 불변** 때문에 나중에 못 고친다.
        - 확정 종가가 없는 종목은 그 종목만 뺀다. 등락률의 분모다. 0으로 꾸미지 않는다.
        """
        bars = self._run.after_hours_bars(session, stock_codes)
        if not bars:
            logger.info("no NXT after-hours bars for %s; the pre-open state leaves that block empty", session)
            return {}
        if not any(bar.all_final for bar in bars):
            logger.warning("NXT bars for %s are all provisional at the pre-open run; left out of the state", session)
            return {}
        # `is not None`이다. `if bar.return_pct`로 두면 **보합(정확히 0)인 종목이 통째로 빠져**
        # 모델이 그것을 '애프터마켓 데이터 없음'으로 읽는다.
        return {bar.stock_code: common.after_hours_entry(bar) for bar in bars if bar.return_pct is not None}

    def macro_window_start(self) -> datetime:
        """매크로 창의 시작 = 전 개장일 마감.

        "밤사이 해외 시장이 얼마나 움직였나"가 이 창이다. 장후의 창(당일 09:00부터)과 다르다.
        """
        previous = self._run.previous_open_day()
        return common.close_at(previous or self._run.run_date)

    def run(self, *, dag_run_id: str, try_number: int) -> int:
        """휴장 판정 → readiness guard → 관측 상태 → LLM → 저장. 저장한 행 수를 준다."""
        from modules.thesis.domain import PREFETCHED_PAST_THESES, LlmRunKind, ThesisSubjectKind
        from modules.thesis.store import ThesisStore

        self._run.skip_unless_open()
        self.check_ready()

        store = ThesisStore(self.connection)
        targets = store.subjects()
        # 장전이 보는 세션은 **전 영업일**이다. 오늘 장은 아직 열리지 않았다.
        session = self._run.previous_open_day()
        # 같은 대상의 지난 장전 예측·장후 리뷰와 그 채점·해설을 프롬프트에 미리 싣는다.
        # 툴에 맡기면 모델이 부를지도, 불렀는지도 우리 손 밖이다. 여기서 본 것이
        # `thesis_precedent`가 된다. 건수는 슬롯마다다.
        past = {
            target.code: store.past_theses(
                as_of_at=self._run.as_of_at,
                subject_code=target.code,
                n=PREFETCHED_PAST_THESES,
            )
            for target in targets
        }
        # 관측 상태를 두 번 만들지 않는다 — 축은 그 상태에서 파생하므로 값이 갈리면 안 된다.
        observed = self._run.observed_state(session, targets)
        # 어젯밤 NXT 마감가를 얹는다. **`common.observed_state`에 넣지 않는다** — 장후 리뷰가
        # 같은 함수를 부르는데 그쪽 기준 시각(15:30)에는 이 값이 미래다.
        if session is not None:
            stock_codes = [target.code for target in targets if target.kind is ThesisSubjectKind.STOCK]
            observed = observed.model_copy(update={"after_hours": self.after_hours_state(session, stock_codes)})
        return self._run.build_and_store(
            try_number=try_number,
            run_kind=LlmRunKind.FORECAST,
            run_slot=RunSlot.PRE_OPEN,
            macro_window_start=self.macro_window_start(),
            targets=targets,
            observed=observed,
            past=past,
            dag_run_id=dag_run_id,
            baselines=common.session_baselines(observed, session) if session else {},
        )


def build() -> ThesisRunResult:
    """Airflow 태스크 진입점. 컨텍스트를 읽어 전망 하나를 돌린다."""
    context = get_current_context()
    run_date = common.resolve_run_date(context)
    dag_run_id = str(context["dag_run"].run_id)
    # 재시도는 새 대화다. dag_run_id는 재시도에도 같아 이 칸이 없으면 구분할 수 없다.
    try_number = int(context["ti"].try_number)

    with closing(common.connection()) as conn:
        written = PreOpenForecast(conn, run_date=run_date).run(dag_run_id=dag_run_id, try_number=try_number)
    return ThesisRunResult(run_date=run_date, slot=SLOT, written=written)
