import json
import re
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Self

import pytest
from pydantic import SecretStr
from sqlalchemy import CheckConstraint, Table

from apps.models.market import (
    InvestorFlowMarketCode,
    MarketInvestorFlowSnapshot,
    StockInvestorEstimateSnapshot,
    StockInvestorTradeDaily,
)
from apps.models.raw import SourceRecord
from modules.collectors.kis import KisPayloadError, KisResultError
from modules.collectors.market import kis_investor_flow
from modules.collectors.market.kis_investor_flow import (
    DAILY_TRADE_UPSERT,
    MARKET_FLOW_UPSERT,
    SOURCE_RECORD_INSERT,
    STOCK_ESTIMATE_UPSERT,
    InvestorFlowMarket,
    InvestorFlowStock,
    KisInvestorFlowCollector,
)

SOURCE_RECORD_ID = 41
TOKEN = SecretStr("token")
APP_KEY = SecretStr("key")
APP_SECRET = SecretStr("secret")
SAMSUNG = InvestorFlowStock.SAMSUNG_ELECTRONICS
BUSINESS_DATE = date(2026, 8, 14)
OBSERVED_AT = datetime(2026, 8, 14, 1, 55, tzinfo=UTC)

# 수집기는 자격 증명만 들고 있어 테스트마다 새로 만들 이유가 없다. HTTP는
# `send_get`을 monkeypatch 해서 가른다.
COLLECTOR = KisInvestorFlowCollector(TOKEN, APP_KEY, APP_SECRET)

# 2026-08-14 장중 실측. 부호 뒤에 0이 채워지는 표기를 그대로 옮겼다.
ESTIMATE_ROWS = [
    {
        "bsop_hour_gb": "2",
        "frgn_fake_ntby_qty": "000000000000878000",
        "orgn_fake_ntby_qty": "-00000000000464000",
        "sum_fake_ntby_qty": "000000000000414000",
    },
    {
        "bsop_hour_gb": "1",
        "frgn_fake_ntby_qty": "000000000001059000",
        "orgn_fake_ntby_qty": "000000000000000000",
        "sum_fake_ntby_qty": "000000000001059000",
    },
]


def market_row(**overrides) -> dict[str, str]:
    """시장 응답 한 행. 실제 응답은 72필드이고 우리가 읽는 것은 그중 21칸이다.

    두 항등식이 성립하도록 짰다. 기관 세부 일곱의 합이 기관계이고, 개인·외국인·기관계·기타
    둘의 합이 0이다. 실제 응답도 그랬다(실측).
    """
    row = {
        "frgn_seln_vol": "2095024",
        "frgn_shnu_vol": "2068783",
        "frgn_ntby_qty": "-26241",
        "frgn_ntby_tr_pbmn": "-161883",
        "orgn_seln_vol": "1057901",
        "orgn_shnu_vol": "1117422",
        "orgn_ntby_qty": "59521",
        "orgn_ntby_tr_pbmn": "291540",
        "prsn_seln_vol": "263424",
        "prsn_shnu_vol": "229004",
        "prsn_ntby_qty": "-34420",
        "prsn_ntby_tr_pbmn": "-98311",
        # 기관 세부 일곱. 합이 orgn_ntby_qty와 같아야 한다.
        "scrt_ntby_qty": "12345",
        "ivtr_ntby_qty": "20000",
        "pe_fund_ntby_vol": "5000",  # 사모펀드만 접미사가 _vol이다
        "bank_ntby_qty": "1000",
        "insu_ntby_qty": "-678",
        "mrbn_ntby_qty": "176",
        "fund_ntby_qty": "21678",
        # 기관에도 개인에도 들어가지 않는 둘. 둘 다 접미사가 _vol이다.
        "etc_corp_ntby_vol": "1200",
        "etc_orgt_ntby_vol": "-60",
    }
    return row | overrides


ZERO_ROW = {key: "0" for key in market_row()}


def body(rt_cd: str = "0", msg1: str = "정상처리 되었습니다.", **outputs) -> bytes:
    return json.dumps({"rt_cd": rt_cd, "msg_cd": "MCA00000", "msg1": msg1, **outputs}, ensure_ascii=False).encode()


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
        (STOCK_ESTIMATE_UPSERT, StockInvestorEstimateSnapshot),
        (MARKET_FLOW_UPSERT, MarketInvestorFlowSnapshot),
        (DAILY_TRADE_UPSERT, StockInvestorTradeDaily),
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


