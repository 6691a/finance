"""`grade_followups`의 스킵 사유 분리.

`select_pending_grades.sql`에는 날짜 상한이 없어 오늘 만든 T+5 조합까지 전부 목록에
들어온다. 그래서 **전건 스킵은 정상 흐름이고 죽이면 안 된다.** 대신 전에는
`graded 0 of N` 한 줄이라 "아직 안 온 날짜"와 "종가가 안 들어온 결함"이 같아 보였다.

셋 중 마지막(목표일이 지났는데 종가가 없다)만 사람이 볼 일이라 그때만 경고를 남긴다.
"""

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from modules.thesis import review
from modules.thesis.state import RunSlot
from modules.thesis.store import PendingGrade
from modules.utility import KST_TIMEZONE

TODAY = datetime.now(UTC).astimezone(KST_TIMEZONE).date()


@contextmanager
def warnings_of(name: str) -> Iterator[list[logging.LogRecord]]:
    """그 로거의 WARNING을 모은다.

    **`caplog`를 쓰지 않는다.** `tests/migrations`가 Alembic `fileConfig`를 부르고 그것이
    `disable_existing_loggers` 기본값으로 이미 만들어진 로거를 꺼 버린다 — 이 파일만
    돌리면 통과하고 전체 실행에서 조용히 실패한다.
    `tests/modules/test_briefing_disclosures.py`가 같은 이유로 같은 형태를 쓴다.
    """
    records: list[logging.LogRecord] = []

    class Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    handler = Capture(level=logging.WARNING)
    logger = logging.getLogger(name)
    logger.addHandler(handler)
    previous_level, previously_disabled = logger.level, logger.disabled
    logger.setLevel(logging.WARNING)
    logger.disabled = False
    try:
        yield records
    finally:
        logger.removeHandler(handler)
        logger.setLevel(previous_level)
        logger.disabled = previously_disabled


def pending(horizon_days: int) -> PendingGrade:
    return PendingGrade(
        thesis_id=horizon_days,
        run_date=TODAY,
        as_of_at=datetime.now(UTC),
        subject_kind="index",
        subject_code="KOSPI",
        prob_up=Decimal("0.4"),
        prob_down=Decimal("0.3"),
        prob_flat=Decimal("0.3"),
        horizon_days=horizon_days,
        run_slot=RunSlot.PRE_OPEN,
    )


class FakeStore:
    """`ThesisStore`의 채점 경로만 흉내 낸다."""

    def __init__(self, connection, target_days: dict[int, date | None]) -> None:
        self.target_days = target_days
        self.stored: list[PendingGrade] = []

    def pending_grades(self):
        return tuple(pending(horizon) for horizon in sorted(self.target_days))

    def nth_open_day(self, run_date: date, horizon_days: int) -> date | None:
        return self.target_days[horizon_days]

    def store_grade(self, **kwargs) -> None:
        self.stored.append(kwargs["pending"])


class FakeConnection:
    def commit(self) -> None:
        pass

    def close(self) -> None:
        pass


@pytest.fixture
def graded(monkeypatch):
    """`target_days`와 종가 유무를 주면 `grade_followups`를 돌려 준다."""

    def run(target_days: dict[int, date | None], returns: dict[int, Decimal | None]):
        store_holder: list[FakeStore] = []

        def fake_store(connection):
            store = FakeStore(connection, target_days)
            store_holder.append(store)
            return store

        monkeypatch.setattr("modules.thesis.store.ThesisStore", fake_store)
        monkeypatch.setattr(review.common, "connection", FakeConnection)
        monkeypatch.setattr(review.common, "close_at", lambda day: datetime.now(UTC))
        monkeypatch.setattr(
            review, "get_current_context", lambda: {"dag_run": type("R", (), {"run_id": "manual__x"})()}
        )
        monkeypatch.setattr(review, "_horizon_return", lambda store, item, day: returns[item.horizon_days])
        count = review.grade_followups()
        return count, store_holder[0]

    return run


def test_a_pair_with_a_close_is_graded(graded):
    count, store = graded({1: TODAY - timedelta(days=1)}, {1: Decimal("1.2")})

    assert count == 1
    assert len(store.stored) == 1


def test_nothing_due_yet_is_not_a_failure(graded):
    """오늘 만든 T+5는 목표일이 안 왔다. 0건 채점이 정상이고 경고도 없어야 한다."""
    with warnings_of(review.logger.name) as records:
        count, _ = graded({5: TODAY + timedelta(days=3)}, {5: None})

    assert count == 0
    assert records == []


def test_a_past_target_day_without_a_close_warns(graded):
    """목표일이 지났는데 종가가 없다. 봉이 안 들어온 것이라 사람이 볼 일이다."""
    with warnings_of(review.logger.name) as records:
        count, _ = graded({1: TODAY - timedelta(days=2)}, {1: None})

    assert count == 0
    assert any("no close" in record.getMessage() for record in records)


def test_an_unfilled_calendar_is_counted_apart_from_a_missing_close(graded):
    """달력이 안 찬 것은 다음 실행이 집는다. 경고 대상이 아니다."""
    with warnings_of(review.logger.name) as records:
        count, _ = graded({3: None}, {3: None})

    assert count == 0
    assert records == []
