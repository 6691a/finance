"""장후 리뷰 DAG와 `modules/thesis_review.py`의 순수 함수.

채점·해설의 알맹이는 `modules/thesis.py`에 있고 `tests/modules/test_thesis.py`가 덮는다.
여기 남은 것은 태스크 그래프와 장후에만 있는 판정 함수다.
"""

from datetime import UTC, date, datetime, timedelta
from typing import Any, Self

import pytest

from dags import market_thesis_review as dag_module
from modules import thesis_common, thesis_review

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