def test_the_slot_is_part_of_the_stock_natural_key():
    """한 응답에 슬롯마다 한 행이 온다. 슬롯이 키에 없으면 마지막 하나만 남는다."""
    assert "ON CONFLICT (provider, stock_code, business_date, source_time_code)" in STOCK_ESTIMATE_UPSERT
    # 수집 시각은 값이라 갱신된다.
    assert "collected_at = EXCLUDED.collected_at" in STOCK_ESTIMATE_UPSERT


def test_stock_codes_match_the_other_collectors():
    """공시·포지션과 한 키로 이으려면 세 수집기의 종목 코드가 같아야 한다."""
    from modules.collectors.document.dart import DartCompany
    from modules.collectors.kis_positioning import PositioningStock

    codes = {stock.value for stock in InvestorFlowStock}
    assert codes == {company.value for company in DartCompany}
    assert codes == {stock.value for stock in PositioningStock}


def test_only_documented_markets_are_requestable():
    """잘못된 코드가 오류 없이 0을 돌려주므로 후보를 Enum에 넣어 두면 조용히 0이 쌓인다.

    코드의 근거는 공식 postman 컬렉션의 파라미터 설명이다(모듈 문서의 "시장 코드는 문서에
    다 있다"). `999/S001`은 코스피가 아니라 주식선물이라 되돌리면 안 된다.
    """
    assert {market.value: (market.primary_code, market.secondary_code) for market in InvestorFlowMarket} == {
        "KOSPI": ("KSP", "0001"),
        "KOSDAQ": ("KSQ", "1001"),
        "FUTURES": ("K2I", "F001"),
        "CALL_OPTION": ("K2I", "OC01"),
        "PUT_OPTION": ("K2I", "OP01"),
        "STOCK_FUTURES": ("999", "S001"),
        "ETF": ("ETF", "T000"),
    }


def test_the_market_enum_matches_the_model_and_its_check_constraint():
    """수집기가 보내는 시장과 테이블이 받는 시장이 어긋나면 저장에서 죽는다.

    셋을 함께 묶는다. 수집기 Enum, 모델 Enum, 그리고 실제 DDL이 되는 CHECK 제약이다.
    Enum 둘만 맞춰 두면 제약을 넓히는 마이그레이션을 빠뜨린 것을 못 잡는다.
    """
    collected = {market.value for market in InvestorFlowMarket}
    assert collected == {code.value for code in InvestorFlowMarketCode}

    constraint = next(
        check
        for check in MarketInvestorFlowSnapshot.__table__.constraints
        if isinstance(check, CheckConstraint) and check.name == "ck_market_investor_flow_snapshot_market_code"
    )
    allowed = set(re.findall(r"'([A-Z_]+)'", str(constraint.sqltext)))
    assert allowed == collected


def test_derivative_markets_reuse_the_spot_response_shape(monkeypatch):
    """일곱 시장이 같은 12개 분류를 같은 필드 이름으로 준다.

    같지 않다면 시장마다 다른 파서가 필요하다는 뜻이라 설계가 통째로 달라진다.
    """
    monkeypatch.setattr(kis_investor_flow, "send_get", fake_send_get(body(output=[market_row()])))

    fetch = COLLECTOR.fetch_market_flow(InvestorFlowMarket.CALL_OPTION, OBSERVED_AT)

    assert fetch.market_code == "CALL_OPTION"
    assert fetch.row.foreign_net_buy_qty == -26241


def test_estimates_keep_every_slot(monkeypatch):
    monkeypatch.setattr(kis_investor_flow, "send_get", fake_send_get(body(output2=ESTIMATE_ROWS)))

    fetch = COLLECTOR.fetch_stock_estimates(SAMSUNG, BUSINESS_DATE)

    assert [row.source_time_code for row in fetch.rows] == ["2", "1"]
    # 부호 뒤에 0이 채워진 값을 그대로 읽는다.
    assert fetch.rows[0].institution_net_buy_qty == -464000
    assert fetch.rows[1].institution_net_buy_qty == 0


def test_estimates_reject_a_sum_that_does_not_add_up(monkeypatch):
    broken = [ESTIMATE_ROWS[0] | {"sum_fake_ntby_qty": "999"}]
    monkeypatch.setattr(kis_investor_flow, "send_get", fake_send_get(body(output2=broken)))

    with pytest.raises(KisPayloadError, match="does not add up"):
        COLLECTOR.fetch_stock_estimates(SAMSUNG, BUSINESS_DATE)


