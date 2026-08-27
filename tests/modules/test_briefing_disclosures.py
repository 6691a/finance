"""새 공시 알림의 조회·계산·렌더 계약. 설계는 docs/briefing/disclosure-briefing.md다."""

import logging
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Self

import pytest

from modules.briefing import disclosures
from modules.briefing.disclosures import DisclosureBatch, EarningsLine, Highlight, NewDisclosure

NOW = datetime(2026, 8, 27, 8, 40, tzinfo=UTC)
WINDOW_START = datetime(2026, 8, 27, 8, 30, tzinfo=UTC)
WINDOW_END = datetime(2026, 8, 27, 8, 40, tzinfo=UTC)

DISCLOSURE_ROWS = [
    (
        "20260827000123",
        "005930",
        "삼성전자",
        "연결재무제표기준영업(잠정)실적(공정공시)",
        date(2026, 8, 27),
        datetime(2026, 8, 27, 8, 31, tzinfo=UTC),
        None,
    ),
    (
        "20260827000456",
        "000660",
        "SK하이닉스",
        "임원·주요주주 특정증권등 소유상황보고서",
        date(2026, 8, 27),
        datetime(2026, 8, 27, 8, 35, tzinfo=UTC),
        "기재정정",
    ),
]

EARNINGS_ROWS = [
    ("20260827000123", "revenue", "CFS", date(2026, 6, 30), Decimal(74120000000000), Decimal(66000000000000)),
    (
        "20260827000123",
        "operating_profit",
        "CFS",
        date(2026, 6, 30),
        Decimal(9340000000000),
        Decimal(6960000000000),
    ),
    ("20260827000123", "net_income", "CFS", date(2026, 6, 30), Decimal(7100000000000), None),
]


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

    def executemany(self, statement: str, parameters) -> None:
        raise AssertionError("the briefing never writes")

    def fetchone(self):
        return self.results.pop(0)

    def fetchall(self):
        return self.results.pop(0)


class FakeConnection:
    def __init__(self, results: list) -> None:
        self.cursor_object = FakeCursor(results)

    def cursor(self) -> FakeCursor:
        return self.cursor_object


def collect(disclosure_rows=None, earnings_rows=None, **kwargs) -> DisclosureBatch:
    results = [DISCLOSURE_ROWS if disclosure_rows is None else disclosure_rows]
    if results[0]:
        results.append(EARNINGS_ROWS if earnings_rows is None else earnings_rows)
    connection = FakeConnection(results)
    return disclosures.collect_batch(connection, NOW, WINDOW_START, WINDOW_END, **kwargs)


def sample_batch(**kwargs) -> DisclosureBatch:
    return DisclosureBatch(generated_at=NOW, window_start=WINDOW_START, window_end=WINDOW_END, **kwargs)


# --- 조회 -----------------------------------------------------------------


def test_the_window_is_passed_to_the_query_as_the_data_interval():
    connection = FakeConnection([DISCLOSURE_ROWS, EARNINGS_ROWS])
    disclosures.collect_batch(connection, NOW, WINDOW_START, WINDOW_END)

    statement, parameters = connection.cursor_object.calls[0]
    assert parameters == (WINDOW_START, WINDOW_END)
    # 반열림 창이라야 한 공시가 두 창에 걸치지 않는다.
    assert "detected_at > bounds.window_start" in statement
    assert "detected_at <= bounds.window_end" in statement


def test_the_query_filters_on_detected_at_not_receipt_date():
    """접수일은 날짜뿐이라 시각으로 자를 수 없다."""
    connection = FakeConnection([DISCLOSURE_ROWS, EARNINGS_ROWS])
    disclosures.collect_batch(connection, NOW, WINDOW_START, WINDOW_END)

    statement, _ = connection.cursor_object.calls[0]
    assert "receipt_date >" not in statement
    assert "receipt_date <" not in statement


def test_an_empty_window_does_not_query_earnings():
    connection = FakeConnection([[]])
    batch = disclosures.collect_batch(connection, NOW, WINDOW_START, WINDOW_END)

    assert batch.is_empty
    assert len(connection.cursor_object.calls) == 1


