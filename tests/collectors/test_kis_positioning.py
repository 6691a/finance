import json
import re
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Self

import pytest
from pydantic import SecretStr
from sqlalchemy import Table

from apps.models.market import (
    KrxCreditBalanceRankingDaily,
    KrxMarketFundsDaily,
    KrxMarketSecuritiesLendingDaily,
    KrxStockCreditBalanceDaily,
    KrxStockSecuritiesLendingDaily,
    KrxStockShortSaleDaily,
)
from apps.models.raw import SourceRecord
from modules.collectors.kis import KisPayloadError, KisResultError
from modules.collectors.market import kis_positioning
from modules.collectors.market.kis_positioning import (
    CREDIT_BALANCE_UPSERT,
    CREDIT_RANKING_DELETE_STALE,
    CREDIT_RANKING_UPSERT,
    LENDING_STOCK_DIVISION,
    LENDING_UPSERT,
    MARKET_FUNDS_UPSERT,
    MARKET_LENDING_UPSERT,
    RANKING_UNIVERSES,
    SHORT_SALE_UPSERT,
    SOURCE_RECORD_INSERT,
    KisPositioningCollector,
    LendingMarket,
    PositioningStock,
)

SOURCE_RECORD_ID = 31
APP_KEY = SecretStr("key")
APP_SECRET = SecretStr("secret")
TOKEN = SecretStr("token")
COLLECTOR = KisPositioningCollector(TOKEN, APP_KEY, APP_SECRET)
SAMSUNG = PositioningStock.SAMSUNG_ELECTRONICS

# 2026-08-13 실측 응답을 줄인 것이다. 필드 이름과 공백 패딩·소수점 표기는 그대로다.
CREDIT_ROW = {
    "deal_date": "20260810",
    "stlm_date": "20260812",
    "stck_prpr": "230000",
    "acml_vol": "16327805",
    "whol_loan_new_stcn": "2309763",
    "whol_loan_rdmp_stcn": "2154321",
    "whol_loan_rmnd_stcn": "24280842",
    "whol_loan_new_amt": "531245490000",
    "whol_loan_rdmp_amt": "495493830000",
    "whol_loan_rmnd_amt": "5584593660000",
    "whol_loan_rmnd_rate": "0.41",
    "whol_loan_gvrt": "14.15",
    "whol_stln_new_stcn": "1200",
    "whol_stln_rdmp_stcn": "980",
    "whol_stln_rmnd_stcn": "45120",
    "whol_stln_new_amt": "276000000",
    "whol_stln_rdmp_amt": "225400000",
    "whol_stln_rmnd_amt": "10377600000",
    "whol_stln_rmnd_rate": "0.00",
    "whol_stln_gvrt": "0.01",
}

RANKING_HEAD = {
    "bstp_cls_code": "1001",
    "hts_kor_isnm": "종합",
    "stnd_date1": "20260806",
    "stnd_date2": "20260812",
}

RANKING_ROW = {
    "mksc_shrn_iscd": "005930",
    "hts_kor_isnm": "삼성전자",
    "stck_prpr": "269000",
    "prdy_vrss": "13500",
    "prdy_vrss_sign": "2",
    "prdy_ctrt": "5.28",
    "acml_vol": "14881255",
    "whol_loan_rmnd_stcn": "24280842",
    "whol_loan_rmnd_amt": "506846396",
    "whol_loan_rmnd_rate": "0.41",
    "whol_stln_rmnd_stcn": "45120",
    "whol_stln_rmnd_amt": "10377",
    "whol_stln_rmnd_rate": "0.00",
    "nday_vrss_loan_rmnd_inrt": "3.21",
    "nday_vrss_stln_rmnd_inrt": "-1.04",
}

FUNDS_ROW = {
    "bsop_date": "20260811",
    "bstp_nmix_prpr": "6345.53",
    "bstp_nmix_prdy_vrss": "45.87",
    "prdy_vrss_sign": "2",
    # 실측에서 등락률과 맞지 않았던 값이다. 저장하지 않는다.
    "prdy_ctrt": "100.73",
    "hts_avls": "5239010332",
    "cust_dpmn_amt": "979289",
    "cust_dpmn_amt_prdy_vrss": "-27891",
    "amt_tnrt": "0.63",
    "uncl_amt": "12345",
    "crdt_loan_rmnd": "300387",
    "futs_tfam_amt": "88123",
    "sttp_amt": "231456",
    "mxtp_amt": "51234",
    "bntp_amt": "412345",
    "mmf_amt": "1523456",
    "secu_lend_amt": "246577",
}