def test_estimates_reject_duplicated_slots(monkeypatch):
    monkeypatch.setattr(
        kis_investor_flow, "send_get", fake_send_get(body(output2=[ESTIMATE_ROWS[0], dict(ESTIMATE_ROWS[0])]))
    )

    with pytest.raises(KisPayloadError, match="duplicated slots"):
        COLLECTOR.fetch_stock_estimates(SAMSUNG, BUSINESS_DATE)


def test_an_empty_estimate_response_is_normal(monkeypatch):
    """갱신 전이면 슬롯이 없다. 없는 종목코드도 0행으로 오지만 종목은 Enum이 막는다."""
    monkeypatch.setattr(kis_investor_flow, "send_get", fake_send_get(body(output2=[])))

    fetch = COLLECTOR.fetch_stock_estimates(SAMSUNG, BUSINESS_DATE)

    assert fetch.rows == ()


def test_market_flow_reads_all_three_investor_groups(monkeypatch):
    monkeypatch.setattr(kis_investor_flow, "send_get", fake_send_get(body(output=[market_row()])))

    fetch = COLLECTOR.fetch_market_flow(InvestorFlowMarket.KOSPI, OBSERVED_AT)

    row = fetch.row
    assert row.foreign_net_buy_qty == -26241
    assert row.institution_net_buy_qty == 59521
    # 개인이 있어야 "누가 팔고 누가 받았나"가 읽힌다.
    assert row.individual_net_buy_qty == -34420
    assert row.individual_net_buy_amount == Decimal(-98311)


def test_market_flow_reads_the_institution_breakdown(monkeypatch):
    """기관계 한 칸으로는 연기금이 사는 장과 투신이 파는 장을 가릴 수 없다."""
    monkeypatch.setattr(kis_investor_flow, "send_get", fake_send_get(body(output=[market_row()])))

    row = COLLECTOR.fetch_market_flow(InvestorFlowMarket.KOSPI, OBSERVED_AT).row

    assert row.investment_trust_net_buy_qty == 20000
    assert row.pension_fund_net_buy_qty == 21678
    # 접미사가 _vol인 셋을 _ntby_qty로 읽으면 여기가 조용히 0이 된다.
    assert row.private_equity_net_buy_qty == 5000
    assert row.other_corporation_net_buy_qty == 1200
    assert row.other_organization_net_buy_qty == -60


def test_the_institution_parts_must_add_up_to_the_institution_total(monkeypatch):
    # 반올림 오차 허용 폭(4)을 넘는 어긋남이어야 실패한다.
    broken = market_row(fund_ntby_qty="21683")
    monkeypatch.setattr(kis_investor_flow, "send_get", fake_send_get(body(output=[broken])))

    with pytest.raises(KisPayloadError, match="institution parts do not add up"):
        COLLECTOR.fetch_market_flow(InvestorFlowMarket.KOSPI, OBSERVED_AT)


def test_the_investor_categories_must_close_to_zero(monkeypatch):
    """시장 전체는 닫혀 있다. 닫히지 않으면 분류 하나를 빠뜨린 것이다."""
    # 반올림 오차 허용 폭(3)을 넘는 어긋남이어야 실패한다.
    broken = market_row(etc_corp_ntby_vol="1204")
    monkeypatch.setattr(kis_investor_flow, "send_get", fake_send_get(body(output=[broken])))

    with pytest.raises(KisPayloadError, match="do not close to zero"):
        COLLECTOR.fetch_market_flow(InvestorFlowMarket.KOSPI, OBSERVED_AT)


def test_the_vol_suffix_categories_are_not_read_as_qty():
    """사모펀드·기타법인·기타단체만 `_ntby_vol`이다. 한 벌로 조립하면 셋이 0이 된다."""
    fields = dict(kis_investor_flow.INSTITUTION_PARTS + kis_investor_flow.OTHER_PARTS)

    assert fields["private_equity"] == "pe_fund_ntby_vol"
    assert fields["other_corporation"] == "etc_corp_ntby_vol"
    assert fields["other_organization"] == "etc_orgt_ntby_vol"
    assert all(
        field.endswith("_ntby_qty") for name, field in kis_investor_flow.INSTITUTION_PARTS if name != "private_equity"
    )


def test_an_all_zero_market_response_fails(monkeypatch):
    """잘못된 시장 코드가 오류가 아니라 값 0으로 온다(실측 후보 6개)."""
    monkeypatch.setattr(kis_investor_flow, "send_get", fake_send_get(body(output=[ZERO_ROW])))

    with pytest.raises(KisPayloadError, match="all-zero"):
        COLLECTOR.fetch_market_flow(InvestorFlowMarket.KOSPI, OBSERVED_AT)


