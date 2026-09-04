"""다중회사 주요계정(`fnlttMultiAcnt`) 수집 테스트.

응답 모양은 2026-09-04에 산업 대표 20사로 실제 호출해 확인했다. 이 파일의 가짜 응답은 그
실측을 줄인 것이다 — 특히 **`account_id`가 없다는 것**과 **`당기순이익(손실)`이 손익계산서
안에서 두 번 온다는 것**이 여기서 지키려는 사실이다.
"""

import json
import re
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Self

import pytest
from pydantic import SecretStr
from sqlalchemy import Table

from apps.models.market import EarningsFact
from modules.collectors.document import dart
from modules.collectors.document.dart import (
    EARNINGS_FACT_UPSERT,
    MULTI_ACCOUNT_METRICS,
    DartCollector,
    DartPayloadError,
    DartStatusError,
    FilingEntity,
    filing_entities,
    parse_multi_accounts,
    recent_report_periods,
)

API_KEY = SecretStr("key")
COLLECTOR = DartCollector(API_KEY)
SOURCE_RECORD_ID = 41

SAMSUNG = FilingEntity(stock_code="005930", name="삼성전자", filing_entity_id="00126380", sector="반도체")
KB = FilingEntity(stock_code="105560", name="KB금융", filing_entity_id="00688996", sector="은행")
ENTITIES = (SAMSUNG, KB)

QUARTER_PERIOD = "2025.01.01 ~ 2025.09.30"


def row(entity: FilingEntity, account_nm: str, thstrm: str, add: str | None = None, **overrides) -> dict:
    payload = {
        "rcept_no": f"2025111400{entity.stock_code}",
        "reprt_code": "11014",
        "bsns_year": "2025",
        "corp_code": entity.filing_entity_id,
        "stock_code": entity.stock_code,
        "fs_div": "CFS",
        "fs_nm": "연결재무제표",
        "sj_div": "IS",
        "sj_nm": "손익계산서",
        "account_nm": account_nm,
        "thstrm_nm": "제 57 기3분기",
        "thstrm_dt": QUARTER_PERIOD,
        "thstrm_amount": thstrm,
        "frmtrm_amount": "1,000,000",
        "ord": "23",
        "currency": "KRW",
    }
    if add is not None:
        payload["thstrm_add_amount"] = add
        payload["frmtrm_add_amount"] = "3,000,000"
    payload.update(overrides)
    return payload


# 실측을 줄인 응답. 삼성전자는 세 지표가 다 있고 KB금융은 매출액이 없다.
PAYLOAD = {
    "status": "000",
    "list": [
        row(SAMSUNG, "매출액", "86,061,747", "239,768,567"),
        row(SAMSUNG, "영업이익", "12,166,062", "23,527,391"),
        row(SAMSUNG, "법인세차감전 순이익", "13,000,000", "25,000,000"),
        row(SAMSUNG, "당기순이익(손실)", "9,000,000", "18,000,000", ord="29"),
        # 같은 이름이 손익계산서 안에서 한 번 더 온다. 값은 같다(전 회사 실측).
        row(SAMSUNG, "당기순이익(손실)", "9,000,000", "18,000,000", ord="61"),
        row(SAMSUNG, "총포괄손익", "9,500,000", "19,000,000"),
        # 포괄손익계산서 블록. 읽지 않는다.
        row(SAMSUNG, "매출액", "999", "999", sj_div="CIS"),
        # 금융사는 영업이익 이름이 다르고 매출액 행이 아예 없다.
        row(KB, "영업이익(손실)", "2,727,566", "5,000,000", ord="59"),
        row(KB, "당기순이익(손실)", "1,916,469", "3,800,000", ord="29"),
        row(KB, "당기순이익(손실)", "1,916,469", "3,800,000", ord="61"),
        row(KB, "이자수익", "7,159,729", "14,000,000"),
    ],
}


class FakeCursor:
    def __init__(self, rows: list[tuple] | None = None) -> None:
        self.calls: list[tuple[str, tuple]] = []
        self._rows = rows or []

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *args: object) -> bool:
        return False

    def execute(self, statement: str, parameters=None) -> None:
        self.calls.append((statement, tuple(parameters or ())))

    def executemany(self, statement: str, parameters) -> None:
        self.calls.extend((statement, tuple(item)) for item in parameters)

    def fetchone(self) -> tuple[int]:
        return (SOURCE_RECORD_ID,)

    def fetchall(self) -> list[tuple]:
        return self._rows


