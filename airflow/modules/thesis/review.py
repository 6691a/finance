"""장후 리뷰(`post_close`)에만 쓰는 것. `market_thesis_review` DAG가 부른다.

리뷰는 **예측이 아니다.** 이미 일어난 일의 해석이라 채점 대상이 아니고, 대신 지난
예측을 채점하고 되돌아보는 일이 여기 붙는다. 장전과 공유하지 않는다 — 공유하면 다시
`if slot ==`이 생긴다. 슬롯을 모르는 것은 `thesis/common.py`에 있다.

## 기준 시각이 실행 시각이 아니다

이 DAG는 20:30에 돌지만 `as_of`는 **15:30 마감**이다. 확정 종가(18:10)를 기다리느라 늦게
돌 뿐, 모델이 보는 것은 장 마감까지다. 15:30 이후에 쌓인 저녁 기사는 일부러 뺀다 —
안 그러면 재실행할 때마다 근거가 달라져 기록이 흔들린다. 그 뒤에 알려진 것은 T+1·3·5
해설(`narrate_followups`)이 따로 붙인다.
"""

import logging
from contextlib import closing
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from airflow.exceptions import AirflowFailException
from airflow.sdk import get_current_context

from modules.thesis import common
from modules.thesis.domain import LlmRunKind, ThesisSubjectKind
from modules.thesis.state import INTRADAY_SLOTS, RunSlot, ThesisRunResult

if TYPE_CHECKING:
    # 런타임 import는 안 한다 — 타입 이름 하나 때문에 모듈을 끌고 올 이유가 없다.
    # `TYPE_CHECKING`은 런타임에 돌지 않는다.
    from modules.thesis.store import PendingGrade

logger = logging.getLogger(__name__)

SLOT = "post_close"

# readiness guard가 확인하는 지수. 둘 다 마감 봉이 있어야 관측 상태가 선다.
GUARD_INDEX_SYMBOLS = ["KOSPI", "KOSDAQ"]


def as_of(run_date: date) -> datetime:
    """조회 창의 끝(UTC) = 그날 15:30 마감. **실행 시각이 아니다**(모듈 docstring)."""
    return common.close_at(run_date)


class PostCloseReview:
    """장후 리뷰 한 번. 연결과 세션 날짜를 들고 돈다.

    `thesis.nxt_review.NxtAfterHoursReview`와 같은 모양이다 — 슬롯을 아는 것은 이 클래스이고,
    슬롯을 모르는 조회·저장은 `common.ThesisRun`이 갖는다.

    **기준 시각 계산(`as_of`·`macro_window_start`)은 모듈 함수로 남는다.** 날짜 하나를 받아
    시각 하나를 주는 순수 계산이라 감쌀 상태가 없다.
    """

    def __init__(self, connection: Any, *, run_date: date) -> None:
        self._run = common.ThesisRun(connection, run_date=run_date, as_of_at=as_of(run_date))

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
                raise common.ThesisNotReady(f"index closing bars for {self._run.run_date} are missing")

    def run(self, *, dag_run_id: str, try_number: int) -> int:
        """휴장 판정 → readiness guard → 관측 상태 → LLM → 저장. 저장한 행 수를 준다."""
        from modules.thesis.store import ThesisStore

        self._run.skip_unless_open()

        targets = ThesisStore(self.connection).subjects()
        watched = [s.code for s in targets if s.kind is ThesisSubjectKind.STOCK]
        self.check_ready(watched)

        # 장후가 보는 세션은 **당일**이다. 오늘 장이 이미 끝났다.
        return self._run.build_and_store(
            try_number=try_number,
            run_kind=LlmRunKind.REVIEW,
            run_slot=RunSlot.POST_CLOSE,
            macro_window_start=macro_window_start(self._run.run_date),
            targets=targets,
            observed=self._run.observed_state(self._run.run_date, targets),
            # 장후는 예측이 아니라 해석이다. 과거 예측 성적을 실어 줄 자리가 아니다.
            past={},
            dag_run_id=dag_run_id,
        )


def macro_window_start(run_date: date) -> datetime:
    """매크로 창의 시작 = 당일 09:00. 장전의 창(전 개장일 마감부터)과 다르다.

    **장중 슬롯도 같은 창을 쓴다.** 그래서 시각 상수와 계산은 `common`이 갖는다 —
    두 슬롯 모듈에 09:00을 각각 적으면 한쪽만 고쳐지는 날이 온다.
    """
    return common.open_at(run_date)


