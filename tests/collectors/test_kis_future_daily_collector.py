"""KIS 국내 지수선물 확정 일봉 수집기.

현물 지수 일봉(`test_kis_index_daily_collector.py`)과 나눠 둔다. 응답 칸 이름이 다르고,
무엇보다 **조회 창이 월물 경계에서 갈린다.** 그 갈림이 이 파일의 절반이다.

API 계약은 2026-08-27 운영 앱키 실측이다(`docs/collection/kis-index-daily-collection.md` 4.4절).
"""

import json
import re
from datetime import date, timedelta
from decimal import Decimal
from itertools import pairwise
from typing import Self

import pytest
from pydantic import SecretStr
from sqlalchemy import Table

from apps.models.market import IndexFutureDaily
from modules.collectors.kis import SOURCE_RECORD_INSERT, DomesticFuture, KisPayloadError, KisResultError
from modules.collectors.market import kis_future_daily
from modules.collectors.market.kis_future_daily import (
    FUTURE_DAILY_MAX_PAGES,
    KisFutureDailyCollector,
    contract_code,
    contract_windows,
)

SOURCE_RECORD_ID = 1

# 2026년 9월물 만기는 9월 10일(둘째 목요일)이다. 실측에서 `A01606`의 마지막 봉이
# 만기일 20260611이었던 것과 같은 규칙이다.
SEPTEMBER_EXPIRY = date(2026, 9, 10)


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


def future_payload(
    dates: tuple[str, ...],
    rt_cd: str = "0",
    echoed_code: str | None = "A01609",
    opens: tuple[str, ...] | None = None,
    highs: tuple[str, ...] | None = None,
    lows: tuple[str, ...] | None = None,
    closes: tuple[str, ...] | None = None,
    volumes: tuple[str, ...] | None = None,
    output2: object | None = None,
) -> bytes:
    """공식 `inquire_daily_fuopchartprice` 응답 모양. KIS는 최신순으로 준다.

    `echoed_code=None`이면 `output1`이 빈 dict다. **만기물의 실제 모양이다**(4.4절 실측).
    """
    opens = opens if opens is not None else tuple(f"  {1100 + i}.35" for i in range(len(dates)))
    highs = highs if highs is not None else tuple(f"  {1109 + i}.40" for i in range(len(dates)))
    lows = lows if lows is not None else tuple(f"  {1078 + i}.20" for i in range(len(dates)))
    closes = closes if closes is not None else tuple(f"  {1089 + i}.85" for i in range(len(dates)))
    volumes = volumes if volumes is not None else tuple(str(109_502 + i) for i in range(len(dates)))
    rows = [
        {
            "stck_bsop_date": day,
            "futs_oprc": opens[i],
            "futs_hgpr": highs[i],
            "futs_lwpr": lows[i],
            "futs_prpr": closes[i],
            "acml_vol": volumes[i],
            "acml_tr_pbmn": "29886993200",
            "mod_yn": "N",
        }
        for i, day in enumerate(dates)
    ]
    return json.dumps(
        {
            "rt_cd": rt_cd,
            "msg_cd": "MCA00000",
            "msg1": "정상처리 되었습니다.",
            "output1": {} if echoed_code is None else {"futs_shrn_iscd": echoed_code, "hts_kor_isnm": "F 202609"},
            "output2": rows if output2 is None else output2,
        }
    ).encode("utf-8")


def future_send(pages: list[tuple[bytes, str]]):
    requests: list[tuple[str, str, dict, str]] = []

    def send(token, app_key, app_secret, path, tr_id, query, tr_cont=""):
        requests.append((path, tr_id, dict(query), tr_cont))
        if len(requests) > len(pages):
            # 대본을 다 쓰면 빈 응답이다. 같은 장을 되풀이하면 걷기가 중복 날짜 오류로 끝나
            # 실제 종료 조건을 가린다. 빈 응답에는 코드 칸도 없다 — 실측의 만기물·`.DJI`가 그렇다.
            return future_payload((), echoed_code=None), 200, {"tr_cont": ""}
        body, next_flag = pages[len(requests) - 1]
        return body, 200, {"tr_cont": next_flag}

    return requests, send


