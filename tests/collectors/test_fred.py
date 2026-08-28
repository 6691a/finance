import json
import re
from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Self
from urllib.error import HTTPError

import pytest
from pydantic import SecretStr, ValidationError
from sqlalchemy import Table

from apps.models.market import IndicatorObservation
from apps.models.raw import SourceRecord
from modules.collectors.indicator.fred import (
    BALANCE_SHEET_SERIES,
    MACRO_SERIES,
    OBSERVATION_UPSERT,
    POLICY_RATE_SERIES,
    SOURCE_RECORD_INSERT,
    TREASURY_SERIES,
    FredCollector,
    FredHTTPError,
    FredPayloadError,
    FredRequest,
    FredResponse,
    FredSeries,
    parse_observations,
)

PAYLOAD = json.dumps(
    {
        "observations": [
            {"realtime_start": "2026-08-03", "realtime_end": "2026-08-03", "date": "2026-08-03", "value": "4.25"},
            {"realtime_start": "2026-08-04", "realtime_end": "2026-08-04", "date": "2026-08-04", "value": "."},
        ]
    }
).encode("utf-8")

SOURCE_RECORD_ID = 1
API_KEY = SecretStr("a" * 32)
COLLECTOR = FredCollector(API_KEY)
STARTED_AT = datetime(2026, 8, 4, 22, 30, tzinfo=UTC)
COMPLETED_AT = datetime(2026, 8, 4, 22, 30, 1, tzinfo=UTC)


def request_for(series_id: str = "DGS10") -> FredRequest:
    return FredRequest(
        series_id=series_id,
        observation_start=date(2026, 7, 29),
        observation_end=date(2026, 8, 4),
    )


def response_for(series_id: str = "DGS10", body: bytes = PAYLOAD) -> FredResponse:
    return FredResponse(
        request=request_for(series_id),
        body=body,
        status=200,
        started_at=STARTED_AT,
        completed_at=COMPLETED_AT,
    )


class FakeCursor:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple]] = []

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *args: object) -> bool:
        return False

    def execute(self, statement: str, parameters: tuple) -> None:
        self.calls.append((statement, parameters))

    def fetchone(self) -> tuple[int]:
        return (SOURCE_RECORD_ID,)


class FakeConnection:
    def __init__(self) -> None:
        self.recorded_cursor = FakeCursor()

    def cursor(self) -> FakeCursor:
        return self.recorded_cursor


def inserted_columns(statement: str) -> tuple[str, ...]:
    columns = re.search(r"INSERT INTO \w+ \(([^)]+)\)", statement, re.DOTALL)
    assert columns is not None
    # 쿼리 파일은 컬럼마다 `-- 설명`을 달아 둔다. 이름만 남기려면 먼저 주석을 걷어낸다.
    names = re.sub(r"--[^\n]*", "", columns.group(1))
    return tuple(name.strip() for name in names.split(",") if name.strip())


def placeholder_count(statement: str) -> int:
    values = re.search(r"VALUES \(([^)]+)\)", statement, re.DOTALL)
    assert values is not None
    return values.group(1).count("%s")


def required_columns(table: Table) -> set[str]:
    """DB가 채워 주지 않는 NOT NULL 컬럼. INSERT가 하나라도 빠뜨리면 런타임에 터진다."""
    return {
        column.name
        for column in table.columns
        if not column.nullable and column.server_default is None and not column.primary_key
    }


def test_source_record_insert_matches_the_model():
    # 수집기는 ORM 없이 문자열 SQL을 쓴다. 컬럼 이름이 어긋나면 실행 시점에야 드러나므로
    # 모델 metadata와 여기서 맞춰 둔다.
    table = SourceRecord.__table__
    columns = inserted_columns(SOURCE_RECORD_INSERT)

    assert set(columns) <= {column.name for column in table.columns}
    assert required_columns(table) <= set(columns)
    assert placeholder_count(SOURCE_RECORD_INSERT) == len(columns)
    assert "RETURNING id" in SOURCE_RECORD_INSERT


