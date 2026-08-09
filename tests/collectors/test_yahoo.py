import json
import re
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Self

import pytest
from sqlalchemy import Table

from apps.models.market import QuoteBar as QuoteBarModel
from apps.models.raw import SourceRecord
from modules.collectors.yahoo import (
    MAX_BACKFILL_DAYS,
    QUOTE_BAR_UPSERT,
    SOURCE_RECORD_INSERT,
    QuoteSymbol,
    SymbolOutcome,
    YahooPayloadError,
    YahooResponse,
    backfill_windows,
    build_url,
    parse_bars,
    store_bars,
)

SOURCE_RECORD_ID = 1

# 봉 시각. 아래 픽스처는 이 시각을 기준으로 1분 간격이다.
FIRST_EPOCH = 1786112400  # 2026-08-07 05:00:00 UTC
STARTED_AT = datetime(2026, 8, 7, 5, 5, tzinfo=UTC)
COMPLETED_AT = datetime(2026, 8, 7, 5, 5, 1, tzinfo=UTC)

# 저장 구간의 시작. 픽스처 봉(05:00~05:02)이 전부 들어오도록 그보다 이르게 둔다.
WINDOW_START = datetime(2026, 8, 7, 4, 50, tzinfo=UTC)


def chart_payload(
    timestamps: list[int] | None = None,
    opens: list[float | None] | None = None,
    highs: list[float | None] | None = None,
    lows: list[float | None] | None = None,
    closes: list[float | None] | None = None,
    volumes: list[float | None] | None = None,
    previous_close: float = 100.0,
    symbol: str = "ES=F",
) -> bytes:
    """Yahoo v8 chart 응답 본문. 인자를 주지 않은 배열은 3분짜리 정상 봉으로 채운다."""
    timestamps = timestamps if timestamps is not None else [FIRST_EPOCH, FIRST_EPOCH + 60, FIRST_EPOCH + 120]
    size = len(timestamps)
    quote = {
        "open": opens if opens is not None else [101.0] * size,
        "high": highs if highs is not None else [102.0] * size,
        "low": lows if lows is not None else [100.5] * size,
        "close": closes if closes is not None else [101.5] * size,
    }
    if volumes is not None:
        quote["volume"] = volumes
    return json.dumps(
        {
            "chart": {
                "result": [
                    {
                        "meta": {
                            "symbol": symbol,
                            "chartPreviousClose": previous_close,
                            "currency": "USD",
                        },
                        "timestamp": timestamps,
                        "indicators": {"quote": [quote]},
                    }
                ],
                "error": None,
            }
        }
    ).encode("utf-8")


def response_for(symbol: QuoteSymbol = QuoteSymbol.SP500_FUT, body: bytes | None = None) -> YahooResponse:
    return YahooResponse(
        symbol=symbol,
        body=body if body is not None else chart_payload(),
        status=200,
        started_at=STARTED_AT,
        completed_at=COMPLETED_AT,
    )


class FakeCursor:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple]] = []
        self.batches = 0

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *args: object) -> bool:
        return False

    def execute(self, statement: str, parameters: tuple) -> None:
        self.calls.append((statement, parameters))

    def executemany(self, statement: str, parameters) -> None:
        # 배치 경로도 (문장, 파라미터) 한 쌍씩 남겨 행 단위 검증을 그대로 유지한다.
        self.batches += 1
        self.calls.extend((statement, tuple(row)) for row in parameters)

    def fetchone(self) -> tuple[int]:
        return (SOURCE_RECORD_ID,)


class FakeConnection:
    def __init__(self) -> None:
        self.recorded_cursor = FakeCursor()

    def cursor(self) -> FakeCursor:
        return self.recorded_cursor


@pytest.fixture(autouse=True)
def without_the_psycopg2_fast_path(monkeypatch):
    """저장 테스트를 PEP 249 경로에 고정한다.

    psycopg2가 설치돼 있으면 `store_bars`는 `execute_batch`를 탄다. 그건 문장을 묶어
    보내므로 커서에 도착하는 SQL이 드라이버 사정에 따라 달라진다. 컬럼 순서 같은 이
    모듈의 계약을 검증하려면 행 단위가 그대로 보이는 경로여야 한다.
    `test_hana.py`가 먼저 쓴 방식이다.
    """
    monkeypatch.setattr("modules.upsert._execute_batch", None)


