"""KIS 미국 현물지수 확정 일봉 수집기.

마감 분봉(`test_kis_overseas_index.py`)과 나눠 둔다. 조회 단위도 저장 테이블도 다르다.

API 계약은 2026-08-27 운영 앱키 실측이다(`docs/collection/kis-index-daily-collection.md` 6.3절).
"""

import json
import re
from datetime import date
from decimal import Decimal
from typing import Self

import pytest
from pydantic import SecretStr
from sqlalchemy import Table

from apps.models.market import IndexDaily
from modules.collectors.kis import SOURCE_RECORD_INSERT, KisPayloadError, KisResultError
from modules.collectors.market import kis_overseas_index_daily
from modules.collectors.market.kis_overseas_index import OverseasIndex
from modules.collectors.market.kis_overseas_index_daily import (
    OVERSEAS_DAILY_MAX_PAGES,
    KisOverseasIndexDailyCollector,
)

SOURCE_RECORD_ID = 1

SPAN_START = date(2026, 8, 20)
SPAN_END = date(2026, 8, 21)


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


def overseas_daily_payload(
    dates: tuple[str, ...],
    rt_cd: str = "0",
    echoed_code: str | None = "SPX",
    opens: tuple[str, ...] | None = None,
    highs: tuple[str, ...] | None = None,
    lows: tuple[str, ...] | None = None,
    closes: tuple[str, ...] | None = None,
    volumes: tuple[str, ...] | None = None,
    output2: object | None = None,
) -> bytes:
    """공식 `inquire_daily_chartprice` 응답 모양. KIS는 최신순으로 준다.

    `echoed_code=None`이면 `output1`에 코드 칸이 없다. **`.DJI`의 실제 모양이다**(6.3절 실측).
    """
    opens = opens if opens is not None else tuple(f"  {7666 + i}.88" for i in range(len(dates)))
    highs = highs if highs is not None else tuple(f"  {7690 + i}.73" for i in range(len(dates)))
    lows = lows if lows is not None else tuple(f"  {7657 + i}.41" for i in range(len(dates)))
    closes = closes if closes is not None else tuple(f"  {7675 + i}.70" for i in range(len(dates)))
    # SPX는 실측에서 0이 온다. 지수라 거래량 개념이 없는 계열이다.
    volumes = volumes if volumes is not None else tuple("0" for _ in dates)
    rows = [
        {
            "stck_bsop_date": day,
            "ovrs_nmix_oprc": opens[i],
            "ovrs_nmix_hgpr": highs[i],
            "ovrs_nmix_lwpr": lows[i],
            "ovrs_nmix_prpr": closes[i],
            "acml_vol": volumes[i],
            "mod_yn": "N",
        }
        for i, day in enumerate(dates)
    ]
    head: dict[str, str] = {"hts_kor_isnm": "S&P500", "ovrs_nmix_prdy_clpr": "  7660.00"}
    if echoed_code is not None:
        head["stck_shrn_iscd"] = echoed_code
    return json.dumps(
        {
            "rt_cd": rt_cd,
            "msg_cd": "MCA00000",
            "msg1": "정상처리 되었습니다.",
            "output1": head,
            "output2": rows if output2 is None else output2,
        }
    ).encode("utf-8")


def overseas_send(pages: list[bytes]):
    requests: list[tuple[str, str, dict, str]] = []

    def send(token, app_key, app_secret, path, tr_id, query, tr_cont=""):
        requests.append((path, tr_id, dict(query), tr_cont))
        if len(requests) > len(pages):
            # 대본을 다 쓰면 빈 응답이다. 같은 장을 되풀이하면 걷기가 중복 날짜 오류로 끝나
            # 실제 종료 조건을 가린다. 빈 응답에는 코드 칸도 없다 — 실측의 만기물·`.DJI`가 그렇다.
            return overseas_daily_payload((), echoed_code=None), 200, {"tr_cont": ""}
        return pages[len(requests) - 1], 200, {"tr_cont": ""}

    return requests, send


def overseas_collector() -> KisOverseasIndexDailyCollector:
    return KisOverseasIndexDailyCollector(SecretStr("token"), SecretStr("key"), SecretStr("secret"))