def test_market_flow_tolerates_per_field_rounding(monkeypatch):
    """수량이 칸마다 따로 반올림되어 온다(실측: 코스닥 외국인 매수-매도와 순매수가 1 차이)."""
    rounded = market_row(frgn_ntby_qty="-26242")
    monkeypatch.setattr(kis_investor_flow, "send_get", fake_send_get(body(output=[rounded])))

    row = COLLECTOR.fetch_market_flow(InvestorFlowMarket.KOSDAQ, OBSERVED_AT).row

    assert row.foreign_net_buy_qty == -26242


def test_market_flow_rejects_a_net_that_does_not_add_up(monkeypatch):
    broken = market_row(frgn_ntby_qty="999")
    monkeypatch.setattr(kis_investor_flow, "send_get", fake_send_get(body(output=[broken])))

    with pytest.raises(KisPayloadError, match="foreign net buy does not add up"):
        COLLECTOR.fetch_market_flow(InvestorFlowMarket.KOSPI, OBSERVED_AT)


def test_market_flow_sends_both_codes(monkeypatch):
    send = fake_send_get(body(output=[market_row()]))
    monkeypatch.setattr(kis_investor_flow, "send_get", send)

    COLLECTOR.fetch_market_flow(InvestorFlowMarket.KOSPI, OBSERVED_AT)

    assert send.sent[0]["query"] == {"FID_INPUT_ISCD": "KSP", "FID_INPUT_ISCD_2": "0001"}


def test_result_code_failures_are_raised(monkeypatch):
    monkeypatch.setattr(kis_investor_flow, "send_get", fake_send_get(body(rt_cd="1", msg1="권한이 없습니다")))

    with pytest.raises(KisResultError) as error:
        COLLECTOR.fetch_stock_estimates(SAMSUNG, BUSINESS_DATE)

    assert error.value.code == "1"


def test_store_estimates_writes_one_row_per_slot(monkeypatch):
    monkeypatch.setattr(kis_investor_flow, "send_get", fake_send_get(body(output2=ESTIMATE_ROWS)))
    fetch = COLLECTOR.fetch_stock_estimates(SAMSUNG, BUSINESS_DATE)
    connection = FakeConnection()

    stored = COLLECTOR.store_stock_estimates(connection, fetch)
    upserts = rows_for(connection.recorded_cursor, "stock_investor_estimate_snapshot")
    assert stored == len(upserts) == 2
    assert [row[2] for row in upserts] == ["2", "1"]
    assert {row[0] for row in upserts} == {"005930"}
    assert {row[1] for row in upserts} == {BUSINESS_DATE}

    statement, parameters = connection.recorded_cursor.calls[0]
    assert "INSERT INTO source_record" in statement
    assert parameters[1:3] == ("kis", "investor_trend_estimate")
    # API 키가 질의 문자열에 있으므로 원본 요청을 남기지 않는다.
    assert parameters[7] is None
    assert json.loads(parameters[8])["slots"] == ["2", "1"]


def test_store_market_flow_writes_one_row(monkeypatch):
    monkeypatch.setattr(kis_investor_flow, "send_get", fake_send_get(body(output=[market_row()])))
    fetch = COLLECTOR.fetch_market_flow(InvestorFlowMarket.KOSPI, OBSERVED_AT)
    connection = FakeConnection()

    assert COLLECTOR.store_market_flow(connection, fetch) == 1
    upsert = rows_for(connection.recorded_cursor, "market_investor_flow_snapshot")[0]
    assert upsert[0] == "KOSPI"
    assert upsert[1] == OBSERVED_AT
    assert upsert[-1] == SOURCE_RECORD_ID


def test_no_delta_is_stored():
    """누적값이라 델타를 저장하지 않는다. 5분 변화량은 조회에서 lag()로 계산한다."""
    columns = {column.name for column in MarketInvestorFlowSnapshot.__table__.columns}

    assert not [name for name in columns if "delta" in name or "change" in name]


