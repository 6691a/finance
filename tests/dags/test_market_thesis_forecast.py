"""장전 전망 DAG와 `modules/thesis_forecast.py`.

추론의 알맹이는 `modules/thesis_*.py` 여섯에 있고 `tests/modules/test_thesis_pipeline.py`가 덮는다.
여기 남은 것은 `@dag`가 만든 객체를 읽어야 알 수 있는 것(스케줄, 태스크 그래프),
장전의 시각 계산, 그리고 `PreOpenForecast`다.
"""

import inspect
from datetime import UTC, date, datetime, timedelta
from typing import Any, Self

import pytest
from airflow.exceptions import AirflowFailException, AirflowSkipException

from dags import market_thesis_forecast as dag_module
from modules import thesis_common, thesis_forecast

DAG = dag_module.market_thesis_forecast
KST_MORNING = datetime(2026, 8, 20, 23, 35, tzinfo=UTC)  # KST 08:35
AS_OF = KST_MORNING
RUN_DATE = date(2026, 8, 21)  # AS_OF의 KST 날짜


def _run(connection: Any, run_date: date) -> thesis_common.ThesisRun:
    """슬롯을 모르는 guard를 부르는 최소 객체."""
    return thesis_common.ThesisRun(connection, run_date=run_date, as_of_at=AS_OF)


def test_the_dag_owns_one_slot_only():
    """**슬롯이 시계가 아니라 DAG로 정해진다.** 이 파일이 나뉜 이유다.

    전에는 한 DAG가 `logical_date`의 시각으로 슬롯을 판정했고, `logical_date`가 없는
    수동 실행은 벽시계로 떨어져 오후에 장전을 다시 돌리면 조용히 장후가 됐다.
    """
    assert DAG.schedule == "35 8 * * 1-5"
    assert str(DAG.timetable.timezone) == "Asia/Seoul"
    assert DAG.max_active_runs == 1
    assert thesis_forecast.SLOT == "pre_open"


def test_the_tasks_run_in_one_line():
    tasks = DAG.task_dict

    # 장후 전용 태스크가 없다. 전에는 여기 둘이 더 있으면서 아무 일도 안 했다.
    assert set(tasks) == {"build_thesis", "notify_slack"}
    # 발송을 마지막에 둔다. Slack이 잠깐 죽어도 추론을 다시 돌리지 않는다.
    assert tasks["build_thesis"].upstream_task_ids == set()
    assert "build_thesis" in tasks["notify_slack"].upstream_task_ids


def test_retries_give_the_readiness_guard_room_to_wait():
    # 재시도 셋은 선행 DAG의 지연을 기다리는 수단이다.
    assert DAG.default_args["retries"] == 3
    assert DAG.default_args["retry_delay"] == timedelta(minutes=10)


def test_a_build_cannot_run_past_the_open():
    """요청 타임아웃은 모델 호출 하나만 막는다. 빌드 전체의 울타리는 태스크 타임아웃이다."""
    assert DAG.task_dict["build_thesis"].execution_timeout == timedelta(minutes=30)


def test_the_dag_carries_its_display_metadata():
    # 프로젝트 규칙: 이모지 + 한글 이름 + 제공처, 한 문장 description, doc_md.
    assert DAG.dag_display_name.startswith("🧠")
    assert DAG.description
    assert DAG.doc_md
    param = DAG.params.get_param(thesis_common.RUN_DATE_PARAM)
    assert param.description
    assert param.schema.get("title")


def test_the_run_date_follows_the_kst_calendar():
    # UTC로 08-20 23:35은 KST로 08-21이다.
    assert thesis_common.resolve_run_date({"logical_date": KST_MORNING}) == date(2026, 8, 21)


def test_a_week_shaped_run_date_is_refused():
    """`date.fromisoformat`은 `2026-W32`도 받아 그 주의 월요일이 된다.

    모양을 먼저 보지 않으면 운영자가 넣은 값과 다른 날을 조용히 추론한다.
    """
    with pytest.raises(AirflowFailException, match="YYYY-MM-DD"):
        thesis_common.resolve_run_date({"params": {thesis_common.RUN_DATE_PARAM: "2026-W32"}})


def test_the_as_of_time_is_the_slot_time_not_the_wall_clock():
    # 장전 08:35 KST. 오후에 clear해 다시 돌려도 이 값이다.
    assert thesis_forecast.as_of(date(2026, 8, 21)) == datetime(2026, 8, 20, 23, 35, tzinfo=UTC)


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


def test_the_guard_passes_when_assessment_kept_up():
    connection = FakeConnection([(AS_OF - timedelta(minutes=5),)])

    thesis_forecast.PreOpenForecast(connection, run_date=RUN_DATE).check_ready()


# --- 세 슬롯이 함께 쓰는 guard (`thesis_common`) ----------------------------------


def test_a_closed_day_is_skipped_not_failed():
    with pytest.raises(AirflowSkipException, match="KRX is closed"):
        _run(FakeConnection([(False,)]), date(2026, 8, 15)).skip_unless_open()


@pytest.mark.parametrize("row", [(True,), None])
def test_an_open_or_unknown_day_runs(row):
    """달력을 아직 못 채웠으면(`None`) 돌린다. 진짜 거래일을 빠뜨리는 쪽이 더 나쁘다."""
    _run(FakeConnection([row]), date(2026, 8, 21)).skip_unless_open()


