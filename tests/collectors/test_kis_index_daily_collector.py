"""KIS 지수 확정 일봉 수집기.

분봉·시장 등락은 `test_kis.py`가 덮는다. 이어받기 규칙과 잘림 판정이 이 API에만 있어
소스와 같은 경계로 나눠 둔다(`docs/analysis/market-technical-indicators.md` 4절).
"""

import json
import re
from datetime import date, timedelta
from decimal import Decimal
from typing import Self

import pytest
from pydantic import SecretStr
from sqlalchemy import Table

from apps.models.market import IndexDaily
from modules.collectors.kis import SOURCE_RECORD_INSERT, DomesticIndex, KisPayloadError, KisResultError
from modules.collectors.market import kis_index_daily
from modules.collectors.market.kis_index_daily import KisIndexDailyCollector

SOURCE_RECORD_ID = 1


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
    """저장 테스트를 PEP 249 경로에 고정한다. 배치 경로는 커서에 닿는 SQL이 드라이버 사정을 탄다."""
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


def daily_collector() -> KisIndexDailyCollector:
    return KisIndexDailyCollector(SecretStr("token"), SecretStr("key"), SecretStr("secret"))


class TestFetchIndexDaily:
    def test_the_request_carries_the_official_contract(self, monkeypatch):
        requests, send = daily_send([(index_daily_payload(("20260821", "20260820")), "")])
        monkeypatch.setattr(kis_index_daily, "send_get", send)

        fetch = daily_collector().fetch(DomesticIndex.KOSPI, DAILY_SPAN_START, DAILY_SPAN_END, sleep=0)

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
        monkeypatch.setattr(kis_index_daily, "send_get", send)

        daily_collector().fetch(DomesticIndex.KOSDAQ, DAILY_SPAN_START, DAILY_SPAN_END, sleep=0)

        assert requests[0][2]["FID_INPUT_ISCD"] == "1001"

    def test_kospi200_uses_its_own_index_code(self, monkeypatch):
        requests, send = daily_send([(index_daily_payload(("20260821",)), "")])
        monkeypatch.setattr(kis_index_daily, "send_get", send)

        fetch = daily_collector().fetch(DomesticIndex.KOSPI200, DAILY_SPAN_START, DAILY_SPAN_END, sleep=0)

        assert requests[0][2]["FID_INPUT_ISCD"] == "2001"
        # 저장 심볼은 업종코드가 아니라 Enum 값이다. 선물과 빼서 베이시스를 낼 때 짝이 된다.
        assert fetch.symbol == "KOSPI200"

    def test_a_continuation_header_asks_for_the_next_page(self, monkeypatch):
        requests, send = daily_send(
            [
                (index_daily_payload(("20260821",)), "M"),
                (index_daily_payload(("20260820",)), ""),
            ]
        )
        monkeypatch.setattr(kis_index_daily, "send_get", send)

        fetch = daily_collector().fetch(DomesticIndex.KOSPI, DAILY_SPAN_START, DAILY_SPAN_END, sleep=0)

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
        monkeypatch.setattr(kis_index_daily, "send_get", send)

        fetch = daily_collector().fetch(DomesticIndex.KOSPI, span_start, DAILY_SPAN_END, sleep=0)

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
        monkeypatch.setattr(kis_index_daily, "send_get", send)

        fetch = daily_collector().fetch(DomesticIndex.KOSPI, span_start, DAILY_SPAN_END, sleep=0)

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
        monkeypatch.setattr(kis_index_daily, "send_get", send)

        fetch = daily_collector().fetch(DomesticIndex.KOSPI, span_start, DAILY_SPAN_END, sleep=0)

        assert len(requests) == 2
        assert len(fetch.bars) == 2

    def test_more_pages_after_the_cap_fail_the_symbol(self, monkeypatch):
        span_start = date(2026, 8, 1)
        pages = [
            (index_daily_payload(((date(2026, 8, 21) - timedelta(days=page)).strftime("%Y%m%d"),)), "M")
            for page in range(kis_index_daily.INDEX_DAILY_MAX_PAGES)
        ]
        requests, send = daily_send(pages)
        monkeypatch.setattr(kis_index_daily, "send_get", send)

        with pytest.raises(KisPayloadError, match="pages"):
            daily_collector().fetch(DomesticIndex.KOSPI, span_start, DAILY_SPAN_END, sleep=0)
        assert len(requests) == kis_index_daily.INDEX_DAILY_MAX_PAGES

    def test_a_result_error_is_raised_as_such(self, monkeypatch):
        _, send = daily_send([(index_daily_payload(("20260821",), rt_cd="1"), "")])
        monkeypatch.setattr(kis_index_daily, "send_get", send)

        with pytest.raises(KisResultError):
            daily_collector().fetch(DomesticIndex.KOSPI, DAILY_SPAN_START, DAILY_SPAN_END, sleep=0)

    def test_an_empty_span_is_a_contract_error(self, monkeypatch):
        _, send = daily_send([(index_daily_payload(()), "")])
        monkeypatch.setattr(kis_index_daily, "send_get", send)

        with pytest.raises(KisPayloadError, match="no daily bars"):
            daily_collector().fetch(DomesticIndex.KOSPI, DAILY_SPAN_START, DAILY_SPAN_END, sleep=0)

    def test_a_duplicate_date_is_a_contract_error(self, monkeypatch):
        _, send = daily_send([(index_daily_payload(("20260821", "20260821")), "")])
        monkeypatch.setattr(kis_index_daily, "send_get", send)

        with pytest.raises(KisPayloadError, match="duplicate"):
            daily_collector().fetch(DomesticIndex.KOSPI, DAILY_SPAN_START, DAILY_SPAN_END, sleep=0)

    def test_a_date_outside_the_span_is_a_contract_error(self, monkeypatch):
        _, send = daily_send([(index_daily_payload(("20260822",)), "")])
        monkeypatch.setattr(kis_index_daily, "send_get", send)

        with pytest.raises(KisPayloadError, match="outside"):
            daily_collector().fetch(DomesticIndex.KOSPI, DAILY_SPAN_START, DAILY_SPAN_END, sleep=0)

    def test_a_broken_ohlc_is_a_contract_error(self, monkeypatch):
        _, send = daily_send([(index_daily_payload(("20260821",), highs=("  100.00",), lows=("  3000.00",)), "")])
        monkeypatch.setattr(kis_index_daily, "send_get", send)

        with pytest.raises(KisPayloadError):
            daily_collector().fetch(DomesticIndex.KOSPI, DAILY_SPAN_START, DAILY_SPAN_END, sleep=0)