def future_collector() -> KisFutureDailyCollector:
    return KisFutureDailyCollector(SecretStr("token"), SecretStr("key"), SecretStr("secret"))


class TestContractWindows:
    def test_the_code_follows_the_intraday_format(self):
        """실측: 일봉 API가 분봉과 같은 `A0` 코드를 받는다. 공식 예제의 `101W09`는 0행이다."""
        assert contract_code(DomesticFuture.KOSPI200_FUT, 2026, 9) == "A01609"
        assert contract_code(DomesticFuture.KOSPI200_FUT, 2026, 6) == "A01606"
        assert contract_code(DomesticFuture.KOSDAQ150_FUT, 2026, 9) == "A06609"

    def test_a_span_inside_one_contract_is_one_window(self):
        windows = contract_windows(DomesticFuture.KOSPI200_FUT, date(2026, 9, 1), date(2026, 9, 5))

        assert [(w.contract_code, w.start_date, w.end_date) for w in windows] == [
            ("A01609", date(2026, 9, 1), date(2026, 9, 5)),
        ]

    def test_the_roll_keeps_the_expiry_day_and_switches_the_next_day(self):
        """만기일 봉은 만기월에 귀속한다. 실측에서 `A01606`의 마지막 행이 만기일이었다."""
        windows = contract_windows(DomesticFuture.KOSPI200_FUT, SEPTEMBER_EXPIRY, date(2026, 9, 11))

        assert [(w.contract_code, w.start_date, w.end_date) for w in windows] == [
            ("A01609", SEPTEMBER_EXPIRY, SEPTEMBER_EXPIRY),
            ("A01612", date(2026, 9, 11), date(2026, 9, 11)),
        ]

    def test_a_backfill_span_is_covered_without_gaps_or_overlaps(self):
        """12월물 만기(12/10)와 연말 사이는 이듬해 3월물이 받는다. 롤이 연을 넘는다."""
        windows = contract_windows(DomesticFuture.KOSPI200_FUT, date(2026, 1, 1), date(2026, 12, 31))

        assert [w.contract_code for w in windows] == ["A01603", "A01606", "A01609", "A01612", "A01703"]
        assert windows[0].start_date == date(2026, 1, 1)
        assert windows[-1].end_date == date(2026, 12, 31)
        for earlier, later in pairwise(windows):
            assert later.start_date == earlier.end_date + timedelta(days=1)

    def test_a_reversed_span_is_rejected(self):
        with pytest.raises(ValueError, match="must not be after"):
            contract_windows(DomesticFuture.KOSPI200_FUT, date(2026, 9, 11), SEPTEMBER_EXPIRY)


