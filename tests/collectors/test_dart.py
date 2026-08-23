import io
import json
import re
import zipfile
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Self

import pytest
from pydantic import SecretStr
from sqlalchemy import Table

from apps.models.market import DisclosureEvent, EarningsFact
from apps.models.raw import SourceRecord
from modules.collectors.document import dart
from modules.collectors.document.dart import (
    DISCLOSURE_EVENT_UPSERT,
    EARNINGS_FACT_UPSERT,
    SOURCE_RECORD_INSERT,
    DartCollector,
    DartCompany,
    DartPayloadError,
    DartStatusError,
    Disclosure,
    DisclosureFetch,
    is_provisional,
    parse_financials,
    parse_provisional,
    periodic_report,
)

API_KEY = SecretStr("key")
COLLECTOR = DartCollector(API_KEY)
SOURCE_RECORD_ID = 21
STARTED_AT = datetime(2026, 8, 12, 22, 0, tzinfo=UTC)
COMPLETED_AT = datetime(2026, 8, 12, 22, 0, 2, tzinfo=UTC)
DETECTED_AT = datetime(2026, 8, 12, 22, 0, 3, tzinfo=UTC)

# 2026-08-12 실측 응답을 줄인 것이다. 필드 이름과 값 모양은 그대로다.
LIST_ROW = {
    "corp_code": "00126380",
    "corp_name": "삼성전자",
    "stock_code": "005930",
    "corp_cls": "Y",
    # 원문은 뒤에 공백 패딩이 붙어 온다.
    "report_nm": "연결재무제표기준영업(잠정)실적(공정공시)               ",
    "rcept_no": "20260730800077",
    "flr_nm": "삼성전자",
    "rcept_dt": "20260730",
    "rm": "유",
}


def list_body(rows: list[dict], status: str = "000", total_page: int = 1, total_count: int | None = None) -> bytes:
    payload = {"status": status, "message": "정상", "page_no": 1, "page_count": 100}
    if status == "000":
        payload |= {"total_count": total_count if total_count is not None else len(rows), "total_page": total_page}
        payload["list"] = rows
    return json.dumps(payload, ensure_ascii=False).encode()


def provisional_xml(
    unit: str = "억원",
    scope: str = "1. 연결실적내용",
    revenue: str = "1,714,995",
    prior_revenue: str = "745,663",
    net_income: str = "716,245",
) -> str:
    """실측 원문의 구조를 그대로 줄인 것. 표를 id로 집는지 검증하는 데 쓴다."""
    return f"""
    <html><body>
    <table id="XFormD1_Form0_Table0"><tbody>
      <tr><td>실적기간</td><td>당기실적</td><td>2026-04-01</td><td>~</td><td>2026-06-30</td></tr>
      <tr><td>전기실적</td><td>2026-01-01</td><td>~</td><td>2026-03-31</td></tr>
      <tr><td>당기누계실적</td><td>2026-01-01</td><td>~</td><td>2026-06-30</td></tr>
    </tbody></table>
    <table id="XFormD1_Form0_RepeatTable0"><tbody>
      <tr><td>{scope}</td><td>단위 : {unit}, %</td></tr>
      <tr><td>매출액</td><td>당해실적</td><td>{revenue}</td><td>1,338,734</td><td>28.11</td><td>-</td>
          <td>{prior_revenue}</td><td>130.00</td><td>-</td></tr>
      <tr><td>누계실적</td><td>3,053,729</td><td>-</td><td>-</td><td>-</td><td>1,537,068</td><td>98.67</td><td>-</td></tr>
      <tr><td>영업이익</td><td>당해실적</td><td>894,924</td><td>572,328</td><td>56.37</td><td>-</td>
          <td>68,059</td><td>1,214.99</td><td>-</td></tr>
      <tr><td>누계실적</td><td>1,467,252</td><td>-</td><td>-</td><td>-</td><td>134,838</td><td>988.13</td><td>-</td></tr>
      <tr><td>당기순이익</td><td>당해실적</td><td>{net_income}</td><td>472,253</td><td>51.67</td><td>-</td>
          <td>51,164</td><td>1,299.90</td><td>-</td></tr>
      <tr><td>누계실적</td><td>1,188,497</td><td>-</td><td>-</td><td>-</td><td>133,393</td><td>790.97</td><td>-</td></tr>
      <tr><td>지배기업 소유주지분 순이익</td><td>당해실적</td><td>712,695</td><td>471,012</td><td>51.31</td><td>-</td>
          <td>50,996</td><td>1,297.55</td><td>-</td></tr>
      <tr><td>누계실적</td><td>1,183,707</td><td>-</td><td>-</td><td>-</td><td>132,975</td><td>790.17</td><td>-</td></tr>
    </tbody></table>
    </body></html>
    """


