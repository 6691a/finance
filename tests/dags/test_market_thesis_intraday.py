"""장중 전망 DAG와 `modules/thesis/intraday.py`.

추론의 알맹이는 `modules/thesis_*.py`에 있고 `tests/modules/test_thesis_pipeline.py`가 덮는다.
여기 남은 것은 `@dag`가 만든 객체를 읽어야 알 수 있는 것(스케줄, 태스크 그래프, 재시도),
슬롯 해석, 장중만의 봉 조회·guard·되짚기, 그리고 `IntradayForecast`다.
"""

import inspect
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from typing import Any, Self

import pytest
from airflow.sdk.exceptions import AirflowFailException

from dags import market_thesis_intraday as dag_module
from modules.technical import base_rate
from modules.thesis import common, intraday
from modules.thesis.domain import ThesisSubjectKind
from modules.thesis.state import INTRADAY_SLOT_TIMES, IntradayObservation, RunSlot

DAG = dag_module.market_thesis_intraday
RUN_DATE = date(2026, 8, 26)
# KST 10:35 = UTC 01:35
MORNING_AS_OF = datetime(2026, 8, 26, 1, 35, tzinfo=UTC)
# KST 12:35 = UTC 03:35
MIDDAY_AS_OF = datetime(2026, 8, 26, 3, 35, tzinfo=UTC)


# --- DAG ---------------------------------------------------------------------


def test_the_schedule_matches_the_slot_table():
    """**cron과 슬롯 표가 같아야 한다.**

    어긋나면 `resolve_slot`이 슬롯을 못 찾아 실행이 죽는다. 죽는 것이 조용히 다른
    슬롯으로 떨어지는 것보다 낫지만, 애초에 어긋나지 않게 여기서 묶는다.
    """
    summary = DAG.schedule.summary
    crons = [part.strip().split(" ", 2)[:2] for part in summary.split(",")]

    assert [(int(hour), int(minute)) for minute, hour in crons] == [
        (at.hour, at.minute) for at in INTRADAY_SLOT_TIMES.values()
    ]
    # 주말은 cron이 뺀다. 휴장일은 `skip_unless_open`이 뺀다.
    assert all(part.strip().endswith("* * 1-5") for part in summary.split(","))
    assert DAG.max_active_runs == 1


def test_every_slot_can_see_the_last_document_assessment():
    """**모든 슬롯에서 직전 평가 실행이 guard를 통과해야 한다.**

    `document_assessment_hourly`가 매시 :25에 돌므로, 슬롯 시각에서 직전 :25까지의 거리가
    `ASSESSMENT_LAG`보다 크면 그 슬롯은 어떤 실행도 통과하지 못한다. `pre_close`(:00)가
    실제로 그랬다(2026-08-26). 한 시간 전 실행이 통과해서도 안 된다 — 그러면 평가가
    한 번 통째로 밀린 것을 guard가 못 본다.
    """
    assessment_minute = 25
    for slot, at in INTRADAY_SLOT_TIMES.items():
        minutes = at.hour * 60 + at.minute
        gap = timedelta(minutes=(minutes - assessment_minute) % 60)
        assert gap <= intraday.ASSESSMENT_LAG, slot
        assert gap + timedelta(hours=1) > intraday.ASSESSMENT_LAG, slot


def test_the_slot_table_is_the_four_the_user_asked_for():
    assert INTRADAY_SLOT_TIMES == {
        RunSlot.INTRADAY_MORNING: time(10, 35),
        RunSlot.INTRADAY_MIDDAY: time(12, 35),
        RunSlot.INTRADAY_AFTERNOON: time(14, 35),
        RunSlot.PRE_CLOSE: time(15, 0),
    }


def test_the_tasks_run_in_one_line():
    tasks = DAG.task_dict

    # 채점·해설은 장후 DAG에만 있다. 확정 종가가 18:10이라 장중에는 할 일이 없다.
    assert set(tasks) == {"build_thesis", "notify_slack"}
    assert tasks["build_thesis"].upstream_task_ids == set()
    assert "build_thesis" in tasks["notify_slack"].upstream_task_ids


def test_a_slow_slot_cannot_block_the_next_one():
    """장전·장후와 다른 재시도·타임아웃이다.

    공유 `DEFAULT_ARGS`(3 × 10분) + `BUILD_TIMEOUT`(30분)이면 최악 두 시간이라
    `max_active_runs=1` 아래에서 10:35 실행이 12:35 실행을 막는다.
    """
    worst_case = DAG.task_dict["build_thesis"].execution_timeout * (DAG.default_args["retries"] + 1)
    worst_case += DAG.default_args["retry_delay"] * DAG.default_args["retries"]

    assert worst_case < timedelta(hours=2)
    assert DAG.default_args["retries"] < common.DEFAULT_ARGS["retries"]


