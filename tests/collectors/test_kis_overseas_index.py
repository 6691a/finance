"""KIS 해외지수 분봉 수집기. 파싱·시간대·저장 계약을 가짜 연결로 검증한다."""

import json
import re
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Self

import pytest
from pydantic import SecretStr
from sqlalchemy import Table

from apps.models.market import IndexBar
from apps.models.raw import SourceRecord
from modules.briefing import market_data
from modules.collectors.kis import (
    INDEX_BAR_UPSERT,
    SOURCE_RECORD_INSERT,
    DomesticIndex,
    KisPayloadError,
    KisResultError,
)
from modules.collectors.market import kis_overseas_index as overseas
from modules.collectors.market.kis_overseas_index import (
    HOUR_CLS_CODE,
    MARKET_DIV_CODE,
    OVERSEAS_INDEX_CHART_PATH,
    OVERSEAS_INDEX_CHART_TR_ID,
    SOURCE_KEY,
    KisOverseasIndexCollector,
    OverseasIndex,
    OverseasIndexFetch,
    parse_overseas_index_bars,
    us_session_date,
)
from modules.collectors.market.yahoo import QuoteSymbol

SESSION = date(2026, 8, 21)
SOURCE_RECORD_ID = 7
STARTED_AT = datetime(2026, 8, 21, 22, 30, tzinfo=UTC)
COMPLETED_AT = datetime(2026, 8, 21, 22, 30, 1, tzinfo=UTC)

# 실측 응답(2026-08-21 SPX)의 끝부분. 최신순이고 16:00 이후는 정산 구간 봉이다.
RAW_BARS = (
    ("164100", "7674.37", "7674.37", "7674.37", "7674.37"),
    ("160100", "7674.30", "7674.30", "7674.30", "7674.30"),
    ("160000", "7674.90", "7674.90", "7674.30", "7674.30"),
    ("155900", "7679.10", "7680.14", "7672.51", "7674.10"),
)


def chart_payload(
    bars=RAW_BARS,
    business_date: str = "20260821",
    previous_close: str = "7641.16",
    code: str = "SPX",
    rt_cd: str = "0",
    msg_cd: str = "MCA00000",
    msg1: str = "정상처리 되었습니다.",
) -> bytes:
    output1 = {
        "hts_kor_isnm": "S&P500",
        "ovrs_nmix_prpr": "7674.37",
        "ovrs_nmix_prdy_clpr": previous_close,
        "stck_shrn_iscd": code,
    }
    if code is None:
        del output1["stck_shrn_iscd"]
    return json.dumps(
        {
            "rt_cd": rt_cd,
            "msg_cd": msg_cd,
            "msg1": msg1,
            "output1": output1,
            "output2": [
                {
                    "stck_bsop_date": business_date,
                    "stck_cntg_hour": hour,
                    "optn_oprc": open_,
                    "optn_hgpr": high,
                    "optn_lwpr": low,
                    "optn_prpr": close,
                    "cntg_vol": "0",
                }
                for hour, open_, high, low, close in bars
            ],
        }
    ).encode()


class FakeCursor:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple]] = []

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *args: object) -> bool:
        return False

    def execute(self, statement: str, parameters: tuple) -> None:
        self.calls.append((statement, parameters))

    def executemany(self, statement: str, parameters) -> None:
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
    # 행 단위 SQL이 커서에 그대로 보여야 컬럼 계약을 검증할 수 있다(test_kis.py와 같다).
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


def collector() -> KisOverseasIndexCollector:
    """자격 증명은 생성자로만 들어간다. 값 자체는 `send_get`을 가짜로 바꾼 테스트에서만 쓰인다."""
    return KisOverseasIndexCollector(SecretStr("t"), SecretStr("k"), SecretStr("s"))


def fetch_for(body: bytes | None = None, index: OverseasIndex = OverseasIndex.SP500) -> OverseasIndexFetch:
    bars, name = parse_overseas_index_bars(body if body is not None else chart_payload(), index, SESSION)
    return OverseasIndexFetch(
        index=index,
        session_date=SESSION,
        name=name,
        bars=bars,
        status=200,
        started_at=STARTED_AT,
        completed_at=COMPLETED_AT,
    )


# --- 파싱 ---------------------------------------------------------------------------------


