import json
import re
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Self
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import Table

from apps.models.market import QuoteBar as QuoteBarModel
from apps.models.raw import SourceRecord
from modules.collectors.kis import (
    MAX_BARS_PER_REQUEST,
    QUOTE_BAR_UPSERT,
    SOURCE_RECORD_INSERT,
    DomesticFuture,
    KisPayloadError,
    KisResponse,
    KisResultError,
    SymbolOutcome,
    expiry_date,
    front_contract,
    parse_bars,
    store_bars,
)

KST = ZoneInfo("Asia/Seoul")
SOURCE_RECORD_ID = 1
CONTRACT = "A01609"

# 픽스처 봉은 KST 15:43~15:45. UTC 로는 06:43~06:45 다.
BAR_HOURS = ("154500", "154400", "154300")  # KIS 는 최신순으로 준다
BAR_DATE = "20260807"
STARTED_AT = datetime(2026, 8, 7, 6, 46, tzinfo=UTC)
COMPLETED_AT = datetime(2026, 8, 7, 6, 46, 1, tzinfo=UTC)
WINDOW_START = datetime(2026, 8, 7, 6, 30, tzinfo=UTC)


def chart_payload(
    hours: tuple[str, ...] = BAR_HOURS,
    business_date: str = BAR_DATE,
    closes: tuple[str, ...] | None = None,
    previous_close: str = "         981.15",
    rt_cd: str = "0",
    msg_cd: str = "",
    msg1: str = "정상처리 되었습니다.",
    name: str = "F 202609",
    volumes: tuple[str, ...] | None = None,
) -> bytes:
    """KIS 분봉 응답 본문. 값은 전부 문자열이고 공백 패딩이 붙는다."""
    closes = closes if closes is not None else tuple(f"      {976 + i}.15" for i in range(len(hours)))
    volumes = volumes if volumes is not None else tuple(str(100 + i) for i in range(len(hours)))
    return json.dumps(
        {
            "rt_cd": rt_cd,
            "msg_cd": msg_cd,
            "msg1": msg1,
            "output1": {
                "hts_kor_isnm": name,
                "futs_prdy_clpr": previous_close,
                "futs_prpr": "      979.15",
                "kospi200_nmix": "      978.00",
            },
            "output2": [
                {
                    "stck_bsop_date": business_date,
                    "stck_cntg_hour": hour,
                    "futs_prpr": closes[i],
                    "futs_oprc": "      977.46",
                    "futs_hgpr": "      980.90",
                    "futs_lwpr": "      976.06",
                    "cntg_vol": volumes[i],
                    "acml_tr_pbmn": "6396104953",
                }
                for i, hour in enumerate(hours)
            ],
        }
    ).encode("utf-8")


def response_for(body: bytes | None = None, contract: str = CONTRACT) -> KisResponse:
    return KisResponse(
        symbol=DomesticFuture.KOSPI200_FUT.value,
        contract_code=contract,
        body=body if body is not None else chart_payload(),
        status=200,
        started_at=STARTED_AT,
        completed_at=COMPLETED_AT,
    )


class FakeCursor:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple]] = []

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *args: object) -> bool:
        return False

    def execute(self, statement: str, parameters: tuple) -> None:
        self.calls.append((statement, parameters))

    def fetchone(self) -> tuple[int]:
        return (SOURCE_RECORD_ID,)


class FakeConnection:
    def __init__(self) -> None:
        self.recorded_cursor = FakeCursor()

    def cursor(self) -> FakeCursor:
        return self.recorded_cursor


def inserted_columns(statement: str) -> tuple[str, ...]:
    columns = re.search(r"INSERT INTO \w+ \(([^)]+)\)", statement, re.DOTALL)
    assert columns is not None
    names = re.sub(r"--[^\n]*", "", columns.group(1))
    return tuple(name.strip() for name in names.split(",") if name.strip())


def placeholder_count(statement: str) -> int:
    values = re.search(r"VALUES \(([^)]+)\)", statement, re.DOTALL)
    assert values is not None
    return values.group(1).count("%s")


def required_columns(table: Table) -> set[str]:
    return {
        column.name
        for column in table.columns
        if not column.nullable and column.server_default is None and not column.primary_key
    }


def upsert_calls(cursor: FakeCursor) -> list[tuple]:
    return [parameters for statement, parameters in cursor.calls if "INSERT INTO quote_bar" in statement]


def test_quote_bar_upsert_matches_the_model_and_its_natural_key():
    table = QuoteBarModel.__table__
    columns = inserted_columns(QUOTE_BAR_UPSERT)

    assert set(columns) <= {column.name for column in table.columns}
    assert required_columns(table) <= set(columns)
    assert placeholder_count(QUOTE_BAR_UPSERT) == len(columns)
    # 월물 코드를 저장하지 않으면 롤오버 갭을 시장 급변과 구분할 수 없다.
    assert "contract_code" in columns