def inserted_columns(statement: str) -> tuple[str, ...]:
    columns = re.search(r"INSERT INTO \w+ \(([^)]+)\)", statement, re.DOTALL)
    assert columns is not None
    # 쿼리 파일은 컬럼마다 `-- 설명`을 달아 둔다. 이름만 남기려면 먼저 주석을 걷어낸다.
    names = re.sub(r"--[^\n]*", "", columns.group(1))
    return tuple(name.strip() for name in names.split(",") if name.strip())


def placeholder_count(statement: str) -> int:
    values = re.search(r"VALUES \(([^)]+)\)", statement, re.DOTALL)
    assert values is not None
    return values.group(1).count("%s")


def required_columns(table: Table) -> set[str]:
    """DB가 채워 주지 않는 NOT NULL 컬럼. INSERT가 하나라도 빠뜨리면 런타임에 터진다."""
    return {
        column.name
        for column in table.columns
        if not column.nullable and column.server_default is None and not column.primary_key
    }


def upsert_calls(cursor: FakeCursor) -> list[tuple]:
    return [parameters for statement, parameters in cursor.calls if "INSERT INTO quote_bar" in statement]


def test_quote_bar_upsert_matches_the_model_and_its_natural_key():
    # 수집기는 문자열 SQL을 쓰고 모델을 import하지 않는다. 컬럼이 어긋나면 런타임에야 터지므로
    # 모델 metadata와 여기서 맞춰 둔다.
    table = QuoteBarModel.__table__
    columns = inserted_columns(QUOTE_BAR_UPSERT)

    assert set(columns) <= {column.name for column in table.columns}
    assert required_columns(table) <= set(columns)
    assert placeholder_count(QUOTE_BAR_UPSERT) == len(columns)

    natural_key = next(
        tuple(column.name for column in constraint.columns)
        for constraint in table.constraints
        if constraint.name == "uq_quote_bar_natural_key"
    )
    assert f"ON CONFLICT ({', '.join(natural_key)}) DO UPDATE" in QUOTE_BAR_UPSERT


def test_source_record_insert_matches_the_model():
    table = SourceRecord.__table__
    columns = inserted_columns(SOURCE_RECORD_INSERT)

    assert set(columns) <= {column.name for column in table.columns}
    assert required_columns(table) <= set(columns)
    assert "RETURNING id" in SOURCE_RECORD_INSERT


def test_symbols_cover_the_futures_that_stay_live_during_the_korean_session():
    # 한국 정규장 시간에는 미국 현물이 멈춘다. 그 구간을 채우는 건 선물뿐이라 최소한
    # 지수선물 둘은 항상 있어야 한다.
    assert QuoteSymbol.SP500_FUT.yahoo_symbol == "ES=F"
    assert QuoteSymbol.NASDAQ100_FUT.yahoo_symbol == "NQ=F"


def test_domestic_symbols_are_not_collected_from_yahoo():
    # 국내에서 받을 수 있는 것은 국내를 우선한다. 코스피는 KIS 로 옮겼고 Yahoo 의 ^KS11 은
    # 분봉 품질이 낮았다(문서 §8.4). 실수로 다시 들어오면 여기서 걸린다.
    assert "KOSPI" not in {symbol.value for symbol in QuoteSymbol}
    assert not any(symbol.yahoo_symbol.startswith("^KS") for symbol in QuoteSymbol)


def test_build_url_requests_one_minute_bars_and_escapes_the_symbol():
    url = build_url(QuoteSymbol.SOX)

    # `^`를 그대로 두면 일부 클라이언트가 URL을 거부한다.
    assert "%5ESOX" in url
    assert "interval=1m" in url
    assert "range=1d" in url


def test_parse_reads_bars_in_utc_with_the_previous_close():
    parsed = parse_bars(chart_payload(previous_close=99.5))

    assert len(parsed.bars) == 3
    first = parsed.bars[0]
    assert first.bar_at == datetime.fromtimestamp(FIRST_EPOCH, UTC)
    assert first.bar_at.tzinfo is UTC
    assert first.close == Decimal("101.5")
    # 전일종가는 봉마다 같은 값이 반복된다. 알림 변동률의 분모라 봉에 함께 실어 둔다.
    assert all(bar.previous_close == Decimal("99.5") for bar in parsed.bars)
    assert parsed.latest_bar_at == datetime.fromtimestamp(FIRST_EPOCH + 120, UTC)