class FakeConnection:
    def __init__(self, rows: list[tuple] | None = None) -> None:
        self.recorded_cursor = FakeCursor(rows)

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


def required_columns(table: Table) -> set[str]:
    return {
        column.name
        for column in table.columns
        if not column.nullable and column.server_default is None and not column.primary_key
    }


def values_of(entries, stock_code: str, scope: str = "CFS") -> dict[tuple[str, str], Decimal]:
    entry = next(entry for entry in entries if entry.stock_code == stock_code and entry.statement_scope == scope)
    return {(value.metric, value.amount_basis): value.current_amount for value in entry.values}


def test_the_metric_table_only_maps_income_statement_lines():
    # 표에 없는 이름은 버린다. 이 셋이 들어오면 순이익이나 영업이익으로 잘못 읽힌다.
    for account_nm in ("법인세차감전 순이익", "총포괄손익", "순이자손익", "영업비용"):
        assert account_nm not in MULTI_ACCOUNT_METRICS

    # 금융·증권사 표기. 이것이 빠지면 삼성생명·미래에셋증권·KB금융의 영업이익이 통째로 사라진다.
    assert MULTI_ACCOUNT_METRICS["영업이익(손실)"] == "operating_profit"
    assert set(MULTI_ACCOUNT_METRICS.values()) == {"revenue", "operating_profit", "net_income"}


def test_parse_splits_period_and_cumulative():
    entries = parse_multi_accounts(PAYLOAD, ENTITIES)
    samsung = values_of(entries, "005930")

    # `thstrm_amount`가 3개월치이고 `thstrm_add_amount`가 사업연도 누계다.
    assert samsung[("revenue", "period")] == Decimal(86061747)
    assert samsung[("revenue", "cumulative")] == Decimal(239768567)


def test_parse_reads_the_period_end_from_the_date_range():
    entries = parse_multi_accounts(PAYLOAD, ENTITIES)
    entry = next(entry for entry in entries if entry.stock_code == "005930")

    # `2025.01.01 ~ 2025.09.30`의 끝 날짜다. 시작 날짜를 읽으면 1분기 행과 겹친다.
    assert {value.period_end for value in entry.values} == {date(2025, 9, 30)}


def test_parse_ignores_the_comprehensive_income_block():
    entries = parse_multi_accounts(PAYLOAD, ENTITIES)
    samsung = values_of(entries, "005930")

    # `sj_div=CIS` 행의 999가 매출액을 덮으면 안 된다.
    assert samsung[("revenue", "period")] == Decimal(86061747)


def test_duplicate_net_income_rows_with_equal_amounts_collapse_to_one():
    entries = parse_multi_accounts(PAYLOAD, ENTITIES)
    entry = next(entry for entry in entries if entry.stock_code == "005930")
    net_income = [value for value in entry.values if value.metric == "net_income"]

    # 손익계산서 안에서 두 번 오지만 값이 같다. 기간 기준마다 한 행이면 된다.
    assert len(net_income) == 2
    assert {value.amount_basis for value in net_income} == {"period", "cumulative"}


def test_duplicate_rows_that_disagree_are_a_payload_error():
    payload = {
        "status": "000",
        "list": [
            row(SAMSUNG, "당기순이익(손실)", "9,000,000", "18,000,000", ord="29"),
            row(SAMSUNG, "당기순이익(손실)", "8,000,000", "18,000,000", ord="61"),
        ],
    }
    # 어느 줄이 맞는지 고를 수 없다. 조용히 뒤엣것으로 덮으면 되짚을 수 없다.
    with pytest.raises(DartPayloadError, match="disagree"):
        parse_multi_accounts(payload, ENTITIES)


def test_a_company_without_revenue_keeps_its_other_metrics():
    entries = parse_multi_accounts(PAYLOAD, ENTITIES)
    kb = values_of(entries, "105560")

    # 삼성생명·KB금융은 매출액 행이 아예 없다. 0으로 채우지 않고 행을 만들지 않는다.
    assert ("revenue", "period") not in kb
    assert kb[("operating_profit", "period")] == Decimal(2727566)
    assert kb[("net_income", "period")] == Decimal(1916469)


def test_an_unrequested_company_is_a_payload_error():
    payload = {"status": "000", "list": [row(SAMSUNG, "매출액", "1", "2", corp_code="00999999")]}
    # 번호를 콤마로 이어 보내는 요청이라, 하나가 잘못 붙으면 남의 실적이 우리 종목코드로 저장된다.
    with pytest.raises(DartPayloadError, match="not requested"):
        parse_multi_accounts(payload, ENTITIES)