def test_source_record_insert_matches_the_model():
    table = SourceRecord.__table__
    columns = inserted_columns(SOURCE_RECORD_INSERT)

    assert set(columns) <= {column.name for column in table.columns}
    assert required_columns(table) <= set(columns)
    assert "RETURNING id" in SOURCE_RECORD_INSERT


@pytest.mark.parametrize(
    ("year", "month", "expected"),
    [
        (2026, 9, date(2026, 9, 10)),
        (2026, 12, date(2026, 12, 10)),
        (2027, 3, date(2027, 3, 11)),
        (2027, 6, date(2027, 6, 10)),
    ],
)
def test_expiry_is_the_second_thursday(year, month, expected):
    result = expiry_date(year, month)

    assert result == expected
    assert result.weekday() == 3  # 목요일


@pytest.mark.parametrize(
    ("today", "expected"),
    [
        (date(2026, 8, 8), "A01609"),   # 9월물 만기 전
        (date(2026, 9, 10), "A01609"),  # 만기 당일은 아직 거래된다
        (date(2026, 9, 11), "A01612"),  # 만기 다음 날 롤오버
        (date(2026, 12, 11), "A01703"),  # 해가 바뀌면 연도 자릿수가 6에서 7로 넘어간다
    ],
)
def test_front_contract_rolls_after_expiry(today, expected):
    assert front_contract(DomesticFuture.KOSPI200_FUT, today) == expected


def test_contract_code_uses_the_regular_product_not_the_mini():
    # 미니(A056)는 계약 크기가 1/5이고 거래량도 적다. 정규(A016)를 써야 한다.
    assert DomesticFuture.KOSPI200_FUT.product_digit == "1"
    assert front_contract(DomesticFuture.KOSPI200_FUT, date(2026, 8, 8)).startswith("A01")


def test_parse_reads_bars_in_utc_and_sorts_them_ascending():
    parsed = parse_bars(chart_payload())

    assert len(parsed.bars) == 3
    # KIS 는 최신순으로 준다. 저장 순서를 Yahoo 쪽과 맞추려면 뒤집어야 한다.
    assert [bar.bar_at for bar in parsed.bars] == sorted(bar.bar_at for bar in parsed.bars)
    # KST 15:43 == UTC 06:43
    assert parsed.bars[0].bar_at == datetime(2026, 8, 7, 6, 43, tzinfo=UTC)
    assert parsed.bars[-1].bar_at == datetime(2026, 8, 7, 6, 45, tzinfo=UTC)
    assert parsed.latest_bar_at == datetime(2026, 8, 7, 6, 45, tzinfo=UTC)
    assert parsed.contract_name == "F 202609"


def test_parse_strips_the_padding_kis_puts_around_numbers():
    parsed = parse_bars(chart_payload())

    assert parsed.bars[0].previous_close == Decimal("981.15")
    assert parsed.bars[0].high == Decimal("980.90")
    assert parsed.bars[0].volume == 102


def test_parse_raises_the_reason_when_kis_reports_a_body_error():
    # 권한이 없거나 종목코드가 틀리면 HTTP 200 에 rt_cd 로 온다.
    body = chart_payload(rt_cd="1", msg_cd="EGW00123", msg1="종목코드 오류")

    with pytest.raises(KisResultError, match="EGW00123"):
        parse_bars(body)


def test_parse_rejects_an_empty_chart():
    # 월물 규칙이 어긋나면 빈 배열이 온다. 성공으로 넘기면 조용한 구멍이 남는다.
    with pytest.raises(KisPayloadError, match="empty chart"):
        parse_bars(chart_payload(hours=(), closes=(), volumes=()))


def test_parse_rejects_a_missing_previous_close():
    with pytest.raises(KisPayloadError, match="previous close"):
        parse_bars(chart_payload(previous_close="   "))


def test_parse_rejects_an_unparsable_timestamp():
    with pytest.raises(KisPayloadError, match="timestamp"):
        parse_bars(chart_payload(hours=("99991",)))


def test_parse_rejects_a_non_numeric_price():
    with pytest.raises(KisPayloadError, match="close"):
        parse_bars(chart_payload(closes=("  -  ",), hours=("154500",), volumes=("1",)))


def test_parse_returns_no_bars_outside_the_session():
    """장 밖은 실패가 아니다.

    KOSPI200 선물은 09:00~15:45 KST 에만 거래된다. 야간장은 이 API 로 오지 않으므로 그
    밖의 시간에는 최근 구간에 새 봉이 없다. 이걸 실패로 다루면 개장 전마다 DAG 가 빨개진다.
    """
    parsed = parse_bars(chart_payload(), since=datetime(2026, 8, 7, 12, 0, tzinfo=UTC))

    assert parsed.bars == ()
    # 거르기 전 마지막 봉 시각은 남는다. 며칠씩 안 움직이면 조용히 끊긴 것이다.
    assert parsed.latest_bar_at == datetime(2026, 8, 7, 6, 45, tzinfo=UTC)


