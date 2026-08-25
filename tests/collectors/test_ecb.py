import json
import re
from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Self

import pytest
from pydantic import ValidationError
from sqlalchemy import Table

from apps.models.market import IndicatorObservation
from apps.models.raw import SourceRecord
from modules.collectors.indicator.ecb import (
    DATA_TYPES,
    ENCODING,
    EURO_YIELD_SERIES,
    EXPECTED_HEADER,
    KEY_PREFIX,
    OBSERVATION_UPSERT,
    SERIES_UNIT,
    SOURCE_KEY,
    SOURCE_RECORD_INSERT,
    SOURCE_UNIT_NAME,
    EcbPayloadError,
    EcbRequest,
    EcbResponse,
    EuroYieldSeries,
    build_series_key,
    build_url,
    parse_curve,
    parse_observations,
    store_observations,
)

HEADER_LINE = ",".join(EXPECTED_HEADER)

# 키에서 만기를 뺀 차원 값. 응답의 가운데 여섯 칸이다.
DIMENSIONS = ("B", "U2", "EUR", "4F", "G_N_A", "SV_C_YM")

OBSERVATION_DATES = (date(2026, 8, 4), date(2026, 8, 5))

# 실제 응답의 값. 만기마다 한 행씩 오고 값이 없는 날은 행 자체가 없다.
VALUES = {
    EuroYieldSeries.EA_3M: "2.2679194997",
    EuroYieldSeries.EA_6M: "2.402376861",
    EuroYieldSeries.EA_1Y: "2.5609984958",
    EuroYieldSeries.EA_2Y: "2.6703142615",
    EuroYieldSeries.EA_3Y: "2.7045552363",
    EuroYieldSeries.EA_5Y: "2.7949326505",
    EuroYieldSeries.EA_7Y: "2.9320995712",
    EuroYieldSeries.EA_10Y: "3.1466241785",
    EuroYieldSeries.EA_15Y: "3.416330268",
    EuroYieldSeries.EA_20Y: "3.5618360878",
    EuroYieldSeries.EA_30Y: "3.597391338",
}


def data_row(
    series: EuroYieldSeries,
    observation_date: date,
    value: str,
    key: str | None = None,
) -> str:
    return ",".join(
        (
            key if key is not None else f"{SOURCE_KEY}.{series.data_type}",
            *DIMENSIONS,
            series.data_type,
            observation_date.isoformat(),
            value,
        )
    )


def csv_bytes(*rows: str, header: str = HEADER_LINE, encoding: str = ENCODING) -> bytes:
    return "\r\n".join((header, *rows)).encode(encoding)


def rows_for(observation_dates: tuple[date, ...] = OBSERVATION_DATES) -> tuple[str, ...]:
    return tuple(
        data_row(series, observation_date, value)
        for observation_date in observation_dates
        for series, value in VALUES.items()
    )


BODY = csv_bytes(*rows_for())
ROW_COUNT = len(OBSERVATION_DATES) * len(EuroYieldSeries)

# 요청 구간이 전부 휴장이면 ECB는 헤더 줄조차 없이 HTTP 200으로 답한다. 오류가 아니다.
EMPTY_BODY = b""

SOURCE_RECORD_ID = 1
STARTED_AT = datetime(2026, 8, 6, 23, 50, tzinfo=UTC)
COMPLETED_AT = datetime(2026, 8, 6, 23, 50, 1, tzinfo=UTC)


def request_for(start: date = date(2026, 8, 3), end: date = date(2026, 8, 6)) -> EcbRequest:
    return EcbRequest(observation_start=start, observation_end=end)