def amended_xml() -> str:
    """정정 공시는 정정 요약 표(`XFormD8_*`)가 앞에 붙는다. 숫자를 거기서 읽으면 안 된다."""
    summary = """
    <table id="XFormD8_Form0_RepeatTable0"><tbody>
      <tr><td>1. 정정관련 공시서류</td><td>연결재무제표기준영업(잠정)실적(공정공시)</td></tr>
      <tr><td>매출액</td><td>당해실적</td><td>999,999</td><td>0</td><td>0</td><td>-</td><td>0</td><td>0</td><td>-</td></tr>
    </tbody></table>
    """
    return provisional_xml(unit="조원", revenue="171.50", prior_revenue="74.57").replace("<body>", f"<body>{summary}")


def document_zip(xml: str, name: str = "20260730800077.xml") -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(name, xml)
    return buffer.getvalue()


# 실측한 손익계산서 행. 계정 id가 계약이고 이름은 보고서마다 바뀐다.
FINANCIAL_ROWS = [
    {
        "sj_div": "IS",
        "account_id": "ifrs-full_Revenue",
        "account_nm": "매출액",
        "thstrm_amount": "74566317000000",
        "thstrm_add_amount": "153706820000000",
        "frmtrm_amount": None,
        "frmtrm_add_amount": "145983903000000",
        "currency": "KRW",
        "rcept_no": "20250814003156",
    },
    {
        "sj_div": "IS",
        "account_id": "dart_OperatingIncomeLoss",
        "account_nm": "영업이익",
        "thstrm_amount": "4676057000000",
        "thstrm_add_amount": "11361329000000",
        "frmtrm_amount": None,
        "frmtrm_add_amount": "17049887000000",
        "currency": "KRW",
        "rcept_no": "20250814003156",
    },
    {
        "sj_div": "IS",
        "account_id": "ifrs-full_ProfitLoss",
        # 보고서에 따라 반기순이익·분기순이익·당기순이익으로 바뀐다.
        "account_nm": "반기순이익",
        "thstrm_amount": "5116435000000",
        "thstrm_add_amount": "13339313000000",
        "frmtrm_amount": None,
        "frmtrm_add_amount": "16596053000000",
        "currency": "KRW",
        "rcept_no": "20250814003156",
    },
    {
        # 포괄손익계산서에도 순이익이 중복으로 온다.
        "sj_div": "CIS",
        "account_id": "ifrs-full_ProfitLoss",
        "account_nm": "반기순이익",
        "thstrm_amount": "5116435000000",
        "thstrm_add_amount": "13339313000000",
        "frmtrm_amount": None,
        "frmtrm_add_amount": "16596053000000",
        "currency": "KRW",
        "rcept_no": "20250814003156",
    },
]


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

    def fetchall(self) -> list:
        return []


class FakeConnection:
    def __init__(self) -> None:
        self.recorded_cursor = FakeCursor()

    def cursor(self) -> FakeCursor:
        return self.recorded_cursor