# 2026-08-14 실측(005930). 응답은 101필드이고 우리가 읽는 것은 그중 22칸이다. 네 항등식이
# 모두 성립하는 실제 값이다.
DAILY_ROW = {
    "stck_bsop_date": "20260814",
    "stck_oprc": "275000",
    "stck_hgpr": "275500",
    "stck_lwpr": "266000",
    "stck_clpr": "274500",
    "acml_vol": "21668266",
    "acml_tr_pbmn": "5874118816500",
    "frgn_ntby_qty": "4913432",
    "frgn_reg_ntby_qty": "4922472",
    "frgn_nreg_ntby_qty": "-9040",
    "prsn_ntby_qty": "-3049224",
    "orgn_ntby_qty": "-1830920",
    "scrt_ntby_qty": "-1390485",
    "ivtr_ntby_qty": "107489",
    "pe_fund_ntby_vol": "-511711",
    "bank_ntby_qty": "19391",
    "insu_ntby_qty": "-82201",
    "mrbn_ntby_qty": "-746",
    "fund_ntby_qty": "27343",
    "etc_ntby_qty": "-33288",
    "etc_corp_ntby_vol": "-33288",
    "etc_orgt_ntby_vol": "0",
    "frgn_ntby_tr_pbmn": "1336152",
    "orgn_ntby_tr_pbmn": "-497830",
    "prsn_ntby_tr_pbmn": "-829331",
}

DAILY_ROW_PREVIOUS = DAILY_ROW | {"stck_bsop_date": "20260813", "stck_clpr": "268000"}


def daily_row(**overrides) -> dict[str, str]:
    return DAILY_ROW | overrides


def test_daily_trade_reads_every_investor_category(monkeypatch):
    """장중 추정에는 개인이 없다. 확정값에서 12분류가 다 오는 것이 이 조회의 이유다."""
    monkeypatch.setattr(kis_investor_flow, "send_get", fake_send_get(body(output2=[DAILY_ROW])))

    fetch = COLLECTOR.fetch_stock_trade_daily(SAMSUNG, BUSINESS_DATE)

    row = fetch.rows[0]
    assert row.business_date == date(2026, 8, 14)
    assert row.individual_net_buy_qty == -3049224
    assert row.foreign_registered_net_buy_qty == 4922472
    assert row.foreign_unregistered_net_buy_qty == -9040
    assert row.pension_fund_net_buy_qty == 27343
    assert row.close_price == Decimal(274500)
    assert (row.open_price, row.high_price, row.low_price) == (Decimal(275000), Decimal(275500), Decimal(266000))
    assert row.foreign_net_buy_amount == Decimal(1336152)


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"frgn_reg_ntby_qty": "4922473"}, "foreign parts do not add up"),
        ({"fund_ntby_qty": "27344"}, "institution parts do not add up"),
        ({"etc_ntby_qty": "-33289"}, "other parts do not add up"),
        ({"prsn_ntby_qty": "-3049225", "etc_ntby_qty": "-33288"}, "do not close to zero"),
    ],
)
def test_daily_trade_enforces_all_four_identities(monkeypatch, overrides, message):
    """네 항등식이 실측으로 성립했다. 깨지면 필드 뜻이 바뀐 것이라 저장하지 않는다."""
    monkeypatch.setattr(kis_investor_flow, "send_get", fake_send_get(body(output2=[daily_row(**overrides)])))

    with pytest.raises(KisPayloadError, match=message):
        COLLECTOR.fetch_stock_trade_daily(SAMSUNG, BUSINESS_DATE)


def test_daily_trade_sends_the_market_division_and_end_date(monkeypatch):
    """시장 구분 하나로 코스피와 코스닥을 함께 받는다. 시장별 코드를 찾을 필요가 없다."""
    send = fake_send_get(body(output2=[DAILY_ROW]))
    monkeypatch.setattr(kis_investor_flow, "send_get", send)

    COLLECTOR.fetch_stock_trade_daily(SAMSUNG, BUSINESS_DATE)

    assert send.sent[0]["tr_id"] == "FHPTJ04160001"
    assert send.sent[0]["query"] == {
        "FID_COND_MRKT_DIV_CODE": "J",
        "FID_INPUT_ISCD": "005930",
        "FID_INPUT_DATE_1": "20260814",
        "FID_ORG_ADJ_PRC": "",
        "FID_ETC_CLS_CODE": "",
    }


def test_daily_trade_rejects_rows_after_the_end_date(monkeypatch):
    """요청 날짜가 구간의 끝이라는 전제 위에서 백필이 날짜를 뒤로 건다.

    끝보다 뒤의 거래일이 섞이면 그 전제가 깨진 것이고, 백필이 같은 구간을 맴돌게 된다.
    """
    ahead = daily_row(stck_bsop_date="20260817")
    monkeypatch.setattr(kis_investor_flow, "send_get", fake_send_get(body(output2=[ahead])))

    with pytest.raises(KisPayloadError, match="returned rows after"):
        COLLECTOR.fetch_stock_trade_daily(SAMSUNG, BUSINESS_DATE)


