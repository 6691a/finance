"""애프터마켓 리뷰 DAG와 `modules/thesis/nxt_review.py`.

추론의 알맹이는 `modules/thesis_*.py` 여섯에 있고 `tests/modules/test_thesis_pipeline.py`가 덮는다.
여기 남은 것은 태스크 그래프, 이 슬롯의 시각 계산, 그리고 `NxtAfterHoursReview`다.
"""

import inspect
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any, Self

import pytest
from airflow.exceptions import AirflowSkipException

from dags import market_thesis_nxt_review as dag_module
from modules.technical import base_rate
from modules.thesis import common, nxt_review
from modules.thesis.nxt_review import AfterHoursBar, NxtAfterHoursReview

DAG = dag_module.market_thesis_nxt_review

RUN_DATE = date(2026, 8, 21)


def _row(
    stock_code: str = "005930",
    *,
    bars: int = 260,
    final: bool = True,
    last_close: str = "270000",
    settled: str | None = "281500",
    return_pct: str | None = "-4.09",
) -> tuple:
    """`select_nxt_after_hours.sql`이 주는 한 줄. 컬럼 순서가 계약이다."""
    return (
        stock_code,
        datetime(2026, 8, 21, 10, 59, tzinfo=UTC),
        Decimal(last_close),
        bars,
        final,
        Decimal(settled) if settled is not None else None,
        Decimal(return_pct) if return_pct is not None else None,
    )



# 기저율 조회 둘. 관측 상태를 만들 때마다 불리므로 가짜 커서가 순번 큐 밖으로 뺀다.
BASE_RATE_QUERIES = frozenset({base_rate.FORWARD_RETURNS, base_rate.UNCONDITIONAL_RETURNS})

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
        # psycopg는 위치(tuple)와 이름(dict) 둘 다 받는다. dict를 tuple로 바꾸면 키만 남는다.
        self.calls.append((statement, dict(parameters) if isinstance(parameters, dict) else tuple(parameters)))
        if statement in BASE_RATE_QUERIES:
            # 기저율 조회는 순번 큐 밖이다. 관측 상태를 만들 때마다 두 번 더 불려서,
            # 큐에 넣으면 이 파일의 모든 테스트가 그 두 칸을 세고 있어야 한다.
            self._row = []
            return
        # 답을 다 쓰면 빈 결과다. 조회가 하나 늘 때마다 모든 테스트의 픽스처를 늘리지 않는다.
        self._row = self._answers.pop(0) if self._answers else []

    def fetchone(self) -> Any:
        return self._row

    def fetchall(self) -> Any:
        return self._row


class FakeConnection:
    def __init__(self, answers: list[Any]) -> None:
        self._cursor = FakeCursor(answers)

    def cursor(self) -> FakeCursor:
        return self._cursor

    @property
    def calls(self) -> list[tuple[str, tuple]]:
        return self._cursor.calls


# `thesis.subjects`가 `instrument/select_watched.sql`로 읽는 행. 지수 둘은 그 함수가
# 하드코딩으로 더한다 — 그래서 이 대역만 주면 대상 넷(지수 둘 + 종목 둘)이 나온다.
WATCHED_ROWS = [("000660", "SK하이닉스"), ("005930", "삼성전자")]


# --- DAG ---------------------------------------------------------------------


def test_the_dag_owns_one_slot_only():
    """**슬롯이 시계가 아니라 DAG로 정해진다.** 이 파일이 따로 있는 이유다."""
    assert DAG.schedule == "0 21 * * 1-5"
    assert str(DAG.timetable.timezone) == "Asia/Seoul"
    assert DAG.max_active_runs == 1
    assert nxt_review.SLOT == "post_nxt_close"


def test_the_tasks_run_in_one_line():
    """채점도 해설도 없다. 리뷰는 예측이 아니고 해설 루프는 이 슬롯을 빼 둔다."""
    assert set(DAG.task_dict) == {"build_thesis", "notify_slack"}
    assert DAG.task_dict["notify_slack"].upstream_task_ids == {"build_thesis"}


def test_retries_give_the_readiness_guard_room_to_wait():
    assert DAG.default_args["retries"] == 3
    assert DAG.default_args["retry_delay"] == timedelta(minutes=10)
    assert DAG.task_dict["build_thesis"].execution_timeout == common.BUILD_TIMEOUT


def test_the_dag_carries_its_display_metadata():
    assert DAG.dag_display_name.startswith("🧠")
    assert DAG.description
    assert DAG.doc_md
    param = DAG.params.get_param(common.RUN_DATE_PARAM)
    assert param.description
    assert param.schema["title"]