def test_the_dag_carries_its_display_metadata():
    assert DAG.dag_display_name.startswith("🧠")
    assert DAG.description
    assert DAG.doc_md
    for name in (common.RUN_DATE_PARAM, intraday.RUN_SLOT_PARAM):
        param = DAG.params.get_param(name)
        assert param.description
        assert param.schema.get("title")


# --- 슬롯 해석 ----------------------------------------------------------------


def test_the_scheduled_time_picks_the_slot():
    context = {"logical_date": MIDDAY_AS_OF}

    assert intraday.resolve_slot(context) is RunSlot.INTRADAY_MIDDAY


def test_a_param_beats_the_scheduled_time():
    """수동 실행의 정식 경로다. 스케줄 시각이 있어도 Param이 이긴다."""
    context = {"logical_date": MIDDAY_AS_OF, "params": {"run_slot": "pre_close"}}

    assert intraday.resolve_slot(context) is RunSlot.PRE_CLOSE


def test_a_manual_run_without_a_param_fails_instead_of_guessing():
    """**벽시계로 떨어지지 않는다.**

    2026-08-21에 `market_thesis_analysis`를 가른 이유가 이것이다 — `logical_date`가 없는
    수동 실행이 벽시계로 떨어져 UI의 Trigger 버튼이 조용히 다른 슬롯을 돌렸다.
    """
    with pytest.raises(AirflowFailException, match="must choose run_slot"):
        intraday.resolve_slot({})


def test_an_off_schedule_logical_time_fails_too():
    # 11:00에 clear해 돌리면 어느 슬롯도 아니다. 가까운 슬롯으로 반올림하지 않는다.
    with pytest.raises(AirflowFailException, match="not an intraday slot"):
        intraday.resolve_slot({"logical_date": datetime(2026, 8, 26, 2, 0, tzinfo=UTC)})


@pytest.mark.parametrize("given", ["post_close", "pre_open", "nonsense"])
def test_a_non_intraday_slot_is_refused(given):
    with pytest.raises(AirflowFailException):
        intraday.resolve_slot({"params": {"run_slot": given}})


def test_the_as_of_time_is_the_slot_time_not_the_wall_clock():
    assert intraday.as_of(RUN_DATE, RunSlot.INTRADAY_MORNING) == MORNING_AS_OF
    assert intraday.as_of(RUN_DATE, RunSlot.PRE_CLOSE) == datetime(2026, 8, 26, 6, 0, tzinfo=UTC)


def test_the_param_offers_exactly_the_intraday_slots():
    enum = DAG.params.get_param(intraday.RUN_SLOT_PARAM).schema["enum"]

    assert set(enum) == {None, *(slot.value for slot in INTRADAY_SLOT_TIMES)}


# --- 대역 --------------------------------------------------------------------



# 기저율 조회 둘. 관측 상태를 만들 때마다 불리므로 가짜 커서가 순번 큐 밖으로 뺀다.
BASE_RATE_QUERIES = frozenset({base_rate.FORWARD_RETURNS, base_rate.UNCONDITIONAL_RETURNS})

class FakeCursor:
    def __init__(self, answers: list[Any]) -> None:
        self._answers = answers
        self._rows: Any = None
        self.calls: list[tuple[str, tuple]] = []

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def execute(self, statement: str, parameters: Any = ()) -> None:
        self.calls.append((statement, tuple(parameters)))
        if statement in BASE_RATE_QUERIES:
            # 기저율 조회는 순번 큐 밖이다. 관측 상태를 만들 때마다 두 번 더 불려서,
            # 큐에 넣으면 이 파일의 모든 테스트가 그 두 칸을 세고 있어야 한다.
            self._rows = []
            return
        self._rows = self._answers.pop(0)

    def fetchone(self) -> Any:
        return self._rows

    def fetchall(self) -> Any:
        return self._rows


class FakeConnection:
    def __init__(self, answers: list[Any]) -> None:
        self.cursor_object = FakeCursor(answers)

    def cursor(self) -> FakeCursor:
        return self.cursor_object


class FakeSubject:
    def __init__(self, code: str, kind: ThesisSubjectKind) -> None:
        self.code = code
        self.kind = kind


INDEX = FakeSubject("KOSPI", ThesisSubjectKind.INDEX)
STOCK = FakeSubject("005930", ThesisSubjectKind.STOCK)
TARGETS = (INDEX, STOCK)