def test_parse_skips_minutes_without_trades():
    # 거래가 없던 분은 배열에 자리는 있고 값이 None이다. 결측이지 오류가 아니다.
    body = chart_payload(closes=[101.5, None, 102.5], opens=[101.0, None, 102.0])

    parsed = parse_bars(body)

    assert [bar.close for bar in parsed.bars] == [Decimal("101.5"), Decimal("102.5")]


def test_parse_skips_nan_values():
    body = chart_payload(closes=[101.5, float("nan"), 102.5])

    parsed = parse_bars(body)

    assert [bar.close for bar in parsed.bars] == [Decimal("101.5"), Decimal("102.5")]


def test_parse_rejects_a_non_finite_previous_close():
    with pytest.raises(YahooPayloadError):
        parse_bars(chart_payload(previous_close=float("inf")))


def test_parse_rejects_arrays_that_do_not_line_up_with_the_timestamps():
    # 봉을 위치로 읽으므로 길이가 어긋나면 값이 조용히 옆 칸으로 밀린다. 먼저 막아야 한다.
    body = chart_payload(closes=[101.5, 102.0])

    with pytest.raises(YahooPayloadError):
        parse_bars(body)


def test_parse_rejects_a_volume_array_that_does_not_line_up():
    body = chart_payload(volumes=[1.0])

    with pytest.raises(YahooPayloadError):
        parse_bars(body)


def test_parse_rejects_a_response_without_a_chart_result():
    with pytest.raises(YahooPayloadError):
        parse_bars(json.dumps({"chart": {"result": [], "error": None}}).encode("utf-8"))


def test_parse_rejects_an_empty_chart():
    # Yahoo가 막히거나 심볼이 폐지되면 200에 빈 배열이 온다. 성공으로 넘기면 조회 구간에
    # 조용히 구멍이 남는다.
    with pytest.raises(YahooPayloadError):
        parse_bars(chart_payload(timestamps=[], closes=[], opens=[], highs=[], lows=[]))


def test_parse_rejects_a_chart_whose_values_are_all_missing():
    body = chart_payload(closes=[None, None, None])

    with pytest.raises(YahooPayloadError):
        parse_bars(body)


def test_parse_returns_no_bars_when_the_market_is_closed():
    """휴장은 실패가 아니다.

    CME는 매일 06:00~07:00 KST에 정비 휴장하고 주말 내내 쉰다. 미국 현물 지수는 한국
    정규장 시간 내내 멈춰 있다. 이때 응답에는 과거 봉이 실려 오지만 최근 구간에는 새 봉이
    없다. 이걸 실패로 다루면 DAG가 매일, 그리고 주말 내내 빨갛게 된다.
    """
    parsed = parse_bars(chart_payload(), since=datetime.fromtimestamp(FIRST_EPOCH + 3600, UTC))

    assert parsed.bars == ()
    # 마지막 봉 시각은 거르기 전 값이라 그대로 남는다. 며칠씩 안 움직이면 조용히 끊긴 것이다.
    assert parsed.latest_bar_at == datetime.fromtimestamp(FIRST_EPOCH + 120, UTC)


def test_store_writes_one_source_record_per_poll_without_the_payload():
    connection = FakeConnection()

    store_bars(connection, [response_for(QuoteSymbol.SP500_FUT), response_for(QuoteSymbol.VIX)], WINDOW_START)

    statements = [statement for statement, _ in connection.recorded_cursor.calls]
    # 심볼이 둘인데 계보 레코드는 하나다. 5분마다 영원히 도는 수집이라 심볼마다 남기면
    # 계보 테이블이 수집 자체보다 빨리 커진다.
    assert sum("INSERT INTO source_record" in statement for statement in statements) == 1
    assert statements[0].startswith("-- 수집 1회를 계보 레코드 1행으로 남긴다.")

    _, parameters = connection.recorded_cursor.calls[0]
    source_type, source, source_key, started, completed, status, record_count, payload, metadata = parameters
    assert (source_type, source, source_key) == ("api", "yahoo", "intraday_1m")
    assert (started, completed) == (STARTED_AT, COMPLETED_AT)
    assert status == "succeeded"
    assert record_count == 6
    # 5분마다 심볼당 40KB를 남기면 하루 수십 MB다. 원본은 저장하지 않는다.
    assert payload is None
    # 저장 구간을 계보에 남긴다. 나중에 어느 창이 비었는지 되짚을 수 있어야 한다.
    assert json.loads(metadata)["window_start"] == WINDOW_START.isoformat()
    assert json.loads(metadata)["window_end"] is None