class TestFetchOverseasDaily:
    def test_the_request_carries_the_official_contract(self, monkeypatch):
        requests, send = overseas_send([overseas_daily_payload(("20260821", "20260820"))])
        monkeypatch.setattr(kis_overseas_index_daily, "send_get", send)

        fetch = overseas_collector().fetch(OverseasIndex.SP500, SPAN_START, SPAN_END, sleep=0)

        path, tr_id, query, tr_cont = requests[0]
        assert path == "/uapi/overseas-price/v1/quotations/inquire-daily-chartprice"
        assert tr_id == "FHKST03030100"
        assert tr_cont == ""
        assert query == {
            "FID_COND_MRKT_DIV_CODE": "N",
            "FID_INPUT_ISCD": "SPX",
            "FID_INPUT_DATE_1": "20260820",
            "FID_INPUT_DATE_2": "20260821",
            "FID_PERIOD_DIV_CODE": "D",
        }
        assert fetch.index is OverseasIndex.SP500
        # 최신순으로 온 것을 거래일 오름차순으로 뒤집는다.
        assert [bar.business_date for bar in fetch.bars] == [date(2026, 8, 20), date(2026, 8, 21)]
        assert fetch.page_count == 1

    def test_nasdaq_uses_the_composite_code(self, monkeypatch):
        """저장 심볼은 `NASDAQ`이고 KIS 코드는 `COMP`다. 나스닥100(`NDX`)이 아니다."""
        requests, send = overseas_send([overseas_daily_payload(("20260821",), echoed_code="COMP")])
        monkeypatch.setattr(kis_overseas_index_daily, "send_get", send)

        fetch = overseas_collector().fetch(OverseasIndex.NASDAQ, SPAN_START, SPAN_END, sleep=0)

        assert requests[0][2]["FID_INPUT_ISCD"] == "COMP"
        assert fetch.index.value == "NASDAQ"

    def test_the_provider_business_date_is_kept_as_given(self, monkeypatch):
        """KIS가 주는 뉴욕 거래일을 그대로 쓴다. UTC 날짜로 다시 계산하지 않는다."""
        _, send = overseas_send([overseas_daily_payload(("20260821",))])
        monkeypatch.setattr(kis_overseas_index_daily, "send_get", send)

        fetch = overseas_collector().fetch(OverseasIndex.SP500, SPAN_START, SPAN_END, sleep=0)

        assert fetch.bars[0].business_date == date(2026, 8, 21)

    def test_a_zero_volume_is_allowed(self, monkeypatch):
        """실측: SPX·NDX는 0, COMP·.DJI는 실값이다. 0을 결측으로 읽으면 안 된다."""
        _, send = overseas_send([overseas_daily_payload(("20260821",), volumes=("0",))])
        monkeypatch.setattr(kis_overseas_index_daily, "send_get", send)

        fetch = overseas_collector().fetch(OverseasIndex.SP500, SPAN_START, SPAN_END, sleep=0)

        assert fetch.bars[0].volume == 0

    def test_a_real_volume_is_kept(self, monkeypatch):
        _, send = overseas_send([overseas_daily_payload(("20260821",), volumes=("7421658300",))])
        monkeypatch.setattr(kis_overseas_index_daily, "send_get", send)

        fetch = overseas_collector().fetch(OverseasIndex.SP500, SPAN_START, SPAN_END, sleep=0)

        assert fetch.bars[0].volume == 7_421_658_300

    def test_a_truncated_page_moves_the_window_back(self, monkeypatch):
        """실측: 행 상한 100에 `tr_cont` 헤더가 비어 온다. 창 걷기가 유일한 페이지 수단이다."""
        span_start = date(2026, 8, 18)
        requests, send = overseas_send(
            [
                overseas_daily_payload(("20260821", "20260820")),
                overseas_daily_payload(("20260819", "20260818")),
            ]
        )
        monkeypatch.setattr(kis_overseas_index_daily, "send_get", send)

        fetch = overseas_collector().fetch(OverseasIndex.SP500, span_start, SPAN_END, sleep=0)

        assert [request[2]["FID_INPUT_DATE_2"] for request in requests] == ["20260821", "20260819"]
        assert [request[2]["FID_INPUT_DATE_1"] for request in requests] == ["20260818", "20260818"]
        assert len(fetch.bars) == 4
        assert fetch.page_count == 2

    def test_a_continuation_header_is_not_followed(self, monkeypatch):
        """공식 예제는 연속조회를 구현하지만 실제 헤더는 늘 비어 있었다. 분기를 두지 않는다."""
        requests: list[tuple[str, str, dict, str]] = []

        def send(token, app_key, app_secret, path, tr_id, query, tr_cont=""):
            requests.append((path, tr_id, dict(query), tr_cont))
            return overseas_daily_payload(("20260821", "20260820")), 200, {"tr_cont": "M"}

        monkeypatch.setattr(kis_overseas_index_daily, "send_get", send)

        overseas_collector().fetch(OverseasIndex.SP500, SPAN_START, SPAN_END, sleep=0)

        assert [request[3] for request in requests] == [""]

    def test_the_echoed_code_is_checked_when_it_is_there(self, monkeypatch):
        _, send = overseas_send([overseas_daily_payload(("20260821",), echoed_code="COMP")])
        monkeypatch.setattr(kis_overseas_index_daily, "send_get", send)

        with pytest.raises(KisPayloadError, match="COMP"):
            overseas_collector().fetch(OverseasIndex.SP500, SPAN_START, SPAN_END, sleep=0)

    def test_a_missing_echoed_code_is_allowed(self, monkeypatch):
        """`.DJI`에는 `stck_shrn_iscd` 칸이 아예 없었다(6.3절 실측)."""
        _, send = overseas_send([overseas_daily_payload(("20260821",), echoed_code=None)])
        monkeypatch.setattr(kis_overseas_index_daily, "send_get", send)

        fetch = overseas_collector().fetch(OverseasIndex.SP500, SPAN_START, SPAN_END, sleep=0)

        assert len(fetch.bars) == 1

    def test_a_body_that_is_not_json_fails(self, monkeypatch):
        _, send = overseas_send([b"<html>maintenance</html>"])
        monkeypatch.setattr(kis_overseas_index_daily, "send_get", send)

        with pytest.raises(KisPayloadError, match="non-JSON"):
            overseas_collector().fetch(OverseasIndex.SP500, SPAN_START, SPAN_END, sleep=0)

    def test_a_result_code_that_is_not_zero_fails(self, monkeypatch):
        _, send = overseas_send([overseas_daily_payload(("20260821",), rt_cd="1")])
        monkeypatch.setattr(kis_overseas_index_daily, "send_get", send)

        with pytest.raises(KisResultError):
            overseas_collector().fetch(OverseasIndex.SP500, SPAN_START, SPAN_END, sleep=0)

    def test_a_response_without_the_output_list_fails(self, monkeypatch):
        _, send = overseas_send([overseas_daily_payload(("20260821",), output2={"not": "a list"})])
        monkeypatch.setattr(kis_overseas_index_daily, "send_get", send)

        with pytest.raises(KisPayloadError, match="output2"):
            overseas_collector().fetch(OverseasIndex.SP500, SPAN_START, SPAN_END, sleep=0)

    def test_a_date_outside_the_span_fails(self, monkeypatch):
        _, send = overseas_send([overseas_daily_payload(("20260825",))])
        monkeypatch.setattr(kis_overseas_index_daily, "send_get", send)

        with pytest.raises(KisPayloadError, match="outside"):
            overseas_collector().fetch(OverseasIndex.SP500, SPAN_START, SPAN_END, sleep=0)

    def test_a_repeated_date_fails(self, monkeypatch):
        _, send = overseas_send([overseas_daily_payload(("20260821", "20260821"))])
        monkeypatch.setattr(kis_overseas_index_daily, "send_get", send)

        with pytest.raises(KisPayloadError, match="duplicate"):
            overseas_collector().fetch(OverseasIndex.SP500, SPAN_START, SPAN_END, sleep=0)

    def test_a_malformed_date_fails(self, monkeypatch):
        _, send = overseas_send([overseas_daily_payload(("2026-08-21",))])
        monkeypatch.setattr(kis_overseas_index_daily, "send_get", send)

        with pytest.raises(KisPayloadError, match="malformed"):
            overseas_collector().fetch(OverseasIndex.SP500, SPAN_START, SPAN_END, sleep=0)

    def test_a_non_positive_price_fails(self, monkeypatch):
        _, send = overseas_send([overseas_daily_payload(("20260821",), closes=("  0.00",))])
        monkeypatch.setattr(kis_overseas_index_daily, "send_get", send)

        with pytest.raises(KisPayloadError, match="malformed"):
            overseas_collector().fetch(OverseasIndex.SP500, SPAN_START, SPAN_END, sleep=0)

    def test_a_high_below_the_close_fails(self, monkeypatch):
        _, send = overseas_send([overseas_daily_payload(("20260821",), highs=("  1.00",))])
        monkeypatch.setattr(kis_overseas_index_daily, "send_get", send)

        with pytest.raises(KisPayloadError, match="malformed"):
            overseas_collector().fetch(OverseasIndex.SP500, SPAN_START, SPAN_END, sleep=0)

    def test_a_negative_volume_fails(self, monkeypatch):
        _, send = overseas_send([overseas_daily_payload(("20260821",), volumes=("-1",))])
        monkeypatch.setattr(kis_overseas_index_daily, "send_get", send)

        with pytest.raises(KisPayloadError, match="malformed"):
            overseas_collector().fetch(OverseasIndex.SP500, SPAN_START, SPAN_END, sleep=0)

    def test_an_empty_chart_fails(self, monkeypatch):
        """실측: KIS는 모르는 코드에도 `rt_cd=0`·0건으로 답한다. 조용한 성공을 만들지 않는다."""
        _, send = overseas_send([overseas_daily_payload(())])
        monkeypatch.setattr(kis_overseas_index_daily, "send_get", send)

        with pytest.raises(KisPayloadError, match="no daily bars"):
            overseas_collector().fetch(OverseasIndex.SP500, SPAN_START, SPAN_END, sleep=0)

    def test_a_span_that_never_reaches_its_start_fails(self, monkeypatch):
        """열 장을 받고도 구간의 시작에 못 닿으면 부분 저장 대신 실패한다."""
        pages = [overseas_daily_payload((f"202608{21 - page:02d}",)) for page in range(OVERSEAS_DAILY_MAX_PAGES)]
        _, send = overseas_send(pages)
        monkeypatch.setattr(kis_overseas_index_daily, "send_get", send)

        with pytest.raises(KisPayloadError, match="after"):
            overseas_collector().fetch(OverseasIndex.SP500, date(2026, 1, 5), SPAN_END, sleep=0)


