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
from modules.collectors.indicator.ecb_irs import (
    CONVERGENCE_SERIES,
    COUNTRIES,
    DATAFLOW,
    ENCODING,
    EXPECTED_HEADER,
    KEY_PREFIX,
    KEY_SUFFIX,
    MATURITY_MONTHS,
    OBSERVATION_UPSERT,
    SERIES_UNIT,
    SOURCE_KEY,
    SOURCE_RECORD_INSERT,
    SOURCE_UNIT_NAME,
    ConvergenceSeries,
    EcbIrsObservation,
    EcbIrsPayloadError,
    EcbIrsRequest,
    EcbIrsResponse,
    build_series_key,
    build_url,
    parse_month,
    parse_observations,
    parse_result,
    store_observations,
)

HEADER_LINE = ",".join(EXPECTED_HEADER)

# `KEY_SUFFIX`가 그대로 IR_TYPE..IR_FV_TYPE 일곱 칸이 된다. 나라와 빈도만 따로 채운다.
SUFFIX_DIMENSIONS = tuple(KEY_SUFFIX.split("."))

OBSERVATION_MONTHS = (date(2026, 6, 1), date(2026, 7, 1))

# 실제 응답의 값. 나라마다 한 행씩 오고 그 달의 값이 아직 없으면 칸이 비어 있다.
VALUES = {
    ConvergenceSeries.FR_10Y: "3.2411",
    ConvergenceSeries.IT_10Y: "3.6208",
    ConvergenceSeries.ES_10Y: "3.3057",
}


def data_row(
    series: ConvergenceSeries,
    observation_month: date,
    value: str,
    key: str | None = None,
    time_period: str | None = None,
) -> str:
    return ",".join(
        (
            key if key is not None else f"{DATAFLOW}.{series.series_key}",
            KEY_PREFIX,
            series.country,
            *SUFFIX_DIMENSIONS,
            time_period if time_period is not None else observation_month.strftime("%Y-%m"),
            value,
        )
    )


def csv_bytes(*rows: str, header: str = HEADER_LINE, encoding: str = ENCODING) -> bytes:
    return "\r\n".join((header, *rows)).encode(encoding)


def rows_for(observation_months: tuple[date, ...] = OBSERVATION_MONTHS) -> tuple[str, ...]:
    return tuple(
        data_row(series, observation_month, value)
        for observation_month in observation_months
        for series, value in VALUES.items()
    )


BODY = csv_bytes(*rows_for())
ROW_COUNT = len(OBSERVATION_MONTHS) * len(ConvergenceSeries)

# 아직 공표되지 않은 달만 물으면 ECB는 헤더 줄조차 없이 HTTP 200으로 답한다. 오류가 아니다.
EMPTY_BODY = b""

SOURCE_RECORD_ID = 1
STARTED_AT = datetime(2026, 8, 16, 22, 50, tzinfo=UTC)
COMPLETED_AT = datetime(2026, 8, 16, 22, 50, 1, tzinfo=UTC)


def request_for(start: date = date(2026, 6, 1), end: date = date(2026, 7, 31)) -> EcbIrsRequest:
    return EcbIrsRequest(observation_start=start, observation_end=end)