class TestFetchFutureDaily:
    def test_the_request_carries_the_official_contract(self, monkeypatch):
        requests, send = future_send([(future_payload(("20260904", "20260903")), "")])
        monkeypatch.setattr(kis_future_daily, "send_get", send)

        fetch = future_collector().fetch(DomesticFuture.KOSPI200_FUT, date(2026, 9, 3), date(2026, 9, 4), sleep=0)

        path, tr_id, query, tr_cont = requests[0]
        assert path == "/uapi/domestic-futureoption/v1/quotations/inquire-daily-fuopchartprice"
        assert tr_id == "FHKIF03020100"
        assert tr_cont == ""
        assert query == {
            "FID_COND_MRKT_DIV_CODE": "F",
            "FID_INPUT_ISCD": "A01609",
            "FID_INPUT_DATE_1": "20260903",
            "FID_INPUT_DATE_2": "20260904",
            "FID_PERIOD_DIV_CODE": "D",
        }
        assert fetch.symbol == "KOSPI200_FUT"
        assert fetch.contracts == ("A01609",)
        assert [bar.business_date for bar in fetch.bars] == [date(2026, 9, 3), date(2026, 9, 4)]
        assert {bar.contract_code for bar in fetch.bars} == {"A01609"}

    def test_kosdaq150_uses_its_own_product_digit(self, monkeypatch):
        requests, send = future_send([(future_payload(("20260904",), echoed_code="A06609"), "")])
        monkeypatch.setattr(kis_future_daily, "send_get", send)

        fetch = future_collector().fetch(DomesticFuture.KOSDAQ150_FUT, date(2026, 9, 4), date(2026, 9, 4), sleep=0)

        assert requests[0][2]["FID_INPUT_ISCD"] == "A06609"
        assert fetch.symbol == "KOSDAQ150_FUT"

    def test_a_span_crossing_expiry_asks_each_contract_for_its_own_days(self, monkeypatch):
        """이 하나가 `front_contract`로는 안 되는 이유다. 과거 창을 현재 월물로 물으면 빈다."""
        requests, send = future_send(
            [
                (future_payload(("20260910",)), ""),
                (future_payload(("20260911",), echoed_code="A01612"), ""),
            ]
        )
        monkeypatch.setattr(kis_future_daily, "send_get", send)

        fetch = future_collector().fetch(DomesticFuture.KOSPI200_FUT, SEPTEMBER_EXPIRY, date(2026, 9, 11), sleep=0)

        assert [request[2]["FID_INPUT_ISCD"] for request in requests] == ["A01609", "A01612"]
        assert fetch.contracts == ("A01609", "A01612")
        assert [(bar.business_date, bar.contract_code) for bar in fetch.bars] == [
            (SEPTEMBER_EXPIRY, "A01609"),
            (date(2026, 9, 11), "A01612"),
        ]

    def test_a_truncated_page_moves_the_window_back(self, monkeypatch):
        """실측: 행 상한 100에 `tr_cont` 헤더가 비어 온다. 창 걷기가 유일한 페이지 수단이다."""
        requests, send = future_send(
            [
                (future_payload(("20260904", "20260903")), ""),
                (future_payload(("20260902", "20260901")), ""),
            ]
        )
        monkeypatch.setattr(kis_future_daily, "send_get", send)

        fetch = future_collector().fetch(DomesticFuture.KOSPI200_FUT, date(2026, 9, 1), date(2026, 9, 4), sleep=0)

        assert [request[2]["FID_INPUT_DATE_2"] for request in requests] == ["20260904", "20260902"]
        assert [request[2]["FID_INPUT_DATE_1"] for request in requests] == ["20260901", "20260901"]
        assert len(fetch.bars) == 4
        assert fetch.page_count == 2

    def test_a_continuation_header_is_not_followed(self, monkeypatch):
        """헤더는 실측에서 늘 비어 있었다. 와도 창 걷기가 답을 낸다 — 분기를 따로 두지 않는다."""
        requests, send = future_send([(future_payload(("20260904", "20260903")), "M")])
        monkeypatch.setattr(kis_future_daily, "send_get", send)

        future_collector().fetch(DomesticFuture.KOSPI200_FUT, date(2026, 9, 3), date(2026, 9, 4), sleep=0)

        assert [request[3] for request in requests] == [""]

    def test_the_echoed_contract_is_checked_when_it_is_there(self, monkeypatch):
        _, send = future_send([(future_payload(("20260904",), echoed_code="A01612"), "")])
        monkeypatch.setattr(kis_future_daily, "send_get", send)

        with pytest.raises(KisPayloadError, match="A01612"):
            future_collector().fetch(DomesticFuture.KOSPI200_FUT, date(2026, 9, 4), date(2026, 9, 4), sleep=0)

    def test_a_missing_echoed_contract_is_allowed(self, monkeypatch):
        """만기물의 `output1`은 빈 dict다(4.4절 실측). 필수로 만들면 백필이 통째로 실패한다."""
        _, send = future_send([(future_payload(("20260904",), echoed_code=None), "")])
        monkeypatch.setattr(kis_future_daily, "send_get", send)

        fetch = future_collector().fetch(DomesticFuture.KOSPI200_FUT, date(2026, 9, 4), date(2026, 9, 4), sleep=0)

        assert [bar.contract_code for bar in fetch.bars] == ["A01609"]

    def test_a_body_that_is_not_json_fails(self, monkeypatch):
        _, send = future_send([(b"<html>maintenance</html>", "")])
        monkeypatch.setattr(kis_future_daily, "send_get", send)

        with pytest.raises(KisPayloadError, match="non-JSON"):
            future_collector().fetch(DomesticFuture.KOSPI200_FUT, date(2026, 9, 4), date(2026, 9, 4), sleep=0)

    def test_a_result_code_that_is_not_zero_fails(self, monkeypatch):
        _, send = future_send([(future_payload(("20260904",), rt_cd="1"), "")])
        monkeypatch.setattr(kis_future_daily, "send_get", send)

        with pytest.raises(KisResultError):
            future_collector().fetch(DomesticFuture.KOSPI200_FUT, date(2026, 9, 4), date(2026, 9, 4), sleep=0)

    def test_a_response_without_the_output_list_fails(self, monkeypatch):
        _, send = future_send([(future_payload(("20260904",), output2={"not": "a list"}), "")])
        monkeypatch.setattr(kis_future_daily, "send_get", send)

        with pytest.raises(KisPayloadError, match="output2"):
            future_collector().fetch(DomesticFuture.KOSPI200_FUT, date(2026, 9, 4), date(2026, 9, 4), sleep=0)

    def test_a_date_outside_the_window_fails(self, monkeypatch):
        _, send = future_send([(future_payload(("20260915",)), "")])
        monkeypatch.setattr(kis_future_daily, "send_get", send)

        with pytest.raises(KisPayloadError, match="outside"):
            future_collector().fetch(DomesticFuture.KOSPI200_FUT, date(2026, 9, 3), date(2026, 9, 4), sleep=0)

    def test_a_repeated_date_fails(self, monkeypatch):
        _, send = future_send([(future_payload(("20260904", "20260904")), "")])
        monkeypatch.setattr(kis_future_daily, "send_get", send)

        with pytest.raises(KisPayloadError, match="duplicate"):
            future_collector().fetch(DomesticFuture.KOSPI200_FUT, date(2026, 9, 3), date(2026, 9, 4), sleep=0)

    def test_a_malformed_date_fails(self, monkeypatch):
        _, send = future_send([(future_payload(("2026-09-04",)), "")])
        monkeypatch.setattr(kis_future_daily, "send_get", send)

        with pytest.raises(KisPayloadError, match="malformed"):
            future_collector().fetch(DomesticFuture.KOSPI200_FUT, date(2026, 9, 4), date(2026, 9, 4), sleep=0)

    def test_a_non_positive_price_fails(self, monkeypatch):
        _, send = future_send([(future_payload(("20260904",), closes=("  0.00",)), "")])
        monkeypatch.setattr(kis_future_daily, "send_get", send)

        with pytest.raises(KisPayloadError, match="malformed"):
            future_collector().fetch(DomesticFuture.KOSPI200_FUT, date(2026, 9, 4), date(2026, 9, 4), sleep=0)

    def test_a_high_below_the_close_fails(self, monkeypatch):
        _, send = future_send([(future_payload(("20260904",), highs=("  1.00",)), "")])
        monkeypatch.setattr(kis_future_daily, "send_get", send)

        with pytest.raises(KisPayloadError, match="malformed"):
            future_collector().fetch(DomesticFuture.KOSPI200_FUT, date(2026, 9, 4), date(2026, 9, 4), sleep=0)

    def test_a_negative_volume_fails(self, monkeypatch):
        _, send = future_send([(future_payload(("20260904",), volumes=("-1",)), "")])
        monkeypatch.setattr(kis_future_daily, "send_get", send)

        with pytest.raises(KisPayloadError, match="malformed"):
            future_collector().fetch(DomesticFuture.KOSPI200_FUT, date(2026, 9, 4), date(2026, 9, 4), sleep=0)

    def test_a_span_with_no_bars_at_all_fails(self, monkeypatch):
        _, send = future_send([(future_payload(()), "")])
        monkeypatch.setattr(kis_future_daily, "send_get", send)

        with pytest.raises(KisPayloadError, match="no daily bars"):
            future_collector().fetch(DomesticFuture.KOSPI200_FUT, date(2026, 9, 3), date(2026, 9, 4), sleep=0)

    def test_a_window_that_never_reaches_its_start_fails(self, monkeypatch):
        """열 장을 받고도 창의 시작에 못 닿으면 부분 저장 대신 실패한다."""
        # 이 구간은 3월물(A01603) 하나 안이다. 창이 갈리면 장 수 상한을 재는 것이 아니게 된다.
        pages = [
            (future_payload((f"202603{10 - page:02d}",), echoed_code="A01603"), "")
            for page in range(FUTURE_DAILY_MAX_PAGES)
        ]
        _, send = future_send(pages)
        monkeypatch.setattr(kis_future_daily, "send_get", send)

        with pytest.raises(KisPayloadError, match="after"):
            future_collector().fetch(DomesticFuture.KOSPI200_FUT, date(2026, 1, 5), date(2026, 3, 10), sleep=0)