SHORT_SALE_ROW = {
    "stck_bsop_date": "20260812",
    "stck_clpr": "255500",
    "acml_vol": "27102479",
    "ssts_cntg_qty": "512345",
    "ssts_vol_rlim": "1.89",
    "acml_ssts_cntg_qty": "899410247",
    "acml_ssts_cntg_qty_rlim": "2.11",
    "ssts_tr_pbmn": "130903647500",
    "ssts_tr_pbmn_rlim": "1.92",
    "acml_ssts_tr_pbmn": "229803647500",
    "acml_ssts_tr_pbmn_rlim": "2.20",
    "acml_tr_pbmn": "6824168124500",
    "avrg_prc": "255499",
}

LENDING_ROW = {
    "bsop_date": "20260812",
    # 실측에서 소수점이 붙어 온다.
    "stck_prpr": "255500.00",
    "prdy_vrss_sign": "2",
    "prdy_vrss": "16000.00",
    "prdy_ctrt": "6.68",
    "acml_vol": "27102479",
    "new_stcn": "799209",
    "rdmp_stcn": "650123",
    "prdy_rmnd_vrss": "149086",
    "rmnd_stcn": "88123456",
    "rmnd_amt": "22515542000",
}


def body(rt_cd: str = "0", msg1: str = "정상처리 되었습니다.", **outputs) -> bytes:
    payload = {"rt_cd": rt_cd, "msg_cd": "MCA00000", "msg1": msg1, **outputs}
    return json.dumps(payload, ensure_ascii=False).encode()


class FakeCursor:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple]] = []

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *args: object) -> bool:
        return False

    def execute(self, statement: str, parameters) -> None:
        self.calls.append((statement, tuple(parameters)))

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


def fake_send_get(payload: bytes):
    sent: list[dict] = []

    def send(token, app_key, app_secret, path, tr_id, query, tr_cont=""):
        sent.append({"path": path, "tr_id": tr_id, "query": dict(query)})
        return payload, 200, {}

    send.sent = sent  # type: ignore[attr-defined]
    return send


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


def rows_for(cursor: FakeCursor, table: str) -> list[tuple]:
    return [parameters for statement, parameters in cursor.calls if f"INSERT INTO {table}" in statement]


@pytest.mark.parametrize(
    ("statement", "model"),
    [
        (CREDIT_BALANCE_UPSERT, KrxStockCreditBalanceDaily),
        (CREDIT_RANKING_UPSERT, KrxCreditBalanceRankingDaily),
        (MARKET_FUNDS_UPSERT, KrxMarketFundsDaily),
        (SHORT_SALE_UPSERT, KrxStockShortSaleDaily),
        (LENDING_UPSERT, KrxStockSecuritiesLendingDaily),
        (MARKET_LENDING_UPSERT, KrxMarketSecuritiesLendingDaily),
        (SOURCE_RECORD_INSERT, SourceRecord),
    ],
)
def test_every_upsert_matches_its_model(statement, model):
    table = model.__table__
    columns = inserted_columns(statement)

    assert set(columns) <= {column.name for column in table.columns}
    assert required_columns(table) <= set(columns)
    if statement is not SOURCE_RECORD_INSERT:
        # provider 만 SQL 에 리터럴로 박혀 있다.
        assert placeholder_count(statement) == len(columns) - 1


def test_credit_balance_keeps_trade_date_and_settlement_date_apart(monkeypatch):
    monkeypatch.setattr(kis_positioning, "send_get", fake_send_get(body(output=[CREDIT_ROW])))

    fetch = COLLECTOR.fetch_credit_balance(SAMSUNG, date(2026, 8, 1), date(2026, 8, 12))

    row = fetch.rows[0]
    # 거래일이 자연키이고 결제일은 값이다. 둘을 바꿔 저장하면 추이가 이틀씩 밀린다.
    assert row.trade_date == date(2026, 8, 10)
    assert row.settlement_date == date(2026, 8, 12)
    assert row.loan_balance_quantity == 24280842
    assert row.loan_balance_amount == Decimal(5584593660000)