def test_store_writes_one_source_record_without_the_payload():
    connection = FakeConnection()

    store_bars(connection, [response_for()], WINDOW_START)

    statements = [statement for statement, _ in connection.recorded_cursor.calls]
    assert sum("INSERT INTO source_record" in statement for statement in statements) == 1

    _, parameters = connection.recorded_cursor.calls[0]
    source_type, source, source_key, started, completed, status, record_count, payload, metadata = parameters
    assert (source_type, source, source_key) == ("api", "kis", "intraday_1m")
    assert (started, completed) == (STARTED_AT, COMPLETED_AT)
    assert status == "succeeded"
    assert record_count == 3
    assert payload is None
    assert json.loads(metadata)["symbols"][0]["contract_code"] == CONTRACT


def test_store_saves_the_contract_code_with_every_bar():
    connection = FakeConnection()

    bar_count, outcomes = store_bars(connection, [response_for()], WINDOW_START)

    assert bar_count == 3
    calls = upsert_calls(connection.recorded_cursor)
    provider, symbol, bar_at, open_, high, low, close, volume, previous_close, contract, source_record_id = calls[0]
    assert provider == "kis"
    # 종목코드가 아니라 안정된 식별자를 저장한다. 월물이 바뀌어도 시계열이 안 끊긴다.
    assert symbol == "KOSPI200_FUT"
    assert contract == CONTRACT
    assert bar_at == datetime(2026, 8, 7, 6, 43, tzinfo=UTC)
    assert (open_, high, low) == (Decimal("977.46"), Decimal("980.90"), Decimal("976.06"))
    # 픽스처가 최신순으로 976·977·978을 붙이므로, 오름차순 정렬 뒤 첫 봉(15:43)은 978.15다.
    assert close == Decimal("978.15")
    assert volume == 102
    assert previous_close == Decimal("981.15")
    assert source_record_id == SOURCE_RECORD_ID
    assert outcomes[0].contract_name == "F 202609"


def test_store_keeps_only_the_bars_inside_the_window():
    connection = FakeConnection()

    bar_count, _ = store_bars(connection, [response_for()], datetime(2026, 8, 7, 6, 44, tzinfo=UTC))

    # 06:43 은 창 밖이라 빠지고 06:44, 06:45 만 남는다.
    assert bar_count == 2


def test_store_records_a_closed_session_as_a_success_with_no_bars():
    connection = FakeConnection()

    bar_count, outcomes = store_bars(connection, [response_for()], datetime(2026, 8, 7, 12, 0, tzinfo=UTC))

    assert bar_count == 0
    assert upsert_calls(connection.recorded_cursor) == []
    assert outcomes[0].error is None
    # 파싱에 성공했으므로 성공이다. 여기서 failed 가 되면 개장 전마다 DAG 가 빨개진다.
    assert connection.recorded_cursor.calls[0][1][5] == "succeeded"


def test_store_marks_the_record_failed_when_nothing_parses():
    connection = FakeConnection()

    bar_count, outcomes = store_bars(connection, [response_for(body=b"not json")], WINDOW_START)

    assert bar_count == 0
    assert connection.recorded_cursor.calls[0][1][5] == "failed"
    assert outcomes[0].error is not None


def test_store_carries_fetch_failures_into_the_metadata():
    failure = SymbolOutcome(symbol="KOSPI200_FUT", contract_code=CONTRACT, status=500, error="boom")
    connection = FakeConnection()

    _, outcomes = store_bars(connection, [response_for()], WINDOW_START, None, [failure])

    assert outcomes[0] == failure
    metadata = json.loads(connection.recorded_cursor.calls[0][1][8])
    assert metadata["symbols"][0]["error"] == "boom"


def test_store_rejects_a_reversed_window():
    with pytest.raises(ValueError, match="since must be before until"):
        store_bars(FakeConnection(), [response_for()], COMPLETED_AT, WINDOW_START)


def test_one_request_never_exceeds_the_kis_page_size():
    # 폴링 구간을 이보다 넓게 잡으면 조용히 구멍이 생긴다. DAG 의 param 상한이 이 값이다.
    assert MAX_BARS_PER_REQUEST == 102
    parsed = parse_bars(chart_payload(hours=tuple(f"1500{i:02d}"[:6] for i in range(3))))
    assert len(parsed.bars) <= MAX_BARS_PER_REQUEST


def test_bars_are_interpreted_as_korean_wall_clock():
    # KIS 는 KST 벽시계를 준다. UTC 로 읽으면 9시간 밀린다.
    parsed = parse_bars(chart_payload(hours=("090000",), closes=("      970.00",), volumes=("1",)))

    assert parsed.bars[0].bar_at == datetime(2026, 8, 7, 0, 0, tzinfo=UTC)
    assert parsed.bars[0].bar_at.astimezone(KST).strftime("%H:%M") == "09:00"


def test_session_window_covers_the_korean_futures_hours():
    # 선물 정규장은 09:00~15:45 KST 다. 주식(15:30)보다 15분 늦다.
    opening = datetime(2026, 8, 7, 9, 0, tzinfo=KST).astimezone(UTC)
    closing = datetime(2026, 8, 7, 15, 45, tzinfo=KST).astimezone(UTC)

    assert closing - opening == timedelta(hours=6, minutes=45)