def build() -> ThesisRunResult:
    """Airflow 태스크 진입점. 컨텍스트를 읽어 리뷰 하나를 돌린다."""
    context = get_current_context()
    run_date = common.resolve_run_date(context)
    dag_run_id = str(context["dag_run"].run_id)
    # 재시도는 새 대화다. dag_run_id는 재시도에도 같아 이 칸이 없으면 구분할 수 없다.
    try_number = int(context["ti"].try_number)

    with closing(common.connection()) as conn:
        written = PostCloseReview(conn, run_date=run_date).run(dag_run_id=dag_run_id, try_number=try_number)
    return ThesisRunResult(run_date=run_date, slot=SLOT, written=written)


def grade_followups() -> int:
    """미채점 예측을 지평마다 채점한다. **LLM 없음.**

    대상은 미채점 조합 전부라 이 실행의 `run_date`로 좁히지 않는다. 리뷰가 실패했던 날의
    것도 여기서 회수된다.

    **`ThesisRun`을 쓰지 않는다.** 채점은 이 실행의 세션이 아니라 *지난* 추론의 날짜를 돌기
    때문에, 세션 날짜를 쥔 객체에 담으면 그 값이 항목마다 거짓이 된다. 연결만 상태다.
    """
    from modules.thesis.store import ThesisStore

    dag_run_id = str(get_current_context()["dag_run"].run_id)
    graded = 0
    with closing(common.connection()) as conn:
        store = ThesisStore(conn)
        pending = store.pending_grades()
        for item in pending:
            target_day = store.nth_open_day(item.run_date, item.horizon_days)
            if target_day is None:
                # 달력이 그날까지 안 채워졌다. 다음 실행이 다시 집는다.
                continue
            value = _horizon_return(store, item, target_day)
            if value is None:
                # 종가가 없다. 0으로 꾸미지 않고 미채점으로 남긴다.
                continue
            store.store_grade(
                pending=item,
                as_of_at=common.close_at(target_day),
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
    from modules.llm import LlmError, RetryableLlmError, model_name, thesis_model
    from modules.thesis.domain import NARRATED_HORIZON_DAYS, LlmRunStatus, ThesisError
    from modules.thesis.outcomes import FollowupNarrator
    from modules.thesis.store import ThesisStore
    from modules.thesis.toolbox import ThesisToolbox

    run_date = ThesisRunResult.model_validate(built).run_date
    context = get_current_context()
    dag_run_id = str(context["dag_run"].run_id)
    # 재시도는 새 대화다. dag_run_id는 재시도에도 같아 이 칸이 없으면 구분할 수 없다.
    try_number = int(context["ti"].try_number)
    model = thesis_model()
    written = 0
    attempted = 0
    failures: list[str] = []
    as_of_at = common.close_at(run_date)
    with closing(common.connection()) as conn:
        run = common.ThesisRun(conn, run_date=run_date, as_of_at=as_of_at)
        store = ThesisStore(conn)
        for horizon in NARRATED_HORIZON_DAYS:
            # 그 지평의 원 추론일을 거슬러 찾는다. 오늘이 T+N이면 추론일은 N영업일 전이다.
            origin = run.origin_day(horizon)
            if origin is None:
                continue
            pending = store.pending_narratives(run_date=origin, horizon_days=horizon)
            for run_slot in RunSlot:
                targets = tuple(t for t in pending if t.run_slot is run_slot)
                if not targets:
                    continue
                toolbox = ThesisToolbox(
                    conn,
                    as_of_at=as_of_at,
                    macro_window_start=common.close_at(origin),
                    watched_codes=[t.subject.code for t in targets],
                    subject_codes=[t.subject.code for t in targets],
                )
                narrator = FollowupNarrator(model, toolbox)
                attempted += 1
                # 원장(13단계)은 (지평, 슬롯)마다 하나다 — 그 단위가 곧 대화 하나다.
                # `run_date`는 실행일이 아니라 **원 추론일**이라 thesis와 같은 축으로 조인된다.
                llm_run_id = store.start_llm_run(
                    kind=LlmRunKind.NARRATION,
                    run_date=origin,
                    run_slot=run_slot,
                    horizon_days=horizon,
                    as_of_at=as_of_at,
                    dag_run_id=dag_run_id,
                    try_number=try_number,
                    llm_model=model_name(model),
                    prompt_version=narrator.prompt_revision,
                )
                try:
                    drafts = narrator.run(run_date=origin, horizon_days=horizon, as_of_at=as_of_at, targets=targets)
                # 넓게 잡되 **반드시 다시 올리거나 실패로 센다.** 잡는 이유는 원장을 닫는 것뿐이다.
                except BaseException as error:
                    store.finish_llm_run(
                        llm_run_id,
                        status=LlmRunStatus.FAILED,
                        records=common.closed_records(toolbox),
                        tool_rounds=toolbox.round_count,
                        error=f"{type(error).__name__}: {error}",
                    )
                    if isinstance(error, ThesisError):
                        # 그 (지평, 슬롯)만 없던 것으로 남는다. 다음 실행이 다시 집는다.
                        # 다만 **세고 나서 판정한다** — 전부 실패했는데 written=0으로 성공하면
                        # 해설이 통째로 빠진 실행이 UI에서 초록으로 보인다.
                        logger.warning(
                            "T+%s %s narration failed for %s: %s", horizon, run_slot.value, origin, error
                        )
                        failures.append(f"T+{horizon} {run_slot.value} {origin}({error})")
                        continue
                    if isinstance(error, LlmError) and not isinstance(error, RetryableLlmError):
                        raise AirflowFailException(str(error)) from error
                    raise
                store.finish_llm_run(
                    llm_run_id,
                    status=LlmRunStatus.SUCCEEDED,
                    records=common.closed_records(toolbox),
                    tool_rounds=toolbox.round_count,
                )

                written += store.store_narratives(
                    horizon_days=horizon,
                    as_of_at=as_of_at,
                    dag_run_id=dag_run_id,
                    drafts=drafts,
                    registry=toolbox.registry,
                    llm_model=model_name(model),
                    prompt_revision=narrator.prompt_revision,
                    narration_run_id=llm_run_id,
                )
    # 해설을 시도한 (지평, 슬롯)이 전부 실패했으면 프롬프트나 앞단 데이터가 깨진 것이다.
    # 다음 실행도 같은 자리에서 멈추므로 여기서 죽는 편이 낫다.
    if failures and len(failures) == attempted:
        raise AirflowFailException(f"Every followup narration failed: {'; '.join(failures)}")
    if failures:
        logger.warning("%s of %s narrations failed: %s", len(failures), attempted, "; ".join(failures))
    logger.info("wrote %s narratives", written)
    return written


def _horizon_return(
    store: Any,
    item: "PendingGrade",
    target_day: date,
) -> Decimal | None:
    """지평 하나의 누적 등락률. 목표가는 종목이 확정 종가, 지수가 마감 봉이다.

    **분모가 슬롯으로 갈린다.** 장전은 예측일 전 영업일 종가이고 장중은 그 슬롯이 실제로
    본 봉의 종가다(`PendingGrade.base_price`). 이미 `subject_kind`로 갈리고 있는 자리에
    축이 하나 더 붙는 것이고, 조회 파일도 그만큼 넷이다.

    장중 슬롯인데 기준가가 없으면 채점하지 않는다. `input_state`에 그 대상의 현재가가
    없었다는 뜻이고, 0이나 전일 종가로 때우면 조용히 다른 것을 재게 된다.
    """
    if item.run_slot in INTRADAY_SLOTS:
        if item.base_price is None:
            logger.warning(
                "thesis %s (%s %s) has no intraday base price; left ungraded",
                item.thesis_id,
                item.run_slot.value,
                item.subject_code,
            )
            return None
        returns = store.intraday_horizon_returns(
            subject_kind=item.subject_kind,
            target_date=target_day,
            target_bar_at=common.close_at(target_day),
            base_prices={item.subject_code: item.base_price},
        )
    elif item.subject_kind is ThesisSubjectKind.STOCK:
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
            base_bar_at=common.close_at(item.run_date),
            target_bar_at=common.close_at(target_day),
        )
    return returns.get(item.subject_code)