class TestStoreIndexDaily:
    def test_rows_land_in_column_order_with_the_source_record(self, monkeypatch):
        _, send = daily_send([(index_daily_payload(("20260821", "20260820")), "")])
        monkeypatch.setattr(kis_index_daily, "send_get", send)
        collector = daily_collector()
        fetch = collector.fetch(DomesticIndex.KOSPI, DAILY_SPAN_START, DAILY_SPAN_END, sleep=0)

        connection = FakeConnection()
        stored = collector.store(connection, fetch)

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

        upserts = [call for call in connection.recorded_cursor.calls if call[0] == kis_index_daily.INDEX_DAILY_UPSERT]
        assert len(upserts) == 2
        provider, symbol, business_date, _open, _high, _low, close, volume, source_record_id = upserts[0][1]
        assert (provider, symbol, business_date) == ("kis", "KOSPI", date(2026, 8, 20))
        assert close == Decimal("3201.55")
        assert volume == 400_000_001
        assert source_record_id == SOURCE_RECORD_ID

    def test_the_index_daily_upsert_matches_the_model(self):
        table = IndexDaily.__table__
        columns = inserted_columns(kis_index_daily.INDEX_DAILY_UPSERT)
        assert set(columns) <= {column.name for column in table.columns}
        assert required_columns(table) <= set(columns)
        assert placeholder_count(kis_index_daily.INDEX_DAILY_UPSERT) == len(columns)