# --- 시각 계산 (감쌀 상태가 없어 모듈 함수다) -----------------------------------


def test_the_as_of_time_is_the_nxt_close_not_the_run_time():
    """21:00에 돌지만 모델이 보는 것은 20:00 마감까지다."""
    assert nxt_review.as_of(RUN_DATE) == datetime(2026, 8, 21, 11, 0, tzinfo=UTC)


def test_the_macro_window_starts_at_the_krx_close():
    """창의 시작은 15:30이다. 장후 슬롯의 창(당일 09:00부터)과 다르다."""
    assert nxt_review.macro_window_start(RUN_DATE) == datetime(2026, 8, 21, 6, 30, tzinfo=UTC)


def test_the_after_hours_window_covers_the_evening_only():
    """프리·주간 봉이 섞이면 애프터 등락이 하루 등락이 된다."""
    start, end = nxt_review.after_hours_window(RUN_DATE)

    assert start == datetime(2026, 8, 21, 6, 30, tzinfo=UTC)
    assert end == datetime(2026, 8, 21, 11, 0, tzinfo=UTC)


# --- AfterHoursBar -----------------------------------------------------------


def test_the_bar_model_names_every_column():
    """SQL이 열을 늘려도 인덱스가 밀리는 사고는 `from_row` 하나에서 멈춘다."""
    bar = AfterHoursBar.from_row(_row())

    assert bar.stock_code == "005930"
    assert bar.last_close == Decimal(270000)
    assert bar.bar_count == 260
    assert bar.all_final is True
    assert bar.settled_close == Decimal(281500)
    assert bar.return_pct == Decimal("-4.09")


def test_the_bar_model_is_frozen():
    """재시도 경로에서 값이 바뀌면 원본과 프롬프트가 어긋난다."""
    bar = AfterHoursBar.from_row(_row())

    with pytest.raises(Exception, match="frozen|Instance is frozen"):
        bar.last_close = Decimal(1)


# --- NxtAfterHoursReview -----------------------------------------------------


def test_the_review_targets_stocks_only():
    """NXT에는 지수가 없다. 지수를 대상에 두면 매번 "안 움직였다"가 나온다."""
    review = NxtAfterHoursReview(FakeConnection([WATCHED_ROWS]), run_date=RUN_DATE)

    assert review.watched == ["000660", "005930"]


def test_the_review_reads_each_lookup_once():
    """대상과 봉은 두 곳에서 쓰인다. 두 번 조회하면 그 사이 값이 갈릴 수 있다."""
    connection = FakeConnection([WATCHED_ROWS, [_row("005930")]])
    review = NxtAfterHoursReview(connection, run_date=RUN_DATE)

    assert review.bars == review.bars
    assert len([call for call in connection.calls if "stock_bar" in call[0]]) == 1


def test_the_bar_query_gets_the_evening_window():
    """KST 경계 계산은 파이썬이 한다. SQL에 시간대 변환을 넣지 않는다."""
    connection = FakeConnection([WATCHED_ROWS, [_row("005930")]])

    assert NxtAfterHoursReview(connection, run_date=RUN_DATE).bars

    statement, parameters = connection.calls[1]
    assert "stock_bar" in statement
    assert parameters[0] == datetime(2026, 8, 21, 6, 30, tzinfo=UTC)
    assert parameters[1] == datetime(2026, 8, 21, 11, 0, tzinfo=UTC)
    assert parameters[2] == RUN_DATE


def test_a_day_without_after_hours_bars_is_skipped_not_failed():
    """체결이 진짜 0인 날과 수집 실패를 응답만으로 가를 수 없다. 죽이지 않는다."""
    review = NxtAfterHoursReview(FakeConnection([WATCHED_ROWS, []]), run_date=RUN_DATE)

    with pytest.raises(AirflowSkipException, match="no NXT after-hours bars"):
        review.check_ready()


def test_provisional_only_bars_wait_for_the_rest_backfill():
    """21:00에 잠정만 있으면 20:05 백필이 안 돈 것이다. 그 위에 추론하면 영영 못 고친다."""
    rows = [_row("005930", final=False), _row("000660", final=False)]
    review = NxtAfterHoursReview(FakeConnection([WATCHED_ROWS, rows]), run_date=RUN_DATE)

    with pytest.raises(common.ThesisNotReady, match="all provisional"):
        review.check_ready()


def test_the_guard_waits_for_every_settled_close():
    """확정 종가는 애프터 등락률의 분모다. 하나라도 빠지면 값을 만들 수 없다."""
    review = NxtAfterHoursReview(FakeConnection([WATCHED_ROWS, [_row("005930")], (1,)]), run_date=RUN_DATE)

    with pytest.raises(common.ThesisNotReady, match="settled closes"):
        review.check_ready()