def bar(minutes_before: int, close: str, previous: str, as_of: datetime = MORNING_AS_OF) -> tuple:
    return (as_of - timedelta(minutes=minutes_before), Decimal(close), Decimal(previous))


def forecast(connection: Any, slot: RunSlot = RunSlot.INTRADAY_MORNING) -> intraday.IntradayForecast:
    return intraday.IntradayForecast(connection, run_date=RUN_DATE, run_slot=slot)


# --- 봉 조회와 guard ----------------------------------------------------------


def test_the_bar_lookup_floors_at_the_open():
    """하한이 없으면 수집이 죽은 날에도 어제 마감 봉이 "지금 가격"으로 실린다."""
    connection = FakeConnection([[("KOSPI", *bar(5, "3150", "3125"))], [("005930", *bar(1, "71500", "70000"))]])

    forecast(connection)._bars(TARGETS, MORNING_AS_OF)

    for _, parameters in connection.cursor_object.calls:
        assert parameters[1] == MORNING_AS_OF
        assert parameters[2] == common.open_at(RUN_DATE)


def test_a_missing_bar_is_a_stopped_collector_not_a_delay():
    # 지수 봉만 있고 종목 봉이 없다. 0건은 지연이 아니다.
    connection = FakeConnection([[("KOSPI", *bar(5, "3150", "3125"))], []])

    with pytest.raises(common.ThesisNotReady, match="no intraday bars today for 005930"):
        forecast(connection).check_ready(TARGETS)


def test_a_stale_bar_waits_for_a_retry():
    connection = FakeConnection([[("KOSPI", *bar(40, "3150", "3125"))], [("005930", *bar(1, "71500", "70000"))]])

    with pytest.raises(common.ThesisNotReady, match="older than"):
        forecast(connection).check_ready(TARGETS)


def test_the_guard_hands_back_the_bars_it_checked():
    """관측 상태가 같은 값을 다시 읽으면 그 사이 들어온 봉 때문에 둘이 달라진다."""
    connection = FakeConnection(
        [
            [("KOSPI", *bar(5, "3150", "3125"))],
            [("005930", *bar(1, "71500", "70000"))],
            (MORNING_AS_OF - timedelta(minutes=10),),
        ]
    )

    bars = forecast(connection).check_ready(TARGETS)

    assert set(bars) == {"KOSPI", "005930"}


def test_a_dead_document_collector_does_not_pass_the_guard():
    connection = FakeConnection(
        [
            [("KOSPI", *bar(5, "3150", "3125"))],
            [("005930", *bar(1, "71500", "70000"))],
            (MORNING_AS_OF - timedelta(days=3),),
            (0, 0),
        ]
    )

    with pytest.raises(common.ThesisNotReady, match="has not caught up"):
        forecast(connection).check_ready(TARGETS)


def test_settled_closes_are_never_required_intraday():
    """`stock_investor_trade_daily`는 18:10에 들어온다. 장중에 요구하면 영영 안 돈다."""
    source = inspect.getsource(intraday.IntradayForecast.check_ready)

    assert "require_settled_closes" not in source


# --- 관측 상태 ----------------------------------------------------------------


def test_the_state_carries_the_price_and_the_bar_it_came_from():
    bars = {"KOSPI": bar(5, "3150", "3125")}
    state = forecast(FakeConnection([[], []])).observed_state((INDEX,), bars)

    observation = state.intraday["KOSPI"]
    assert isinstance(observation, IntradayObservation)
    assert observation.price == 3150.0
    assert observation.return_pct == 0.8
    assert observation.bar_at == bars["KOSPI"][0]


def test_the_state_leaves_the_settled_slots_empty():
    """오늘은 아직 마감이 없다. `index`·`stock`을 채우면 모델이 종가로 읽는다."""
    state = forecast(FakeConnection([[], []])).observed_state((INDEX,), {"KOSPI": bar(5, "3150", "3125")})

    assert state.session is None
    assert state.index == {}
    assert state.stock == {}


def test_a_zero_denominator_drops_the_target_instead_of_dividing():
    state = forecast(FakeConnection([[], []])).observed_state((INDEX,), {"KOSPI": bar(5, "3150", "0")})

    assert state.intraday == {}


# --- 오늘 앞 슬롯 되짚기 -------------------------------------------------------


def same_day_row(slot: str, as_of: datetime) -> tuple:
    return (slot, as_of, Decimal("0.62"), Decimal("0.30"), Decimal("0.08"), "오른다", "내린다", "횡보")