def response_for(body: bytes = BODY, request: EcbIrsRequest | None = None) -> EcbIrsResponse:
    return EcbIrsResponse(
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


def test_each_series_maps_onto_one_country():
    # 나라가 겹치면 같은 시계열을 두 번 저장한다. `series_id`가 겹치면 멱등 키가 충돌한다.
    assert len(set(COUNTRIES)) == len(COUNTRIES)
    assert len(set(CONVERGENCE_SERIES)) == len(CONVERGENCE_SERIES)


def test_germany_is_not_collected_here():
    # 분데스방크가 같은 값을 일별로 준다. 여기서도 받으면 국가 비교 패널에 독일 선이 둘 그려진다.
    assert "DE" not in COUNTRIES


def test_series_ids_are_readable_and_mark_the_monthly_frequency():
    # DB와 대시보드에 남는 값만 보고 무슨 시계열인지 알 수 있어야 한다. 이 테이블은 대부분
    # 일별이라 끝의 `M`이 없으면 빈도가 다르다는 걸 알 수 없다.
    assert ConvergenceSeries.FR_10Y.value == "FR10YM"
    assert ConvergenceSeries.FR_10Y.country == "FR"
    assert ConvergenceSeries.FR_10Y.country_name == "프랑스"
    assert all(series.value.endswith("M") for series in ConvergenceSeries)


def test_the_maturity_is_a_constant_because_every_country_quotes_one_bond():
    assert MATURITY_MONTHS == 120


def test_build_series_key_asks_for_every_country_at_once():
    key = build_series_key()

    assert key.startswith(f"{KEY_PREFIX}.")
    assert key.endswith(f".{KEY_SUFFIX}")
    # 국가 차원만 `+`로 이어 붙인다. 앞뒤 차원에 `+`가 들어가면 다른 만기·유형까지 딸려 온다.
    assert key.count("+") == len(COUNTRIES) - 1
    for country in COUNTRIES:
        assert country in key


def test_build_url_asks_in_months_and_trims_the_metadata_columns():
    url = build_url(request_for())

    assert url.startswith(f"https://data-api.ecb.europa.eu/service/data/{DATAFLOW}/{build_series_key()}?")
    # 구간은 날짜로 받고 요청에는 달로 바꿔 넘긴다. 다른 수집 DAG와 `modules.period`를 함께 쓴다.
    assert "startPeriod=2026-06" in url
    assert "endPeriod=2026-07" in url
    assert "detail=dataonly" in url
    assert "format=csvdata" in url


def test_parse_result_reads_every_country():
    result = parse_result(BODY, request_for())

    assert len(result.observations) == ROW_COUNT
    first_month = [
        observation for observation in result.observations if observation.observation_date == date(2026, 6, 1)
    ]
    assert {observation.series: observation.value for observation in first_month} == {
        series: Decimal(value) for series, value in VALUES.items()
    }


def test_parse_result_pins_every_observation_to_the_first_of_the_month():
    # 달 중간 날짜가 섞이면 같은 달이 두 행이 되고 그 뒤로는 어느 쪽이 진짜인지 알 수 없다.
    result = parse_result(BODY, request_for())

    assert all(observation.observation_date.day == 1 for observation in result.observations)


def test_parse_result_reports_the_range_the_response_covers():
    result = parse_result(BODY, request_for())

    assert (result.response_first_date, result.response_last_date) == OBSERVATION_MONTHS
    assert result.response_row_count == ROW_COUNT


def test_parse_result_drops_a_month_the_period_starts_after():
    # 구간 판정은 그 달의 1일로 한다. 구간이 달 중간에서 시작하면 그 달은 통째로 빠진다.
    result = parse_result(BODY, request_for(start=date(2026, 6, 15)))

    assert {observation.observation_date for observation in result.observations} == {date(2026, 7, 1)}
    assert result.response_first_date == date(2026, 6, 1)
    assert result.response_row_count == ROW_COUNT


def test_parse_result_reads_an_empty_body_as_a_period_without_values():
    result = parse_result(EMPTY_BODY, request_for())

    assert result.observations == ()
    assert (result.response_first_date, result.response_last_date) == (None, None)
    assert result.response_row_count == 0


@pytest.mark.parametrize("cell", ["", "NaN"])
def test_parse_result_skips_a_month_without_a_value(cell):
    # 그 달의 값이 아직 없다. 결측이지 오류가 아니다.
    body = csv_bytes(data_row(ConvergenceSeries.FR_10Y, date(2026, 6, 1), cell))

    assert parse_observations(body, request_for()) == ()


def test_parse_result_rejects_a_changed_header():
    # ECB가 차원을 추가하면 값이 조용히 옆 칸으로 밀린다. 헤더 대조가 먼저 실패해야 그걸 안다.
    body = csv_bytes(*rows_for(), header=HEADER_LINE + ",OBS_STATUS")

    with pytest.raises(EcbIrsPayloadError, match="header"):
        parse_result(body, request_for())


def test_parse_result_rejects_a_row_with_a_missing_cell():
    body = csv_bytes(data_row(ConvergenceSeries.FR_10Y, date(2026, 6, 1), "3.24").rsplit(",", 1)[0])

    with pytest.raises(EcbIrsPayloadError, match="cells"):
        parse_result(body, request_for())


def test_parse_result_rejects_a_country_we_did_not_ask_for():
    # 독일이 섞여 오면 분데스방크 수집과 같은 나라가 두 번 쌓인다.
    body = csv_bytes(
        ",".join(("IRS.M.DE.L.L40.CI.0000.EUR.N.Z", KEY_PREFIX, "DE", *SUFFIX_DIMENSIONS, "2026-06", "2.51"))
    )

    with pytest.raises(EcbIrsPayloadError, match="unrequested"):
        parse_result(body, request_for())


def test_parse_result_rejects_a_row_from_another_series():
    # `L40`이 잔존 10년 수렴 기준이다. 다른 만기 유형이 섞이면 같은 달에 값이 두 개가 된다.
    body = csv_bytes(
        data_row(
            ConvergenceSeries.FR_10Y,
            date(2026, 6, 1),
            "3.24",
            key="IRS.M.FR.L.L20.CI.0000.EUR.N.Z",
        )
    )

    with pytest.raises(EcbIrsPayloadError, match="expected"):
        parse_result(body, request_for())


@pytest.mark.parametrize("time_period", ["2026-06-15", "2026-W32", "2026", "2026-06-15T00:00:00", "not-a-month"])
def test_parse_result_rejects_a_period_that_is_not_a_month(time_period):
    # `date.fromisoformat`은 `2026-06-15`도 받는다. 빈도가 섞여 들어오면 조용히 엉뚱한
    # 날짜로 저장되므로 모양을 먼저 본다.
    body = csv_bytes(data_row(ConvergenceSeries.FR_10Y, date(2026, 6, 1), "3.24", time_period=time_period))

    with pytest.raises(EcbIrsPayloadError, match="non-monthly"):
        parse_result(body, request_for())


def test_parse_month_rejects_a_month_number_that_is_not_a_month():
    with pytest.raises(EcbIrsPayloadError, match="not a real month"):
        parse_month("2026-13")


def test_parse_month_reads_a_month_as_its_first_day():
    assert parse_month(" 2026-06 ") == date(2026, 6, 1)


@pytest.mark.parametrize("value", ["Infinity", "abc", "1.2.3"])
def test_parse_result_rejects_values_that_are_not_finite_numbers(value):
    body = csv_bytes(data_row(ConvergenceSeries.FR_10Y, date(2026, 6, 1), value))

    with pytest.raises(EcbIrsPayloadError):
        parse_result(body, request_for())


def test_parse_result_rejects_a_body_that_is_not_utf8():
    with pytest.raises(EcbIrsPayloadError, match=ENCODING):
        parse_result(b"\xff\xfe", request_for())


def test_an_observation_must_be_keyed_to_the_first_of_the_month():
    with pytest.raises(ValidationError, match="first day"):
        EcbIrsObservation(
            series=ConvergenceSeries.FR_10Y,
            observation_date=date(2026, 6, 15),
            value=Decimal("3.24"),
        )


def test_request_rejects_a_reversed_period():
    with pytest.raises(ValidationError, match="observation_start"):
        EcbIrsRequest(observation_start=date(2026, 7, 31), observation_end=date(2026, 6, 1))


def test_request_and_response_are_frozen_so_a_retry_cannot_mutate_them():
    response = response_for()

    with pytest.raises(ValidationError):
        response.request.observation_start = date(2026, 1, 1)
    with pytest.raises(ValidationError):
        response.status = 500


def test_response_normalizes_timestamps_to_utc():
    response = EcbIrsResponse(
        request=request_for(),
        body=BODY,
        status=200,
        started_at=datetime(2026, 8, 17, 7, 50, tzinfo=timezone(timedelta(hours=9))),
        completed_at=COMPLETED_AT,
    )

    assert response.started_at == STARTED_AT
    assert response.started_at.utcoffset() == timedelta(0)


def test_response_rejects_naive_timestamps():
    with pytest.raises(ValidationError):
        EcbIrsResponse(
            request=request_for(),
            body=BODY,
            status=200,
            started_at=datetime(2026, 8, 16, 22, 50),  # noqa: DTZ001
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

    # 수집 단위가 시계열이 아니라 조회 한 번이다. `ecb.py`와 provider가 같고 `source_key`로 갈린다.
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
        "observation_start": "2026-06-01",
        "observation_end": "2026-07-31",
        "start_month": "2026-06",
        "end_month": "2026-07",
        "response_first_date": "2026-06-01",
        "response_last_date": "2026-07-01",
        "response_row_count": ROW_COUNT,
        "countries": list(COUNTRIES),
        "series_ids": list(CONVERGENCE_SERIES),
    }


def test_store_does_not_take_the_source_key_of_the_daily_curve_collector():
    # `ecb.py`와 provider가 `ecb`로 같다. 두 수집을 가르는 것은 `source_key` 하나뿐이다.
    connection = FakeConnection()

    store_observations(connection, response_for())

    assert connection.recorded_cursor.calls[0][1][2] == SOURCE_KEY
    assert SOURCE_KEY.startswith(f"{DATAFLOW}.")


def test_store_links_each_observation_to_the_stored_source_record():
    connection = FakeConnection()

    store_observations(connection, response_for())

    provider, series_id, observation_date, value, unit, source_record_id = connection.recorded_cursor.calls[1][1]

    assert (series_id, observation_date, value) == ("FR10YM", date(2026, 6, 1), Decimal("3.2411"))
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

    with pytest.raises(EcbIrsPayloadError):
        store_observations(connection, response_for(body=csv_bytes(*rows_for(), header=HEADER_LINE + ",OBS_STATUS")))

    assert connection.recorded_cursor.calls == []


def test_store_repeats_the_same_upsert_for_a_rerun_of_the_same_period():
    # 월평균은 다음 달에 개정되는 일이 있어 이 갱신이 실제로 쓰인다.
    first, second = FakeConnection(), FakeConnection()

    assert store_observations(first, response_for()) == store_observations(second, response_for())
    assert [statement for statement, _ in first.recorded_cursor.calls] == [
        statement for statement, _ in second.recorded_cursor.calls
    ]