def test_store_upserts_every_bar_with_the_readable_symbol():
    connection = FakeConnection()

    bar_count, outcomes = store_bars(connection, [response_for(QuoteSymbol.NASDAQ100_FUT)], WINDOW_START)

    assert bar_count == 3
    calls = upsert_calls(connection.recorded_cursor)
    assert len(calls) == 3
    provider, symbol, bar_at, open_, high, low, close, volume, previous_close, contract, source_record_id = calls[0]
    assert provider == "yahoo"
    # Yahoo 심볼(`NQ=F`)이 아니라 읽히는 식별자를 저장한다. 제공처를 바꿔도 저장된 값이 안 바뀐다.
    assert symbol == "NASDAQ100_FUT"
    assert bar_at == datetime.fromtimestamp(FIRST_EPOCH, UTC)
    assert (open_, high, low, close) == (Decimal("101.0"), Decimal("102.0"), Decimal("100.5"), Decimal("101.5"))
    assert volume is None
    assert previous_close == Decimal("100.0")
    # Yahoo 는 연속 심볼(`ES=F`)을 주므로 월물 코드가 없다. KIS 선물만 채운다.
    assert contract is None
    assert source_record_id == SOURCE_RECORD_ID
    assert outcomes[0].bar_count == 3
    assert outcomes[0].latest_bar_at is not None


def test_store_keeps_only_the_bars_inside_the_window():
    # `range=1d`가 600봉 넘게 준다. 매번 전부 쓰면 폴링마다 수천 건의 no-op UPDATE가 난다.
    old = int((COMPLETED_AT - timedelta(hours=3)).timestamp())
    recent = int((COMPLETED_AT - timedelta(minutes=2)).timestamp())
    body = chart_payload(timestamps=[old, recent])

    connection = FakeConnection()
    bar_count, _ = store_bars(connection, [response_for(body=body)], COMPLETED_AT - timedelta(minutes=15))

    assert bar_count == 1
    assert upsert_calls(connection.recorded_cursor)[0][2] == datetime.fromtimestamp(recent, UTC)


def test_store_honours_the_upper_bound_so_backfill_windows_do_not_overlap():
    """백필은 구간을 8일씩 쪼개 여러 번 부른다. 경계 봉이 두 창에 다 들어가면 안 된다.

    Yahoo가 요청한 구간 밖의 봉도 함께 돌려주므로 상한이 없으면 다음 창의 첫 봉이 이번
    창에도 저장된다. 멱등 키가 있어 행이 늘지는 않지만 창마다 같은 봉을 다시 쓰게 된다.
    """
    boundary = datetime.fromtimestamp(FIRST_EPOCH + 60, UTC)

    connection = FakeConnection()
    bar_count, _ = store_bars(connection, [response_for()], WINDOW_START, boundary)

    # 경계(FIRST_EPOCH+60) 이상은 빠지고 그 앞의 한 봉만 남는다.
    assert bar_count == 1
    assert upsert_calls(connection.recorded_cursor)[0][2] == datetime.fromtimestamp(FIRST_EPOCH, UTC)


def test_store_records_a_quiet_window_as_a_success_with_no_bars():
    """휴장 구간의 폴링도 성공이고 계보 레코드를 남긴다.

    조회했지만 값이 없는 구간과 아직 조회하지 않은 구간이 구분돼야 한다. 주말에는 모든
    심볼이 이 상태다.
    """
    old = int((COMPLETED_AT - timedelta(hours=3)).timestamp())
    body = chart_payload(timestamps=[old])

    connection = FakeConnection()
    bar_count, outcomes = store_bars(connection, [response_for(body=body)], WINDOW_START)

    assert bar_count == 0
    assert upsert_calls(connection.recorded_cursor) == []
    assert outcomes[0].error is None
    assert outcomes[0].bar_count == 0
    # 파싱에 성공했으므로 성공이다. 여기서 "failed"가 되면 주말 내내 DAG가 빨갛게 된다.
    assert connection.recorded_cursor.calls[0][1][5] == "succeeded"


