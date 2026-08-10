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
from modules.collectors.bbk import (
    BUND_SERIES,
    DATASET,
    ENCODING,
    KEY_PREFIX,
    MATURITY_CODES,
    OBSERVATION_UPSERT,
    SERIES_UNIT,
    SOURCE_KEY,
    SOURCE_RECORD_INSERT,
    SOURCE_UNIT_NAME,
    BbkPayloadError,
    BbkRequest,
    BbkResponse,
    BundSeries,
    build_series_key,
    build_url,
    parse_curve,
    parse_observations,
    store_observations,
)

OBSERVATION_DATES = (date(2026, 8, 5), date(2026, 8, 6))

# 실제 응답의 값. 만기가 열로 늘어서고 값 열마다 `_FLAGS` 열이 따라붙는다.
VALUES = {
    BundSeries.DE_1Y: ("2.61", "2.62"),
    BundSeries.DE_2Y: ("2.70", "2.71"),
    BundSeries.DE_3Y: ("2.74", "2.75"),
    BundSeries.DE_5Y: ("2.80", "2.82"),
    BundSeries.DE_7Y: ("2.95", "2.96"),
    BundSeries.DE_10Y: ("3.16", "3.17"),
    BundSeries.DE_15Y: ("3.44", "3.45"),
    BundSeries.DE_20Y: ("3.60", "3.61"),
    BundSeries.DE_30Y: ("3.72", "3.73"),
}
ROW_COUNT = len(OBSERVATION_DATES) * len(BundSeries)


def header_line(series: tuple[BundSeries, ...] = tuple(BundSeries)) -> str:
    columns = ['""']
    for member in series:
        columns.extend((member.column_name, f"{member.column_name}_FLAGS"))
    return ",".join(columns)


# 데이터 앞에 붙는 메타데이터 줄. 첫 칸이 날짜가 아니므로 건너뛴다.
def metadata_lines(series: tuple[BundSeries, ...] = tuple(BundSeries)) -> tuple[str, ...]:
    padding = "," * (2 * len(series))
    return (
        f'"",Term structure of interest rates on listed Federal securities (method by Svensson){padding}',
        f"Decimals,2{padding[:-1]}",
        f"last update,2026-08-06 12:48:47{padding[:-1]}",
    )


def data_line(index: int, values: dict[BundSeries, tuple[str, ...]] = VALUES) -> str:
    cells = [OBSERVATION_DATES[index].isoformat()]
    for member in BundSeries:
        cells.extend((values[member][index], ""))
    return ",".join(cells)


def csv_bytes(*lines: str, header: str | None = None, encoding: str = ENCODING) -> bytes:
    body = "\r\n".join((header if header is not None else header_line(), *lines))
    return body.encode(encoding)


BODY = csv_bytes(*metadata_lines(), data_line(0), data_line(1))

# 요청 구간이 전부 휴장이면 메타데이터만 오고 데이터 줄이 없다.
EMPTY_BODY = csv_bytes(*metadata_lines())

SOURCE_RECORD_ID = 1
STARTED_AT = datetime(2026, 8, 6, 23, 10, tzinfo=UTC)
COMPLETED_AT = datetime(2026, 8, 6, 23, 10, 1, tzinfo=UTC)


def request_for(start: date = date(2026, 8, 3), end: date = date(2026, 8, 7)) -> BbkRequest:
    return BbkRequest(observation_start=start, observation_end=end)