def test_credit_balance_requests_a_settlement_date_past_the_window(monkeypatch):
    send = fake_send_get(body(output=[CREDIT_ROW]))
    monkeypatch.setattr(kis_positioning, "send_get", send)

    # 과거 구간이라 오늘로 잘리지 않는다.
    COLLECTOR.fetch_credit_balance(SAMSUNG, date(2026, 1, 2), date(2026, 1, 31))

    # 입력이 결제일이라 거래일 구간 끝보다 뒤를 요청해야 그 거래일 행이 들어온다.
    assert send.sent[0]["query"]["FID_INPUT_DATE_1"] == "20260214"


def test_credit_balance_never_requests_a_future_settlement_date(monkeypatch):
    send = fake_send_get(body(output=[CREDIT_ROW]))
    monkeypatch.setattr(kis_positioning, "send_get", send)
    today = datetime.now(UTC).date()

    COLLECTOR.fetch_credit_balance(SAMSUNG, today - timedelta(days=7), today)

    # padding 을 더하면 미래가 된다. 그때는 오늘로 자른다.
    assert send.sent[0]["query"]["FID_INPUT_DATE_1"] == today.strftime("%Y%m%d")


def test_credit_balance_drops_rows_outside_the_trade_window(monkeypatch):
    older = CREDIT_ROW | {"deal_date": "20260701", "stlm_date": "20260703"}
    monkeypatch.setattr(kis_positioning, "send_get", fake_send_get(body(output=[CREDIT_ROW, older])))

    fetch = COLLECTOR.fetch_credit_balance(SAMSUNG, date(2026, 8, 1), date(2026, 8, 12))

    assert [row.trade_date for row in fetch.rows] == [date(2026, 8, 10)]
    assert fetch.metadata["returned"] == 2
    assert fetch.metadata["kept"] == 1


def test_ranking_reads_the_standard_date_from_the_later_field(monkeypatch):
    monkeypatch.setattr(kis_positioning, "send_get", fake_send_get(body(output1=[RANKING_HEAD], output2=[RANKING_ROW])))

    fetch = COLLECTOR.fetch_credit_ranking()

    # stnd_date2 가 기준일이고 stnd_date1 이 비교일이다. 초판 문서가 반대로 적었다.
    assert fetch.metadata["standard_date"] == "2026-08-12"
    assert fetch.metadata["comparison_date"] == "2026-08-06"


def test_ranking_numbers_rows_from_the_array_order(monkeypatch):
    second = RANKING_ROW | {"mksc_shrn_iscd": "000660", "hts_kor_isnm": "SK하이닉스"}
    monkeypatch.setattr(
        kis_positioning, "send_get", fake_send_get(body(output1=[RANKING_HEAD], output2=[RANKING_ROW, second]))
    )

    fetch = COLLECTOR.fetch_credit_ranking()

    # 응답에 순번 필드가 없다(실측). 건수를 상수로 박지도 않는다.
    assert [row.rank for row in fetch.rows] == [1, 2]
    assert [row.stock_code for row in fetch.rows] == ["005930", "000660"]
    assert fetch.rows[0].loan_balance_growth_rate == Decimal("3.21")
    assert fetch.rows[0].short_loan_balance_growth_rate == Decimal("-1.04")


def test_ranking_accepts_codes_with_letters(monkeypatch):
    """단축코드가 숫자만은 아니다. 실측에서 `0126Z0`이 왔다(신주인수권증서·ETN 등)."""
    warrant = RANKING_ROW | {"mksc_shrn_iscd": "0126Z0", "hts_kor_isnm": "어떤 증서"}
    monkeypatch.setattr(kis_positioning, "send_get", fake_send_get(body(output1=[RANKING_HEAD], output2=[warrant])))

    fetch = COLLECTOR.fetch_credit_ranking()

    assert fetch.rows[0].stock_code == "0126Z0"