def test_observation_upsert_matches_the_model_and_its_natural_key():
    table = IndicatorObservation.__table__
    columns = inserted_columns(OBSERVATION_UPSERT)

    assert set(columns) <= {column.name for column in table.columns}
    assert required_columns(table) <= set(columns)
    assert placeholder_count(OBSERVATION_UPSERT) == len(columns)

    natural_key = next(
        tuple(column.name for column in constraint.columns)
        for constraint in table.constraints
        if constraint.name == "uq_indicator_observation_natural_key"
    )
    assert f"ON CONFLICT ({', '.join(natural_key)}) DO UPDATE" in OBSERVATION_UPSERT


def test_treasury_series_cover_short_and_long_maturities():
    assert "DGS10" in TREASURY_SERIES
    assert len(set(TREASURY_SERIES)) == len(TREASURY_SERIES)


def test_the_series_groups_do_not_overlap():
    """DAG이 넷으로 나뉘어 각각 목록을 돈다. 겹치면 같은 계열을 하루에 두 번 받는다.

    **덮는지도 함께 본다.** 목록이 전부 `kind`로 걸러지므로, 새 종류를 더하면서 목록을
    안 만들면 그 계열이 조용히 어느 DAG에도 안 실린다. 전에 거시 목록이 `is_monthly`로
    걸렀는데 월간 잔액(`JPASSETS_M`)이 들어오면서 그 필터가 거짓이 됐다.
    """
    groups = (set(TREASURY_SERIES), set(MACRO_SERIES), set(POLICY_RATE_SERIES), set(BALANCE_SHEET_SERIES))

    assert set.intersection(*groups) == set()
    assert sum(len(group) for group in groups) == len(set.union(*groups))
    assert set.union(*groups) == {series.value for series in FredSeries}


def test_policy_rate_series_are_daily_and_marked_as_such():
    # 정책금리는 중앙은행이 정하는 값이다. 시장이 만드는 값과 한 `kind`에 두면 시장금리 패널이
    # 정책금리 계단을 함께 그린다.
    assert set(POLICY_RATE_SERIES) == {"DFEDTARU", "EADFR"}
    assert all(not FredSeries(series).is_monthly for series in POLICY_RATE_SERIES)
    assert FredSeries.EADFR.fred_id == "ECBDFR"
    assert FredSeries.DFEDTARU.unit == "Percent"


def test_each_series_declares_its_own_unit():
    """단위를 모듈 상수 하나로 두면 국채 아닌 계열에 거짓이 실린다.

    observations 응답에는 단위가 없어서(`series` 엔드포인트에만 있다) Enum에 적은 값이
    유일한 근거다.
    """
    assert FredSeries.DGS10.unit == "Percent"
    assert FredSeries.CPI_M.unit == "Index 1982-1984=100"
    assert FredSeries.RETAIL_SALES_M.unit == "Millions of Dollars"
    assert FredSeries.UNEMPLOYMENT_M.unit == "Percent"
    assert FredSeries.NONFARM_PAYROLL_M.unit == "Thousands of Persons"
    # 단위가 계열마다 다르다는 것 자체가 계약이다.
    assert len({series.unit for series in FredSeries}) > 1


def test_monthly_series_are_marked_in_the_stored_identifier():
    # 한 테이블에 일별과 월간이 섞여 있어 표시가 없으면 조회하는 쪽이 주기를 구분할 수 없다.
    assert all(series.value.endswith("_M") for series in FredSeries if series.is_monthly)
    assert not any(series.is_monthly for series in FredSeries if series.kind == "government_bond")


