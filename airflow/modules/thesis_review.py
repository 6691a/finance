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
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from airflow.exceptions import AirflowFailException
from airflow.sdk import get_current_context

from modules import thesis_common
from modules.thesis_state import ThesisRunResult
from modules.utility import KST_TIMEZONE

if TYPE_CHECKING:
    # 런타임 import는 못 한다 — `modules.thesis`가 LangChain을 끌고 와서 DagBag 30초
    # 타임아웃에 걸린다(`thesis_common` docstring). `TYPE_CHECKING`은 런타임에 안 돈다.
    from modules import thesis as market_thesis_types

logger = logging.getLogger(__name__)

SLOT = "post_close"

# 매크로 창의 시작 = 당일 개장. "오늘 장중에 무엇이 움직였나"가 이 창이다.
SESSION_OPEN_TIME = time(9, 0)

# readiness guard가 확인하는 지수. 둘 다 마감 봉이 있어야 관측 상태가 선다.
GUARD_INDEX_SYMBOLS = ["KOSPI", "KOSDAQ"]


def as_of(run_date: date) -> datetime:
    """조회 창의 끝(UTC) = 그날 15:30 마감. **실행 시각이 아니다**(모듈 docstring)."""
    return thesis_common.close_at(run_date)


class PostCloseReview:
    """장후 리뷰 한 번. 연결과 세션 날짜를 들고 돈다.

    `thesis_nxt_review.NxtAfterHoursReview`와 같은 모양이다 — 슬롯을 아는 것은 이 클래스이고,
    슬롯을 모르는 조회·저장은 `thesis_common.ThesisRun`이 갖는다.

    **기준 시각 계산(`as_of`·`macro_window_start`)은 모듈 함수로 남는다.** 날짜 하나를 받아
    시각 하나를 주는 순수 계산이라 감쌀 상태가 없다.
    """

    def __init__(self, connection: Any, *, run_date: date) -> None:
        self._run = thesis_common.ThesisRun(connection, run_date=run_date, as_of_at=as_of(run_date))

    @property
    def connection(self) -> Any:
        return self._run.connection

    def check_ready(self, watched: list[str]) -> None:
        """확정 종가와 지수 마감 봉이 둘 다 들어왔는지 본다.

        확정 종가는 `kis_investor_trade_daily`가 18:10에, 지수 마감 봉은 `kis_quote_intraday`가
        16:00까지 채운다. 둘 다 없으면 채점도 관측 상태도 설 수 없다.
        """
        self._run.require_settled_closes(watched)
        with self.connection.cursor() as cursor:
            cursor.execute(
                "SELECT count(*) FROM index_bar WHERE provider = 'kis' AND bar_at = %s AND symbol = ANY(%s)",
                (self._run.as_of_at, GUARD_INDEX_SYMBOLS),
            )
            if cursor.fetchone()[0] < len(GUARD_INDEX_SYMBOLS):
                raise thesis_common.ThesisNotReady(f"index closing bars for {self._run.run_date} are missing")

    def run(self, *, dag_run_id: str) -> int:
        """휴장 판정 → readiness guard → 관측 상태 → LLM → 저장. 저장한 행 수를 준다."""
        from modules import thesis as market_thesis

        self._run.skip_unless_open()

        targets = market_thesis.ThesisStore(self.connection).subjects()
        watched = [s.code for s in targets if s.kind is market_thesis.ThesisSubjectKind.STOCK]
        self.check_ready(watched)

        # 장후가 보는 세션은 **당일**이다. 오늘 장이 이미 끝났다.
        return self._run.build_and_store(
            run_slot=market_thesis.RunSlot.POST_CLOSE,
            macro_window_start=macro_window_start(self._run.run_date),
            targets=targets,
            observed=self._run.observed_state(market_thesis, self._run.run_date, targets),
            # 장후는 예측이 아니라 해석이다. 과거 예측 성적을 실어 줄 자리가 아니다.
            past={},
            dag_run_id=dag_run_id,
        )


def macro_window_start(run_date: date) -> datetime:
    """매크로 창의 시작 = 당일 09:00. 장전의 창(전 개장일 마감부터)과 다르다.

    DB를 보지 않는다 — 오늘 장이 열렸다는 것은 readiness guard가 이미 확인했다.
    """
    return datetime.combine(run_date, SESSION_OPEN_TIME, tzinfo=KST_TIMEZONE).astimezone(UTC)


def build() -> ThesisRunResult:
    """Airflow 태스크 진입점. 컨텍스트를 읽어 리뷰 하나를 돌린다."""
    context = get_current_context()
    run_date = thesis_common.resolve_run_date(context)
    dag_run_id = str(context["dag_run"].run_id)

    with closing(thesis_common.connection()) as conn:
        written = PostCloseReview(conn, run_date=run_date).run(dag_run_id=dag_run_id)
    return ThesisRunResult(run_date=run_date, slot=SLOT, written=written)


