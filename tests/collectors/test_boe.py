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
from modules.collectors.indicator.boe import (
    ENCODING,
    EXPECTED_HEADER,
    FETCH_PADDING_DAYS,
    GILT_SERIES,
    OBSERVATION_UPSERT,
    SERIES_CODES,
    SERIES_UNIT,
    SOURCE_KEY,
    SOURCE_RECORD_INSERT,
    SOURCE_UNIT_NAME,
    BoeNotCsvError,
    BoePayloadError,
    BoeRequest,
    BoeResponse,
    GiltSeries,
    build_url,
    format_query_date,
    parse_curve,
    parse_observations,
    parse_query_date,
    store_observations,
)

HEADER_LINE = ",".join(EXPECTED_HEADER)

# 실제 응답의 값. 만기마다 한 행씩 오고 값이 없는 날은 행 자체가 없다.
VALUES = {
    date(2026, 8, 3): {
        GiltSeries.GILT_5Y: "4.4656",
        GiltSeries.GILT_10Y: "4.9382",
        GiltSeries.GILT_20Y: "5.4495",
    },
    date(2026, 8, 4): {
        GiltSeries.GILT_5Y: "4.4217",
        GiltSeries.GILT_10Y: "4.9031",
        GiltSeries.GILT_20Y: "5.4259",
    },
}

MONTH_LABELS = {7: "Jul", 8: "Aug"}


def data_row(observation_date: date, series: GiltSeries, value: str) -> str:
    label = f"{observation_date.day:02d} {MONTH_LABELS[observation_date.month]} {observation_date.year}"
    return f"{label},{series.boe_code},{value}"


def csv_bytes(*rows: str, header: str = HEADER_LINE, encoding: str = ENCODING) -> bytes:
    return "\r\n".join((header, *rows)).encode(encoding)


def rows_for(values: dict[date, dict[GiltSeries, str]] = VALUES) -> tuple[str, ...]:
    return tuple(
        data_row(observation_date, series, value)
        for observation_date, by_series in values.items()
        for series, value in by_series.items()
    )


BODY = csv_bytes(*rows_for())

# IADB는 값이 없는 구간과 잘못된 코드 둘 다 이 페이지를 HTTP 200으로 돌려준다.
ERROR_PAGE = b'<!DOCTYPE html>\r\n<html lang="en">\r\n<head><title>Error</title></head>\r\n</html>'

SOURCE_RECORD_ID = 1
STARTED_AT = datetime(2026, 8, 6, 23, 40, tzinfo=UTC)
COMPLETED_AT = datetime(2026, 8, 6, 23, 40, 1, tzinfo=UTC)


def request_for(start: date = date(2026, 8, 3), end: date = date(2026, 8, 6)) -> BoeRequest:
    return BoeRequest(observation_start=start, observation_end=end)


