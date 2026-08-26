"""장후 리뷰 DAG와 `modules/thesis_review.py`.

채점·해설의 알맹이는 `modules/thesis_outcomes.py`·`thesis_store.py`에 있고
`tests/modules/test_thesis_pipeline.py`가 덮는다.
여기 남은 것은 태스크 그래프, 장후의 시각 계산, 그리고 `PostCloseReview`다.
"""

import inspect
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any, Self

import pytest

from dags import market_thesis_review as dag_module
from modules import thesis_common, thesis_review
from modules.thesis_domain import ThesisSubjectKind
from modules.thesis_state import RunSlot
from modules.thesis_store import PendingGrade

DAG = dag_module.market_thesis_review


def test_the_dag_owns_one_slot_only():
    """**슬롯이 시계가 아니라 DAG로 정해진다.** 이 파일이 나뉜 이유다."""
    assert DAG.schedule == "30 20 * * 1-5"
    assert str(DAG.timetable.timezone) == "Asia/Seoul"
    assert DAG.max_active_runs == 1
    assert thesis_review.SLOT == "post_close"


def test_the_tasks_run_in_one_line():
    tasks = DAG.task_dict

    assert set(tasks) == {"build_thesis", "grade_followups", "narrate_followups", "notify_slack"}
    # 발송을 마지막에 둔다. Slack이 잠깐 죽어도 앞의 셋을 다시 돌리지 않는다.
    assert tasks["build_thesis"].upstream_task_ids == set()
    assert "build_thesis" in tasks["grade_followups"].upstream_task_ids
    assert "grade_followups" in tasks["narrate_followups"].upstream_task_ids
    assert "narrate_followups" in tasks["notify_slack"].upstream_task_ids


def test_retries_give_the_readiness_guard_room_to_wait():
    assert DAG.default_args["retries"] == 3
    assert DAG.default_args["retry_delay"] == timedelta(minutes=10)


def test_only_the_build_has_a_task_timeout():
    """채점·해설은 밀린 날짜를 따라잡느라 길어질 수 있어 울타리를 두지 않는다."""
    assert DAG.task_dict["build_thesis"].execution_timeout == thesis_common.BUILD_TIMEOUT
    assert DAG.task_dict["narrate_followups"].execution_timeout is None


def test_the_dag_carries_its_display_metadata():
    assert DAG.dag_display_name.startswith("🧠")
    assert DAG.description
    assert DAG.doc_md
    param = DAG.params.get_param(thesis_common.RUN_DATE_PARAM)
    assert param.description
    assert param.schema.get("title")


def test_the_as_of_time_is_the_close_not_the_run_time():
    """20:30에 돌지만 기준 시각은 15:30 마감이다.

    이게 아니면 재실행할 때마다 저녁 기사가 섞여 근거가 달라진다.
    """
    assert thesis_review.as_of(date(2026, 8, 21)) == datetime(2026, 8, 21, 6, 30, tzinfo=UTC)


def test_the_macro_window_starts_at_the_open():
    """장후의 창은 당일 09:00부터다. 장전(전 개장일 마감부터)과 다르다."""
    assert thesis_review.macro_window_start(date(2026, 8, 21)) == datetime(2026, 8, 21, 0, 0, tzinfo=UTC)


class FakeCursor:
    def __init__(self, answers: list[Any]) -> None:
        self._answers = answers
        self._row: Any = None
        self.calls: list[tuple[str, tuple]] = []

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def execute(self, statement: str, parameters: Any = ()) -> None:
        self.calls.append((statement, tuple(parameters)))
        self._row = self._answers.pop(0)

    def fetchone(self) -> Any:
        return self._row


class FakeConnection:
    def __init__(self, answers: list[Any]) -> None:
        self._cursor = FakeCursor(answers)

    def cursor(self) -> FakeCursor:
        return self._cursor


def test_the_guard_waits_for_every_settled_close():
    # 종목 하나라도 빠지면 채점 원본이 없다.
    connection = FakeConnection([(1,)])

    with pytest.raises(thesis_common.ThesisNotReady, match="settled closes"):
        thesis_review.PostCloseReview(connection, run_date=date(2026, 8, 21)).check_ready(["005930", "000660"])


def test_the_guard_waits_for_the_index_closing_bars():
    connection = FakeConnection([(2,), (1,)])

    with pytest.raises(thesis_common.ThesisNotReady, match="index closing bars"):
        thesis_review.PostCloseReview(connection, run_date=date(2026, 8, 21)).check_ready(["005930", "000660"])


def test_the_guard_passes_when_both_sources_are_in():
    connection = FakeConnection([(2,), (2,)])

    thesis_review.PostCloseReview(connection, run_date=date(2026, 8, 21)).check_ready(["005930", "000660"])


def test_the_index_guard_asks_for_the_close_bar_not_the_run_time():
    """20:30에 돌지만 찾는 봉은 15:30이다. 실행 시각으로 찾으면 영영 못 찾는다."""
    connection = FakeConnection([(2,), (2,)])

    thesis_review.PostCloseReview(connection, run_date=date(2026, 8, 21)).check_ready(["005930"])

    statement, parameters = connection.cursor().calls[-1]
    assert "index_bar" in statement
    assert parameters[0] == thesis_review.as_of(date(2026, 8, 21))
    assert parameters[1] == thesis_review.GUARD_INDEX_SYMBOLS