def grade_followups() -> int:
    """미채점 예측을 지평마다 채점한다. **LLM 없음.**

    대상은 미채점 조합 전부라 이 실행의 `run_date`로 좁히지 않는다. 리뷰가 실패했던 날의
    것도 여기서 회수된다.

    **`ThesisRun`을 쓰지 않는다.** 채점은 이 실행의 세션이 아니라 *지난* 추론의 날짜를 돌기
    때문에, 세션 날짜를 쥔 객체에 담으면 그 값이 항목마다 거짓이 된다. 연결만 상태다.
    """
    from modules import thesis as market_thesis

    dag_run_id = str(get_current_context()["dag_run"].run_id)
    graded = 0
    with closing(thesis_common.connection()) as conn:
        store = market_thesis.ThesisStore(conn)
        pending = store.pending_grades()
        for item in pending:
            target_day = store.nth_open_day(item.run_date, item.horizon_days)
            if target_day is None:
                # 달력이 그날까지 안 채워졌다. 다음 실행이 다시 집는다.
                continue
            value = _horizon_return(store, market_thesis, item, target_day)
            if value is None:
                # 종가가 없다. 0으로 꾸미지 않고 미채점으로 남긴다.
                continue
            store.store_grade(
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
    """지평마다 해설과 판정을 붙인다. (지평, 원 추론의 슬롯)마다 LLM 호출 하나다.

    슬롯을 나누는 이유는 `FollowupNarrator.run`에 있다 — 같은 날 장전·장후 추론이 같은
    대상을 가져 한 호출에 섞으면 응답을 대상에 되돌릴 수 없다.
    """
    from modules import thesis as market_thesis
    from modules.llm import LlmError, RetryableLlmError, model_name, thesis_model

    run_date = ThesisRunResult.model_validate(built).run_date
    dag_run_id = str(get_current_context()["dag_run"].run_id)
    model = thesis_model()
    written = 0
    as_of_at = thesis_common.close_at(run_date)
    with closing(thesis_common.connection()) as conn:
        run = thesis_common.ThesisRun(conn, run_date=run_date, as_of_at=as_of_at)
        store = market_thesis.ThesisStore(conn)
        for horizon in market_thesis.NARRATED_HORIZON_DAYS:
            # 그 지평의 원 추론일을 거슬러 찾는다. 오늘이 T+N이면 추론일은 N영업일 전이다.
            origin = run.origin_day(horizon)
            if origin is None:
                continue
            pending = store.pending_narratives(run_date=origin, horizon_days=horizon)
            for run_slot in market_thesis.RunSlot:
                targets = tuple(t for t in pending if t.run_slot is run_slot)
                if not targets:
                    continue
                toolbox = market_thesis.ThesisToolbox(
                    conn,
                    as_of_at=as_of_at,
                    macro_window_start=thesis_common.close_at(origin),
                    watched_codes=[t.subject.code for t in targets],
                    subject_codes=[t.subject.code for t in targets],
                )
                narrator = market_thesis.FollowupNarrator(model, toolbox)
                try:
                    drafts = narrator.run(run_date=origin, horizon_days=horizon, as_of_at=as_of_at, targets=targets)
                except market_thesis.ThesisError as error:
                    # 그 (지평, 슬롯)만 없던 것으로 남는다. 다음 실행이 다시 집는다.
                    logger.warning("T+%s %s narration failed for %s: %s", horizon, run_slot.value, origin, error)
                    continue
                except LlmError as error:
                    if isinstance(error, RetryableLlmError):
                        raise
                    raise AirflowFailException(str(error)) from error

                written += store.store_narratives(
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


def _horizon_return(
    store: Any,
    market_thesis: Any,
    item: "market_thesis_types.PendingGrade",
    target_day: date,
) -> Decimal | None:
    """지평 하나의 누적 등락률. 종목은 확정 종가, 지수는 마감 봉을 본다."""
    if item.subject_kind is market_thesis.ThesisSubjectKind.STOCK:
        returns = store.horizon_returns(
            subject_kind=item.subject_kind,
            run_date=item.run_date,
            target_date=target_day,
            codes=[item.subject_code],
        )
    else:
        returns = store.horizon_returns(
            subject_kind=item.subject_kind,
            run_date=item.run_date,
            target_date=target_day,
            codes=[item.subject_code],
            base_bar_at=thesis_common.close_at(item.run_date),
            target_bar_at=thesis_common.close_at(target_day),
        )
    return returns.get(item.subject_code)