def test_the_request_uses_the_provider_coordinate_not_the_stored_identifier():
    # `CPIAUCSL`은 DB만 보고 무슨 값인지 알 수 없어 읽히는 이름으로 저장한다. 요청은 좌표로 간다.
    assert FredSeries.CPI_M.fred_id == "CPIAUCSL"
    url = COLLECTOR.build_url(request_for("CPI_M"))

    assert "series_id=CPIAUCSL" in url
    assert "series_id=CPI_M" not in url


def test_build_url_requests_one_series_as_json_for_the_given_period():
    url = COLLECTOR.build_url(request_for())

    assert "series_id=DGS10" in url
    assert "file_type=json" in url
    assert "observation_start=2026-07-29" in url
    assert "observation_end=2026-08-04" in url


def test_request_rejects_unknown_series_and_reversed_period():
    with pytest.raises(ValidationError, match="Unknown FRED series"):
        request_for("GDP")

    with pytest.raises(ValidationError, match="observation_start"):
        FredRequest(series_id="DGS10", observation_start=date(2026, 8, 4), observation_end=date(2026, 7, 29))


def test_the_collector_requires_an_api_key():
    with pytest.raises(ValueError, match="API key"):
        FredCollector(SecretStr(""))


def test_request_and_response_are_frozen_so_a_retry_cannot_mutate_them():
    response = response_for()

    with pytest.raises(ValidationError):
        response.request.series_id = "DGS2"
    with pytest.raises(ValidationError):
        response.status = 500


def test_response_normalizes_timestamps_to_utc():
    response = FredResponse(
        request=request_for(),
        body=PAYLOAD,
        status=200,
        started_at=datetime(2026, 8, 5, 7, 30, tzinfo=timezone(timedelta(hours=9))),
        completed_at=COMPLETED_AT,
    )

    assert response.started_at == STARTED_AT
    assert response.started_at.utcoffset() == timedelta(0)


def test_response_rejects_naive_timestamps():
    with pytest.raises(ValidationError):
        FredResponse(
            request=request_for(),
            body=PAYLOAD,
            status=200,
            started_at=datetime(2026, 8, 4, 22, 30),  # noqa: DTZ001
            completed_at=COMPLETED_AT,
        )


def test_parse_observations_skips_the_fred_missing_marker():
    observations = parse_observations(PAYLOAD)

    assert len(observations) == 1
    assert observations[0].observation_date == date(2026, 8, 3)
    assert observations[0].value == Decimal("4.25")


def test_parse_observations_rejects_a_response_without_observations():
    with pytest.raises(FredPayloadError):
        parse_observations(b'{"error_code": 400}')

    with pytest.raises(FredPayloadError):
        parse_observations(b"not json")


def test_parse_observations_rejects_a_broken_item_instead_of_storing_a_partial_result():
    body = json.dumps({"observations": [{"date": "2026-08-03", "value": "4.25"}, {"date": "nope"}]}).encode("utf-8")

    with pytest.raises(FredPayloadError):
        parse_observations(body)


@pytest.mark.parametrize("value", ["NaN", "Infinity"])
def test_parse_observations_rejects_non_finite_values(value):
    body = json.dumps({"observations": [{"date": "2026-08-03", "value": value}]}).encode("utf-8")

    with pytest.raises(FredPayloadError):
        parse_observations(body)


def test_fetch_preserves_retry_after_for_rate_limit(monkeypatch):
    def raise_429(url, timeout):
        raise HTTPError(url, 429, "rate limited", {"Retry-After": "60"}, None)

    monkeypatch.setattr("modules.collectors.indicator.fred.urlopen", raise_429)

    with pytest.raises(FredHTTPError) as raised:
        COLLECTOR.fetch_series(request_for())

    assert raised.value.status == 429
    assert raised.value.retry_after == "60"
    assert API_KEY.get_secret_value() not in str(raised.value)