@pytest.mark.parametrize("code", ["12345", "1234567", "12-456"])
def test_ranking_rejects_malformed_codes(monkeypatch, code):
    bad = RANKING_ROW | {"mksc_shrn_iscd": code}
    monkeypatch.setattr(kis_positioning, "send_get", fake_send_get(body(output1=[RANKING_HEAD], output2=[bad])))

    with pytest.raises(KisPayloadError, match="malformed stock code"):
        COLLECTOR.fetch_credit_ranking()


def test_ranking_rejects_an_empty_snapshot(monkeypatch):
    # 늘 최신 완전 스냅샷을 주는 API다. 빈 배열은 휴장이 아니라 고장이다.
    monkeypatch.setattr(kis_positioning, "send_get", fake_send_get(body(output1=[RANKING_HEAD], output2=[])))

    with pytest.raises(KisPayloadError, match="no rows"):
        COLLECTOR.fetch_credit_ranking()


def test_ranking_rejects_duplicated_stock_codes(monkeypatch):
    monkeypatch.setattr(
        kis_positioning,
        "send_get",
        fake_send_get(body(output1=[RANKING_HEAD], output2=[RANKING_ROW, dict(RANKING_ROW)])),
    )

    with pytest.raises(KisPayloadError, match="duplicated"):
        COLLECTOR.fetch_credit_ranking()


def test_ranking_rejects_unordered_dates(monkeypatch):
    swapped = RANKING_HEAD | {"stnd_date1": "20260812", "stnd_date2": "20260806"}
    monkeypatch.setattr(kis_positioning, "send_get", fake_send_get(body(output1=[swapped], output2=[RANKING_ROW])))

    with pytest.raises(KisPayloadError, match="not ordered"):
        COLLECTOR.fetch_credit_ranking()


def test_market_funds_uses_the_response_date_and_skips_the_odd_change_rate(monkeypatch):
    monkeypatch.setattr(kis_positioning, "send_get", fake_send_get(body(output=[FUNDS_ROW])))

    fetch = COLLECTOR.fetch_market_funds(date(2026, 8, 12))

    row = fetch.rows[0]
    # 요청일이 아니라 응답의 bsop_date 로 저장한다.
    assert row.business_date == date(2026, 8, 11)
    assert row.customer_deposit_change == Decimal(-27891)
    # prdy_ctrt(100.73)은 등락률이 아니라 읽지 않는다.
    assert "prdy_ctrt" not in row.model_dump()


def test_short_sale_reads_output2_only(monkeypatch):
    quote = {"stck_prpr": "269500", "acml_vol": "14908315"}
    monkeypatch.setattr(kis_positioning, "send_get", fake_send_get(body(output1=quote, output2=[SHORT_SALE_ROW])))

    fetch = COLLECTOR.fetch_short_sale(SAMSUNG, date(2026, 8, 1), date(2026, 8, 12))

    row = fetch.rows[0]
    assert row.business_date == date(2026, 8, 12)
    assert row.short_sale_quantity == 512345
    assert row.short_sale_volume_ratio == Decimal("1.89")
    assert row.short_sale_average_price == Decimal(255499)


def test_lending_asks_for_the_stock_division_not_the_market(monkeypatch):
    send = fake_send_get(body(output1=[LENDING_ROW]))
    monkeypatch.setattr(kis_positioning, "send_get", send)

    fetch = COLLECTOR.fetch_lending(SAMSUNG, date(2026, 8, 1), date(2026, 8, 12))

    # 1을 보내면 코스피 전체 숫자가 종목 행에 들어간다(실측).
    assert LENDING_STOCK_DIVISION == "3"
    assert send.sent[0]["query"]["MRKT_DIV_CLS_CODE"] == "3"
    assert send.sent[0]["query"]["MKSC_SHRN_ISCD"] == "005930"
    # 소수점이 붙어 오는 수량 필드를 정수로 읽는다.
    assert fetch.rows[0].close_price == Decimal("255500.00")
    assert fetch.rows[0].balance_quantity == 88123456