def test_parse_reads_new_york_wall_clock_as_utc():
    """8월은 EDT(UTC-4)다. 16:41 ET = 20:41Z."""
    bars, name = parse_overseas_index_bars(chart_payload(), OverseasIndex.SP500, SESSION)

    assert name == "S&P500"
    assert bars[-1].bar_at == datetime(2026, 8, 21, 20, 41, tzinfo=UTC)
    assert bars[0].bar_at == datetime(2026, 8, 21, 19, 59, tzinfo=UTC)


def test_parse_handles_standard_time_in_winter():
    """1월은 EST(UTC-5)다. 16:00 ET = 21:00Z. 고정 offset으로 계산하면 한 시간 어긋난다."""
    body = chart_payload(bars=RAW_BARS[2:3], business_date="20260115")

    bars, _ = parse_overseas_index_bars(body, OverseasIndex.SP500, date(2026, 1, 15))

    assert bars[0].bar_at == datetime(2026, 1, 15, 21, 0, tzinfo=UTC)


def test_parse_sorts_ascending_and_keeps_the_settlement_bars():
    """응답은 최신순이다. 정산 구간 봉을 버리지 않아야 마지막 봉이 공식 종가가 된다."""
    bars, _ = parse_overseas_index_bars(chart_payload(), OverseasIndex.SP500, SESSION)

    assert [bar.bar_at for bar in bars] == sorted(bar.bar_at for bar in bars)
    assert len(bars) == len(RAW_BARS)
    assert bars[-1].close == Decimal("7674.37")
    assert bars[0].open == Decimal("7679.10")
    assert bars[0].volume == 0


def test_parse_reads_previous_close_from_output1():
    bars, _ = parse_overseas_index_bars(chart_payload(previous_close="7641.16"), OverseasIndex.SP500, SESSION)

    assert {bar.previous_close for bar in bars} == {Decimal("7641.16")}


def test_parse_fails_on_a_stale_session_date():
    """날짜 커서가 없는 API라 묵은 봉이 그대로 온다. 어제 봉을 오늘 것처럼 저장하지 않는다."""
    body = chart_payload(business_date="20260820")

    with pytest.raises(KisPayloadError, match="20260820.*20260821"):
        parse_overseas_index_bars(body, OverseasIndex.SP500, SESSION)


def test_parse_fails_when_kis_answers_for_another_code():
    body = chart_payload(code="COMP")

    with pytest.raises(KisPayloadError, match="COMP"):
        parse_overseas_index_bars(body, OverseasIndex.SP500, SESSION)


def test_parse_tolerates_a_missing_code_echo():
    """`.DJI` 응답에는 `stck_shrn_iscd`가 없었다. 없으면 대조를 건너뛴다."""
    bars, _ = parse_overseas_index_bars(chart_payload(code=None), OverseasIndex.SP500, SESSION)

    assert len(bars) == len(RAW_BARS)


def test_parse_rejects_an_empty_chart():
    """모르는 코드에도 rt_cd=0에 0건으로 답한다. 조용한 0건을 성공으로 두지 않는다."""
    with pytest.raises(KisPayloadError, match="empty chart"):
        parse_overseas_index_bars(chart_payload(bars=()), OverseasIndex.NASDAQ, SESSION)


def test_parse_raises_the_body_error_code():
    body = chart_payload(rt_cd="1", msg_cd="EGW00123", msg1="기간이 만료된 token 입니다.")

    with pytest.raises(KisResultError) as excinfo:
        parse_overseas_index_bars(body, OverseasIndex.SP500, SESSION)

    assert excinfo.value.code == "EGW00123"


@pytest.mark.parametrize("previous_close", ["", "0", "0.00"])
def test_parse_rejects_a_missing_previous_close(previous_close):
    with pytest.raises(KisPayloadError, match="previous close"):
        parse_overseas_index_bars(chart_payload(previous_close=previous_close), OverseasIndex.SP500, SESSION)


def test_parse_rejects_a_non_numeric_price():
    body = chart_payload(bars=(("160000", "n/a", "1", "1", "1"),))

    with pytest.raises(KisPayloadError, match="non-numeric open"):
        parse_overseas_index_bars(body, OverseasIndex.SP500, SESSION)