def response_for(body: bytes = BODY, request: BbkRequest | None = None) -> BbkResponse:
    return BbkResponse(
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
    # 만기 코드를 잘못 적으면 그 시계열만 조용히 사라진다. 만기가 겹치면 곡선이 같은 점을 두 번 찍는다.
    assert len(set(MATURITY_CODES)) == len(MATURITY_CODES)
    assert len(set(BUND_SERIES)) == len(BUND_SERIES)
    assert len({series.maturity_months for series in BundSeries}) == len(BundSeries)
    assert all(series.maturity_months > 0 for series in BundSeries)


def test_series_ids_are_readable():
    # DB와 대시보드에 남는 값만 보고 무슨 시계열인지 알 수 있어야 한다. `R10XX`는 만기를 담지 않는다.
    assert BundSeries.DE_10Y.value == "DE10Y"
    assert BundSeries.DE_10Y.maturity_code == "R10XX"
    assert BundSeries.DE_10Y.label == "독일 10년물"
    assert BundSeries.DE_10Y.maturity_months == 120


def test_the_german_curve_lines_up_with_the_euro_area_curve():
    # 만기가 어긋나면 독일 곡선과 유로 지역 AAA 곡선을 겹쳐 볼 수 없다.
    from modules.collectors.ecb import EuroYieldSeries

    euro_area_years = {series.maturity_months for series in EuroYieldSeries if series.maturity_months >= 12}
    assert {series.maturity_months for series in BundSeries} == euro_area_years


def test_build_series_key_asks_for_every_maturity_at_once():
    key = build_series_key()

    assert key.startswith(f"{KEY_PREFIX}.")
    # 만기 차원만 `+`로 이어 붙인다. 다른 차원에 `+`가 들어가면 다른 종류의 곡선까지 딸려 온다.
    assert key.count("+") == len(MATURITY_CODES) - 1
    for code in MATURITY_CODES:
        assert code in key


def test_build_url_keeps_the_period_and_asks_for_english_formatting():
    url = build_url(request_for())

    assert url.startswith(f"https://api.statistiken.bundesbank.de/rest/data/{DATASET}/{build_series_key()}?")
    assert "startPeriod=2026-08-03" in url
    assert "endPeriod=2026-08-07" in url
    # 이걸 빼면 구분자가 `;`, 소수점이 `,`가 되어 파싱이 통째로 어긋난다.
    assert "lang=en" in url


def test_parse_curve_reads_every_maturity():
    curve = parse_curve(BODY, request_for())

    assert len(curve.observations) == ROW_COUNT
    first_day = [observation for observation in curve.observations if observation.observation_date == date(2026, 8, 5)]
    assert {observation.series: observation.value for observation in first_day} == {
        series: Decimal(values[0]) for series, values in VALUES.items()
    }


def test_parse_curve_reports_the_range_the_response_covers():
    curve = parse_curve(BODY, request_for())

    assert (curve.response_first_date, curve.response_last_date) == OBSERVATION_DATES
    # 가로 형식이라 응답 줄 수는 날짜 수다. 관측값 수와 다르다.
    assert curve.response_row_count == len(OBSERVATION_DATES)


def test_parse_curve_keeps_rows_outside_the_period_out_of_the_observations():
    curve = parse_curve(BODY, request_for(start=date(2026, 8, 6)))

    assert {observation.observation_date for observation in curve.observations} == {date(2026, 8, 6)}
    assert curve.response_first_date == date(2026, 8, 5)


def test_parse_curve_reads_a_body_without_data_lines_as_a_period_without_values():
    # 메타데이터 줄만 오면 요청 구간이 전부 휴장이라는 뜻이다. 오류가 아니다.
    curve = parse_curve(EMPTY_BODY, request_for())

    assert curve.observations == ()
    assert (curve.response_first_date, curve.response_last_date) == (None, None)
    assert curve.response_row_count == 0


def test_parse_curve_skips_a_cell_without_a_quote():
    values = {**VALUES, BundSeries.DE_30Y: ("", "3.73")}
    body = csv_bytes(*metadata_lines(), data_line(0, values), data_line(1, values))

    observations = parse_observations(body, request_for())

    assert len(observations) == ROW_COUNT - 1
    missing = [
        observation
        for observation in observations
        if observation.series is BundSeries.DE_30Y and observation.observation_date == date(2026, 8, 5)
    ]
    assert missing == []


def test_parse_curve_rejects_a_header_without_the_date_column():
    body = csv_bytes(data_line(0), header=header_line().replace('""', "DATE", 1))

    with pytest.raises(BbkPayloadError, match="date column"):
        parse_curve(body, request_for())


def test_parse_curve_rejects_a_series_column_we_did_not_ask_for():
    # 다른 만기나 다른 추정 방식의 곡선이 조용히 섞여 저장되는 것보다 멈추는 편이 낫다.
    extra = f"{DATASET}.{KEY_PREFIX}.R25XX.R.A.A._Z._Z.A"
    body = csv_bytes(data_line(0), header=f"{header_line()},{extra},{extra}_FLAGS")

    with pytest.raises(BbkPayloadError, match="unrequested"):
        parse_curve(body, request_for())


def test_parse_curve_rejects_a_response_missing_a_series_column():
    # 열이 빠지면 그 만기만 조용히 사라진다. 이름으로 묶으므로 여기서 잡힌다.
    kept = tuple(series for series in BundSeries if series is not BundSeries.DE_30Y)
    cells = [OBSERVATION_DATES[0].isoformat()]
    for member in kept:
        cells.extend((VALUES[member][0], ""))
    body = csv_bytes(",".join(cells), header=header_line(kept))

    with pytest.raises(BbkPayloadError, match="missing"):
        parse_curve(body, request_for())


def test_parse_curve_rejects_a_row_with_a_missing_cell():
    body = csv_bytes(*metadata_lines(), "2026-08-05,2.61")

    with pytest.raises(BbkPayloadError, match="cells"):
        parse_curve(body, request_for())


@pytest.mark.parametrize("value", ["Infinity", "abc", "1.2.3", "2,61"])
def test_parse_curve_rejects_values_that_are_not_finite_numbers(value):
    # `2,61`은 독일어 표기다. `lang=en`이 빠지면 이 꼴이 오고, 조용히 저장되면 안 된다.
    values = {**VALUES, BundSeries.DE_10Y: (value, "3.17")}
    body = csv_bytes(*metadata_lines(), data_line(0, values))

    with pytest.raises(BbkPayloadError):
        parse_curve(body, request_for())


def test_parse_curve_rejects_an_empty_body():
    with pytest.raises(BbkPayloadError, match="empty body"):
        parse_curve(b"", request_for())


def test_request_rejects_a_reversed_period():
    with pytest.raises(ValidationError, match="observation_start"):
        BbkRequest(observation_start=date(2026, 8, 7), observation_end=date(2026, 8, 3))


def test_request_and_response_are_frozen_so_a_retry_cannot_mutate_them():
    response = response_for()

    with pytest.raises(ValidationError):
        response.request.observation_start = date(2026, 1, 1)
    with pytest.raises(ValidationError):
        response.status = 500


def test_response_normalizes_timestamps_to_utc():
    response = BbkResponse(
        request=request_for(),
        body=BODY,
        status=200,
        started_at=datetime(2026, 8, 7, 8, 10, tzinfo=timezone(timedelta(hours=9))),
        completed_at=COMPLETED_AT,
    )

    assert response.started_at == STARTED_AT
    assert response.started_at.utcoffset() == timedelta(0)


def test_response_rejects_naive_timestamps():
    with pytest.raises(ValidationError):
        BbkResponse(
            request=request_for(),
            body=BODY,
            status=200,
            started_at=datetime(2026, 8, 6, 23, 10),  # noqa: DTZ001
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

    # 수집 단위가 시계열이 아니라 조회 한 번이다. mof, boe, ecb와 같다.
    assert (source_type, source, source_key, status) == ("api", "bbk", SOURCE_KEY, "succeeded")
    assert (started_at, completed_at) == (STARTED_AT, COMPLETED_AT)
    assert started_at.tzinfo is not None
    assert record_count == ROW_COUNT
    assert payload is None
    assert json.loads(metadata) == {
        "http_status": 200,
        "url": build_url(request_for()),
        "series_key": build_series_key(),
        "source_unit_name": SOURCE_UNIT_NAME,
        "observation_start": "2026-08-03",
        "observation_end": "2026-08-07",
        "response_first_date": "2026-08-05",
        "response_last_date": "2026-08-06",
        "response_row_count": len(OBSERVATION_DATES),
        "maturity_codes": list(MATURITY_CODES),
        "series_ids": list(BUND_SERIES),
    }


def test_store_links_each_observation_to_the_stored_source_record():
    connection = FakeConnection()

    store_observations(connection, response_for())

    provider, series_id, observation_date, value, unit, source_record_id = connection.recorded_cursor.calls[1][1]

    assert (series_id, observation_date, value) == ("DE1Y", date(2026, 8, 5), Decimal("2.61"))
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
    extra = f"{DATASET}.{KEY_PREFIX}.R25XX.R.A.A._Z._Z.A"

    with pytest.raises(BbkPayloadError):
        store_observations(
            connection,
            response_for(body=csv_bytes(data_line(0), header=f"{header_line()},{extra},{extra}_FLAGS")),
        )

    assert connection.recorded_cursor.calls == []


def test_store_repeats_the_same_upsert_for_a_rerun_of_the_same_period():
    first, second = FakeConnection(), FakeConnection()

    assert store_observations(first, response_for()) == store_observations(second, response_for())
    assert [statement for statement, _ in first.recorded_cursor.calls] == [
        statement for statement, _ in second.recorded_cursor.calls
    ]