def test_a_balance_sheet_style_period_is_rejected():
    payload = {"status": "000", "list": [row(SAMSUNG, "매출액", "1", "2", thstrm_dt="2025.09.30 현재")]}
    # 구간이 없는 값을 기간 종료일로 읽으면 안 된다.
    with pytest.raises(DartPayloadError, match="date range"):
        parse_multi_accounts(payload, ENTITIES)


def test_an_annual_report_has_no_cumulative_column():
    payload = {
        "status": "000",
        "list": [row(SAMSUNG, "매출액", "333,605,938", None, thstrm_dt="2025.01.01 ~ 2025.12.31")],
    }
    entries = parse_multi_accounts(payload, ENTITIES)
    samsung = values_of(entries, "005930")

    # 사업보고서에는 누계 칸이 없다. 연간치가 `period`로만 들어가고 종료일이 12-31이다.
    assert set(samsung) == {("revenue", "period")}
    assert next(iter(entries)).values[0].period_end == date(2025, 12, 31)


def test_multi_account_rows_have_no_account_id():
    entries = parse_multi_accounts(PAYLOAD, ENTITIES)

    # 이 API는 `account_id`를 주지 않는다. 되짚을 근거는 계정명뿐이라 그것을 저장한다.
    for entry in entries:
        for value in entry.values:
            assert value.source_account_id is None
            assert value.source_account_name in MULTI_ACCOUNT_METRICS


def test_no_data_status_is_zero_rows_not_a_failure(monkeypatch):
    monkeypatch.setattr(dart, "_get", fake_get([json.dumps({"status": "013", "message": "없음"}).encode()]))
    fetch = COLLECTOR.fetch_multi_accounts(ENTITIES, 2026, "11012")

    # 정기보고서는 기간 종료 뒤 45일까지 제출한다. 아직 없는 것은 정상이다.
    assert fetch.entries == ()
    assert fetch.row_count == 0
    assert fetch.value_count == 0


def test_other_statuses_are_raised(monkeypatch):
    monkeypatch.setattr(dart, "_get", fake_get([json.dumps({"status": "020", "message": "제한"}).encode()]))
    with pytest.raises(DartStatusError) as error:
        COLLECTOR.fetch_multi_accounts(ENTITIES, 2025, "11014")
    assert error.value.code == "020"


def test_rows_with_no_known_account_fail_instead_of_returning_zero(monkeypatch):
    payload = {"status": "000", "list": [row(SAMSUNG, "영업활동현금흐름", "1", "2")]}
    monkeypatch.setattr(dart, "_get", fake_get([json.dumps(payload).encode()]))

    # 계정명이 바뀐 것이다. 0건으로 두면 매 실행이 같은 자리에서 조용히 끝난다.
    with pytest.raises(DartPayloadError, match="no known accounts"):
        COLLECTOR.fetch_multi_accounts(ENTITIES, 2025, "11014")


def test_one_call_carries_every_company(monkeypatch):
    get = fake_get([json.dumps(PAYLOAD).encode()])
    monkeypatch.setattr(dart, "_get", get)
    fetch = COLLECTOR.fetch_multi_accounts(ENTITIES, 2025, "11014")

    # 회사 번호를 콤마로 이어 보내므로 스무 곳이 호출 하나다.
    assert len(get.calls) == 1
    assert get.calls[0][1]["corp_code"] == "00126380,00688996"
    assert fetch.requested_count == 2
    assert fetch.answered_count == 2


def test_store_writes_one_lineage_record_for_the_whole_call(monkeypatch):
    monkeypatch.setattr(dart, "_get", fake_get([json.dumps(PAYLOAD).encode()]))
    fetch = COLLECTOR.fetch_multi_accounts(ENTITIES, 2025, "11014")
    connection = FakeConnection()

    stored = COLLECTOR.store_multi_accounts(connection, fetch)

    calls = connection.recorded_cursor.calls
    lineage = [call for call in calls if "INSERT INTO source_record" in call[0]]
    facts = [call for call in calls if "INSERT INTO earnings_fact" in call[0]]

    # 응답 하나가 회사 스무 곳을 담는다. 회사마다 계보를 만들면 스무 행이 남는다.
    assert len(lineage) == 1
    assert len(facts) == stored == fetch.value_count
    assert json.loads(lineage[0][1][-1])["report_code"] == "11014"