def response_for(body: bytes = BODY, request: BoeRequest | None = None) -> BoeResponse:
    return BoeResponse(
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


def test_each_series_maps_onto_one_iadb_code_and_maturity():
    # 코드를 잘못 적으면 그 시계열만 조용히 사라진다. 만기가 겹치면 비교 패널이 같은 줄을 두 번 그린다.
    assert len(set(SERIES_CODES)) == len(SERIES_CODES)
    assert len(set(GILT_SERIES)) == len(GILT_SERIES)
    assert len({series.maturity_months for series in GiltSeries}) == len(GiltSeries)
    assert all(series.maturity_months > 0 for series in GiltSeries)


def test_series_ids_are_readable():
    # DB와 대시보드에 남는 값만 보고 무슨 시계열인지 알 수 있어야 한다. IADB 코드는 만기를 담지 않는다.
    assert GiltSeries.GILT_10Y.value == "GILT10Y"
    assert GiltSeries.GILT_10Y.boe_code == "IUDMNPY"
    assert GiltSeries.GILT_10Y.label == "영국 10년물"
    assert GiltSeries.GILT_10Y.maturity_months == 120


@pytest.mark.parametrize(
    ("day", "expected"),
    [(date(2026, 8, 3), "03/Aug/2026"), (date(2026, 12, 31), "31/Dec/2026")],
)
def test_format_query_date_uses_the_english_month_name(day, expected):
    # `strftime("%b")`는 실행 환경의 LC_TIME을 탄다. 표를 직접 두고 그 값을 고정한다.
    assert format_query_date(day) == expected


def test_build_url_asks_for_every_series_over_the_padded_period():
    url = build_url(date(2026, 7, 20), date(2026, 8, 6))

    assert url.startswith("https://www.bankofengland.co.uk/boeapps/iadb/fromshowcolumns.asp?")
    assert "Datefrom=20%2FJul%2F2026" in url
    assert "Dateto=06%2FAug%2F2026" in url
    # 세로 형식이라야 시계열을 늘려도 헤더 대조가 시계열 목록에 묶이지 않는다.
    assert "CSVF=CN" in url
    for code in SERIES_CODES:
        assert code in url


@pytest.mark.parametrize(
    ("text", "expected"),
    [("03 Aug 2026", date(2026, 8, 3)), ("01 Jan 1979", date(1979, 1, 1)), (" 31 Dec 2026 ", date(2026, 12, 31))],
)
def test_parse_query_date_reads_the_iadb_format(text, expected):
    assert parse_query_date(text) == expected


@pytest.mark.parametrize("text", ["03 Ago 2026", "2026-08-03", "03 Aug", "32 Aug 2026", "03 Aug 2026 12:00", ""])
def test_parse_query_date_rejects_anything_else(text):
    # 표기가 바뀌면 조용히 엉뚱한 날짜로 저장되는 것보다 수집이 멈추는 편이 낫다.
    with pytest.raises(BoePayloadError):
        parse_query_date(text)


def test_request_pads_the_fetch_window_but_not_the_storage_window():
    # 값이 없는 구간과 잘못된 코드를 응답만으로 가를 수 없어 앞을 넉넉히 붙여 받는다.
    request = request_for()

    assert request.fetch_start == request.observation_start - timedelta(days=FETCH_PADDING_DAYS)
    assert request.fetch_end == request.observation_end


def test_parse_curve_reads_every_maturity():
    curve = parse_curve(BODY, request_for())

    assert len(curve.observations) == len(VALUES) * len(GiltSeries)
    first_day = [observation for observation in curve.observations if observation.observation_date == date(2026, 8, 3)]
    assert {observation.series: observation.value for observation in first_day} == {
        series: Decimal(value) for series, value in VALUES[date(2026, 8, 3)].items()
    }


def test_parse_curve_reports_the_range_the_response_covers():
    curve = parse_curve(BODY, request_for())

    assert (curve.response_first_date, curve.response_last_date) == (date(2026, 8, 3), date(2026, 8, 4))
    assert curve.response_row_count == len(VALUES) * len(GiltSeries)


def test_parse_curve_drops_the_padding_rows_before_the_period():
    # 요청은 구간보다 앞에서부터 하므로 응답에는 항상 구간 밖의 행이 들어 있다.
    body = csv_bytes(data_row(date(2026, 7, 31), GiltSeries.GILT_10Y, "4.90"), *rows_for())

    curve = parse_curve(body, request_for(start=date(2026, 8, 1)))

    assert {observation.observation_date for observation in curve.observations} == {
        date(2026, 8, 3),
        date(2026, 8, 4),
    }
    # 구간 밖이어도 응답이 그 날짜를 담고 있었다는 사실은 남는다.
    assert curve.response_first_date == date(2026, 7, 31)


def test_parse_curve_skips_a_cell_without_a_quote():
    body = csv_bytes(data_row(date(2026, 8, 3), GiltSeries.GILT_20Y, "ND"), *rows_for())

    observations = parse_observations(body, request_for())

    assert len(observations) == len(VALUES) * len(GiltSeries)


def test_parse_curve_rejects_a_changed_header():
    # BoE가 열을 추가하면 값이 조용히 옆 칸으로 밀린다. 헤더 대조가 먼저 실패해야 그걸 안다.
    body = csv_bytes(*rows_for(), header=HEADER_LINE + ",UNIT")

    with pytest.raises(BoePayloadError, match="header"):
        parse_curve(body, request_for())


def test_parse_curve_rejects_an_html_error_page():
    # 값이 없는 구간과 잘못된 코드가 같은 페이지로 오므로 패딩을 붙이고도 나오면 실패시킨다.
    with pytest.raises(BoeNotCsvError):
        parse_curve(ERROR_PAGE, request_for())


def test_parse_curve_rejects_a_row_with_a_missing_cell():
    body = csv_bytes("03 Aug 2026,IUDMNPY")

    with pytest.raises(BoePayloadError, match="cells"):
        parse_curve(body, request_for())


def test_parse_curve_rejects_a_series_code_we_did_not_ask_for():
    # 다른 노드의 값이 조용히 섞여 저장되는 것보다 멈추는 편이 낫다.
    body = csv_bytes("03 Aug 2026,IUDSIZC,3.367")

    with pytest.raises(BoePayloadError, match="unrequested"):
        parse_curve(body, request_for())


@pytest.mark.parametrize("value", ["NaN", "Infinity", "abc", "1.2.3"])
def test_parse_curve_rejects_values_that_are_not_finite_numbers(value):
    body = csv_bytes(data_row(date(2026, 8, 3), GiltSeries.GILT_10Y, value))

    with pytest.raises(BoePayloadError):
        parse_curve(body, request_for())


def test_parse_curve_rejects_a_body_without_data_rows():
    with pytest.raises(BoePayloadError, match="no data rows"):
        parse_curve(csv_bytes(), request_for())


def test_request_rejects_a_reversed_period():
    with pytest.raises(ValidationError, match="observation_start"):
        BoeRequest(observation_start=date(2026, 8, 6), observation_end=date(2026, 8, 3))


def test_request_and_response_are_frozen_so_a_retry_cannot_mutate_them():
    response = response_for()

    with pytest.raises(ValidationError):
        response.request.observation_start = date(2026, 1, 1)
    with pytest.raises(ValidationError):
        response.status = 500


def test_response_normalizes_timestamps_to_utc():
    response = BoeResponse(
        request=request_for(),
        body=BODY,
        status=200,
        started_at=datetime(2026, 8, 7, 8, 40, tzinfo=timezone(timedelta(hours=9))),
        completed_at=COMPLETED_AT,
    )

    assert response.started_at == STARTED_AT
    assert response.started_at.utcoffset() == timedelta(0)


def test_response_rejects_naive_timestamps():
    with pytest.raises(ValidationError):
        BoeResponse(
            request=request_for(),
            body=BODY,
            status=200,
            started_at=datetime(2026, 8, 6, 23, 40),  # noqa: DTZ001
            completed_at=COMPLETED_AT,
        )


def test_store_writes_the_query_once_and_upserts_every_observation():
    connection = FakeConnection()

    assert store_observations(connection, response_for()) == len(VALUES) * len(GiltSeries)

    statements = [statement for statement, _ in connection.recorded_cursor.calls]
    assert len(statements) == 1 + len(VALUES) * len(GiltSeries)
    assert "INSERT INTO source_record" in statements[0]
    assert "INSERT INTO indicator_observation" in statements[1]
    assert "ON CONFLICT (provider, series_id, observation_date) DO UPDATE" in statements[1]


def test_store_records_the_query_as_the_collection_unit():
    connection = FakeConnection()

    store_observations(connection, response_for())

    source_type, source, source_key, started_at, completed_at, status, record_count, payload, metadata = (
        connection.recorded_cursor.calls[0][1]
    )

    # 수집 단위가 시계열이 아니라 조회 한 번이다. fred, ecos와 갈리는 지점이고 mof와 같다.
    assert (source_type, source, source_key, status) == ("api", "boe", SOURCE_KEY, "succeeded")
    assert (started_at, completed_at) == (STARTED_AT, COMPLETED_AT)
    assert started_at.tzinfo is not None
    assert record_count == len(VALUES) * len(GiltSeries)
    # 원본이 CSV라 jsonb 컬럼에 넣지 않는다. 대신 어느 구간을 물어 어느 구간이 왔는지를 metadata가 남긴다.
    assert payload is None
    assert json.loads(metadata) == {
        "http_status": 200,
        "url": build_url(date(2026, 7, 20), date(2026, 8, 6)),
        "source_unit_name": SOURCE_UNIT_NAME,
        "observation_start": "2026-08-03",
        "observation_end": "2026-08-06",
        "fetch_start": "2026-07-20",
        "fetch_end": "2026-08-06",
        "response_first_date": "2026-08-03",
        "response_last_date": "2026-08-04",
        "response_row_count": len(VALUES) * len(GiltSeries),
        "series_codes": list(SERIES_CODES),
        "series_ids": list(GILT_SERIES),
    }


def test_store_links_each_observation_to_the_stored_source_record():
    connection = FakeConnection()

    store_observations(connection, response_for())

    provider, series_id, observation_date, value, unit, source_record_id = connection.recorded_cursor.calls[1][1]

    assert (series_id, observation_date, value) == ("GILT5Y", date(2026, 8, 3), Decimal("4.4656"))
    # 저장 단위는 제공처 표기가 아니라 다른 수집기와 맞춘 표기다. 여러 나라 금리를 같이 조회한다.
    assert unit == SERIES_UNIT != SOURCE_UNIT_NAME
    assert source_record_id == SOURCE_RECORD_ID
    # 멱등 키가 제공처까지 포함하므로 관측값의 provider는 원본 레코드의 source와 같아야 한다.
    assert provider == connection.recorded_cursor.calls[0][1][1]


def test_store_keeps_the_source_record_for_a_period_without_observations():
    # 조회했지만 값이 없는 구간과 아직 조회하지 않은 구간은 구분돼야 한다.
    connection = FakeConnection()
    request = request_for(start=date(2026, 8, 10), end=date(2026, 8, 12))

    assert store_observations(connection, response_for(request=request)) == 0

    statements = [statement for statement, _ in connection.recorded_cursor.calls]
    assert len(statements) == 1
    assert "INSERT INTO source_record" in statements[0]
    assert connection.recorded_cursor.calls[0][1][6] == 0


def test_store_writes_nothing_when_the_response_is_broken():
    connection = FakeConnection()

    with pytest.raises(BoeNotCsvError):
        store_observations(connection, response_for(body=ERROR_PAGE))

    assert connection.recorded_cursor.calls == []


def test_store_repeats_the_same_upsert_for_a_rerun_of_the_same_period():
    first, second = FakeConnection(), FakeConnection()

    assert store_observations(first, response_for()) == store_observations(second, response_for())
    assert [statement for statement, _ in first.recorded_cursor.calls] == [
        statement for statement, _ in second.recorded_cursor.calls
    ]
