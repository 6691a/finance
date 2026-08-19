import json
from datetime import UTC, datetime, timedelta
from typing import Self

import pytest
from sqlalchemy import Table

from apps.models.raw import SourceRecord
from modules.briefing import ops

# KST 2026-08-18(화) 08:00. 전 영업일은 월요일이다.
TUESDAY = datetime(2026, 8, 17, 23, 0, tzinfo=UTC)
# KST 2026-08-16(일) 08:00. 주말이라 평일 전용 소스는 조용해도 정상이다.
SUNDAY = datetime(2026, 8, 15, 23, 0, tzinfo=UTC)

HEALTHY_ROWS = [
    (name, 4, 4, 0, 120, TUESDAY) for name, _, _ in [(source.name, 0, 0) for source in ops.EXPECTED_SOURCES]
]

NO_BACKLOG = (0, 0, 0, 0, 0, 0, None)


class FakeCursor:
    def __init__(self, results: list) -> None:
        self.results = results
        self.calls: list[tuple[str, tuple]] = []

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *args: object) -> bool:
        return False

    def execute(self, statement: str, parameters: tuple = ()) -> None:
        self.calls.append((statement, parameters))

    def fetchone(self):
        return self.results.pop(0)

    def fetchall(self):
        return self.results.pop(0)


class FakeConnection:
    def __init__(self, *results) -> None:
        self.results = list(results)
        self.cursors: list[FakeCursor] = []

    def cursor(self) -> FakeCursor:
        cursor = FakeCursor(self.results)
        self.cursors.append(cursor)
        return cursor


def summary(rows=None, failures=(), backlog=NO_BACKLOG, now=TUESDAY):
    connection = FakeConnection(HEALTHY_ROWS if rows is None else rows, list(failures), backlog)
    return ops.collect_summary(connection, now)


def test_all_green_reports_every_source_as_healthy():
    result = summary()

    assert result.is_healthy
    assert not result.silent
    assert not result.failures


def test_a_missing_source_is_reported_as_silent():
    rows = [row for row in HEALTHY_ROWS if row[0] != "fred"]

    result = summary(rows)

    assert not result.is_healthy
    assert "fred" in {source.name for source in result.silent}


def test_weekday_only_sources_stay_quiet_on_weekends():
    """KIS·DART는 평일에만 돈다. 주말에 조용하다고 알리면 매주 거짓 경보가 뜬다."""
    weekend_rows = [row for row in HEALTHY_ROWS if row[0] not in {"kis", "dart"}]

    result = summary(weekend_rows, now=SUNDAY)

    assert "kis" not in {source.name for source in result.silent}


def test_failed_runs_break_the_all_green():
    rows = [("fred", 2, 1, 1, 10, TUESDAY), *[row for row in HEALTHY_ROWS if row[0] != "fred"]]
    failures = [("fred", "DGS10", TUESDAY, '{"http_status": 500}')]

    result = summary(rows, failures)

    assert not result.is_healthy
    assert result.failures[0].source == "fred"
    assert "500" in _block_text(ops.render_blocks(result))


def test_assessment_backlog_is_caught_without_a_source_record():
    """문서 평가도 source_record를 남기지 않는다. 밀린 건수가 그 신호다."""
    piled_up = ops.ASSESSMENT_BACKLOG_LIMIT + 1
    result = summary(backlog=(0, 0, 0, 0, 0, piled_up, datetime(2026, 8, 14, 0, 0, tzinfo=UTC)))

    assert not result.is_healthy
    assert result.assessment_backlog == piled_up


def test_a_backlog_under_the_limit_is_normal():
    """매시 배치가 처리하는 중이면 몇십 건은 항상 밀려 있다. 그걸로 경보를 울리면 매일 뜬다."""
    result = summary(backlog=(0, 0, 0, 0, 0, ops.ASSESSMENT_BACKLOG_LIMIT, None))

    assert result.is_healthy


def test_a_healthy_report_is_still_sent_as_a_heartbeat():
    """침묵이 정상 신호이면 고장으로 인한 침묵과 구분할 수 없다."""
    text = _block_text(ops.render_blocks(summary()))

    assert "모든 수집 정상" in text


def test_unknown_sources_are_folded_into_one_row():
    """문서 피드는 DB 테이블이 정하고 수십 개다. 하나씩 그리면 표가 화면을 넘는다."""
    rows = [*HEALTHY_ROWS, ("yonhap", 12, 12, 0, 40, TUESDAY), ("hankyung", 12, 12, 0, 30, TUESDAY)]

    text = _block_text(ops.render_blocks(summary(rows)))

    assert "yonhap" not in text
    assert "문서 피드" in text


def test_the_window_is_a_full_day():
    connection = FakeConnection(HEALTHY_ROWS, [], NO_BACKLOG)
    ops.collect_summary(connection, TUESDAY)

    assert connection.cursors[0].calls[0][1][0] == TUESDAY - timedelta(hours=ops.WINDOW_HOURS)


def test_fallback_text_is_one_line():
    assert "\n" not in ops.render_text(summary())


@pytest.mark.parametrize(
    ("statement", "table", "columns"),
    [
        (
            ops.BRIEFING_WINDOW,
            SourceRecord.__table__,
            ("source", "started_at", "completed_at", "status", "record_count"),
        ),
        (ops.RECENT_FAILURES, SourceRecord.__table__, ("source_key", "metadata")),
    ],
)
def test_queries_name_columns_that_exist(statement: str, table: Table, columns: tuple[str, ...]):
    for column in columns:
        assert column in table.columns, f"{table.name}.{column}"
        assert column in statement


def _block_text(blocks) -> str:
    return json.dumps(blocks, ensure_ascii=False)