def test_the_upsert_matches_the_model():
    columns = re.search(r"INSERT INTO \w+ \(([^)]+)\)", EARNINGS_FACT_UPSERT, re.DOTALL)
    assert columns is not None
    names = {name.strip() for name in re.sub(r"--[^\n]*", "", columns.group(1)).split(",") if name.strip()}
    table = EarningsFact.__table__

    assert names <= {column.name for column in table.columns}
    assert required_columns(table) <= names


def test_filing_entities_read_the_master_not_a_constant():
    connection = FakeConnection([("005930", "삼성전자", "00126380", "반도체")])
    entities = filing_entities(connection)

    # 명단이 코드가 아니라 DB에 있다. 대상을 늘릴 때 수집기를 고치지 않는다.
    assert entities == (SAMSUNG,)
    assert "filing_entity_id IS NOT NULL" in connection.recorded_cursor.calls[0][0]
    # 발급 기관이 시장마다 다르다. 이 조건이 없으면 미국 CIK를 DART가 자기 것으로 읽는다.
    assert "market IN ('kospi', 'kosdaq')" in connection.recorded_cursor.calls[0][0]


def test_a_sector_that_is_not_set_stays_none():
    connection = FakeConnection([("005930", "삼성전자", "00126380", None)])
    assert filing_entities(connection)[0].sector is None


@pytest.mark.parametrize(
    ("today", "expected"),
    [
        # 2026-06-30이 지났으므로 2026 반기가 가장 최근이다.
        (date(2026, 9, 4), ((2025, "11014"), (2025, "11011"), (2026, "11013"), (2026, "11012"))),
        # 연초. 2025-12-31이 막 지났고 사업보고서는 아직 없겠지만 기간 자체는 끝났다.
        (date(2026, 1, 2), ((2025, "11013"), (2025, "11012"), (2025, "11014"), (2025, "11011"))),
        # 분기 종료 당일은 아직 끝나지 않은 것으로 본다.
        (date(2026, 6, 30), ((2025, "11012"), (2025, "11014"), (2025, "11011"), (2026, "11013"))),
    ],
)
def test_recent_report_periods_walk_back_from_the_kst_date(today, expected):
    assert recent_report_periods(today) == expected


def test_recent_report_periods_are_in_calendar_order():
    periods = recent_report_periods(date(2026, 9, 4), 4)
    ends = {"11013": 3, "11012": 6, "11014": 9, "11011": 12}
    stamps = [(year, ends[code]) for year, code in periods]

    # 보고서 코드는 시간 순서와 무관하다(11014가 3분기, 11011이 사업보고서). 그래도 훑는
    # 순서는 달력 순이어야 로그를 읽을 수 있다.
    assert stamps == sorted(stamps)


def test_a_fetch_without_entities_is_a_programming_error():
    with pytest.raises(ValueError, match="at least one"):
        COLLECTOR.fetch_multi_accounts((), 2025, "11014")


def test_the_lineage_metadata_does_not_carry_the_api_key(monkeypatch):
    monkeypatch.setattr(dart, "_get", fake_get([json.dumps(PAYLOAD).encode()]))
    fetch = COLLECTOR.fetch_multi_accounts(ENTITIES, 2025, "11014")
    connection = FakeConnection()
    COLLECTOR.store_multi_accounts(connection, fetch)

    recorded = json.dumps(
        [list(map(str, call[1])) for call in connection.recorded_cursor.calls],
        ensure_ascii=False,
    )
    assert API_KEY.get_secret_value() not in recorded


def test_store_keeps_a_record_even_when_the_period_is_empty(monkeypatch):
    monkeypatch.setattr(dart, "_get", fake_get([json.dumps({"status": "013", "message": "없음"}).encode()]))
    fetch = COLLECTOR.fetch_multi_accounts(ENTITIES, 2026, "11012")
    connection = FakeConnection()

    assert COLLECTOR.store_multi_accounts(connection, fetch) == 0

    # 조회했지만 아직 없는 기간과 아직 조회하지 않은 기간이 구분돼야 한다.
    lineage = [call for call in connection.recorded_cursor.calls if "INSERT INTO source_record" in call[0]]
    assert len(lineage) == 1


def test_started_and_completed_are_utc(monkeypatch):
    monkeypatch.setattr(dart, "_get", fake_get([json.dumps(PAYLOAD).encode()]))
    fetch = COLLECTOR.fetch_multi_accounts(ENTITIES, 2025, "11014")

    assert fetch.started_at.tzinfo is not None
    assert fetch.started_at.astimezone(UTC) <= fetch.completed_at.astimezone(UTC)
    assert isinstance(fetch.completed_at, datetime)