@pytest.mark.parametrize(
    ("fetcher", "payload"),
    [
        (COLLECTOR.fetch_short_sale, body(output2=[SHORT_SALE_ROW | {"stck_bsop_date": "20261231"}])),
        (COLLECTOR.fetch_lending, body(output1=[LENDING_ROW | {"bsop_date": "20261231"}])),
    ],
)
def test_rows_after_the_requested_end_are_rejected(monkeypatch, fetcher, payload):
    monkeypatch.setattr(kis_positioning, "send_get", fake_send_get(payload))

    with pytest.raises(KisPayloadError, match="returned rows after"):
        fetcher(SAMSUNG, date(2026, 8, 1), date(2026, 8, 12))


def test_result_code_failures_are_raised(monkeypatch):
    monkeypatch.setattr(kis_positioning, "send_get", fake_send_get(body(rt_cd="1", msg1="권한이 없습니다")))

    with pytest.raises(KisResultError) as error:
        COLLECTOR.fetch_market_funds(date(2026, 8, 12))

    assert error.value.code == "1"


def test_bad_numbers_fail_instead_of_being_stored(monkeypatch):
    monkeypatch.setattr(kis_positioning, "send_get", fake_send_get(body(output=[CREDIT_ROW | {"acml_vol": "abc"}])))

    with pytest.raises(KisPayloadError, match="acml_vol"):
        COLLECTOR.fetch_credit_balance(SAMSUNG, date(2026, 8, 1), date(2026, 8, 12))


def test_store_credit_balance_writes_the_row_and_its_lineage(monkeypatch):
    monkeypatch.setattr(kis_positioning, "send_get", fake_send_get(body(output=[CREDIT_ROW])))
    fetch = COLLECTOR.fetch_credit_balance(SAMSUNG, date(2026, 8, 1), date(2026, 8, 12))
    connection = FakeConnection()

    assert COLLECTOR.store_credit_balance(connection, fetch) == 1
    statement, parameters = connection.recorded_cursor.calls[0]
    assert "INSERT INTO source_record" in statement
    assert parameters[1:3] == ("kis", "daily_credit_balance")
    # API 키가 질의 문자열에 있으므로 원본 요청을 남기지 않는다.
    assert parameters[7] is None

    upsert = rows_for(connection.recorded_cursor, "krx_stock_credit_balance_daily")[0]
    assert upsert[0] == "005930"
    assert upsert[1] == date(2026, 8, 10)
    assert upsert[-1] == SOURCE_RECORD_ID


def test_store_ranking_removes_slots_past_the_last_rank(monkeypatch):
    monkeypatch.setattr(
        kis_positioning,
        "send_get",
        fake_send_get(
            body(
                output1=[RANKING_HEAD],
                output2=[RANKING_ROW, RANKING_ROW | {"mksc_shrn_iscd": "000660"}],
            )
        ),
    )
    fetch = COLLECTOR.fetch_credit_ranking()
    connection = FakeConnection()

    assert COLLECTOR.store_credit_ranking(connection, fetch) == 2
    deletes = [
        parameters
        for statement, parameters in connection.recorded_cursor.calls
        if "DELETE FROM krx_credit_balance_ranking_daily" in statement
    ]
    # 응답이 짧아졌을 때 탈락 종목이 유령 행으로 남지 않게 한다.
    assert deletes == [(date(2026, 8, 12), "0000", "2", 5, 2)]
    assert "rank > %s" in CREDIT_RANKING_DELETE_STALE


@pytest.mark.parametrize(
    ("dataset", "payload", "table", "count"),
    [
        ("market_funds", body(output=[FUNDS_ROW]), "krx_market_funds_daily", 1),
        ("short_sale", body(output2=[SHORT_SALE_ROW]), "krx_stock_short_sale_daily", 1),
        ("lending", body(output1=[LENDING_ROW]), "krx_stock_securities_lending_daily", 1),
    ],
)
def test_each_dataset_stores_one_row_per_business_day(monkeypatch, dataset, payload, table, count):
    monkeypatch.setattr(kis_positioning, "send_get", fake_send_get(payload))
    fetcher = getattr(COLLECTOR, f"fetch_{dataset}")
    if dataset == "market_funds":
        fetch = fetcher(date(2026, 8, 12))
    else:
        fetch = fetcher(SAMSUNG, date(2026, 8, 1), date(2026, 8, 12))
    connection = FakeConnection()

    assert getattr(COLLECTOR, f"store_{dataset}")(connection, fetch) == count
    assert len(rows_for(connection.recorded_cursor, table)) == count


