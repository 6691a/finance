import json
from datetime import UTC, datetime, timedelta
from typing import Self

import pytest
from sqlalchemy import Table

from apps.models.analysis import ThesisOutcome
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

# 추론 지표. (지평, 채점, 평균 Brier, flat, 해설, 지지, 반박, 보류)
NO_THESIS: list = []
NO_THESIS_BACKLOG = (0, 0)


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
    thesis_rows=None,
    thesis_backlog=NO_THESIS_BACKLOG,
):
    connection = FakeConnection(
        HEALTHY_ROWS if rows is None else rows,
        list(failures),
        backlog,
        NO_THESIS if thesis_rows is None else thesis_rows,
        thesis_backlog,
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
    connection = FakeConnection(HEALTHY_ROWS, [], NO_BACKLOG, NO_THESIS, NO_THESIS_BACKLOG)
    ops.OpsBriefingReader(connection, TUESDAY).summary()

    assert connection.cursors[0].calls[0][1][0] == TUESDAY - timedelta(hours=ops.WINDOW_HOURS)


# --- 추론 품질 ---------------------------------------------------------------

# (지평, 채점, 평균 Brier, flat, 해설, 지지, 반박, 보류, 크기 채점, 평균 크기 오차)
# 크기 오차는 지평 0에만 있다. 나머지 지평은 크기를 받지 않아 NULL이 정상이다.
THESIS_ROWS = [
    (0, 12, 0.612, 3, 0, 0, 0, 0, 9, 0.42),
    (1, 12, 0.701, 4, 12, 1, 4, 7, 0, None),
    (5, 8, 0.588, 2, 8, 2, 1, 5, 0, None),
]


def test_the_thesis_section_is_absent_until_something_is_graded():
    # 추론이 정말 없는 날은 조회가 0행을 준다. 그건 정상이라 섹션을 안 그린다.
    text = _block_text(ops.render_blocks(summary()))

    assert "추론 품질" not in text


def test_the_thesis_section_marks_whether_it_beats_the_uniform_baseline():
    text = _block_text(ops.render_blocks(summary(thesis_rows=THESIS_ROWS)))

    assert "추론 품질" in text
    # 숫자만 보면 매번 0.667과 비교해야 한다. 기호가 갈라 준다.
    assert "0.612 ✓" in text
    assert "0.701 ✗" in text
    assert "0.588 ✓" in text
    assert str(ops.UNIFORM_BRIER) in text


def test_the_thesis_section_shows_the_size_error_with_its_own_sample_count():
    """부호가 뜻이다 — 양수면 과소추정이고 그것이 프롬프트를 고칠 방향이다."""
    text = _block_text(ops.render_blocks(summary(thesis_rows=THESIS_ROWS)))

    assert "+0.42%p 과소" in text
    # flat과 미채점이 빠져 Brier의 n(12)과 다르다. 표본을 함께 적지 않으면 섞어 읽는다.
    assert "n=9" in text


def test_a_horizon_without_a_size_grade_shows_a_dash():
    """크기는 지평 0에서만 받는다. 지평 1·3·5의 빈 칸은 결함이 아니다."""
    text = _block_text(ops.render_blocks(summary(thesis_rows=THESIS_ROWS)))

    # 지평 1 행에 0.00%p가 찍히면 "오차가 없었다"로 읽힌다.
    assert "0.00%p" not in text


def test_the_thesis_section_shows_the_verdict_split():
    text = _block_text(ops.render_blocks(summary(thesis_rows=THESIS_ROWS)))

    # 판정은 Brier와 다른 것을 잰다. 분포가 한눈에 보여야 한쪽으로 쏠린 것을 잡는다.
    assert "1/4/7" in text
    assert "2/1/5" in text


def test_the_thesis_section_carries_no_narrative_text():
    """해설 전문은 DB에만 둔다.

    매일 해설 몇 편이 운영 리포트에 쌓이면 정작 봐야 할 실패 목록이 묻힌다.
    """
    text = _block_text(ops.render_blocks(summary(thesis_rows=THESIS_ROWS)))

    assert "해설" not in text.replace("미해설", "")


def test_an_overdue_grade_breaks_the_all_green():
    # 목표 영업일이 지났는데도 안 된 것만 센다. 아직 안 지난 것은 정상이다.
    built = summary(thesis_rows=THESIS_ROWS, thesis_backlog=(3, 2))

    assert built.thesis.has_backlog
    assert not built.is_healthy
    assert "추론 적체 5건" in ops.render_text(built)


def test_a_horizon_without_grades_shows_a_dash_not_a_zero():
    text = _block_text(ops.render_blocks(summary(thesis_rows=[(5, 0, None, 0, 0, 0, 0, 0, 0, None)])))

    # 0.000은 "완벽했다"로 읽힌다. 채점이 없는 것과 완벽한 것을 가른다.
    assert "0.000" not in text


def test_fallback_text_is_one_line():
    assert "\n" not in ops.render_text(summary())


def test_the_horizon_list_matches_the_thesis_module():
    from modules.thesis.domain import HORIZON_DAYS

    # ops는 LLM 층을 import하지 않는다 — 감시하는 쪽이 감시받는 쪽을 부르면 그쪽이 죽은 날
    # 이 리포트도 같이 흔들린다. 대신 값을 한 벌 더 두고 여기서 대조한다.
    assert set(ops.THESIS_HORIZONS) == set(HORIZON_DAYS)


@pytest.mark.parametrize(
    ("statement", "table", "columns"),
    [
        (
            ops.BRIEFING_WINDOW,
            SourceRecord.__table__,
            ("source", "started_at", "completed_at", "status", "record_count"),
        ),
        (ops.RECENT_FAILURES, SourceRecord.__table__, ("source_key", "metadata")),
        (
            ops.THESIS_CALIBRATION,
            ThesisOutcome.__table__,
            ("horizon_days", "brier_score", "actual_outcome", "narrative", "verdict"),
        ),
        (ops.THESIS_BACKLOG, ThesisOutcome.__table__, ("horizon_days", "evaluated_at", "narrative")),
    ],
)
def test_queries_name_columns_that_exist(statement: str, table: Table, columns: tuple[str, ...]):
    for column in columns:
        assert column in table.columns, f"{table.name}.{column}"
        assert column in statement


def _block_text(blocks) -> str:
    return json.dumps(blocks, ensure_ascii=False)


def _activity_table(blocks) -> dict:
    """수집 현황 표 블록. 렌더에는 추론 지평 표도 있어 첫 table 만 집는다."""
    return next(block for block in blocks if block.get("type") == "table")


# 감시 리포트의 빈 조회. 둘 다 GROUP BY 없는 집계라 한 행이 반드시 오고, 안 오면 쿼리나
# 스키마가 깨진 것이다. 그때 0을 찍으면 적체 없음(=초록)으로 위장한다.


def test_a_document_summary_without_a_row_is_an_error_not_zero_backlog():
    with pytest.raises(ops.OpsQueryError):
        summary(backlog=None)


def test_a_thesis_backlog_without_a_row_is_an_error_not_zero_backlog():
    with pytest.raises(ops.OpsQueryError):
        summary(thesis_backlog=None)