def test_earnings_are_attached_to_their_disclosure():
    batch = collect()

    samsung, hynix = batch.disclosures
    assert [line.metric for line in samsung.earnings] == ["revenue", "operating_profit", "net_income"]
    assert hynix.earnings == ()


def test_the_earnings_query_asks_only_for_the_disclosures_in_this_window():
    connection = FakeConnection([DISCLOSURE_ROWS, EARNINGS_ROWS])
    disclosures.collect_batch(connection, NOW, WINDOW_START, WINDOW_END)

    _, parameters = connection.cursor_object.calls[1]
    assert parameters == (["20260827000123", "20260827000456"],)


def test_the_message_cap_drops_the_tail_and_warns():
    """조용히 자르지 않는다. 두 종목뿐이라 이 자리가 보이면 수집 쪽에 사고가 있는 것이다.

    **`caplog`를 쓰지 않는다.** `tests/migrations`가 Alembic `fileConfig`를 부르고, 그것이
    `disable_existing_loggers` 기본값으로 이미 만들어진 로거를 전부 꺼 버린다. 그래서 이
    테스트만 돌리면 통과하고 전체 실행에서는 조용히 실패했다. 핸들러를 직접 붙이고
    `disabled`도 되돌려서 잰다.
    """
    records: list[logging.LogRecord] = []

    class Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    handler = Capture(level=logging.WARNING)
    logger = logging.getLogger("modules.briefing.disclosures")
    logger.addHandler(handler)
    previous_level, previously_disabled = logger.level, logger.disabled
    logger.setLevel(logging.WARNING)
    logger.disabled = False
    try:
        batch = collect(max_disclosures=1)
    finally:
        logger.removeHandler(handler)
        logger.setLevel(previous_level)
        logger.disabled = previously_disabled

    assert len(batch.disclosures) == 1
    assert any("dropped 1 disclosures" in record.getMessage() for record in records)


# --- 계산 -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("current", "prior", "expected"),
    [
        (Decimal(120), Decimal(100), Decimal(20)),
        (Decimal(80), Decimal(100), Decimal(-20)),
        (Decimal(100), Decimal(100), Decimal(0)),
    ],
)
def test_year_over_year_is_a_percentage(current, prior, expected):
    assert disclosures.year_over_year(current, prior) == expected


@pytest.mark.parametrize("prior", [None, Decimal(0), Decimal(-100)])
def test_year_over_year_is_none_when_the_base_cannot_carry_a_ratio(prior):
    """결측·0·적자를 0%로 지어내지 않는다. 결측과 '변화 없음'이 같아지면 화면이 거짓말한다."""
    assert disclosures.year_over_year(Decimal(100), prior) is None


@pytest.mark.parametrize(
    ("amount", "expected"),
    [
        (Decimal(74120000000000), "74조 1,200억 원"),
        (Decimal(9000000000000), "9조 원"),
        (Decimal(934000000000), "9,340억 원"),
        (Decimal(12345678), "12,345,678원"),
        (Decimal(-934000000000), "-9,340억 원"),
    ],
)
def test_amounts_carry_their_unit(amount, expected):
    assert disclosures.format_amount(amount) == expected


# --- 렌더 -----------------------------------------------------------------


def test_every_disclosure_is_rendered_even_without_highlights():
    """고르는 것이 아니라 강조하는 것이다. 강조가 없어도 목록은 전부 나간다."""
    batch = collect()
    rendered = disclosures.render_blocks(batch)

    body = "".join(block["text"]["text"] for block in rendered if block["type"] == "section")
    assert "삼성전자" in body
    assert "SK하이닉스" in body
    assert "⭐" not in body


def test_a_highlighted_disclosure_gets_a_star_and_the_reason():
    batch = collect()
    highlights = (Highlight(rcept_no="20260827000123", reason="영업이익이 전년 대비 크게 늘었다"),)

    body = "".join(
        block["text"]["text"] for block in disclosures.render_blocks(batch, highlights) if block["type"] == "section"
    )
    assert "⭐ *삼성전자*" in body
    assert "영업이익이 전년 대비 크게 늘었다" in body
    assert "⭐ *SK하이닉스*" not in body