@pytest.fixture(autouse=True)
def without_the_psycopg2_fast_path(monkeypatch):
    monkeypatch.setattr("modules.upsert._execute_batch", None)


def fake_get(responses: list[bytes]):
    calls: list[tuple[str, dict | None]] = []

    def get(url: str, params: dict | None = None) -> bytes:
        calls.append((url, params))
        return responses[len(calls) - 1]

    get.calls = calls  # type: ignore[attr-defined]
    return get


def inserted_columns(statement: str) -> tuple[str, ...]:
    columns = re.search(r"INSERT INTO \w+ \(([^)]+)\)", statement, re.DOTALL)
    assert columns is not None
    names = re.sub(r"--[^\n]*", "", columns.group(1))
    return tuple(name.strip() for name in names.split(",") if name.strip())


def required_columns(table: Table) -> set[str]:
    return {
        column.name
        for column in table.columns
        if not column.nullable and column.server_default is None and not column.primary_key
    }


def test_upserts_match_their_models():
    for statement, model in (
        (DISCLOSURE_EVENT_UPSERT, DisclosureEvent),
        (EARNINGS_FACT_UPSERT, EarningsFact),
        (SOURCE_RECORD_INSERT, SourceRecord),
    ):
        table = model.__table__
        columns = inserted_columns(statement)
        assert set(columns) <= {column.name for column in table.columns}
        assert required_columns(table) <= set(columns)


def test_disclosure_upsert_keeps_the_first_detected_at():
    updated = set(re.findall(r"^\s{4}(\w+) = EXCLUDED", DISCLOSURE_EVENT_UPSERT, re.MULTILINE))

    # 최초 감지 시각이 재수집으로 덮이면 의미가 사라진다. 2분 폴링이라 이 값이 공시 시각의
    # 상한 노릇을 한다.
    assert "detected_at" not in updated
    assert "ON CONFLICT (provider, rcept_no)" in DISCLOSURE_EVENT_UPSERT


def test_earnings_upsert_uses_the_five_part_natural_key():
    assert "ON CONFLICT (provider, rcept_no, statement_scope, amount_basis, metric)" in EARNINGS_FACT_UPSERT


def test_company_codes_match_the_verified_mapping():
    # corpCode.xml로 확인했다(실측 2026-08-12).
    assert DartCompany.SAMSUNG_ELECTRONICS.corp_code == "00126380"
    assert DartCompany.SK_HYNIX.corp_code == "00164779"
    assert {company.value for company in DartCompany} == {"005930", "000660"}


def test_disclosure_list_strips_padding_and_parses_the_date(monkeypatch):
    monkeypatch.setattr(dart, "_get", fake_get([list_body([LIST_ROW])]))

    fetch = COLLECTOR.fetch_disclosures(DartCompany.SAMSUNG_ELECTRONICS, date(2026, 7, 24), date(2026, 7, 30))

    disclosure = fetch.disclosures[0]
    assert disclosure.report_name == "연결재무제표기준영업(잠정)실적(공정공시)"
    assert disclosure.receipt_date == date(2026, 7, 30)
    assert disclosure.remarks == "유"
    assert fetch.page_count == 1


def test_no_data_status_is_zero_rows_not_a_failure(monkeypatch):
    monkeypatch.setattr(dart, "_get", fake_get([list_body([], status="013")]))

    fetch = COLLECTOR.fetch_disclosures(DartCompany.SK_HYNIX, date(2026, 7, 24), date(2026, 7, 30))

    assert fetch.disclosures == ()


def test_other_statuses_are_raised(monkeypatch):
    monkeypatch.setattr(dart, "_get", fake_get([list_body([], status="020")]))

    with pytest.raises(DartStatusError) as error:
        COLLECTOR.fetch_disclosures(DartCompany.SK_HYNIX, date(2026, 7, 24), date(2026, 7, 30))

    assert error.value.code == "020"