def test_store_keeps_going_when_one_symbol_is_broken():
    connection = FakeConnection()

    bar_count, outcomes = store_bars(
        connection,
        [
            response_for(QuoteSymbol.SP500_FUT, body=b"not json"),
            response_for(QuoteSymbol.VIX),
        ],
        WINDOW_START,
    )

    assert bar_count == 3
    broken, healthy = outcomes
    assert broken.symbol == "SP500_FUT"
    assert broken.error is not None
    assert broken.bar_count == 0
    assert healthy.symbol == "VIX"
    assert healthy.error is None
    # 실패한 심볼도 계보에 남아야 나중에 왜 구멍이 났는지 알 수 있다.
    metadata = json.loads(connection.recorded_cursor.calls[0][1][8])
    assert [entry["symbol"] for entry in metadata["symbols"]] == ["SP500_FUT", "VIX"]


def test_store_carries_fetch_failures_into_the_metadata():
    # 요청 단계에서 실패한 심볼은 응답이 없다. DAG가 결과만 넘겨 계보에 남긴다.
    failure = SymbolOutcome(symbol="SOX", yahoo_symbol="^SOX", status=429, error="rate limited")
    connection = FakeConnection()

    _, outcomes = store_bars(connection, [response_for()], WINDOW_START, None, [failure])

    assert outcomes[0] == failure
    metadata = json.loads(connection.recorded_cursor.calls[0][1][8])
    assert metadata["symbols"][0]["error"] == "rate limited"


def test_store_marks_the_record_failed_when_nothing_parses():
    connection = FakeConnection()

    bar_count, _ = store_bars(connection, [response_for(body=b"not json")], WINDOW_START)

    assert bar_count == 0
    assert connection.recorded_cursor.calls[0][1][5] == "failed"


def test_store_rejects_a_reversed_window():
    with pytest.raises(ValueError, match="since must be before until"):
        store_bars(FakeConnection(), [response_for()], COMPLETED_AT, WINDOW_START)


def test_backfill_windows_split_the_range_into_chunks_yahoo_accepts():
    # 8일을 넘겨 요청하면 부분 응답이 아니라 거절이 온다.
    start = datetime(2026, 7, 10, tzinfo=UTC)
    end = datetime(2026, 8, 1, tzinfo=UTC)

    windows = backfill_windows(start, end)

    assert len(windows) == 3
    assert all((w_end - w_start).days <= MAX_BACKFILL_DAYS for w_start, w_end in windows)
    # 창이 빈틈없이 이어지고 요청 구간을 정확히 덮는다.
    assert windows[0][0] == start
    assert windows[-1][1] == end
    assert all(windows[i][1] == windows[i + 1][0] for i in range(len(windows) - 1))


def test_backfill_windows_keep_a_short_range_in_one_request():
    windows = backfill_windows(datetime(2026, 7, 10, tzinfo=UTC), datetime(2026, 7, 12, tzinfo=UTC))

    assert len(windows) == 1


def test_backfill_windows_reject_a_reversed_range():
    with pytest.raises(ValueError, match="start must be before end"):
        backfill_windows(datetime(2026, 8, 1, tzinfo=UTC), datetime(2026, 7, 10, tzinfo=UTC))


def test_build_url_asks_for_an_explicit_period_when_backfilling():
    start = datetime(2026, 7, 10, tzinfo=UTC)
    end = datetime(2026, 7, 18, tzinfo=UTC)

    url = build_url(QuoteSymbol.SP500_FUT, (start, end))

    assert f"period1={int(start.timestamp())}" in url
    assert f"period2={int(end.timestamp())}" in url
    # 구간을 직접 주면 range는 의미가 없다. 둘을 같이 보내면 Yahoo가 range를 우선한다.
    assert "range=" not in url


def test_parse_surfaces_the_reason_yahoo_rejected_the_range():
    """보관 기간보다 과거를 요청하면 Yahoo가 200에 error를 담아 준다.

    설명을 그대로 올려야 백필이 왜 비었는지 로그만 보고 알 수 있다.
    """
    body = json.dumps(
        {
            "chart": {
                "result": None,
                "error": {"code": "Unprocessable Entity", "description": "1m data not available for startTime=..."},
            }
        }
    ).encode("utf-8")

    with pytest.raises(YahooPayloadError, match="1m data not available"):
        parse_bars(body)