def test_empty_daily_responses_leave_only_a_lineage_record(monkeypatch):
    """휴장일이나 미발표일의 0건은 실패가 아니다. 조회했다는 사실만 남긴다."""
    monkeypatch.setattr(kis_positioning, "send_get", fake_send_get(body(output2=[])))
    fetch = COLLECTOR.fetch_short_sale(SAMSUNG, date(2026, 8, 1), date(2026, 8, 12))
    connection = FakeConnection()

    assert COLLECTOR.store_short_sale(connection, fetch) == 0
    assert rows_for(connection.recorded_cursor, "krx_stock_short_sale_daily") == []
    assert "INSERT INTO source_record" in connection.recorded_cursor.calls[0][0]


def test_market_funds_metadata_records_the_returned_window(monkeypatch):
    second = FUNDS_ROW | {"bsop_date": "20260810"}
    monkeypatch.setattr(kis_positioning, "send_get", fake_send_get(body(output=[FUNDS_ROW, second])))

    fetch = COLLECTOR.fetch_market_funds(date(2026, 8, 12))

    # 한 응답이 100영업일이라 어느 구간이 왔는지 계보에 남겨야 재현할 수 있다.
    assert fetch.metadata["first_date"] == "2026-08-11"
    assert fetch.metadata["last_date"] == "2026-08-10"
    assert fetch.metadata["returned"] == 2


def test_started_and_completed_times_are_utc(monkeypatch):
    monkeypatch.setattr(kis_positioning, "send_get", fake_send_get(body(output=[FUNDS_ROW])))

    fetch = COLLECTOR.fetch_market_funds(date(2026, 8, 12))

    assert fetch.started_at.tzinfo is not None
    assert fetch.started_at.astimezone(UTC) <= fetch.completed_at.astimezone(UTC)
    assert isinstance(fetch.started_at, datetime)


def test_market_lending_asks_for_the_market_division(monkeypatch):
    """`1`이 코스피, `2`가 코스닥이다(실측). 종목 조회(`3`)와 같은 endpoint다."""
    send = fake_send_get(body(output1=[LENDING_ROW]))
    monkeypatch.setattr(kis_positioning, "send_get", send)

    fetch = COLLECTOR.fetch_market_lending(LendingMarket.KOSDAQ, date(2026, 8, 1), date(2026, 8, 12))

    assert send.sent[0]["query"]["MRKT_DIV_CLS_CODE"] == "2"
    assert fetch.market_code == "KOSDAQ"
    assert fetch.stock_code is None


def test_market_lending_has_no_total_row():
    """제공처의 전체(`5`)는 두 시장의 정확한 합이라 저장하지 않는다."""
    assert {market.value for market in LendingMarket} == {"KOSPI", "KOSDAQ"}
    assert {market.division for market in LendingMarket} == {"1", "2"}


def test_store_market_lending_writes_the_market_code(monkeypatch):
    monkeypatch.setattr(kis_positioning, "send_get", fake_send_get(body(output1=[LENDING_ROW])))
    fetch = COLLECTOR.fetch_market_lending(LendingMarket.KOSPI, date(2026, 8, 1), date(2026, 8, 12))
    connection = FakeConnection()

    assert COLLECTOR.store_market_lending(connection, fetch) == 1
    upsert = rows_for(connection.recorded_cursor, "krx_market_securities_lending_daily")[0]
    assert upsert[0] == "KOSPI"
    assert upsert[1] == date(2026, 8, 12)