class TestStoreOverseasDaily:
    def test_the_upsert_matches_the_model_and_its_natural_key(self):
        table = IndexDaily.__table__
        statement = kis_overseas_index_daily.INDEX_DAILY_UPSERT
        columns = inserted_columns(statement)

        assert set(columns) <= {column.name for column in table.columns}
        assert required_columns(table) <= set(columns)
        assert placeholder_count(statement) == len(columns)
        assert "ON CONFLICT (provider, symbol, business_date) DO UPDATE" in statement

    def test_store_writes_lineage_and_bars(self, monkeypatch):
        _, send = overseas_send([overseas_daily_payload(("20260821", "20260820"), echoed_code="COMP")])
        monkeypatch.setattr(kis_overseas_index_daily, "send_get", send)
        fetch = overseas_collector().fetch(OverseasIndex.NASDAQ, SPAN_START, SPAN_END, sleep=0)
        connection = FakeConnection()

        stored = overseas_collector().store(connection, fetch)

        assert stored == 2
        statement, parameters = connection.recorded_cursor.calls[0]
        assert statement == SOURCE_RECORD_INSERT
        assert parameters[0:3] == ("api", "kis", "inquire_daily_chartprice")
        assert parameters[5] == "succeeded"
        assert parameters[6] == 2
        assert parameters[7] is None
        metadata = json.loads(parameters[8])
        # 저장 심볼과 KIS 코드가 다르다. 둘 다 남겨야 어떤 요청이었는지 재현된다.
        assert metadata["symbol"] == "NASDAQ"
        assert metadata["kis_code"] == "COMP"
        assert metadata["bar_count"] == 2

    def test_store_writes_rows_in_the_upsert_column_order(self, monkeypatch):
        _, send = overseas_send([overseas_daily_payload(("20260821",))])
        monkeypatch.setattr(kis_overseas_index_daily, "send_get", send)
        fetch = overseas_collector().fetch(OverseasIndex.SP500, SPAN_START, SPAN_END, sleep=0)
        connection = FakeConnection()

        overseas_collector().store(connection, fetch)

        rows = [
            parameters
            for statement, parameters in connection.recorded_cursor.calls
            if "index_daily (" in statement
        ]
        assert len(rows) == 1
        provider, symbol, business_date, opened, high, low, close, volume, source_record_id = rows[0]
        assert (provider, symbol) == ("kis", "SP500")
        assert business_date == date(2026, 8, 21)
        assert (opened, high, low, close) == (
            Decimal("7666.88"),
            Decimal("7690.73"),
            Decimal("7657.41"),
            Decimal("7675.70"),
        )
        assert volume == 0
        assert source_record_id == SOURCE_RECORD_ID