def response_for(body: bytes = BODY, request: EcbRequest | None = None) -> EcbResponse:
    return EcbResponse(
        request=request or request_for(),
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


def test_each_series_maps_onto_one_maturity_dimension():
    # 만기 차원을 잘못 적으면 그 시계열만 조용히 사라진다. 만기가 겹치면 비교 패널이 같은 줄을 두 번 그린다.
    assert len(set(DATA_TYPES)) == len(DATA_TYPES)
    assert len(set(EURO_YIELD_SERIES)) == len(EURO_YIELD_SERIES)
    assert len({series.maturity_months for series in EuroYieldSeries}) == len(EuroYieldSeries)
    assert all(series.maturity_months > 0 for series in EuroYieldSeries)


def test_series_ids_are_readable():
    # DB와 대시보드에 남는 값만 보고 무슨 시계열인지 알 수 있어야 한다.
    assert EuroYieldSeries.EA_10Y.value == "EA10Y"
    assert EuroYieldSeries.EA_10Y.data_type == "SR_10Y"
    assert EuroYieldSeries.EA_10Y.label == "유로 지역 10년물"
    assert EuroYieldSeries.EA_10Y.maturity_months == 120


def test_build_series_key_asks_for_every_maturity_at_once():
    key = build_series_key()

    assert key.startswith(f"{KEY_PREFIX}.")
    # 마지막 차원만 `+`로 이어 붙인다. 앞 차원에 `+`가 들어가면 다른 등급의 곡선까지 딸려 온다.
    assert key.count("+") == len(DATA_TYPES) - 1
    for data_type in DATA_TYPES:
        assert data_type in key


def test_build_url_keeps_the_period_and_trims_the_metadata_columns():
    url = build_url(request_for())

    assert url.startswith(f"https://data-api.ecb.europa.eu/service/data/YC/{build_series_key()}?")
    assert "startPeriod=2026-08-03" in url
    assert "endPeriod=2026-08-06" in url
    # `detail`을 빼면 제목과 각주까지 딸려 와 한 행이 1KB를 넘는다.
    assert "detail=dataonly" in url
    assert "format=csvdata" in url


def test_parse_curve_reads_every_maturity():
    curve = parse_curve(BODY, request_for())

    assert len(curve.observations) == ROW_COUNT
    first_day = [observation for observation in curve.observations if observation.observation_date == date(2026, 8, 4)]
    assert {observation.series: observation.value for observation in first_day} == {
        series: Decimal(value) for series, value in VALUES.items()
    }


def test_parse_curve_reports_the_range_the_response_covers():
    curve = parse_curve(BODY, request_for())

    assert (curve.response_first_date, curve.response_last_date) == OBSERVATION_DATES
    assert curve.response_row_count == ROW_COUNT


def test_parse_curve_keeps_rows_outside_the_period_out_of_the_observations():
    body = csv_bytes(*rows_for((date(2026, 7, 31), date(2026, 8, 4))))

    curve = parse_curve(body, request_for(start=date(2026, 8, 1)))

    assert {observation.observation_date for observation in curve.observations} == {date(2026, 8, 4)}
    assert curve.response_first_date == date(2026, 7, 31)


def test_parse_curve_reads_an_empty_body_as_a_period_without_values():
    # 요청 구간이 전부 휴장이면 헤더 줄조차 없이 온다. 오류가 아니다.
    curve = parse_curve(EMPTY_BODY, request_for())

    assert curve.observations == ()
    assert (curve.response_first_date, curve.response_last_date) == (None, None)
    assert curve.response_row_count == 0


def test_parse_curve_skips_a_cell_without_a_quote():
    body = csv_bytes(data_row(EuroYieldSeries.EA_30Y, date(2026, 8, 4), ""), *rows_for())

    observations = parse_observations(body, request_for())

    assert len(observations) == ROW_COUNT


def test_parse_curve_rejects_a_changed_header():
    # ECB가 차원을 추가하면 값이 조용히 옆 칸으로 밀린다. 헤더 대조가 먼저 실패해야 그걸 안다.
    body = csv_bytes(*rows_for(), header=HEADER_LINE + ",OBS_STATUS")

    with pytest.raises(EcbPayloadError, match="header"):
        parse_curve(body, request_for())


def test_parse_curve_rejects_a_row_with_a_missing_cell():
    body = csv_bytes(f"{SOURCE_KEY}.SR_10Y,B,U2,EUR,4F,G_N_A,SV_C_YM,SR_10Y,2026-08-04")

    with pytest.raises(EcbPayloadError, match="cells"):
        parse_curve(body, request_for())


def test_parse_curve_rejects_a_maturity_we_did_not_ask_for():
    body = csv_bytes(f"{SOURCE_KEY}.SR_9M,B,U2,EUR,4F,G_N_A,SV_C_YM,SR_9M,2026-08-04,2.45")

    with pytest.raises(EcbPayloadError, match="unrequested"):
        parse_curve(body, request_for())


def test_parse_curve_rejects_a_row_from_another_curve():
    # `G_N_C`는 전체 발행자 곡선이다. AAA 곡선과 섞이면 같은 만기에 값이 두 개가 된다.
    body = csv_bytes(
        data_row(
            EuroYieldSeries.EA_10Y,
            date(2026, 8, 4),
            "3.20",
            key="YC.B.U2.EUR.4F.G_N_C.SV_C_YM.SR_10Y",
        )
    )

    with pytest.raises(EcbPayloadError, match="expected"):
        parse_curve(body, request_for())


@pytest.mark.parametrize("time_period", ["2026-W32", "2026-08", "2026", "2026-08-04T00:00:00", "not-a-date"])
def test_parse_curve_rejects_a_time_period_that_is_not_a_calendar_day(time_period):
    # `date.fromisoformat`은 `2026-W32`도 받아 그 주의 월요일로 바꾼다. 주간·월간 빈도의 값이
    # 섞여 들어오면 조용히 엉뚱한 날짜로 저장되므로 모양을 먼저 본다.
    body = csv_bytes(f"{SOURCE_KEY}.SR_10Y,B,U2,EUR,4F,G_N_A,SV_C_YM,SR_10Y,{time_period},3.14")

    with pytest.raises(EcbPayloadError, match="calendar-day"):
        parse_curve(body, request_for())


@pytest.mark.parametrize("value", ["Infinity", "abc", "1.2.3"])
def test_parse_curve_rejects_values_that_are_not_finite_numbers(value):
    body = csv_bytes(data_row(EuroYieldSeries.EA_10Y, date(2026, 8, 4), value))

    with pytest.raises(EcbPayloadError):
        parse_curve(body, request_for())


def test_request_rejects_a_reversed_period():
    with pytest.raises(ValidationError, match="observation_start"):
        EcbRequest(observation_start=date(2026, 8, 6), observation_end=date(2026, 8, 3))


def test_request_and_response_are_frozen_so_a_retry_cannot_mutate_them():
    response = response_for()

    with pytest.raises(ValidationError):
        response.request.observation_start = date(2026, 1, 1)
    with pytest.raises(ValidationError):
        response.status = 500


def test_response_normalizes_timestamps_to_utc():
    response = EcbResponse(
        request=request_for(),
        body=BODY,
        status=200,
        started_at=datetime(2026, 8, 7, 8, 50, tzinfo=timezone(timedelta(hours=9))),
        completed_at=COMPLETED_AT,
    )

    assert response.started_at == STARTED_AT
    assert response.started_at.utcoffset() == timedelta(0)


def test_response_rejects_naive_timestamps():
    with pytest.raises(ValidationError):
        EcbResponse(
            request=request_for(),
            body=BODY,
            status=200,
            started_at=datetime(2026, 8, 6, 23, 50),  # noqa: DTZ001
            completed_at=COMPLETED_AT,
        )


def test_store_writes_the_query_once_and_upserts_every_observation():
    connection = FakeConnection()

    assert store_observations(connection, response_for()) == ROW_COUNT

    statements = [statement for statement, _ in connection.recorded_cursor.calls]
    assert len(statements) == 1 + ROW_COUNT
    assert "INSERT INTO source_record" in statements[0]
    assert "INSERT INTO indicator_observation" in statements[1]
    assert "ON CONFLICT (provider, series_id, observation_date) DO UPDATE" in statements[1]


def test_store_records_the_query_as_the_collection_unit():
    connection = FakeConnection()

    store_observations(connection, response_for())

    source_type, source, source_key, started_at, completed_at, status, record_count, payload, metadata = (
        connection.recorded_cursor.calls[0][1]
    )

    # 수집 단위가 시계열이 아니라 조회 한 번이다. fred, ecos와 갈리는 지점이고 mof, boe와 같다.
    assert (source_type, source, source_key, status) == ("api", "ecb", SOURCE_KEY, "succeeded")
    assert (started_at, completed_at) == (STARTED_AT, COMPLETED_AT)
    assert started_at.tzinfo is not None
    assert record_count == ROW_COUNT
    # 원본이 CSV라 jsonb 컬럼에 넣지 않는다. 대신 어느 구간을 물어 어느 구간이 왔는지를 metadata가 남긴다.
    assert payload is None
    assert json.loads(metadata) == {
        "http_status": 200,
        "url": build_url(request_for()),
        "series_key": build_series_key(),
        "source_unit_name": SOURCE_UNIT_NAME,
        "observation_start": "2026-08-03",
        "observation_end": "2026-08-06",
        "response_first_date": "2026-08-04",
        "response_last_date": "2026-08-05",
        "response_row_count": ROW_COUNT,
        "data_types": list(DATA_TYPES),
        "series_ids": list(EURO_YIELD_SERIES),
    }


def test_store_links_each_observation_to_the_stored_source_record():
    connection = FakeConnection()

    store_observations(connection, response_for())

    provider, series_id, observation_date, value, unit, source_record_id = connection.recorded_cursor.calls[1][1]

    assert (series_id, observation_date, value) == ("EA3M", date(2026, 8, 4), Decimal("2.2679194997"))
    # 저장 단위는 제공처 표기 `PCPA`가 아니라 다른 수집기와 맞춘 표기다. 여러 나라 금리를 같이 조회한다.
    assert unit == SERIES_UNIT != SOURCE_UNIT_NAME
    assert source_record_id == SOURCE_RECORD_ID
    # 멱등 키가 제공처까지 포함하므로 관측값의 provider는 원본 레코드의 source와 같아야 한다.
    assert provider == connection.recorded_cursor.calls[0][1][1]


def test_store_keeps_the_source_record_for_a_period_without_observations():
    # 조회했지만 값이 없는 구간과 아직 조회하지 않은 구간은 구분돼야 한다.
    connection = FakeConnection()

    assert store_observations(connection, response_for(body=EMPTY_BODY)) == 0

    statements = [statement for statement, _ in connection.recorded_cursor.calls]
    assert len(statements) == 1
    assert "INSERT INTO source_record" in statements[0]
    assert connection.recorded_cursor.calls[0][1][6] == 0
    assert json.loads(connection.recorded_cursor.calls[0][1][8])["response_first_date"] is None


def test_store_writes_nothing_when_the_response_is_broken():
    connection = FakeConnection()

    with pytest.raises(EcbPayloadError):
        store_observations(connection, response_for(body=csv_bytes(*rows_for(), header=HEADER_LINE + ",OBS_STATUS")))

    assert connection.recorded_cursor.calls == []


def test_store_repeats_the_same_upsert_for_a_rerun_of_the_same_period():
    first, second = FakeConnection(), FakeConnection()

    assert store_observations(first, response_for()) == store_observations(second, response_for())
    assert [statement for statement, _ in first.recorded_cursor.calls] == [
        statement for statement, _ in second.recorded_cursor.calls
    ]