def test_the_guard_passes_when_final_bars_and_closes_are_in():
    rows = [_row("005930"), _row("000660")]
    review = NxtAfterHoursReview(FakeConnection([WATCHED_ROWS, rows, (2,)]), run_date=RUN_DATE)

    review.check_ready()


def test_the_observed_state_keeps_the_two_sessions_apart():
    """애프터 등락만 주면 "왜 애프터에서 더 빠졌나"를 말할 수 없다."""
    # 호출 순서대로: 대상 조회, `observed_state`의 지수·종목 조회 둘, 기술적 관측(일봉 하나 +
    # 종목마다 신호 하나), 그 뒤 봉 조회.
    connection = FakeConnection(
        [
            WATCHED_ROWS,
            [("KOSPI", 3150, 3125)],
            [("005930", 281500)],
            [],  # technical/select_history.sql
            [],  # technical_signal/select_thesis_recent.sql · 000660
            [],  # technical_signal/select_thesis_recent.sql · 005930
            [_row("005930")],
        ]
    )

    state = NxtAfterHoursReview(connection, run_date=RUN_DATE).observed_state()

    assert state.session == RUN_DATE
    assert state.regular["005930"].close == 281500.0
    assert state.after_hours["005930"].return_pct == -4.09
    assert state.after_hours["005930"].bars == 260
    # 지수는 subject가 아니라 맥락이다. 키 이름이 그것을 밝힌다.
    payload = state.model_dump(mode="json")
    assert "index_regular" in payload
    assert "index" not in payload


def test_a_flat_after_hours_stock_is_kept_not_dropped():
    """보합(정확히 0)을 결측으로 취급하면 모델이 "애프터 데이터 없음"으로 읽는다."""
    connection = FakeConnection(
        [
            WATCHED_ROWS,
            [("KOSPI", 3150, 3125)],
            [("005930", 281500)],
            [],  # technical/select_history.sql
            [],  # technical_signal/select_thesis_recent.sql · 000660
            [],  # technical_signal/select_thesis_recent.sql · 005930
            [_row("005930", return_pct="0")],
        ]
    )

    state = NxtAfterHoursReview(connection, run_date=RUN_DATE).observed_state()

    assert state.after_hours["005930"].return_pct == 0.0


def test_a_stock_without_a_settled_close_is_left_out_not_zeroed():
    """등락률이 NULL이면 그 종목은 관측 상태에 없다. 0으로 꾸미지 않는다."""
    rows = [_row("005930", settled=None, return_pct=None)]
    connection = FakeConnection([WATCHED_ROWS, [], [], rows])

    state = NxtAfterHoursReview(connection, run_date=RUN_DATE).observed_state()

    assert state.after_hours == {}


# --- run() ---------------------------------------------------------------------


def test_run_hands_build_and_store_every_argument_it_requires(monkeypatch):
    """`run()`이 넘기는 kwargs를 `build_and_store`의 시그니처에 묶는다.

    2026-08-23에 형제 브랜치 둘을 합치며 `past`가 필수 인자로 생겼는데 이 호출은 그것을
    모른 채 합쳐져 매 실행 `TypeError`였다. 충돌 없이 합쳐진 자리라 테스트만이 잡는다.
    """
    signature = inspect.signature(common.ThesisRun.build_and_store)
    received: dict[str, Any] = {}

    def fake_build_and_store(self: Any, **kwargs: Any) -> int:
        received.update(kwargs)
        return 2

    monkeypatch.setattr(common.ThesisRun, "skip_unless_open", lambda self: None)
    monkeypatch.setattr(NxtAfterHoursReview, "check_ready", lambda self: None)
    monkeypatch.setattr(NxtAfterHoursReview, "observed_state", lambda self: {"session": "2026-08-21"})
    monkeypatch.setattr(NxtAfterHoursReview, "targets", property(lambda self: ()))
    monkeypatch.setattr(common.ThesisRun, "build_and_store", fake_build_and_store)

    review = NxtAfterHoursReview(FakeConnection([]), run_date=RUN_DATE)
    written = review.run(dag_run_id="manual__1", try_number=1)

    assert written == 2
    # 필수 인자가 빠지면 여기서 `TypeError`다.
    signature.bind(review, **received)
    assert received["run_slot"].value == "post_nxt_close"
    # 기준 시각은 이제 인자가 아니라 `ThesisRun`의 상태다.
    assert review._run.as_of_at == nxt_review.as_of(RUN_DATE)
    # 리뷰는 해석이라 과거 예측 성적을 싣지 않는다. 장후 리뷰와 같다.
    assert received["past"] == {}