def test_disclosure_list_follows_every_page(monkeypatch):
    get = fake_get([list_body([LIST_ROW], total_page=2, total_count=2), list_body([LIST_ROW], total_page=2)])
    monkeypatch.setattr(dart, "_get", get)

    fetch = COLLECTOR.fetch_disclosures(DartCompany.SAMSUNG_ELECTRONICS, date(2026, 7, 24), date(2026, 7, 30))

    assert fetch.page_count == 2
    assert len(fetch.disclosures) == 2
    assert [call[1]["page_no"] for call in get.calls] == ["1", "2"]


def test_report_classification():
    assert is_provisional("[기재정정]연결재무제표기준영업(잠정)실적(공정공시)")
    assert is_provisional("연결재무제표기준영업(잠정)실적(공정공시)")
    assert not is_provisional("임원ㆍ주요주주특정증권등소유상황보고서")

    # 1분기와 3분기가 같은 이름이라 월로 가른다.
    assert periodic_report("분기보고서 (2026.03)") == (2026, "11013", date(2026, 3, 31))
    assert periodic_report("분기보고서 (2025.09)") == (2025, "11014", date(2025, 9, 30))
    assert periodic_report("반기보고서 (2025.06)") == (2025, "11012", date(2025, 6, 30))
    assert periodic_report("[기재정정]사업보고서 (2025.12)") == (2025, "11011", date(2025, 12, 31))
    assert periodic_report("증권발행실적보고서") is None


def test_provisional_reads_period_and_cumulative_rows():
    values, metadata = parse_provisional(provisional_xml())

    assert metadata == {"unit_multiplier": "100000000", "statement_scope": "CFS"}
    revenue = {value.amount_basis: value for value in values if value.metric == "revenue"}
    # 억원 단위를 원으로 정규화한다.
    assert revenue["period"].current_amount == Decimal(1714995) * 100_000_000
    assert revenue["period"].prior_year_amount == Decimal(745663) * 100_000_000
    assert revenue["period"].period_end == date(2026, 6, 30)
    assert revenue["cumulative"].current_amount == Decimal(3053729) * 100_000_000
    assert {value.metric for value in values} == {"revenue", "operating_profit", "net_income"}


def test_provisional_does_not_read_the_owners_share_row_as_net_income():
    values = parse_provisional(provisional_xml())[0]

    net = {value.amount_basis: value for value in values if value.metric == "net_income"}
    # 당기순이익 바로 아래 지배기업 소유주지분 순이익이 있다.
    assert net["period"].current_amount == Decimal(716245) * 100_000_000
    assert net["period"].source_account_name == "당기순이익"
    assert len([value for value in values if value.metric == "net_income"]) == 2


@pytest.mark.parametrize(
    ("unit", "multiplier"),
    [("억원", 100_000_000), ("백만원", 1_000_000), ("조원", 1_000_000_000_000), ("원", 1)],
)
def test_provisional_honours_the_declared_unit(unit, multiplier):
    values = parse_provisional(provisional_xml(unit=unit, revenue="100"))[0]

    revenue = next(value for value in values if value.metric == "revenue" and value.amount_basis == "period")
    assert revenue.current_amount == Decimal(100) * multiplier


def test_provisional_rejects_an_unknown_unit():
    with pytest.raises(DartPayloadError, match="unknown unit"):
        parse_provisional(provisional_xml(unit="달러"))


def test_separate_statements_are_marked_ofs():
    values = parse_provisional(provisional_xml(scope="2. 별도실적내용"))[0]

    assert {value.statement_scope for value in values} == {"OFS"}


def test_amended_filing_reads_the_content_table_not_the_summary(monkeypatch):
    monkeypatch.setattr(dart, "_get", fake_get([document_zip(amended_xml())]))
    disclosure = Disclosure.from_payload(LIST_ROW | {"report_nm": "[기재정정]연결재무제표기준영업(잠정)실적(공정공시)"})

    fetch = COLLECTOR.fetch_provisional(disclosure)

    assert fetch is not None
    revenue = next(value for value in fetch.values if value.metric == "revenue" and value.amount_basis == "period")
    # 정정 요약 표의 999,999가 아니라 본문 표의 171.50 조원이다.
    assert revenue.current_amount == Decimal("171.50") * 1_000_000_000_000
    assert fetch.metadata["sha256"]