class TestStoreFutureDaily:
    def test_the_upsert_matches_the_model_and_its_natural_key(self):
        table = IndexFutureDaily.__table__
        statement = kis_future_daily.INDEX_FUTURE_DAILY_UPSERT
        columns = inserted_columns(statement)

        assert set(columns) <= {column.name for column in table.columns}
        assert required_columns(table) <= set(columns)
        assert "contract_code" in columns
        assert placeholder_count(statement) == len(columns)
        assert "ON CONFLICT (provider, symbol, business_date) DO UPDATE" in statement

    def test_store_writes_lineage_and_bars(self, monkeypatch):
        _, send = future_send([(future_payload(("20260904", "20260903")), "")])
        monkeypatch.setattr(kis_future_daily, "send_get", send)
        fetch = future_collector().fetch(DomesticFuture.KOSPI200_FUT, date(2026, 9, 3), date(2026, 9, 4), sleep=0)
        connection = FakeConnection()

        stored = future_collector().store(connection, fetch)

        assert stored == 2
        statement, parameters = connection.recorded_cursor.calls[0]
        assert statement == SOURCE_RECORD_INSERT
        assert parameters[0:3] == ("api", "kis", "inquire_daily_fuopchartprice")
        assert parameters[5] == "succeeded"
        assert parameters[6] == 2
        # 원본은 남기지 않는다. 어느 구간을 어느 월물로 몇 장에 받았는지면 재현에 충분하다.
        assert parameters[7] is None
        metadata = json.loads(parameters[8])
        assert metadata["symbol"] == "KOSPI200_FUT"
        assert metadata["contracts"] == ["A01609"]
        assert metadata["bar_count"] == 2

    def test_store_writes_rows_in_the_upsert_column_order(self, monkeypatch):
        _, send = future_send([(future_payload(("20260904",)), "")])
        monkeypatch.setattr(kis_future_daily, "send_get", send)
        fetch = future_collector().fetch(DomesticFuture.KOSPI200_FUT, date(2026, 9, 4), date(2026, 9, 4), sleep=0)
        connection = FakeConnection()

        future_collector().store(connection, fetch)

        rows = [
            parameters
            for statement, parameters in connection.recorded_cursor.calls
            if "index_future_daily (" in statement
        ]
        assert len(rows) == 1
        provider, symbol, business_date, opened, high, low, close, volume, code, source_record_id = rows[0]
        assert (provider, symbol) == ("kis", "KOSPI200_FUT")
        assert business_date == date(2026, 9, 4)
        assert (opened, high, low, close) == (
            Decimal("1100.35"),
            Decimal("1109.40"),
            Decimal("1078.20"),
            Decimal("1089.85"),
        )
        assert volume == 109_502
        assert code == "A01609"
        assert source_record_id == SOURCE_RECORD_ID
