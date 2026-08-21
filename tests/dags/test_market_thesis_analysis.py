"""DAG 객체와 그 안의 순수 함수만 검증한다.

추론·채점·해설의 알맹이는 `modules/thesis.py`에 있고 `tests/modules/test_thesis.py`가 덮는다.
여기 남은 것은 `@dag`가 만든 객체를 읽어야 알 수 있는 것(스케줄, 태스크 그래프)과 DAG
파일에만 있는 판정 함수다.
"""

from datetime import UTC, date, datetime, timedelta
from typing import Any, Self

import pytest
from airflow.exceptions import AirflowFailException

from dags import market_thesis_analysis as module

DAG = module.market_thesis_analysis
KST_MORNING = datetime(2026, 8, 20, 23, 35, tzinfo=UTC)  # KST 08:35
KST_EVENING = datetime(2026, 8, 21, 11, 30, tzinfo=UTC)  # KST 20:30


def test_the_dag_stays_on_its_two_slots():
    # 시각은 앞단 DAG의 데이터가 준비되는 때에 맞춘 값이라 주석이 아니라 테스트가 지킨다.
    schedule = DAG.timetable.serialize()

    assert schedule["expressions"] == ["35 8 * * 1-5", "30 20 * * 1-5"]
    assert schedule["timezone"] == "Asia/Seoul"
    assert DAG.max_active_runs == 1


def test_the_tasks_run_in_one_line():
    tasks = DAG.task_dict

    assert set(tasks) == {"build_thesis", "grade_followups", "narrate_followups", "notify_slack"}
    # 발송을 마지막에 둔다. Slack이 잠깐 죽어도 앞의 셋을 다시 돌리지 않는다.
    assert tasks["build_thesis"].upstream_task_ids == set()
    assert "build_thesis" in tasks["grade_followups"].upstream_task_ids
    assert "grade_followups" in tasks["narrate_followups"].upstream_task_ids
    assert "narrate_followups" in tasks["notify_slack"].upstream_task_ids


def test_retries_give_the_readiness_guard_room_to_wait():
    # 재시도 셋은 선행 DAG의 지연을 기다리는 수단이다.
    assert DAG.default_args["retries"] == 3
    assert DAG.default_args["retry_delay"] == timedelta(minutes=10)


def test_the_dag_carries_its_display_metadata():
    # 프로젝트 규칙: 이모지 + 한글 이름 + 제공처, 한 문장 description, doc_md.
    assert DAG.dag_display_name.startswith("🧠")
    assert DAG.description
    assert DAG.doc_md
    param = DAG.params.get_param(module.RUN_DATE_PARAM)
    assert param.description
    assert param.schema.get("title")


@pytest.mark.parametrize(
    ("logical", "expected"),
    [(KST_MORNING, "pre_open"), (KST_EVENING, "post_close")],
)
def test_the_slot_comes_from_logical_time_not_the_wall_clock(logical, expected):
    # 재실행이 늦어져도 슬롯이 안 바뀌어야 한다.
    assert module.resolve_slot({"logical_date": logical}) is not None
    assert module.resolve_slot({"logical_date": logical}) == expected


def test_the_run_date_follows_the_kst_calendar():
    # UTC로 08-20 23:35은 KST로 08-21이다.
    assert module.resolve_run_date({"logical_date": KST_MORNING}) == date(2026, 8, 21)


def test_a_week_shaped_run_date_is_refused():
    """`date.fromisoformat`은 `2026-W32`도 받아 그 주의 월요일이 된다.

    모양을 먼저 보지 않으면 운영자가 넣은 값과 다른 날을 조용히 추론한다.
    """
    with pytest.raises(AirflowFailException, match="YYYY-MM-DD"):
        module.resolve_run_date({"params": {module.RUN_DATE_PARAM: "2026-W32"}})


def test_the_as_of_time_is_fixed_by_the_slot():
    morning = module.slot_as_of(date(2026, 8, 21), "pre_open")
    evening = module.slot_as_of(date(2026, 8, 21), "post_close")

    # 장전 08:35 KST, 장후 15:30 KST. 벽시계가 아니다.
    assert morning == datetime(2026, 8, 20, 23, 35, tzinfo=UTC)
    assert evening == datetime(2026, 8, 21, 6, 30, tzinfo=UTC)


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


AS_OF = datetime(2026, 8, 20, 23, 35, tzinfo=UTC)


def test_the_post_close_guard_waits_for_every_settled_close():
    # 종목 하나라도 빠지면 채점 원본이 없다.
    connection = FakeConnection([(1,)])

    with pytest.raises(module.ThesisNotReady, match="settled closes"):
        module.check_ready(connection, date(2026, 8, 21), "post_close", AS_OF, ["005930", "000660"])


def test_the_post_close_guard_waits_for_the_index_closing_bars():
    connection = FakeConnection([(2,), (1,)])

    with pytest.raises(module.ThesisNotReady, match="index closing bars"):
        module.check_ready(connection, date(2026, 8, 21), "post_close", AS_OF, ["005930", "000660"])


def test_the_post_close_guard_passes_when_both_sources_are_in():
    connection = FakeConnection([(2,), (2,)])

    module.check_ready(connection, date(2026, 8, 21), "post_close", AS_OF, ["005930", "000660"])


def test_the_pre_open_guard_passes_when_assessment_kept_up():
    connection = FakeConnection([(AS_OF - timedelta(minutes=5),)])

    module.check_ready(connection, date(2026, 8, 21), "pre_open", AS_OF, [])


def test_a_quiet_hour_passes_only_when_collection_is_alive():
    """직전 1시간 0건은 평가할 것이 없었다는 뜻일 수도 있다.

    **그것만 보면 수집이 며칠째 죽어 있어도 매번 통과한다.** 최근 24시간에 문서가
    하나라도 있어야 인정한다.
    """
    alive = FakeConnection([(AS_OF - timedelta(hours=6),), (0, 40)])

    module.check_ready(alive, date(2026, 8, 21), "pre_open", AS_OF, [])


def test_a_dead_collector_does_not_pass_the_pre_open_guard():
    dead = FakeConnection([(AS_OF - timedelta(days=3),), (0, 0)])

    # 근거 없는 추론이 조용히 나가는 것을 막는다.
    with pytest.raises(module.ThesisNotReady, match="has not caught up"):
        module.check_ready(dead, date(2026, 8, 21), "pre_open", AS_OF, [])


def test_a_backlog_does_not_pass_either():
    # 문서가 계속 들어오는데 평가가 밀린 상태. 기다려야 한다.
    behind = FakeConnection([(AS_OF - timedelta(hours=6),), (12, 300)])

    with pytest.raises(module.ThesisNotReady):
        module.check_ready(behind, date(2026, 8, 21), "pre_open", AS_OF, [])
