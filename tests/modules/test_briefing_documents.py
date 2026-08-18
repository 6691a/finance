import json
from datetime import UTC, datetime, timedelta
from typing import Self

import pytest
from sqlalchemy import Table

from apps.models.content import Document, DocumentInstrument
from modules.briefing import documents

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

TOP_ROWS = [
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


def summary(count_row=COUNT_ROW, top_rows=None):
    connection = FakeConnection(count_row, TOP_ROWS if top_rows is None else top_rows)
    return documents.collect_summary(connection, NOW), connection


def test_counts_and_top_documents_are_parsed():
    result, _ = summary()

    assert (result.detected, result.assessed, result.backlog) == (14, 11, 3)
    assert (result.positive, result.negative, result.neutral) == (4, 5, 2)
    assert [document.title for document in result.top] == [
        "원/달러 환율 1,400원 돌파",
        "반도체 감산 계획 발표",
    ]
    assert result.top[0].tickers == ("005930",)


def test_the_window_is_measured_from_assessment_not_publication():
    """평가는 수집보다 늦게 따라온다. 이 리포트가 답하는 질문은 방금 무엇을 평가했나다."""
    _, connection = summary()

    since = connection.cursors[0].calls[0][1][0]
    assert since == NOW - timedelta(hours=documents.WINDOW_HOURS)
    assert "assessed_at" in documents.BRIEFING_SUMMARY


def test_an_empty_window_still_reports_the_backlog():
    result, _ = summary(EMPTY_COUNT_ROW, [])

    assert result.is_empty
    text = _block_text(documents.render_blocks(result, None))
    assert "신규 평가 문서 없음" in text
    assert "3" in text  # 대기 건수


def test_top_documents_are_linked_with_their_score():
    result, _ = summary()

    text = _block_text(documents.render_blocks(result, "요약"))

    assert "https://example.test/a" in text
    assert "7" in text
    assert "요약" in text


def test_comment_input_carries_counts_and_reasons():
    result, _ = summary()

    payload = json.loads(documents.comment_input(result))

    assert payload["counts"]["assessed"] == 11
    assert payload["top"][0]["reason"] == "수출주 원가에 직접 영향"
    assert payload["top"][0]["value_score"] == 7


def test_fallback_text_is_one_line():
    result, _ = summary()

    assert "\n" not in documents.render_text(result)


@pytest.mark.parametrize(
    ("statement", "table", "columns"),
    [
        (documents.BRIEFING_SUMMARY, Document.__table__, ("detected_at", "assessed_at", "direction")),
        (
            documents.BRIEFING_TOP,
            Document.__table__,
            ("title", "source_slug", "value_score", "canonical_url", "assessment"),
        ),
        (documents.BRIEFING_TOP, DocumentInstrument.__table__, ("document_id", "ticker")),
    ],
)
def test_queries_name_columns_that_exist(statement: str, table: Table, columns: tuple[str, ...]):
    for column in columns:
        assert column in table.columns, f"{table.name}.{column}"
        assert column in statement


def _block_text(blocks) -> str:
    return json.dumps(blocks, ensure_ascii=False)
