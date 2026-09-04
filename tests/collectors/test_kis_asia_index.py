"""KIS 아시아 지수 장중 분봉. 시간대·`since` 절단·저장 계약을 가짜 연결로 검증한다.

미국 마감 분봉(`test_kis_overseas_index.py`)과 같은 수집기 클래스를 쓰되 조회 방식이 다르다 —
세션 날짜를 요구하지 않고 최근 구간만 남긴다. 응답에 어제 봉이 섞여 오는 것이 정상이다
(2026-09-04 실측: 10:03 KST 조회의 오래된 봉이 전날 14:39였다).
"""

import json
import re
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Self

import pytest
from pydantic import SecretStr

from modules.collectors.kis import INDEX_BAR_UPSERT, SOURCE_RECORD_INSERT, KisPayloadError
from modules.collectors.market import kis_overseas_index as overseas
from modules.collectors.market.kis_overseas_index import (
    ASIA_SOURCE_KEY,
    HOUR_CLS_CODE,
    MARKET_DIV_CODE,
    OVERSEAS_INDEX_CHART_PATH,
    OVERSEAS_INDEX_CHART_TR_ID,
    AsiaIndex,
    KisOverseasIndexCollector,
    parse_index_bars,
)
from modules.collectors.market.yahoo import QuoteSymbol

SOURCE_RECORD_ID = 11

# 실측 응답(2026-09-04 10:03 KST, JP#NI225)의 모양. 최신순이고 전날 봉이 뒤에 붙어 온다.
RAW_BARS = (
    ("20260904", "094800", "64750.28", "64760.00", "64700.00", "64709.74"),
    ("20260904", "094700", "64818.77", "64820.00", "64740.00", "64750.28"),
    ("20260903", "143900", "64100.00", "64120.00", "64080.00", "64086.69"),
)