def test_store_writes_the_raw_response_once_and_upserts_only_valid_observations():
    connection = FakeConnection()

    assert COLLECTOR.store_observations(connection, response_for()) == 1

    statements = [statement for statement, _ in connection.recorded_cursor.calls]
    assert len(statements) == 2
    assert "INSERT INTO source_record" in statements[0]
    assert "INSERT INTO indicator_observation" in statements[1]
    assert "ON CONFLICT (provider, series_id, observation_date) DO UPDATE" in statements[1]


def test_store_records_lineage_and_collection_state_on_the_source_record():
    connection = FakeConnection()

    COLLECTOR.store_observations(connection, response_for())

    source_type, source, source_key, started_at, completed_at, status, record_count, payload, metadata = (
        connection.recorded_cursor.calls[0][1]
    )

    assert (source_type, source, source_key, status) == ("api", "fred", "DGS10", "succeeded")
    assert (started_at, completed_at) == (STARTED_AT, COMPLETED_AT)
    assert started_at.tzinfo is not None
    assert record_count == 1
    assert json.loads(payload) == json.loads(PAYLOAD)
    assert json.loads(metadata) == {
        "http_status": 200,
        "observation_start": "2026-07-29",
        "observation_end": "2026-08-04",
    }


def test_store_links_each_observation_to_the_stored_source_record():
    connection = FakeConnection()

    COLLECTOR.store_observations(connection, response_for())

    provider, series_id, observation_date, value, unit, source_record_id = connection.recorded_cursor.calls[1][1]

    assert (series_id, observation_date, value, unit) == (
        "DGS10",
        date(2026, 8, 3),
        Decimal("4.25"),
        FredSeries.DGS10.unit,
    )
    assert source_record_id == SOURCE_RECORD_ID
    # 멱등 키가 제공처까지 포함하므로 관측값의 provider는 원본 레코드의 source와 같아야 한다.
    assert provider == connection.recorded_cursor.calls[0][1][1]


def test_store_writes_nothing_when_the_payload_is_broken():
    connection = FakeConnection()

    with pytest.raises(FredPayloadError):
        COLLECTOR.store_observations(connection, response_for(body=b'{"observations": "nope"}'))

    assert connection.recorded_cursor.calls == []


def test_store_repeats_the_same_upsert_for_a_rerun_of_the_same_period():
    first, second = FakeConnection(), FakeConnection()

    assert COLLECTOR.store_observations(first, response_for()) == COLLECTOR.store_observations(second, response_for()) == 1
    assert [statement for statement, _ in first.recorded_cursor.calls] == [
        statement for statement, _ in second.recorded_cursor.calls
    ]


def test_balance_sheet_series_carry_their_own_currency_unit():
    """한 통화로 환산하지 않는다. 환산하면 환율 변동이 자산 증감으로 위장한다."""
    units = {series.value: series.unit for series in FredSeries if series.kind == "balance_sheet"}

    assert units == {
        "FEDASSETS_W": "Millions of Dollars",
        "EAASSETS_W": "Millions of Euros",
        "JPASSETS_M": "Hundred Millions of Yen",
    }
    # 단위가 셋이라 잔액을 한 축에 놓을 수 없다. 비교는 증가율로 한다.
    assert len(set(units.values())) == len(units)


def test_balance_sheet_series_declare_their_frequency_in_the_id():
    # 한 테이블에 일별·주간·월간이 섞여 있어 표시가 없으면 조회하는 쪽이 주기를 구분할 수 없다.
    assert set(BALANCE_SHEET_SERIES) == {"FEDASSETS_W", "EAASSETS_W", "JPASSETS_M"}
    assert FredSeries.JP_ASSETS_M.is_monthly
    assert not FredSeries.FED_ASSETS_W.is_monthly


def test_the_monthly_balance_sheet_series_is_not_in_the_macro_dag():
    """거시 목록을 `is_monthly`로 거르던 때는 월간 잔액이 그 DAG에 조용히 실렸다."""
    assert "JPASSETS_M" not in MACRO_SERIES