@pytest.mark.parametrize(("universe", "label"), RANKING_UNIVERSES)
def test_ranking_can_ask_for_each_universe(monkeypatch, universe, label):
    """`0000`이 전체, `1001`이 코스닥이다(실측: 코스닥 1위 알테오젠)."""
    send = fake_send_get(body(output1=[RANKING_HEAD], output2=[RANKING_ROW]))
    monkeypatch.setattr(kis_positioning, "send_get", send)

    fetch = COLLECTOR.fetch_credit_ranking(universe)

    assert send.sent[0]["query"]["FID_INPUT_ISCD"] == universe
    # 저장하는 모집단은 우리가 보낸 값이다. 응답 헤더의 bstp_cls_code 는 다른 체계다.
    assert fetch.metadata["universe_code"] == universe
    assert label


# 신용잔고의 0행. 되돌렸던 가드를 2026-08-28 KIS 실조회로 다시 세웠다 — 요청 결제일이
# 지켜지고(1년 전·2년 전 요청도 그 구간을 준다) 한 번에 30행이며 `tr_cont`는 안 온다.
# 그래서 "겹치는데 0행"은 결함이고 "안 겹쳐서 0행"은 정상이다.


def warnings_of(name: str):
    """그 로거의 WARNING을 모은다. `caplog`는 `tests/migrations`의 Alembic `fileConfig`가
    로거를 꺼서 전체 실행에서 조용히 실패한다(`test_briefing_disclosures.py` 참고)."""
    import contextlib
    import logging

    @contextlib.contextmanager
    def capture():
        records: list[logging.LogRecord] = []

        class Capture(logging.Handler):
            def emit(self, record: logging.LogRecord) -> None:
                records.append(record)

        handler = Capture(level=logging.WARNING)
        logger = logging.getLogger(name)
        logger.addHandler(handler)
        previous_level, previously_disabled = logger.level, logger.disabled
        logger.setLevel(logging.WARNING)
        logger.disabled = False
        try:
            yield records
        finally:
            logger.removeHandler(handler)
            logger.setLevel(previous_level)
            logger.disabled = previously_disabled

    return capture()


def test_credit_balance_with_an_overlapping_response_but_no_kept_row_is_an_error(monkeypatch):
    """30행은 거래일이 이어져 있어 겹치면 반드시 한 행은 남는다. 안 남으면 거르기가 깨진 것이다."""
    below = CREDIT_ROW | {"deal_date": "20260731", "stlm_date": "20260804"}
    above = CREDIT_ROW | {"deal_date": "20260813", "stlm_date": "20260817"}
    monkeypatch.setattr(kis_positioning, "send_get", fake_send_get(body(output=[above, below])))

    with pytest.raises(KisPayloadError, match="kept no row"):
        COLLECTOR.fetch_credit_balance(SAMSUNG, date(2026, 8, 1), date(2026, 8, 12))


def test_credit_balance_that_misses_the_window_entirely_only_warns(monkeypatch):
    """창 전체가 아직 결제 전이면(연휴 등) 0행이 정상이다. 죽이면 경보만 는다."""
    monkeypatch.setattr(kis_positioning, "send_get", fake_send_get(body(output=[CREDIT_ROW])))

    with warnings_of(kis_positioning.logger.name) as records:
        fetch = COLLECTOR.fetch_credit_balance(SAMSUNG, date(2026, 1, 2), date(2026, 1, 31))

    assert fetch.rows == ()
    assert any("has no row" in record.getMessage() for record in records)


def test_an_empty_credit_balance_response_is_an_error(monkeypatch):
    monkeypatch.setattr(kis_positioning, "send_get", fake_send_get(body(output=[])))

    with pytest.raises(KisPayloadError, match="returned no row"):
        COLLECTOR.fetch_credit_balance(SAMSUNG, date(2026, 8, 1), date(2026, 8, 12))


def test_a_window_longer_than_the_thirty_row_cap_warns_about_the_missing_head(monkeypatch):
    """한 번에 30행뿐이라 긴 백필 창은 앞부분이 조용히 빈다."""
    monkeypatch.setattr(kis_positioning, "send_get", fake_send_get(body(output=[CREDIT_ROW])))

    with warnings_of(kis_positioning.logger.name) as records:
        fetch = COLLECTOR.fetch_credit_balance(SAMSUNG, date(2026, 1, 2), date(2026, 8, 12))

    assert len(fetch.rows) == 1
    assert any("30-row cap" in record.getMessage() for record in records)
