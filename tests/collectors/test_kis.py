import json
import re
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Self
from zoneinfo import ZoneInfo

import pytest
from pydantic import SecretStr
from sqlalchemy import Table

from apps.models.market import IndexBar, IndexDaily, IndexFutureBar, MarketMovementSnapshot
from apps.models.market import StockBar as StockBarModel
from apps.models.market import StockExchange as StockExchangeModel
from apps.models.raw import SourceRecord
from modules.collectors import kis
from modules.collectors.kis import (
    CONTRACT_MONTHS,
    INDEX_BAR_UPSERT,
    INDEX_FUTURE_BAR_UPSERT,
    MARKET_MOVEMENT_UPSERT,
    MAX_BARS_PER_REQUEST,
    MOVEMENT_INDEXES,
    SESSION_FIRST_BAR,
    SESSION_LAST_BAR,
    SOURCE_RECORD_INSERT,
    STOCK_BAR_UPSERT,
    TOKEN_REFRESH_MARGIN,
    DomesticFuture,
    DomesticIndex,
    DomesticStock,
    KisPayloadError,
    KisQuoteCollector,
    KisResponse,
    KisResultError,
    StockExchange,
    SymbolOutcome,
    access_token,
    expiry_date,
    front_contract,
    last_settled_close,
    parse_bars,
    parse_market_movement,
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


class RowCursor(FakeCursor):
    """조회 한 건을 돌려주는 커서. 기본 `FakeCursor`는 source_record id 만 준다."""

    def __init__(self, row) -> None:
        super().__init__()
        self.row = row

    def fetchone(self):
        return self.row


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
    """
    monkeypatch.setattr("modules.upsert._execute_batch", None)


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
    return [parameters for statement, parameters in cursor.calls if "_bar (" in statement]


@pytest.mark.parametrize(
    ("statement", "model", "literal_count"),
    [
        (INDEX_BAR_UPSERT, IndexBar, 0),
        (INDEX_FUTURE_BAR_UPSERT, IndexFutureBar, 0),
        # stock_bar 는 ingest_method/is_final 이 SQL 리터럴('rest', true)이다.
        (STOCK_BAR_UPSERT, StockBarModel, 2),
    ],
)
def test_bar_upserts_match_their_models(statement, model, literal_count):
    table = model.__table__
    columns = inserted_columns(statement)

    assert set(columns) <= {column.name for column in table.columns}
    assert required_columns(table) <= set(columns)
    assert placeholder_count(statement) == len(columns) - literal_count


def test_the_stock_bar_rest_upsert_confirms_finality():
    # REST 확정 경로는 가드 없이 무조건 덮고 is_final=true 로 전환한다(문서 5.2).
    columns = inserted_columns(STOCK_BAR_UPSERT)
    assert "ingest_method" in columns
    assert "is_final" in columns
    assert "'rest'" in STOCK_BAR_UPSERT
    assert "is_final = true" in STOCK_BAR_UPSERT
    assert "WHERE stock_bar.is_final" not in STOCK_BAR_UPSERT


def test_the_collector_exchanges_match_the_model_enum():
    """`stock_bar.exchange`의 CHECK와 수집기 enum이 어긋나면 저장이 런타임에 터진다.

    모델 쪽에는 해외 상장 종목용 NYSE가 더 있다. 수집기(KIS)는 국내 두 거래소만 안다.
    """
    assert {member.value for member in StockExchange} <= {member.value for member in StockExchangeModel}
    assert StockExchange.KRX.division_code == "J"
    assert StockExchange.NXT.division_code == "NX"


def test_the_future_upsert_carries_the_contract_code():
    # 월물 코드를 저장하지 않으면 롤오버 갭을 시장 급변과 구분할 수 없다.
    assert "contract_code" in inserted_columns(INDEX_FUTURE_BAR_UPSERT)
    # 지수·종목 쪽에는 월물 칸이 없다.
    assert "contract_code" not in inserted_columns(INDEX_BAR_UPSERT)
    assert "contract_code" not in inserted_columns(STOCK_BAR_UPSERT)


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
        (date(2026, 8, 8), "A01609"),  # 9월물 만기 전
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


def test_kosdaq150_resolves_to_the_contract_code_that_actually_answered():
    # 상품 자릿수가 틀리면 조회가 0봉으로 끝난다. `A06609`(코스닥150F 202609)로 102봉을
    # 받은 것을 확인했으므로 그 코드가 그대로 나와야 한다.
    assert DomesticFuture.KOSDAQ150_FUT.product_digit == "6"
    assert front_contract(DomesticFuture.KOSDAQ150_FUT, date(2026, 8, 8)) == "A06609"


def test_every_future_shares_the_quarterly_roll():
    # 분기물이라는 전제로 `front_contract`가 한 벌만 있다. 월물 상품을 넣으면 조용히
    # 틀린 계약을 조회하므로 여기서 막는다.
    for future in DomesticFuture:
        assert front_contract(future, date(2026, 8, 8))[-2:] in {f"{month:02d}" for month in CONTRACT_MONTHS}


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

    COLLECTOR.store_bars(connection, [response_for()], WINDOW_START)

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

    bar_count, outcomes = COLLECTOR.store_bars(connection, [response_for()], WINDOW_START)

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

    bar_count, _ = COLLECTOR.store_bars(connection, [response_for()], datetime(2026, 8, 7, 6, 44, tzinfo=UTC))

    # 06:43 은 창 밖이라 빠지고 06:44, 06:45 만 남는다.
    assert bar_count == 2


def test_store_records_a_closed_session_as_a_success_with_no_bars():
    connection = FakeConnection()

    bar_count, outcomes = COLLECTOR.store_bars(connection, [response_for()], datetime(2026, 8, 7, 12, 0, tzinfo=UTC))

    assert bar_count == 0
    assert upsert_calls(connection.recorded_cursor) == []
    assert outcomes[0].error is None
    # 파싱에 성공했으므로 성공이다. 여기서 failed 가 되면 개장 전마다 DAG 가 빨개진다.
    assert connection.recorded_cursor.calls[0][1][5] == "succeeded"


def test_store_marks_the_record_failed_when_nothing_parses():
    connection = FakeConnection()

    bar_count, outcomes = COLLECTOR.store_bars(connection, [response_for(body=b"not json")], WINDOW_START)

    assert bar_count == 0
    assert connection.recorded_cursor.calls[0][1][5] == "failed"
    assert outcomes[0].error is not None


def test_store_carries_fetch_failures_into_the_metadata():
    failure = SymbolOutcome(symbol="KOSPI200_FUT", contract_code=CONTRACT, status=500, error="boom")
    connection = FakeConnection()

    _, outcomes = COLLECTOR.store_bars(connection, [response_for()], WINDOW_START, [failure])

    assert outcomes[0] == failure
    metadata = json.loads(connection.recorded_cursor.calls[0][1][8])
    assert metadata["symbols"][0]["error"] == "boom"


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


# --- 토큰 캐시 --------------------------------------------------------------
#
# 발급 횟수에 제한이 있어 폴링마다 받을 수 없다. 캐시가 틀려도 예외가 아니라 발급 제한으로
# 나타나서 늦게 안다. 저장소는 구조적 타입이라 Airflow `Variable` 없이 검증한다.


class FakeStore:
    """`TokenStore`를 만족하는 최소 구현. 운영에서는 Airflow `Variable`이 들어간다."""

    def __init__(self, stored: str | None = None) -> None:
        self.stored = stored
        self.writes = 0

    def get(self, key: str, default: str | None = None) -> str | None:
        return self.stored if self.stored is not None else default

    def set(self, key: str, value: str) -> None:
        self.stored = value
        self.writes += 1


def cached_token(token: str, expires_in: timedelta) -> str:
    return json.dumps({"token": token, "expires_at": (datetime.now(UTC) + expires_in).isoformat()})


@pytest.fixture
def issued(monkeypatch):
    """발급 호출을 센다. 실제 KIS 발급은 횟수 제한이 있어 여기서는 절대 부르지 않는다."""
    calls = []

    def fake_issue_token(app_key: SecretStr, app_secret: SecretStr):
        calls.append(app_key)
        return SecretStr("fresh"), datetime.now(UTC) + timedelta(hours=24)

    monkeypatch.setattr(kis, "issue_token", fake_issue_token)
    return calls


def token_for(store: FakeStore, *, force: bool = False) -> SecretStr:
    return access_token(store, SecretStr("key"), SecretStr("secret"), force=force)


def test_a_live_cached_token_is_reused(issued):
    store = FakeStore(cached_token("cached", timedelta(hours=12)))

    assert token_for(store).get_secret_value() == "cached"
    assert issued == []


def test_a_token_close_to_expiry_is_replaced_before_it_dies(issued):
    # 만료 직전 토큰을 그대로 쓰면 폴링 도중 401이 난다. 여유분 안쪽이면 미리 갈아 끼운다.
    store = FakeStore(cached_token("stale", TOKEN_REFRESH_MARGIN - timedelta(minutes=1)))

    assert token_for(store).get_secret_value() == "fresh"
    assert len(issued) == 1
    assert json.loads(store.stored)["token"] == "fresh"


@pytest.mark.parametrize(
    "stored",
    [None, "not json", json.dumps({"token": "x"}), json.dumps({"token": "x", "expires_at": "언젠가"})],
    ids=["empty", "broken_json", "missing_expiry", "unparsable_expiry"],
)
def test_an_unusable_cache_falls_back_to_issuing(issued, stored):
    # 캐시가 깨졌다고 태스크가 죽으면 안 된다. 새로 받고 캐시를 덮어쓴다.
    store = FakeStore(stored)

    assert token_for(store).get_secret_value() == "fresh"
    assert len(issued) == 1


def test_force_ignores_a_live_cache(issued):
    # 401을 만났을 때 쓰는 경로다. 캐시가 살아 있어도 다시 받아야 그 401을 벗어난다.
    store = FakeStore(cached_token("cached", timedelta(hours=12)))

    assert token_for(store, force=True).get_secret_value() == "fresh"
    assert len(issued) == 1


def index_price_payload(
    upper: str = "3",
    rising: str = "  512",
    unchanged: str = "71",
    falling: str = "355",
    lower: str = "0",
    rt_cd: str = "0",
    msg1: str = "정상처리 되었습니다.",
) -> bytes:
    """지수 현재가 응답. 실측 필드 이름과 공백 패딩을 그대로 옮겼다."""
    return json.dumps(
        {
            "rt_cd": rt_cd,
            "msg_cd": "MCA00000",
            "msg1": msg1,
            "output": {
                "bstp_nmix_prpr": "6579.04",
                "acml_vol": "412345",
                "acml_tr_pbmn": "9876543210",
                "uplm_issu_cnt": upper,
                "ascn_issu_cnt": rising,
                "stnr_issu_cnt": unchanged,
                "down_issu_cnt": falling,
                "lslm_issu_cnt": lower,
            },
        }
    ).encode()


def index_price_response(symbol: str = "KOSPI", **kwargs) -> KisResponse:
    return KisResponse(
        symbol=symbol,
        contract_code=None,
        body=index_price_payload(**kwargs),
        status=200,
        started_at=STARTED_AT,
        completed_at=COMPLETED_AT,
    )


OBSERVED_AT = datetime(2026, 8, 12, 4, 30, tzinfo=UTC)


def movement_upserts(cursor: FakeCursor) -> list[tuple]:
    return [parameters for statement, parameters in cursor.calls if "INSERT INTO market_movement_snapshot" in statement]


def test_market_movement_upsert_matches_the_model_and_its_natural_key():
    table = MarketMovementSnapshot.__table__
    columns = inserted_columns(MARKET_MOVEMENT_UPSERT)

    assert set(columns) <= {column.name for column in table.columns}
    assert required_columns(table) <= set(columns)
    # provider 는 SQL 에 리터럴로 박혀 있다.
    assert placeholder_count(MARKET_MOVEMENT_UPSERT) == len(columns) - 1
    assert "ON CONFLICT (provider, symbol, observed_at)" in MARKET_MOVEMENT_UPSERT


def test_movement_targets_exclude_kospi200():
    # 코스피200은 코스피의 부분집합이라 시장 전반의 분포가 아니다.
    assert MOVEMENT_INDEXES == (DomesticIndex.KOSPI, DomesticIndex.KOSDAQ)
    assert DomesticIndex.KOSPI200 not in MOVEMENT_INDEXES


def test_movement_parses_padded_counts():
    movement = parse_market_movement(index_price_response(), OBSERVED_AT)

    assert (movement.upper_limit_count, movement.rising_count) == (3, 512)
    assert (movement.unchanged_count, movement.falling_count, movement.lower_limit_count) == (71, 355, 0)
    assert movement.observed_at == OBSERVED_AT
    assert movement.closed is False


def test_all_zero_counts_mean_the_market_is_closed():
    # 실측: 장 밖에서는 다섯 값과 거래량이 0으로 리셋되고 지수 값만 전일 종가로 남는다.
    movement = parse_market_movement(
        index_price_response(upper="0", rising="0", unchanged="0", falling="0", lower="0"), OBSERVED_AT
    )

    assert movement.closed is True


def test_a_single_zero_is_a_normal_value():
    # 보합 0은 장중에 있을 수 있다. all-zero 만 장 밖이다.
    movement = parse_market_movement(index_price_response(unchanged="0"), OBSERVED_AT)

    assert movement.closed is False
    assert movement.unchanged_count == 0


@pytest.mark.parametrize("value", ["-1", "abc"])
def test_bad_counts_fail_instead_of_being_stored(value):
    with pytest.raises(KisPayloadError):
        parse_market_movement(index_price_response(rising=value), OBSERVED_AT)


def test_movement_result_code_failure_is_raised():
    with pytest.raises(KisResultError) as error:
        parse_market_movement(index_price_response(rt_cd="1", msg1="조회할 수 없습니다"), OBSERVED_AT)

    assert error.value.code == "1"


def test_store_movement_writes_one_row_per_index():
    connection = FakeConnection()
    responses = [index_price_response("KOSPI"), index_price_response("KOSDAQ", rising="900")]

    stored, outcomes = COLLECTOR.store_market_movement(connection, responses, OBSERVED_AT)

    assert stored == 2
    upserts = movement_upserts(connection.recorded_cursor)
    assert [row[0] for row in upserts] == ["KOSPI", "KOSDAQ"]
    assert upserts[0] == ("KOSPI", OBSERVED_AT, 3, 512, 71, 355, 0, SOURCE_RECORD_ID)
    assert {outcome.error for outcome in outcomes} == {None}


def test_closed_market_leaves_a_lineage_record_but_no_rows():
    connection = FakeConnection()
    closed = index_price_response(upper="0", rising="0", unchanged="0", falling="0", lower="0")

    stored, outcomes = COLLECTOR.store_market_movement(connection, [closed], OBSERVED_AT)

    assert stored == 0
    assert movement_upserts(connection.recorded_cursor) == []
    # 조회했지만 장이 닫혀 있었다는 사실은 남는다.
    statement, parameters = connection.recorded_cursor.calls[0]
    assert "INSERT INTO source_record" in statement
    assert parameters[2] == "inquire_index_price"
    assert parameters[5] == "succeeded"
    assert json.loads(parameters[8])["closed_symbols"] == [closed.symbol]
    assert {outcome.error for outcome in outcomes} == {None}


def test_one_index_failing_does_not_drop_the_other():
    connection = FakeConnection()
    responses = [index_price_response("KOSPI"), index_price_response("KOSDAQ", rt_cd="1", msg1="권한 없음")]

    stored, outcomes = COLLECTOR.store_market_movement(connection, responses, OBSERVED_AT)

    assert stored == 1
    assert [row[0] for row in movement_upserts(connection.recorded_cursor)] == ["KOSPI"]
    assert [outcome.symbol for outcome in outcomes if outcome.error] == ["KOSDAQ"]


# 2026-08-14 005930 실측을 줄인 것이다. 한 응답이 120봉이고 시각은 최신순으로 온다.
STOCK_DATE = date(2026, 8, 14)
TOKEN = SecretStr("token")
APP_KEY = SecretStr("key")
APP_SECRET = SecretStr("secret")
COLLECTOR = KisQuoteCollector(TOKEN, APP_KEY, APP_SECRET)


def stock_row(hour: str, business_date: str = "20260814", volume: str = "1000", close: str = "274500") -> dict:
    return {
        "stck_bsop_date": business_date,
        "stck_cntg_hour": hour,
        "stck_oprc": close,
        "stck_hgpr": close,
        "stck_lwpr": close,
        "stck_prpr": close,
        "cntg_vol": volume,
        "acml_tr_pbmn": "5874118816500",
    }


def stock_body(rows: list[dict]) -> bytes:
    return json.dumps(
        {
            "rt_cd": "0",
            "msg_cd": "MCA00000",
            "msg1": "정상처리 되었습니다.",
            # output1 은 조회한 날짜가 아니라 지금 시세다. 그래서 전일종가를 여기서 읽지 않는다.
            "output1": {"hts_kor_isnm": "삼성전자", "stck_prdy_clpr": "268000", "acml_vol": "21669476"},
            "output2": rows,
        },
        ensure_ascii=False,
    ).encode()


def fake_stock_send(pages: list[list[dict]]):
    sent: list[dict] = []
    remaining = list(pages)

    def send(token, app_key, app_secret, path, tr_id, query, tr_cont=""):
        sent.append({"path": path, "tr_id": tr_id, "query": dict(query)})
        rows = remaining.pop(0) if remaining else []
        return stock_body(rows), 200, {}

    send.sent = sent  # type: ignore[attr-defined]
    return send


def test_stock_bars_walk_the_session_backwards(monkeypatch):
    """한 응답이 120봉이라 정규장을 덮으려면 커서를 뒤로 걸어야 한다."""
    send = fake_stock_send(
        [
            [stock_row("153000"), stock_row("120000")],
            [stock_row("115900"), stock_row("090000")],
        ]
    )
    monkeypatch.setattr(kis, "send_get", send)

    fetch = COLLECTOR.fetch_stock_bars(DomesticStock.SAMSUNG_ELECTRONICS, STOCK_DATE, Decimal(268000))

    assert fetch.call_count == 2
    assert [call["query"]["FID_INPUT_HOUR_1"] for call in send.sent] == ["153000", "115900"]
    assert send.sent[0]["tr_id"] == "FHKST03010230"
    # KRX 만 본다. NX(NXT)와 UN(통합)은 쓰지 않는다.
    assert send.sent[0]["query"]["FID_COND_MRKT_DIV_CODE"] == "J"
    # 저장은 오름차순이다.
    assert [bar.bar_at.astimezone(KST).strftime("%H%M%S") for bar in fetch.bars] == [
        "090000",
        "115900",
        "120000",
        "153000",
    ]


def test_stock_bars_drop_the_previous_session(monkeypatch):
    """09:00 이전을 요청하면 직전 세션의 뒷부분이 딸려 온다(실측 99봉).

    시각만 키로 쓰면 전날 값이 그날 봉을 덮어써서 하루 합이 누적 거래량과 어긋난다.
    """
    send = fake_stock_send(
        [
            [
                stock_row("090100", volume="500"),
                stock_row("151900", business_date="20260813", volume="99999"),
            ],
            [stock_row("090000", volume="700")],
        ]
    )
    monkeypatch.setattr(kis, "send_get", send)

    fetch = COLLECTOR.fetch_stock_bars(DomesticStock.SAMSUNG_ELECTRONICS, STOCK_DATE, Decimal(268000))

    assert [bar.volume for bar in fetch.bars] == [700, 500]
    assert all(bar.bar_at.astimezone(KST).date() == STOCK_DATE for bar in fetch.bars)


def test_stock_bars_keep_only_the_regular_session(monkeypatch):
    """15:32 같은 시간외 체결이 섞이면 한 심볼의 시계열에 성격이 다른 거래가 들어간다."""
    send = fake_stock_send([[stock_row("153200", volume="11196308"), stock_row("153000"), stock_row("090000")]])
    monkeypatch.setattr(kis, "send_get", send)

    fetch = COLLECTOR.fetch_stock_bars(DomesticStock.SAMSUNG_ELECTRONICS, STOCK_DATE, Decimal(268000))

    hours = [bar.bar_at.astimezone(KST).time() for bar in fetch.bars]
    assert hours == [SESSION_FIRST_BAR, SESSION_LAST_BAR]


def test_stock_bars_stop_when_a_day_is_empty(monkeypatch):
    """휴장일은 0봉으로 온다. 실패가 아니다."""
    send = fake_stock_send([[]])
    monkeypatch.setattr(kis, "send_get", send)

    fetch = COLLECTOR.fetch_stock_bars(DomesticStock.SAMSUNG_ELECTRONICS, STOCK_DATE, Decimal(268000))

    assert fetch.bars == ()
    assert fetch.call_count == 1


def test_stock_bars_never_call_forever(monkeypatch):
    """커서가 나아가지 않아도 호출이 무한히 늘지 않는다."""
    send = fake_stock_send([[stock_row("153000")] for _ in range(20)])
    monkeypatch.setattr(kis, "send_get", send)

    fetch = COLLECTOR.fetch_stock_bars(DomesticStock.SAMSUNG_ELECTRONICS, STOCK_DATE, Decimal(268000))

    assert fetch.call_count == kis.MAX_STOCK_BAR_CALLS


def test_stock_bars_carry_the_previous_close_given_by_the_caller(monkeypatch):
    """응답의 output1 은 요청한 날짜와 무관하게 지금 시세다(실측).

    그대로 쓰면 백필한 모든 봉에 오늘의 전일종가가 박힌다.
    """
    send = fake_stock_send([[stock_row("090000")]])
    monkeypatch.setattr(kis, "send_get", send)

    fetch = COLLECTOR.fetch_stock_bars(DomesticStock.SAMSUNG_ELECTRONICS, STOCK_DATE, Decimal(111))

    assert fetch.bars[0].previous_close == Decimal(111)


def test_stock_bars_stop_before_the_minute_still_forming(monkeypatch):
    """장중 조정은 진행 중인 분을 저장하지 않는다.

    REST upsert 가 `is_final=true` 로 굳히기 때문에, 아직 체결이 더 붙을 분을 저장하면 그
    부분 봉이 확정으로 남는다. 마감 뒤에 도는 확정 DAG 는 `until` 을 주지 않아 영향이 없다.
    """
    send = fake_stock_send([[stock_row("100000"), stock_row("095900"), stock_row("090000")]])
    monkeypatch.setattr(kis, "send_get", send)

    fetch = COLLECTOR.fetch_stock_bars(
        DomesticStock.SAMSUNG_ELECTRONICS,
        STOCK_DATE,
        Decimal(268000),
        until=datetime(2026, 8, 14, 10, 0, 30, tzinfo=KST),
    )

    # 커서가 마감(15:30)이 아니라 기준 시각의 분으로 시작한다. 미래 시각을 물으면 KIS 가
    # 무엇을 돌려주는지 계약에 없다.
    assert send.sent[0]["query"]["FID_INPUT_HOUR_1"] == "100000"
    assert [bar.bar_at.astimezone(KST).strftime("%H%M%S") for bar in fetch.bars] == ["090000", "095900"]


def test_stock_bars_ignore_a_future_cutoff(monkeypatch):
    """기준 시각이 마감보다 뒤면 창은 그대로 마감까지다."""
    send = fake_stock_send([[stock_row("153000"), stock_row("090000")]])
    monkeypatch.setattr(kis, "send_get", send)

    fetch = COLLECTOR.fetch_stock_bars(
        DomesticStock.SAMSUNG_ELECTRONICS,
        STOCK_DATE,
        Decimal(268000),
        until=datetime(2026, 8, 14, 20, 5, tzinfo=KST),
    )

    assert send.sent[0]["query"]["FID_INPUT_HOUR_1"] == "153000"
    assert len(fetch.bars) == 2


def test_a_cutoff_on_another_day_does_not_cut_the_session(monkeypatch):
    """어제를 백필하는데 오늘 시각으로 창을 자르면 안 된다."""
    send = fake_stock_send([[stock_row("153000"), stock_row("090000")]])
    monkeypatch.setattr(kis, "send_get", send)

    fetch = COLLECTOR.fetch_stock_bars(
        DomesticStock.SAMSUNG_ELECTRONICS,
        STOCK_DATE,
        Decimal(268000),
        until=datetime(2026, 8, 15, 9, 30, tzinfo=KST),
    )

    assert send.sent[0]["query"]["FID_INPUT_HOUR_1"] == "153000"
    assert len(fetch.bars) == 2


def test_stock_bars_can_be_capped_to_one_call(monkeypatch):
    """5분마다 도는 조정이 하루치를 매번 다시 받으면 호출이 12배가 된다.

    한 번에 120봉이 오므로 한 호출이면 최근 두 시간을 덮는다. 그보다 오래된 구멍은 마감 뒤
    확정 DAG 가 메운다.
    """
    send = fake_stock_send([[stock_row("153000"), stock_row("120000")], [stock_row("115900")]])
    monkeypatch.setattr(kis, "send_get", send)

    fetch = COLLECTOR.fetch_stock_bars(
        DomesticStock.SAMSUNG_ELECTRONICS,
        STOCK_DATE,
        Decimal(268000),
        max_calls=1,
    )

    assert fetch.call_count == 1
    assert len(fetch.bars) == 2


def test_a_call_cap_never_exceeds_the_exchange_limit(monkeypatch):
    """상한을 크게 줘도 거래소별 안전장치를 넘지 못한다."""
    send = fake_stock_send([[stock_row("153000")] for _ in range(30)])
    monkeypatch.setattr(kis, "send_get", send)

    fetch = COLLECTOR.fetch_stock_bars(
        DomesticStock.SAMSUNG_ELECTRONICS,
        STOCK_DATE,
        Decimal(268000),
        max_calls=99,
    )

    assert fetch.call_count == kis.MAX_STOCK_BAR_CALLS


def test_the_last_settled_close_is_read_for_the_day_before():
    """그날 종가를 분모로 쓰면 변동률이 항상 0에 가깝게 나온다."""
    connection = FakeConnection()
    connection.recorded_cursor = RowCursor((Decimal(268000),))

    value = last_settled_close(connection, "005930", STOCK_DATE)

    statement, parameters = connection.recorded_cursor.calls[0]
    assert value == Decimal(268000)
    assert parameters == ("005930", STOCK_DATE)
    assert "business_date < %s" in statement
    assert "ORDER BY business_date DESC" in statement


def test_a_missing_settled_close_is_none():
    """확정 일별 수급이 그 구간을 아직 안 채웠다는 뜻이다. 분모를 지어내지 않는다."""
    connection = FakeConnection()
    connection.recorded_cursor = RowCursor(None)

    assert last_settled_close(connection, "005930", STOCK_DATE) is None


def test_store_stock_bars_writes_the_stock_code_as_the_symbol(monkeypatch):
    """봉과 수급을 한 화면에서 겹치려면 심볼이 종목코드여야 한다."""
    send = fake_stock_send([[stock_row("090000"), stock_row("090100")]])
    monkeypatch.setattr(kis, "send_get", send)
    fetch = COLLECTOR.fetch_stock_bars(DomesticStock.SAMSUNG_ELECTRONICS, STOCK_DATE, Decimal(268000))
    connection = FakeConnection()

    assert COLLECTOR.store_stock_bars(connection, fetch) == 2

    rows = [args for statement, args in connection.recorded_cursor.calls if "INSERT INTO stock_bar" in statement]
    assert [row[1] for row in rows] == ["005930", "005930"]
    assert all(row[0] == "kis" for row in rows)
    # 거래소가 자연키의 한 축이라 행마다 함께 저장된다. 기본은 KRX다.
    assert all(row[2] == "KRX" for row in rows)


def test_store_stock_bars_records_the_call_count(monkeypatch):
    """호출 수가 계보에 남아야 한 거래일에 몇 번 물어봤는지 나중에 읽을 수 있다."""
    send = fake_stock_send([[stock_row("153000")], [stock_row("090000")]])
    monkeypatch.setattr(kis, "send_get", send)
    fetch = COLLECTOR.fetch_stock_bars(DomesticStock.SAMSUNG_ELECTRONICS, STOCK_DATE, Decimal(268000))
    connection = FakeConnection()
    COLLECTOR.store_stock_bars(connection, fetch)

    record = next(
        args for statement, args in connection.recorded_cursor.calls if "INSERT INTO source_record" in statement
    )
    metadata = json.loads(record[-1])
    assert record[2] == "stock_minute_bars"
    assert metadata["business_date"] == "2026-08-14"
    assert metadata["call_count"] == 2
    assert metadata["interval"] == "1m"


# ---------------------------------------------------------------------------
# 지수 일봉 (docs/market-technical-indicators.md 4절)
# ---------------------------------------------------------------------------

DAILY_SPAN_START = date(2026, 8, 20)
DAILY_SPAN_END = date(2026, 8, 21)


def index_daily_payload(
    dates: tuple[str, ...],
    rt_cd: str = "0",
    closes: tuple[str, ...] | None = None,
    highs: tuple[str, ...] | None = None,
    lows: tuple[str, ...] | None = None,
    volumes: tuple[str, ...] | None = None,
) -> bytes:
    """공식 `inquire_daily_indexchartprice` 응답 모양. KIS는 최신순으로 준다."""
    closes = closes if closes is not None else tuple(f"  {3200 + i}.55" for i in range(len(dates)))
    highs = highs if highs is not None else tuple(f"  {3210 + i}.90" for i in range(len(dates)))
    lows = lows if lows is not None else tuple(f"  {3190 + i}.10" for i in range(len(dates)))
    volumes = volumes if volumes is not None else tuple(str(400_000_000 + i) for i in range(len(dates)))
    return json.dumps(
        {
            "rt_cd": rt_cd,
            "msg_cd": "MCA00000",
            "msg1": "정상처리 되었습니다.",
            "output1": {"hts_kor_isnm": "코스피", "prdy_nmix": "  3195.00"},
            "output2": [
                {
                    "stck_bsop_date": day,
                    "bstp_nmix_oprc": f"  {3195 + i}.00",
                    "bstp_nmix_hgpr": highs[i],
                    "bstp_nmix_lwpr": lows[i],
                    "bstp_nmix_prpr": closes[i],
                    "acml_vol": volumes[i],
                    "mod_yn": "N",
                }
                for i, day in enumerate(dates)
            ],
        }
    ).encode("utf-8")


def daily_send(pages: list[tuple[bytes, str]]):
    """페이지 목록을 차례로 돌려주는 가짜 send_get. (요청 기록, 함수)를 준다."""
    requests: list[tuple[str, str, dict, str]] = []

    def send(token, app_key, app_secret, path, tr_id, query, tr_cont=""):
        requests.append((path, tr_id, dict(query), tr_cont))
        if len(requests) > len(pages):
            # 대본을 다 쓰면 빈 응답이다. 같은 장을 되풀이하면 걷기가 중복 날짜 오류로 끝나
            # 실제 종료 조건을 가린다.
            return index_daily_payload(()), 200, {"tr_cont": ""}
        body, next_flag = pages[len(requests) - 1]
        return body, 200, {"tr_cont": next_flag}

    return requests, send


def daily_collector() -> KisQuoteCollector:
    return KisQuoteCollector(SecretStr("token"), SecretStr("key"), SecretStr("secret"))


class TestFetchIndexDaily:
    def test_the_request_carries_the_official_contract(self, monkeypatch):
        requests, send = daily_send([(index_daily_payload(("20260821", "20260820")), "")])
        monkeypatch.setattr(kis, "send_get", send)

        fetch = daily_collector().fetch_index_daily(DomesticIndex.KOSPI, DAILY_SPAN_START, DAILY_SPAN_END, sleep=0)

        path, tr_id, query, tr_cont = requests[0]
        assert path == "/uapi/domestic-stock/v1/quotations/inquire-daily-indexchartprice"
        assert tr_id == "FHKUP03500100"
        assert tr_cont == ""
        assert query == {
            "FID_COND_MRKT_DIV_CODE": "U",
            "FID_INPUT_ISCD": "0001",
            "FID_INPUT_DATE_1": "20260820",
            "FID_INPUT_DATE_2": "20260821",
            "FID_PERIOD_DIV_CODE": "D",
        }
        assert fetch.symbol == "KOSPI"
        assert [bar.business_date for bar in fetch.bars] == [date(2026, 8, 20), date(2026, 8, 21)]
        assert fetch.page_count == 1

    def test_kosdaq_uses_its_own_index_code(self, monkeypatch):
        requests, send = daily_send([(index_daily_payload(("20260821",)), "")])
        monkeypatch.setattr(kis, "send_get", send)

        daily_collector().fetch_index_daily(DomesticIndex.KOSDAQ, DAILY_SPAN_START, DAILY_SPAN_END, sleep=0)

        assert requests[0][2]["FID_INPUT_ISCD"] == "1001"

    def test_a_continuation_header_asks_for_the_next_page(self, monkeypatch):
        requests, send = daily_send(
            [
                (index_daily_payload(("20260821",)), "M"),
                (index_daily_payload(("20260820",)), ""),
            ]
        )
        monkeypatch.setattr(kis, "send_get", send)

        fetch = daily_collector().fetch_index_daily(DomesticIndex.KOSPI, DAILY_SPAN_START, DAILY_SPAN_END, sleep=0)

        assert [request[3] for request in requests] == ["", "N"]
        # 연속조회는 같은 구간을 다시 묻는다
        assert requests[1][2]["FID_INPUT_DATE_2"] == "20260821"
        assert [bar.business_date for bar in fetch.bars] == [date(2026, 8, 20), date(2026, 8, 21)]
        assert fetch.page_count == 2

    def test_a_page_that_misses_the_span_start_walks_the_window_back(self, monkeypatch):
        """구간의 시작에 못 닿은 응답은 조용히 잘린 것이다(확정 수급 API와 같은 행태).

        **판정 기준은 행 수가 아니라 구간이다.** 2026-08-24에 한 장의 상한을 100봉으로
        가정했다가 실제가 50봉이라, 잘린 응답을 "구간을 다 줬다"로 읽고 걷지 않았다.
        그래서 지수 일봉이 두 달 넘게 50봉에 묶였다. 여기 세 봉짜리 짧은 응답이 걷는지를
        보는 이유가 그것이다 — 상한을 몰라도 판정이 선다.
        """
        span_start = date(2026, 1, 5)
        requests, send = daily_send(
            [
                (index_daily_payload(("20260821", "20260820", "20260819")), ""),
                (index_daily_payload(("20260105",)), ""),
            ]
        )
        monkeypatch.setattr(kis, "send_get", send)

        fetch = daily_collector().fetch_index_daily(DomesticIndex.KOSPI, span_start, DAILY_SPAN_END, sleep=0)

        assert len(requests) == 2
        # 요청 구간의 시작은 그대로 두고 끝만 가장 오래된 날짜 하루 전으로 옮긴다
        assert requests[1][2]["FID_INPUT_DATE_1"] == "20260105"
        assert requests[1][2]["FID_INPUT_DATE_2"] == "20260818"
        assert requests[1][3] == ""
        assert len(fetch.bars) == 4
        assert fetch.page_count == 2

    def test_reaching_the_span_start_stops_the_walk(self, monkeypatch):
        """구간의 시작에 닿았으면 더 부르지 않는다. 걷기가 끝나는 정상 경로다."""
        span_start = date(2026, 8, 19)
        requests, send = daily_send([(index_daily_payload(("20260821", "20260820", "20260819")), "")])
        monkeypatch.setattr(kis, "send_get", send)

        fetch = daily_collector().fetch_index_daily(DomesticIndex.KOSPI, span_start, DAILY_SPAN_END, sleep=0)

        assert len(requests) == 1
        assert fetch.page_count == 1
        assert len(fetch.bars) == 3

    def test_an_empty_walk_page_stops_the_walk(self, monkeypatch):
        """구간의 시작에 못 닿았어도 빈 응답이면 거기서 끝이다. 그 심볼의 이력이 짧은 것이다."""
        span_start = date(2026, 1, 5)
        requests, send = daily_send(
            [
                (index_daily_payload(("20260821", "20260820")), ""),
                (index_daily_payload(()), ""),
            ]
        )
        monkeypatch.setattr(kis, "send_get", send)

        fetch = daily_collector().fetch_index_daily(DomesticIndex.KOSPI, span_start, DAILY_SPAN_END, sleep=0)

        assert len(requests) == 2
        assert len(fetch.bars) == 2

    def test_more_pages_after_the_cap_fail_the_symbol(self, monkeypatch):
        span_start = date(2026, 8, 1)
        pages = [
            (index_daily_payload(((date(2026, 8, 21) - timedelta(days=page)).strftime("%Y%m%d"),)), "M")
            for page in range(kis.INDEX_DAILY_MAX_PAGES)
        ]
        requests, send = daily_send(pages)
        monkeypatch.setattr(kis, "send_get", send)

        with pytest.raises(KisPayloadError, match="pages"):
            daily_collector().fetch_index_daily(DomesticIndex.KOSPI, span_start, DAILY_SPAN_END, sleep=0)
        assert len(requests) == kis.INDEX_DAILY_MAX_PAGES

    def test_a_result_error_is_raised_as_such(self, monkeypatch):
        _, send = daily_send([(index_daily_payload(("20260821",), rt_cd="1"), "")])
        monkeypatch.setattr(kis, "send_get", send)

        with pytest.raises(KisResultError):
            daily_collector().fetch_index_daily(DomesticIndex.KOSPI, DAILY_SPAN_START, DAILY_SPAN_END, sleep=0)

    def test_an_empty_span_is_a_contract_error(self, monkeypatch):
        _, send = daily_send([(index_daily_payload(()), "")])
        monkeypatch.setattr(kis, "send_get", send)

        with pytest.raises(KisPayloadError, match="no daily bars"):
            daily_collector().fetch_index_daily(DomesticIndex.KOSPI, DAILY_SPAN_START, DAILY_SPAN_END, sleep=0)

    def test_a_duplicate_date_is_a_contract_error(self, monkeypatch):
        _, send = daily_send([(index_daily_payload(("20260821", "20260821")), "")])
        monkeypatch.setattr(kis, "send_get", send)

        with pytest.raises(KisPayloadError, match="duplicate"):
            daily_collector().fetch_index_daily(DomesticIndex.KOSPI, DAILY_SPAN_START, DAILY_SPAN_END, sleep=0)

    def test_a_date_outside_the_span_is_a_contract_error(self, monkeypatch):
        _, send = daily_send([(index_daily_payload(("20260822",)), "")])
        monkeypatch.setattr(kis, "send_get", send)

        with pytest.raises(KisPayloadError, match="outside"):
            daily_collector().fetch_index_daily(DomesticIndex.KOSPI, DAILY_SPAN_START, DAILY_SPAN_END, sleep=0)

    def test_a_broken_ohlc_is_a_contract_error(self, monkeypatch):
        _, send = daily_send([(index_daily_payload(("20260821",), highs=("  100.00",), lows=("  3000.00",)), "")])
        monkeypatch.setattr(kis, "send_get", send)

        with pytest.raises(KisPayloadError):
            daily_collector().fetch_index_daily(DomesticIndex.KOSPI, DAILY_SPAN_START, DAILY_SPAN_END, sleep=0)


class TestStoreIndexDaily:
    def test_rows_land_in_column_order_with_the_source_record(self, monkeypatch):
        _, send = daily_send([(index_daily_payload(("20260821", "20260820")), "")])
        monkeypatch.setattr(kis, "send_get", send)
        collector = daily_collector()
        fetch = collector.fetch_index_daily(DomesticIndex.KOSPI, DAILY_SPAN_START, DAILY_SPAN_END, sleep=0)

        connection = FakeConnection()
        stored = collector.store_index_daily(connection, fetch)

        assert stored == 2
        record = connection.recorded_cursor.calls[0]
        assert record[0] == SOURCE_RECORD_INSERT
        assert record[1][1] == "kis"
        assert record[1][2] == "inquire_daily_indexchartprice"
        metadata = json.loads(record[1][8])
        assert metadata["symbol"] == "KOSPI"
        assert metadata["start_date"] == "2026-08-20"
        assert metadata["end_date"] == "2026-08-21"
        assert metadata["page_count"] == 1
        assert metadata["bar_count"] == 2

        upserts = [call for call in connection.recorded_cursor.calls if call[0] == kis.INDEX_DAILY_UPSERT]
        assert len(upserts) == 2
        provider, symbol, business_date, _open, _high, _low, close, volume, source_record_id = upserts[0][1]
        assert (provider, symbol, business_date) == ("kis", "KOSPI", date(2026, 8, 20))
        assert close == Decimal("3201.55")
        assert volume == 400_000_001
        assert source_record_id == SOURCE_RECORD_ID

    def test_the_index_daily_upsert_matches_the_model(self):
        table = IndexDaily.__table__
        columns = inserted_columns(kis.INDEX_DAILY_UPSERT)
        assert set(columns) <= {column.name for column in table.columns}
        assert required_columns(table) <= set(columns)
        assert placeholder_count(kis.INDEX_DAILY_UPSERT) == len(columns)
