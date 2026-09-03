import json
from datetime import UTC, datetime, timedelta
from typing import Self

import pytest

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


def summary(
    rows=None,
    failures=(),
    backlog=NO_BACKLOG,
    now=TUESDAY,
):
    connection = FakeConnection(
        HEALTHY_ROWS if rows is None else rows,
        list(failures),
        backlog,
    )
    return ops.OpsBriefingReader(connection, now).summary()


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


def test_the_activity_table_shows_how_long_a_source_has_been_quiet():
    """무소식 섹션은 창 안에 한 번도 안 돈 소스만 잡는다. 20시간째 조용한 소스는 이 열이 잡는다."""
    stale = TUESDAY - timedelta(hours=20)
    rows = [(source.name, 4, 4, 0, 120, stale) for source in ops.EXPECTED_SOURCES]

    table = _activity_table(ops.render_blocks(summary(rows)))

    assert table["rows"][0][-1]["text"] == "마지막"
    assert all(row[-1]["text"] == "20h" for row in table["rows"][1:])


def test_a_source_without_a_finished_run_shows_a_dash():
    """실행은 있는데 완료가 없으면 0시간이 아니다. 숫자를 지어내지 않는다."""
    rows = [(source.name, 1, 0, 0, 0, None) for source in ops.EXPECTED_SOURCES]

    table = _activity_table(ops.render_blocks(summary(rows)))

    assert all(row[-1]["text"] == "-" for row in table["rows"][1:])


def test_every_activity_row_has_the_same_width_as_the_header():
    rows = [*HEALTHY_ROWS, ("yonhap", 12, 12, 0, 40, TUESDAY)]

    table = _activity_table(ops.render_blocks(summary(rows)))

    assert len({len(row) for row in table["rows"]}) == 1
    assert len(table["rows"][0]) == 5


def test_unknown_sources_are_folded_into_one_row():
    """문서 피드는 DB 테이블이 정하고 수십 개다. 하나씩 그리면 표가 화면을 넘는다."""
    rows = [*HEALTHY_ROWS, ("yonhap", 12, 12, 0, 40, TUESDAY), ("hankyung", 12, 12, 0, 30, TUESDAY)]

    text = _block_text(ops.render_blocks(summary(rows)))

    assert "yonhap" not in text
    assert "문서 피드" in text


def test_the_window_is_a_full_day():
    connection = FakeConnection(HEALTHY_ROWS, [], NO_BACKLOG)
    ops.OpsBriefingReader(connection, TUESDAY).summary()

    assert connection.cursors[0].calls[0][1][0] == TUESDAY - timedelta(hours=ops.WINDOW_HOURS)



def _block_text(blocks) -> str:
    return json.dumps(blocks, ensure_ascii=False)


def _activity_table(blocks) -> dict:
    """수집 현황 표 블록. 표가 여럿이 될 수 있어 첫 table만 집는다."""
    return next(block for block in blocks if block.get("type") == "table")


# 감시 리포트의 빈 조회. GROUP BY 없는 집계라 한 행이 반드시 오고, 안 오면 쿼리나 스키마가
# 깨진 것이다. 그때 0을 찍으면 적체 없음(=초록)으로 위장한다.


def test_a_document_summary_without_a_row_is_an_error_not_zero_backlog():
    with pytest.raises(ops.OpsQueryError):
        summary(backlog=None)


# ---------------------------------------------------------------------------
# G-53 — 돌긴 돌았는데 하루 종일 0건인 소스
# ---------------------------------------------------------------------------


def test_a_source_that_ran_all_day_with_zero_rows_is_reported_as_empty():
    """`succeeded`·`record_count=0`이 24시간 쌓여도 ✅였다. 이 저장소의 조용한 실패가 정확히
    그 모양이라(빈 칸→0, 30행 상한, 개장일 0봉) ops가 잡을 수 있는 마지막 자리였다."""
    rows = [
        (source.name, 288, 288, 0, 0, TUESDAY) if source.name == "kis" else (source.name, 4, 4, 0, 120, TUESDAY)
        for source in ops.EXPECTED_SOURCES
    ]

    result = summary(rows)

    assert [source.name for source in result.empty] == ["kis"]
    assert not result.silent
    assert result.is_healthy is False
    assert "0건 1곳" in ops.render_text(result)
    assert "국내 시세·수급(kis)" in _block_text(ops.render_blocks(result))


def test_zero_rows_on_a_weekend_are_normal():
    """주말은 장이 없어 0건이 정상이다. 무소식과 같은 주말 규칙을 쓴다."""
    rows = [(source.name, 4, 4, 0, 0, SUNDAY) for source in ops.EXPECTED_SOURCES]

    result = summary(rows, now=SUNDAY)

    assert not result.empty


def test_a_source_that_only_failed_is_a_failure_not_an_empty_success():
    """전부 실패한 소스는 실패 목록이 말한다. 0건 섹션은 "성공했는데 비었다"만 잡는다."""
    rows = [
        (source.name, 4, 0, 4, 0, TUESDAY) if source.name == "fred" else (source.name, 4, 4, 0, 120, TUESDAY)
        for source in ops.EXPECTED_SOURCES
    ]

    result = summary(rows)

    assert not result.empty