def test_parse_rejects_a_broken_body():
    with pytest.raises(KisPayloadError):
        parse_overseas_index_bars(b"<html>", OverseasIndex.SP500, SESSION)


# --- 요청 ---------------------------------------------------------------------------------


def test_fetch_sends_the_documented_query(monkeypatch):
    sent: dict = {}

    def fake_send_get(token, app_key, app_secret, path, tr_id, query, tr_cont=""):
        sent.update(path=path, tr_id=tr_id, query=query)
        return chart_payload(code="COMP"), 200, {}

    monkeypatch.setattr(overseas, "send_get", fake_send_get)

    fetch = collector().fetch(OverseasIndex.NASDAQ, SESSION)

    assert sent["path"] == OVERSEAS_INDEX_CHART_PATH
    assert sent["tr_id"] == OVERSEAS_INDEX_CHART_TR_ID
    assert sent["query"] == {
        "FID_COND_MRKT_DIV_CODE": MARKET_DIV_CODE,
        "FID_INPUT_ISCD": "COMP",
        "FID_HOUR_CLS_CODE": HOUR_CLS_CODE,
        "FID_PW_DATA_INCU_YN": "Y",
    }
    assert fetch.index is OverseasIndex.NASDAQ
    assert fetch.latest_bar_at == datetime(2026, 8, 21, 20, 41, tzinfo=UTC)


# --- 저장 ---------------------------------------------------------------------------------


def test_store_writes_one_source_record_without_the_payload():
    connection = FakeConnection()

    stored = collector().store(connection, fetch_for())

    statement, parameters = connection.recorded_cursor.calls[0]
    assert statement == SOURCE_RECORD_INSERT
    assert stored == len(RAW_BARS)
    assert parameters[:3] == ("api", "kis", SOURCE_KEY)
    assert parameters[5:8] == ("succeeded", len(RAW_BARS), None)
    metadata = json.loads(parameters[8])
    assert metadata["symbol"] == "SP500"
    assert metadata["kis_code"] == "SPX"
    assert metadata["session_date"] == "2026-08-21"
    assert metadata["bar_count"] == len(RAW_BARS)
    assert metadata["latest_bar_at"] == "2026-08-21T20:41:00+00:00"


def test_store_row_shape_matches_the_index_bar_upsert():
    connection = FakeConnection()

    collector().store(connection, fetch_for())

    rows = [parameters for statement, parameters in connection.recorded_cursor.calls if statement == INDEX_BAR_UPSERT]
    assert len(rows) == len(RAW_BARS)
    assert all(len(row) == placeholder_count(INDEX_BAR_UPSERT) for row in rows)
    first = rows[0]
    assert first[:2] == ("kis", "SP500")
    assert first[2] == datetime(2026, 8, 21, 19, 59, tzinfo=UTC)
    assert first[-1] == SOURCE_RECORD_ID


@pytest.mark.parametrize(
    ("statement", "table"),
    [(INDEX_BAR_UPSERT, IndexBar.__table__), (SOURCE_RECORD_INSERT, SourceRecord.__table__)],
)
def test_sql_columns_match_the_models(statement, table):
    columns = inserted_columns(statement)

    assert set(columns) <= {column.name for column in table.columns}
    assert required_columns(table) <= set(columns)


# --- 식별자 --------------------------------------------------------------------------------


def test_symbols_do_not_collide_with_other_collectors():
    """`quote_bar.symbol` 공간은 제공처가 달라도 하나다. 브리핑·대시보드가 심볼로 찾는다."""
    ours = {index.value for index in OverseasIndex}

    assert ours.isdisjoint({symbol.value for symbol in QuoteSymbol})
    assert ours.isdisjoint({index.value for index in DomesticIndex})


def test_session_date_matches_the_briefing_helper():
    """브리핑 모듈을 import하지 않으려고 따로 둔 한 줄. 둘이 어긋나면 DAG가 엉뚱한 날을 묻는다."""
    # KST 화요일 07:30 = UTC 월요일 22:30 = 뉴욕 월요일 18:30 → 월요일 세션.
    moment = datetime(2026, 8, 24, 22, 30, tzinfo=UTC)

    assert us_session_date(moment) == market_data.us_session_date(moment) == date(2026, 8, 24)