def chart_payload(
    bars=RAW_BARS,
    previous_close: str = "64214.48",
    code: str | None = "JP#NI225",
    name: str = "일본니케이 225지수",
) -> bytes:
    output1 = {"hts_kor_isnm": name, "ovrs_nmix_prdy_clpr": previous_close}
    if code is not None:
        output1["stck_shrn_iscd"] = code
    return json.dumps(
        {
            "rt_cd": "0",
            "msg_cd": "MCA00000",
            "msg1": "정상처리 되었습니다.",
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
                for business_date, hour, open_, high, low, close in bars
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
    monkeypatch.setattr("modules.upsert._execute_batch", None)


def placeholder_count(statement: str) -> int:
    values = re.search(r"VALUES \(([^)]+)\)", statement, re.DOTALL)
    assert values is not None
    return values.group(1).count("%s")


def collector() -> KisOverseasIndexCollector:
    return KisOverseasIndexCollector(SecretStr("t"), SecretStr("k"), SecretStr("s"))


def fake_send_get(monkeypatch, body: bytes) -> dict:
    sent: dict = {}

    def send_get(token, app_key, app_secret, path, tr_id, query, tr_cont=""):
        sent.update(path=path, tr_id=tr_id, query=query)
        return body, 200, {}

    monkeypatch.setattr(overseas, "send_get", send_get)
    return sent


# --- 파싱: 시간대 ---------------------------------------------------------------------------


def test_parse_reads_tokyo_wall_clock_as_utc():
    """JST는 UTC+9다. 09:48 JST = 00:48Z. 뉴욕 상수를 그대로 쓰면 13시간 어긋난다."""
    bars, name = parse_index_bars(chart_payload(), AsiaIndex.NIKKEI225)

    assert name == "일본니케이 225지수"
    assert bars[-1].bar_at == datetime(2026, 9, 4, 0, 48, tzinfo=UTC)
    assert bars[-1].close == Decimal("64709.74")
    assert {bar.previous_close for bar in bars} == {Decimal("64214.48")}


def test_parse_reads_hong_kong_wall_clock_as_utc():
    """HKT는 UTC+8이고 서머타임이 없다. 16:08 HKT = 08:08Z."""
    body = chart_payload(bars=(("20260903", "160800", "25213.31", "25213.31", "25213.31", "25213.31"),), code="HK#HS")

    bars, _ = parse_index_bars(body, AsiaIndex.HSI)

    assert bars[0].bar_at == datetime(2026, 9, 3, 8, 8, tzinfo=UTC)


def test_parse_keeps_yesterday_bars_and_sorts_ascending():
    """장중 폴링 응답에는 전날 봉이 섞여 온다. 세션 날짜를 요구하지 않고 그대로 읽는다."""
    bars, _ = parse_index_bars(chart_payload(), AsiaIndex.NIKKEI225)

    assert [bar.bar_at for bar in bars] == sorted(bar.bar_at for bar in bars)
    assert bars[0].bar_at == datetime(2026, 9, 3, 5, 39, tzinfo=UTC)
    assert len(bars) == len(RAW_BARS)


def test_parse_fails_when_kis_answers_for_another_code():
    with pytest.raises(KisPayloadError, match="SHANG"):
        parse_index_bars(chart_payload(code="SHANG"), AsiaIndex.NIKKEI225)


def test_parse_rejects_an_empty_chart():
    """모르는 코드에도 rt_cd=0에 0건으로 답한다(`N225`·`HSI`가 그랬다). 조용한 0건은 실패다."""
    with pytest.raises(KisPayloadError, match="empty chart"):
        parse_index_bars(chart_payload(bars=()), AsiaIndex.SSE_COMP)


# --- 조회: since 절단 ----------------------------------------------------------------------


def test_fetch_since_keeps_only_the_recent_bars(monkeypatch):
    """폴링은 최근 구간만 저장한다. 전날 봉은 응답에 있어도 버린다."""
    fake_send_get(monkeypatch, chart_payload())
    since = datetime(2026, 9, 4, 0, 30, tzinfo=UTC)

    fetch = collector().fetch_since(AsiaIndex.NIKKEI225, since)

    assert [bar.bar_at for bar in fetch.bars] == [
        datetime(2026, 9, 4, 0, 47, tzinfo=UTC),
        datetime(2026, 9, 4, 0, 48, tzinfo=UTC),
    ]
    assert fetch.latest_bar_at == datetime(2026, 9, 4, 0, 48, tzinfo=UTC)
    assert fetch.since == since


def test_fetch_since_with_nothing_recent_is_empty_not_an_error(monkeypatch):
    """휴장이나 개장 전이면 응답은 있는데 최근 봉이 없다. 그건 실패가 아니다."""
    fake_send_get(monkeypatch, chart_payload())

    fetch = collector().fetch_since(AsiaIndex.NIKKEI225, datetime(2026, 9, 4, 6, 0, tzinfo=UTC))

    assert fetch.bars == ()
    assert fetch.latest_bar_at is None


def test_fetch_since_sends_the_raw_code_with_the_hash(monkeypatch):
    """`JP#NI225`의 `#`은 그대로 보낸다. URL 인코딩하면(`JP%23NI225`) 0건이다(실측)."""
    sent = fake_send_get(monkeypatch, chart_payload())

    collector().fetch_since(AsiaIndex.NIKKEI225, datetime(2026, 9, 4, 0, 30, tzinfo=UTC))

    assert sent["path"] == OVERSEAS_INDEX_CHART_PATH
    assert sent["tr_id"] == OVERSEAS_INDEX_CHART_TR_ID
    assert sent["query"] == {
        "FID_COND_MRKT_DIV_CODE": MARKET_DIV_CODE,
        "FID_INPUT_ISCD": "JP#NI225",
        "FID_HOUR_CLS_CODE": HOUR_CLS_CODE,
        "FID_PW_DATA_INCU_YN": "Y",
    }


# --- 저장 ---------------------------------------------------------------------------------


def test_store_writes_the_asia_source_key_and_metadata(monkeypatch):
    fake_send_get(monkeypatch, chart_payload())
    since = datetime(2026, 9, 4, 0, 30, tzinfo=UTC)
    fetch = collector().fetch_since(AsiaIndex.NIKKEI225, since)
    connection = FakeConnection()

    stored = collector().store(connection, fetch)

    statement, parameters = connection.recorded_cursor.calls[0]
    assert statement == SOURCE_RECORD_INSERT
    assert stored == 2
    assert parameters[:3] == ("api", "kis", ASIA_SOURCE_KEY)
    assert parameters[5:8] == ("succeeded", 2, None)
    metadata = json.loads(parameters[8])
    assert metadata["symbol"] == "NIKKEI225"
    assert metadata["kis_code"] == "JP#NI225"
    assert metadata["since"] == "2026-09-04T00:30:00+00:00"
    assert metadata["bar_count"] == 2
    assert metadata["latest_bar_at"] == "2026-09-04T00:48:00+00:00"


def test_store_records_an_empty_poll_with_a_null_latest_bar(monkeypatch):
    """0건도 계보를 남긴다. 조회했는데 없던 구간과 아직 조회하지 않은 구간이 구분돼야 한다."""
    fake_send_get(monkeypatch, chart_payload())
    fetch = collector().fetch_since(AsiaIndex.NIKKEI225, datetime(2026, 9, 4, 6, 0, tzinfo=UTC))
    connection = FakeConnection()

    stored = collector().store(connection, fetch)

    assert stored == 0
    _, parameters = connection.recorded_cursor.calls[0]
    assert json.loads(parameters[8])["latest_bar_at"] is None
    assert len(connection.recorded_cursor.calls) == 1


def test_store_row_shape_matches_the_index_bar_upsert(monkeypatch):
    fake_send_get(monkeypatch, chart_payload())
    fetch = collector().fetch_since(AsiaIndex.NIKKEI225, datetime(2026, 9, 4, 0, 30, tzinfo=UTC))
    connection = FakeConnection()

    collector().store(connection, fetch)

    rows = [parameters for statement, parameters in connection.recorded_cursor.calls if statement == INDEX_BAR_UPSERT]
    assert len(rows) == 2
    assert all(len(row) == placeholder_count(INDEX_BAR_UPSERT) for row in rows)
    assert rows[0][:3] == ("kis", "NIKKEI225", datetime(2026, 9, 4, 0, 47, tzinfo=UTC))
    assert rows[0][-1] == SOURCE_RECORD_ID


# --- 식별자 --------------------------------------------------------------------------------


def test_asia_symbols_reuse_the_yahoo_symbols():
    """같은 지수는 같은 심볼이다. `quote_symbol`의 라벨·국가를 공유하고 `(provider, symbol)`로 갈린다."""
    yahoo = {symbol.value: symbol for symbol in QuoteSymbol}

    for index in AsiaIndex:
        assert index.value in yahoo, index
        assert yahoo[index.value].kind == "index", index
        assert index.label == yahoo[index.value].label, index


def test_asia_timezones_have_no_daylight_saving():
    """네 시장 모두 서머타임이 없다. 한 해 어느 날이든 UTC 오프셋이 같아야 한다."""
    for index in AsiaIndex:
        winter = datetime(2026, 1, 15, 12, tzinfo=index.timezone).utcoffset()
        summer = datetime(2026, 7, 15, 12, tzinfo=index.timezone).utcoffset()
        assert winter == summer, index


# --- 일봉 ---------------------------------------------------------------------------------


def daily_payload(dates: tuple[str, ...], code: str = "JP#NI225", name: str = "일본니케이 225지수") -> bytes:
    """`inquire_daily_chartprice` 응답 모양(2026-09-04 실측). 최신순이고 값에 공백 패딩이 붙는다."""
    rows = [
        {
            "stck_bsop_date": day,
            "ovrs_nmix_oprc": "  64498.94",
            "ovrs_nmix_hgpr": "  64846.31",
            "ovrs_nmix_lwpr": "  64228.47",
            "ovrs_nmix_prpr": "  64783.30",
            "acml_vol": "0",
            "mod_yn": "N",
        }
        for day in dates
    ]
    head = {"hts_kor_isnm": name, "ovrs_nmix_prdy_clpr": "  64214.48", "stck_shrn_iscd": code}
    return json.dumps({"rt_cd": "0", "msg_cd": "MCA00000", "msg1": "정상", "output1": head, "output2": rows}).encode()


def test_daily_fetch_accepts_an_asia_index(monkeypatch):
    """일봉 수집기는 미국 Enum에 묶여 있었다. 코드를 가진 것이면 받아야 아시아 일봉 DAG가 같은 수집기를 쓴다."""
    from modules.collectors.market import kis_overseas_index_daily as daily
    from modules.collectors.market.kis_overseas_index_daily import KisOverseasIndexDailyCollector

    requests: list[dict] = []

    def send_get(token, app_key, app_secret, path, tr_id, query, tr_cont=""):
        requests.append(dict(query))
        return daily_payload(("20260904", "20260903")), 200, {"tr_cont": ""}

    monkeypatch.setattr(daily, "send_get", send_get)
    collector = KisOverseasIndexDailyCollector(SecretStr("t"), SecretStr("k"), SecretStr("s"))

    fetch = collector.fetch(AsiaIndex.NIKKEI225, date(2026, 9, 3), date(2026, 9, 4), sleep=0)

    assert requests[0]["FID_INPUT_ISCD"] == "JP#NI225"
    assert fetch.index is AsiaIndex.NIKKEI225
    assert [bar.business_date for bar in fetch.bars] == [date(2026, 9, 3), date(2026, 9, 4)]

    connection = FakeConnection()
    collector.store(connection, fetch)
    rows = [
        parameters for statement, parameters in connection.recorded_cursor.calls if statement != SOURCE_RECORD_INSERT
    ]
    assert rows and rows[0][:2] == ("kis", "NIKKEI225")
    assert json.loads(connection.recorded_cursor.calls[0][1][8])["kis_code"] == "JP#NI225"