def test_daily_trade_rejects_duplicated_business_dates(monkeypatch):
    monkeypatch.setattr(kis_investor_flow, "send_get", fake_send_get(body(output2=[DAILY_ROW, DAILY_ROW])))

    with pytest.raises(KisPayloadError, match="duplicated business dates"):
        COLLECTOR.fetch_stock_trade_daily(SAMSUNG, BUSINESS_DATE)


def test_daily_trade_rejects_an_unreadable_date(monkeypatch):
    monkeypatch.setattr(
        kis_investor_flow, "send_get", fake_send_get(body(output2=[daily_row(stck_bsop_date="2026-08-14")]))
    )

    with pytest.raises(KisPayloadError, match="unreadable stck_bsop_date"):
        COLLECTOR.fetch_stock_trade_daily(SAMSUNG, BUSINESS_DATE)


def test_store_daily_trade_writes_one_row_per_business_date(monkeypatch):
    monkeypatch.setattr(kis_investor_flow, "send_get", fake_send_get(body(output2=[DAILY_ROW, DAILY_ROW_PREVIOUS])))
    fetch = COLLECTOR.fetch_stock_trade_daily(SAMSUNG, BUSINESS_DATE)
    connection = FakeConnection()

    assert COLLECTOR.store_stock_trade_daily(connection, fetch) == 2

    rows = rows_for(connection.recorded_cursor, "stock_investor_trade_daily")
    assert [row[1] for row in rows] == [date(2026, 8, 14), date(2026, 8, 13)]
    assert all(row[0] == "005930" for row in rows)
    assert all(row[-1] == SOURCE_RECORD_ID for row in rows)


def test_store_daily_trade_records_the_covered_range(monkeypatch):
    """백필이 어디까지 갔는지 계보만 보고 읽을 수 있어야 한다."""
    monkeypatch.setattr(kis_investor_flow, "send_get", fake_send_get(body(output2=[DAILY_ROW, DAILY_ROW_PREVIOUS])))
    fetch = COLLECTOR.fetch_stock_trade_daily(SAMSUNG, BUSINESS_DATE)
    connection = FakeConnection()
    COLLECTOR.store_stock_trade_daily(connection, fetch)

    record = rows_for(connection.recorded_cursor, "source_record")[0]
    metadata = json.loads(record[-1])
    assert record[2] == "investor_trade_by_stock_daily"
    assert metadata["covered_from"] == "2026-08-13"
    assert metadata["covered_to"] == "2026-08-14"


def test_the_daily_amount_unit_is_recorded_as_million_won():
    """실측으로 확정한 배율이다. 원으로 오해하면 화면이 백만 배 작게 그린다.

    같은 응답 안에서 acml_tr_pbmn만 원이라 한 벌로 환산하면 안 된다.
    """
    table = StockInvestorTradeDaily.__table__

    assert "백만원" in table.columns["foreign_net_buy_amount"].comment
    assert "원이다" in table.columns["accumulated_trade_amount"].comment


@pytest.mark.parametrize(
    "overrides",
    [
        {"stck_hgpr": "265000"},  # 고가가 저가보다 낮다
        {"stck_lwpr": "276000"},  # 저가가 종가보다 높다
    ],
)
def test_daily_trade_rejects_an_inconsistent_candle(monkeypatch, overrides):
    """필드를 잘못 짚으면 값이 그럴듯한 자리에 들어가 그림만 이상해진다."""
    monkeypatch.setattr(kis_investor_flow, "send_get", fake_send_get(body(output2=[daily_row(**overrides)])))

    with pytest.raises(KisPayloadError, match="daily candle is inconsistent"):
        COLLECTOR.fetch_stock_trade_daily(SAMSUNG, BUSINESS_DATE)


def test_a_candle_of_zeros_passes(monkeypatch):
    """상장 전이나 거래정지 구간은 네 값이 0으로 온다. 그것까지 실패시키면 백필이 멈춘다."""
    flat = daily_row(stck_oprc="0", stck_hgpr="0", stck_lwpr="0", stck_clpr="0")
    monkeypatch.setattr(kis_investor_flow, "send_get", fake_send_get(body(output2=[flat])))

    row = COLLECTOR.fetch_stock_trade_daily(SAMSUNG, BUSINESS_DATE).rows[0]

    assert row.high_price == Decimal(0)