def test_missing_document_is_not_a_failure(monkeypatch):
    body = b"<result><status>014</status><message>\xed\x8c\x8c\xec\x9d\xbc\xec\x9d\xb4 \xec\x97\x86\xec\x8a\xb5\xeb\x8b\x88\xeb\x8b\xa4</message></result>"
    monkeypatch.setattr(dart, "_get", fake_get([body]))

    assert COLLECTOR.fetch_provisional(Disclosure.from_payload(LIST_ROW)) is None


def test_financials_pick_accounts_by_id_not_by_name():
    values = parse_financials({"list": FINANCIAL_ROWS}, date(2025, 6, 30), "CFS")

    by_key = {(value.metric, value.amount_basis): value for value in values}
    assert by_key[("net_income", "period")].current_amount == Decimal(5116435000000)
    assert by_key[("net_income", "period")].source_account_name == "반기순이익"
    # 포괄손익계산서 중복 행은 읽지 않는다.
    assert len([value for value in values if value.metric == "net_income"]) == 2


def test_financials_split_period_and_cumulative():
    values = parse_financials({"list": FINANCIAL_ROWS}, date(2025, 6, 30), "CFS")

    revenue = {value.amount_basis: value for value in values if value.metric == "revenue"}
    assert revenue["period"].current_amount == Decimal(74566317000000)
    assert revenue["cumulative"].current_amount == Decimal(153706820000000)
    # 전년 동기는 누계만 온다. 없는 값을 0으로 만들지 않는다.
    assert revenue["period"].prior_year_amount is None
    assert revenue["cumulative"].prior_year_amount == Decimal(145983903000000)
    # 재무제표 금액은 원 단위라 배수를 곱하지 않는다.
    assert revenue["period"].currency == "KRW"


def test_store_disclosures_writes_one_lineage_record_and_keeps_detected_at():
    connection = FakeConnection()
    fetch = DisclosureFetch(
        company="005930",
        corp_code="00126380",
        begin_date=date(2026, 7, 24),
        end_date=date(2026, 7, 30),
        disclosures=(Disclosure.from_payload(LIST_ROW),),
        page_count=1,
        total_count=1,
        started_at=STARTED_AT,
        completed_at=COMPLETED_AT,
    )

    assert COLLECTOR.store_disclosures(connection, fetch, DETECTED_AT) == 1
    statement, parameters = connection.recorded_cursor.calls[0]
    assert "INSERT INTO source_record" in statement
    assert parameters[1:3] == ("dart", "disclosure_list")
    # API 키가 질의 문자열에 있으므로 원본 요청을 남기지 않는다.
    assert parameters[7] is None

    upsert = connection.recorded_cursor.calls[1][1]
    assert upsert[3] == "20260730800077"
    assert upsert[8] == DETECTED_AT
    assert upsert[10] == SOURCE_RECORD_ID


def test_store_earnings_writes_one_row_per_metric(monkeypatch):
    monkeypatch.setattr(dart, "_get", fake_get([document_zip(provisional_xml())]))
    fetch = COLLECTOR.fetch_provisional(Disclosure.from_payload(LIST_ROW))
    assert fetch is not None
    connection = FakeConnection()

    stored = COLLECTOR.store_earnings(connection, fetch)

    upserts = [
        parameters
        for statement, parameters in connection.recorded_cursor.calls
        if "INSERT INTO earnings_fact" in statement
    ]
    assert stored == len(upserts) == 6  # 지표 3 × 기간 기준 2
    assert {row[2] for row in upserts} == {"provisional"}
    assert {row[1] for row in upserts} == {"20260730800077"}