def test_the_morning_forecast_is_measured_against_the_previous_close():
    """`pre_open`의 기준가는 전일 종가다. **그 슬롯이 채점될 때 쓰이는 값과 같아야 한다.**"""
    current = bar(5, "3100", "3125", as_of=MIDDAY_AS_OF)
    connection = FakeConnection([[same_day_row("pre_open", MORNING_AS_OF)], []])

    same_day = forecast(connection, RunSlot.INTRADAY_MIDDAY).same_day((INDEX,), {"KOSPI": current})

    row = same_day["KOSPI"][0]
    assert row.run_slot is RunSlot.PRE_OPEN
    assert row.base_price == 3125.0
    assert row.current_price == 3100.0
    assert row.return_pct == -0.8


def test_an_earlier_intraday_slot_is_measured_against_its_own_bar():
    current = bar(5, "3100", "3125", as_of=MIDDAY_AS_OF)
    connection = FakeConnection(
        [
            [same_day_row("intraday_morning", MORNING_AS_OF)],
            # 10:35 직전 봉을 다시 묻는 조회
            [("KOSPI", *bar(5, "3150", "3125"))],
        ]
    )

    same_day = forecast(connection, RunSlot.INTRADAY_MIDDAY).same_day((INDEX,), {"KOSPI": current})

    row = same_day["KOSPI"][0]
    assert row.base_price == 3150.0
    assert row.return_pct == -1.59


def test_the_lookback_only_sees_slots_before_the_cutoff():
    connection = FakeConnection([[], []])

    forecast(connection, RunSlot.INTRADAY_MIDDAY).same_day((INDEX,), {"KOSPI": bar(5, "3100", "3125")})

    _, parameters = connection.cursor_object.calls[0]
    assert parameters == (RUN_DATE, "index", "KOSPI", MIDDAY_AS_OF)


def test_a_missing_base_price_drops_that_row_not_the_run():
    """되짚기는 재료이지 필수가 아니다. 없다고 추론을 멈추지 않는다."""
    connection = FakeConnection([[same_day_row("intraday_morning", MORNING_AS_OF)], []])

    same_day = forecast(connection, RunSlot.INTRADAY_MIDDAY).same_day((INDEX,), {"KOSPI": bar(5, "3100", "3125")})

    assert same_day == {}


# --- run ---------------------------------------------------------------------


class FakeStore:
    """`store.ThesisStore` 대역. LangChain 경로를 타지 않고 대상과 과거만 준다."""

    def __init__(self, connection: Any) -> None:
        self.connection = connection

    def subjects(self) -> tuple[FakeSubject, ...]:
        return TARGETS

    def past_theses(self, *, as_of_at: datetime, subject_code: str, n: int) -> tuple:
        del as_of_at, subject_code, n
        return ()


def test_run_hands_build_and_store_every_argument_it_requires(monkeypatch):
    """`run()`이 넘기는 kwargs를 `build_and_store`의 시그니처에 묶는다.

    `thesis.forecast`의 같은 이름 테스트와 짝이다 — 인자가 하나 늘 때 한쪽만 고쳐지면
    매 실행 `TypeError`이고, 충돌 없이 합쳐지는 자리라 테스트만이 잡는다.
    """
    from modules.thesis import store

    signature = inspect.signature(common.ThesisRun.build_and_store)
    received: dict[str, Any] = {}

    def fake_build_and_store(self: Any, **kwargs: Any) -> int:
        received.update(kwargs)
        return 2

    monkeypatch.setattr(common.ThesisRun, "skip_unless_open", lambda self: None)
    monkeypatch.setattr(intraday.IntradayForecast, "check_ready", lambda self, targets: {})
    monkeypatch.setattr(
        intraday.IntradayForecast, "observed_state", lambda self, targets, bars: {"intraday": {}}
    )
    monkeypatch.setattr(intraday.IntradayForecast, "same_day", lambda self, targets, bars: {})
    monkeypatch.setattr(store, "ThesisStore", FakeStore)
    monkeypatch.setattr(common.ThesisRun, "build_and_store", fake_build_and_store)

    run = forecast(FakeConnection([]), RunSlot.INTRADAY_AFTERNOON)
    written = run.run(dag_run_id="manual__1", try_number=1)

    assert written == 2
    signature.bind(run._run, **received)
    assert received["run_slot"] is RunSlot.INTRADAY_AFTERNOON
    # 창의 시작은 당일 09:00이다. 장후와 같은 창이라 계산이 `common`에 있다.
    assert received["macro_window_start"] == common.open_at(RUN_DATE)
    assert received["same_day"] == {}
    assert run._run.as_of_at == intraday.as_of(RUN_DATE, RunSlot.INTRADAY_AFTERNOON)