def test_missing_settled_closes_wait_instead_of_skipping():
    with pytest.raises(thesis_common.ThesisNotReady, match="settled closes"):
        _run(FakeConnection([(1,)]), date(2026, 8, 21)).require_settled_closes(["005930", "000660"])


def test_a_quiet_hour_passes_only_when_collection_is_alive():
    """직전 1시간 0건은 평가할 것이 없었다는 뜻일 수도 있다.

    **그것만 보면 수집이 며칠째 죽어 있어도 매번 통과한다.** 최근 24시간에 문서가
    하나라도 있어야 인정한다.
    """
    alive = FakeConnection([(AS_OF - timedelta(hours=6),), (0, 40)])

    thesis_forecast.PreOpenForecast(alive, run_date=RUN_DATE).check_ready()


def test_a_dead_collector_does_not_pass_the_guard():
    dead = FakeConnection([(AS_OF - timedelta(days=3),), (0, 0)])

    # 근거 없는 추론이 조용히 나가는 것을 막는다.
    with pytest.raises(thesis_common.ThesisNotReady, match="has not caught up"):
        thesis_forecast.PreOpenForecast(dead, run_date=RUN_DATE).check_ready()


def test_a_backlog_does_not_pass_either():
    # 문서가 계속 들어오는데 평가가 밀린 상태. 기다려야 한다.
    behind = FakeConnection([(AS_OF - timedelta(hours=6),), (12, 300)])

    with pytest.raises(thesis_common.ThesisNotReady):
        thesis_forecast.PreOpenForecast(behind, run_date=RUN_DATE).check_ready()


# --- PreOpenForecast ----------------------------------------------------------


def test_the_macro_window_starts_at_the_previous_open_day_close():
    """창의 시작은 전 개장일 15:30이다. 장후의 창(당일 09:00부터)과 다르다."""
    forecast = thesis_forecast.PreOpenForecast(FakeConnection([(date(2026, 8, 20),)]), run_date=RUN_DATE)

    assert forecast.macro_window_start() == datetime(2026, 8, 20, 6, 30, tzinfo=UTC)


def test_an_unfilled_calendar_falls_back_to_the_run_date():
    """달력이 아직 없으면 당일 마감으로 둔다. 창이 없어 추론이 멈추는 것보다 낫다."""
    forecast = thesis_forecast.PreOpenForecast(FakeConnection([None]), run_date=RUN_DATE)

    assert forecast.macro_window_start() == datetime(2026, 8, 21, 6, 30, tzinfo=UTC)


class FakeSubject:
    def __init__(self, code: str) -> None:
        self.code = code


class FakeStore:
    """`thesis.ThesisStore` 대역. LangChain 경로를 타지 않고 대상과 과거만 준다."""

    def __init__(self, connection: Any) -> None:
        self.connection = connection

    def subjects(self) -> tuple[FakeSubject, ...]:
        return (FakeSubject("005930"),)

    def past_theses(self, *, as_of_at: datetime, subject_code: str, n: int) -> tuple:
        del as_of_at, subject_code, n
        return ()


def test_run_hands_build_and_store_every_argument_it_requires(monkeypatch):
    """`run()`이 넘기는 kwargs를 `build_and_store`의 시그니처에 묶는다.

    2026-08-23에 형제 브랜치 둘을 합치며 `past`가 필수 인자로 생겼는데 한 호출이 그것을
    모른 채 합쳐져 매 실행 `TypeError`였다. 충돌 없이 합쳐진 자리라 테스트만이 잡는다.
    """
    from modules import thesis_store

    signature = inspect.signature(thesis_common.ThesisRun.build_and_store)
    received: dict[str, Any] = {}

    def fake_build_and_store(self: Any, **kwargs: Any) -> int:
        received.update(kwargs)
        return 3

    monkeypatch.setattr(thesis_common.ThesisRun, "skip_unless_open", lambda self: None)
    monkeypatch.setattr(thesis_common.ThesisRun, "previous_open_day", lambda self: date(2026, 8, 20))
    monkeypatch.setattr(
        thesis_common.ThesisRun, "observed_state", lambda self, session, targets: {"session": str(session)}
    )
    monkeypatch.setattr(thesis_forecast.PreOpenForecast, "check_ready", lambda self: None)
    monkeypatch.setattr(thesis_store, "ThesisStore", FakeStore)
    monkeypatch.setattr(thesis_common.ThesisRun, "build_and_store", fake_build_and_store)

    forecast = thesis_forecast.PreOpenForecast(FakeConnection([]), run_date=RUN_DATE)
    written = forecast.run(dag_run_id="manual__1")

    assert written == 3
    # 필수 인자가 빠지면 여기서 `TypeError`다.
    signature.bind(forecast._run, **received)
    assert received["run_slot"].value == "pre_open"
    # 창의 시작은 전 개장일 마감이다. 장전만의 값이라 여기서 묶는다.
    assert received["macro_window_start"] == thesis_common.close_at(date(2026, 8, 20))
    # **장전만 과거 성적을 싣는다.** 리뷰 두 슬롯은 `past={}`다.
    assert received["past"] == {"005930": ()}
    assert forecast._run.as_of_at == thesis_forecast.as_of(RUN_DATE)