def test_earnings_lines_show_the_year_over_year_only_when_the_base_exists():
    batch = collect()
    body = "".join(
        block["text"]["text"] for block in disclosures.render_blocks(batch) if block["type"] == "section"
    )

    assert "매출 74조 1,200억 원 (전년 대비 +12.3%)" in body
    assert "영업이익 9조 3,400억 원 (전년 대비 +34.2%)" in body
    # 전년 값이 없는 지표는 금액만 나간다. 0%로 메우지 않는다.
    assert "순이익 7조 1,000억 원" in body
    assert "순이익 7조 1,000억 원 (전년" not in body


def test_a_separate_statement_scope_is_marked():
    """연결이 기본이라 별도일 때만 화면에 밝힌다."""
    batch = sample_batch(
        disclosures=(
            NewDisclosure(
                rcept_no="1",
                stock_code="005930",
                company_name="삼성전자",
                report_name="분기보고서",
                receipt_date=date(2026, 8, 27),
                detected_at=NOW,
                earnings=(
                    EarningsLine(metric="revenue", statement_scope="OFS", current_amount=Decimal(100000000000)),
                ),
            ),
        )
    )
    body = "".join(
        block["text"]["text"] for block in disclosures.render_blocks(batch) if block["type"] == "section"
    )
    assert "매출(별도) 1,000억 원" in body


def test_the_detected_time_is_labelled_as_first_seen():
    """공시 시각이 아니다. DART가 분 단위 접수 시각을 주지 않는다."""
    body = "".join(
        block["text"]["text"] for block in disclosures.render_blocks(collect()) if block["type"] == "section"
    )
    assert "최초 감지 08/27 17:31 KST" in body


def test_the_report_name_links_to_the_dart_viewer():
    body = "".join(
        block["text"]["text"] for block in disclosures.render_blocks(collect()) if block["type"] == "section"
    )
    assert "<https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260827000123|" in body


def test_the_viewer_url_is_the_one_the_thesis_tools_use():
    """상수를 두 벌 두지 않는다."""
    from modules.thesis_domain import DART_VIEWER_URL

    assert DART_VIEWER_URL.format(rcept_no="1") == NewDisclosure(
        rcept_no="1",
        stock_code="005930",
        company_name="삼성전자",
        report_name="x",
        receipt_date=date(2026, 8, 27),
        detected_at=NOW,
    ).viewer_url


def test_a_highlight_failure_is_shown_not_swallowed():
    """강조가 없는 알림과 실패한 알림은 구분돼야 한다."""
    rendered = disclosures.render_blocks(collect(), None, "boom")
    contexts = [block for block in rendered if block["type"] == "context"]
    assert "공시 강조 실패: boom" in contexts[0]["elements"][0]["text"]


def test_the_fallback_text_names_the_highlighted_disclosure_first():
    batch = collect()
    highlights = (Highlight(rcept_no="20260827000456", reason="정정이다"),)
    assert disclosures.render_text(batch, highlights) == "새 공시 2건 · SK하이닉스 임원·주요주주 특정증권등 소유상황보고서"
    assert disclosures.render_text(batch) == "새 공시 2건 · 삼성전자 연결재무제표기준영업(잠정)실적(공정공시)"


# --- 모델 입력 --------------------------------------------------------------


def test_the_pick_input_carries_kst_times():
    """UTC ISO를 그대로 실으면 모델이 '오늘'을 하루 어긋나게 읽는다."""
    payload = disclosures.pick_input(collect())
    assert "+09:00" in payload
    assert "detected_at_kst" in payload


def test_the_pick_input_carries_formatted_amounts_not_raw_numbers():
    payload = disclosures.pick_input(collect())
    assert "74조 1,200억 원" in payload
    assert "74120000000000" not in payload


def test_a_long_highlight_error_is_trimmed_before_it_reaches_slack():
    """Pydantic·LangChain 예외는 수백 자에 URL까지 달고 온다. 공시보다 오류가 길면 안 된다."""
    error = "ValidationError: 1 validation error for ChatXAI\n  " + "자세한 설명이 " * 40
    rendered = disclosures.render_blocks(collect(), None, error)

    shown = next(block for block in rendered if block["type"] == "context")["elements"][0]["text"]
    assert shown.startswith("⚠️ 공시 강조 실패: ValidationError: 1 validation error for ChatXAI")
    assert len(shown) < disclosures.MAX_ERROR_CHARS + 40
    assert "\n" not in shown