class FakeSubject:
    def __init__(self, code: str, kind: Any) -> None:
        self.code = code
        self.kind = kind


def test_run_hands_build_and_store_every_argument_it_requires(monkeypatch):
    """`run()`이 넘기는 kwargs를 `build_and_store`의 시그니처에 묶는다.

    2026-08-23에 형제 브랜치 둘을 합치며 `past`가 필수 인자로 생겼는데 한 호출이 그것을
    모른 채 합쳐져 매 실행 `TypeError`였다. 충돌 없이 합쳐진 자리라 테스트만이 잡는다.
    """
    from modules import thesis_store
    from modules.thesis_domain import ThesisSubjectKind

    run_date = date(2026, 8, 21)
    signature = inspect.signature(thesis_common.ThesisRun.build_and_store)
    received: dict[str, Any] = {}
    guarded: list[list[str]] = []

    class FakeStore:
        def __init__(self, connection: Any) -> None:
            self.connection = connection

        def subjects(self) -> tuple[FakeSubject, ...]:
            return (
                FakeSubject("KOSPI", ThesisSubjectKind.INDEX),
                FakeSubject("005930", ThesisSubjectKind.STOCK),
            )

    def fake_build_and_store(self: Any, **kwargs: Any) -> int:
        received.update(kwargs)
        return 4

    monkeypatch.setattr(thesis_common.ThesisRun, "skip_unless_open", lambda self: None)
    monkeypatch.setattr(
        thesis_common.ThesisRun, "observed_state", lambda self, session, targets: {"session": str(session)}
    )
    monkeypatch.setattr(
        thesis_review.PostCloseReview, "check_ready", lambda self, watched: guarded.append(list(watched))
    )
    monkeypatch.setattr(thesis_store, "ThesisStore", FakeStore)
    monkeypatch.setattr(thesis_common.ThesisRun, "build_and_store", fake_build_and_store)

    review = thesis_review.PostCloseReview(FakeConnection([]), run_date=run_date)
    written = review.run(dag_run_id="manual__1", try_number=1)

    assert written == 4
    # 필수 인자가 빠지면 여기서 `TypeError`다.
    signature.bind(review._run, **received)
    assert received["run_slot"].value == "post_close"
    assert received["macro_window_start"] == thesis_review.macro_window_start(run_date)
    # **확정 종가 guard는 종목만 본다.** 지수는 확정 종가 테이블에 없다.
    assert guarded == [["005930"]]
    # 리뷰는 해석이라 과거 예측 성적을 싣지 않는다. NXT 리뷰와 같다.
    assert received["past"] == {}
    assert review._run.as_of_at == thesis_review.as_of(run_date)


# --- 채점 기준가가 슬롯으로 갈린다 -------------------------------------------


class RecordingStore:
    """`_horizon_return`이 어느 조회를 골랐는지만 본다."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def horizon_returns(self, **kwargs: Any) -> dict[str, Decimal]:
        self.calls.append(("daily", kwargs))
        return {"KOSPI": Decimal("1.5")}

    def intraday_horizon_returns(self, **kwargs: Any) -> dict[str, Decimal]:
        self.calls.append(("intraday", kwargs))
        return {"KOSPI": Decimal("0.4")}


def pending(run_slot: RunSlot, base_price: Decimal | None) -> PendingGrade:
    return PendingGrade(
        thesis_id=11,
        run_date=date(2026, 8, 26),
        as_of_at=datetime(2026, 8, 26, 1, 35, tzinfo=UTC),
        subject_kind=ThesisSubjectKind.INDEX,
        subject_code="KOSPI",
        prob_up=Decimal("0.6"),
        prob_down=Decimal("0.3"),
        prob_flat=Decimal("0.1"),
        horizon_days=0,
        run_slot=run_slot,
        base_price=base_price,
    )


def test_the_morning_slot_still_divides_by_the_previous_close():
    store = RecordingStore()

    result = thesis_review._horizon_return(store, pending(RunSlot.PRE_OPEN, None), date(2026, 8, 26))

    assert result == Decimal("1.5")
    assert store.calls[0][0] == "daily"


def test_an_intraday_slot_divides_by_the_price_it_actually_saw():
    """장중 슬롯의 분모는 그 슬롯이 본 봉이다. 전일 종가로 재면 다른 것을 재게 된다."""
    store = RecordingStore()

    result = thesis_review._horizon_return(
        store, pending(RunSlot.INTRADAY_MORNING, Decimal(3150)), date(2026, 8, 26)
    )

    assert result == Decimal("0.4")
    kind, kwargs = store.calls[0]
    assert kind == "intraday"
    assert kwargs["base_prices"] == {"KOSPI": Decimal(3150)}
    assert kwargs["target_bar_at"] == thesis_common.close_at(date(2026, 8, 26))


def test_an_intraday_thesis_without_a_base_price_stays_ungraded():
    """0이나 전일 종가로 때우면 조용히 다른 것을 재게 된다. 미채점으로 남긴다."""
    store = RecordingStore()

    assert thesis_review._horizon_return(store, pending(RunSlot.PRE_CLOSE, None), date(2026, 8, 26)) is None
    assert store.calls == []
