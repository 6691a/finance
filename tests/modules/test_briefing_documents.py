import json
from datetime import UTC, datetime, timedelta
from typing import Self

import pytest
from sqlalchemy import Table

from apps.models.content import Document, DocumentInstrument
from modules.briefing import documents
from modules.briefing.picks import Pick

NOW = datetime(2026, 8, 18, 8, 0, tzinfo=UTC)

COUNT_ROW = (
    14,  # detected
    11,  # assessed
    4,  # positive
    5,  # negative
    2,  # neutral
    3,  # backlog
    datetime(2026, 8, 18, 5, 0, tzinfo=UTC),  # oldest pending
)

CANDIDATE_ROWS = [
    (
        41,
        "원/달러 환율 1,400원 돌파",
        "yonhap",
        "negative",
        7,
        "https://example.test/a",
        "수출주 원가에 직접 영향",
        ["005930"],
    ),
    (42, "반도체 감산 계획 발표", "hankyung", "positive", 6, "https://example.test/b", "공급 축소", []),
]

EMPTY_COUNT_ROW = (0, 0, 0, 0, 0, 3, datetime(2026, 8, 18, 5, 0, tzinfo=UTC))


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


def summary(count_row=COUNT_ROW, candidate_rows=None):
    connection = FakeConnection(count_row, CANDIDATE_ROWS if candidate_rows is None else candidate_rows)
    return documents.collect_summary(connection, NOW), connection


def pick(document_id: int, why: str = "", watch: bool = False) -> Pick:
    return Pick(document_id=document_id, why=why, watch=watch)


def test_counts_and_candidates_are_parsed():
    result, _ = summary()

    assert (result.detected, result.assessed, result.backlog) == (14, 11, 3)
    assert (result.positive, result.negative, result.neutral) == (4, 5, 2)
    assert [document.title for document in result.candidates] == [
        "원/달러 환율 1,400원 돌파",
        "반도체 감산 계획 발표",
    ]
    assert result.candidates[0].tickers == ("005930",)
    assert result.allowed_ids == frozenset({41, 42})


def test_the_window_is_measured_from_assessment_not_publication():
    """평가는 수집보다 늦게 따라온다. 이 리포트가 답하는 질문은 방금 무엇을 평가했나다."""
    _, connection = summary()

    since = connection.cursors[0].calls[0][1][0]
    assert since == NOW - timedelta(hours=documents.WINDOW_HOURS)
    assert "assessed_at" in documents.BRIEFING_SUMMARY


@pytest.mark.parametrize(
    ("now_utc", "expected_hours"),
    [
        (datetime(2026, 8, 17, 23, 0, tzinfo=UTC), 12.0),  # KST 08:00 ← 전날 20:00
        (datetime(2026, 8, 18, 3, 0, tzinfo=UTC), 4.0),  # KST 12:00 ← 08:00
        (datetime(2026, 8, 18, 6, 30, tzinfo=UTC), 3.5),  # KST 15:30 ← 12:00
        (datetime(2026, 8, 18, 8, 0, tzinfo=UTC), 1.5),  # KST 17:00 ← 15:30
        (datetime(2026, 8, 18, 11, 0, tzinfo=UTC), 3.0),  # KST 20:00 ← 17:00
        (datetime(2026, 8, 18, 3, 7, tzinfo=UTC), 4.1),  # 실행이 밀려도 직전 슬롯부터 잰다
        (datetime(2026, 8, 17, 22, 0, tzinfo=UTC), 14.0),  # KST 07:00 수동 실행 ← 전날 17:00
    ],
)
def test_the_window_reaches_back_to_the_previous_send_slot(now_utc, expected_hours):
    """발송이 하루 여러 번이라 창은 직전 발송 이후만. 24시간 고정이면 같은 문서가 매번 실린다."""
    assert documents.window_hours_at(now_utc) == expected_hours


def test_fractional_windows_render_without_a_trailing_zero():
    """4.0시간이 아니라 4시간, 3.5시간은 그대로. 창이 반시간 단위라 표기가 지저분해지기 쉽다."""
    connection = FakeConnection(EMPTY_COUNT_ROW, [])
    whole = documents.collect_summary(connection, NOW, window_hours=4.0)
    connection = FakeConnection(EMPTY_COUNT_ROW, [])
    fractional = documents.collect_summary(connection, NOW, window_hours=3.5)

    assert "최근 4시간" in _block_text(documents.render_blocks(whole))
    assert "최근 3.5시간" in _block_text(documents.render_blocks(fractional))


def test_an_empty_window_still_reports_the_backlog():
    result, _ = summary(EMPTY_COUNT_ROW, [])

    assert result.is_empty
    text = _block_text(documents.render_blocks(result))
    assert "신규 평가 문서 없음" in text
    assert "3" in text  # 대기 건수


def test_only_the_picked_documents_are_drawn():
    """점수가 아니라 선별이 무엇을 그릴지 정한다. 고르지 않은 문서는 채널에 나오지 않는다."""
    result, _ = summary()

    text = _block_text(documents.render_blocks(result, [pick(42, "공급 축소가 가격에 닿는다")]))

    assert "반도체 감산 계획 발표" in text
    assert "공급 축소가 가격에 닿는다" in text
    assert "원/달러 환율 1,400원 돌파" not in text


def test_watch_documents_get_their_own_section():
    result, _ = summary()

    text = _block_text(documents.render_blocks(result, [pick(42), pick(41, watch=True)]))

    assert "읽을 것" in text
    assert "주의" in text


def test_picking_nothing_says_so_instead_of_falling_back_to_the_score():
    """한산한 날 억지로 채우지 않는 것이 설계다. 빈 선별과 실패는 다른 결과다."""
    result, _ = summary()

    text = _block_text(documents.render_blocks(result, []))

    assert "읽을 만한 문서 없음" in text
    assert "원/달러 환율 1,400원 돌파" not in text


def test_a_failed_pick_falls_back_to_the_score_order_and_says_so():
    """선별이 실패해도 리포트는 나간다. 다만 실패했다는 사실은 채널에 남는다."""
    result, _ = summary()

    text = _block_text(documents.render_blocks(result, None, "read timeout"))

    assert "원/달러 환율 1,400원 돌파" in text
    assert "문서 선별 실패" in text
    assert "read timeout" in text


def test_pick_input_carries_every_candidate_with_its_id():
    result, _ = summary()

    payload = json.loads(documents.pick_input(result))

    assert payload["counts"]["assessed"] == 11
    assert [document["document_id"] for document in payload["documents"]] == [41, 42]
    assert payload["documents"][0]["reason"] == "수출주 원가에 직접 영향"


def test_fallback_text_names_the_first_picked_document():
    result, _ = summary()

    text = documents.render_text(result, [pick(42)])

    assert "\n" not in text
    assert "반도체 감산 계획 발표" in text


@pytest.mark.parametrize(
    ("statement", "table", "columns"),
    [
        (documents.BRIEFING_SUMMARY, Document.__table__, ("detected_at", "assessed_at", "direction")),
        (
            documents.BRIEFING_CANDIDATES,
            Document.__table__,
            ("title", "source_slug", "value_score", "canonical_url", "assessment"),
        ),
        (documents.BRIEFING_CANDIDATES, DocumentInstrument.__table__, ("document_id", "ticker")),
    ],
)
def test_queries_name_columns_that_exist(statement: str, table: Table, columns: tuple[str, ...]):
    for column in columns:
        assert column in table.columns, f"{table.name}.{column}"
        assert column in statement


def _block_text(blocks) -> str:
    return json.dumps(blocks, ensure_ascii=False)
