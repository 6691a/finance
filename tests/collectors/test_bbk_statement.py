import json
import re
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Self

import pytest
from pydantic import ValidationError
from sqlalchemy import Table

from apps.models.market import IndicatorObservation
from apps.models.raw import SourceRecord
from modules.collectors.indicator.bbk_statement import (
    BALANCE_SHEET_SERIES,
    DATASET,
    ENCODING,
    FREQUENCY,
    OBSERVATION_UPSERT,
    SERIES_UNIT,
    SOURCE,
    SOURCE_KEY,
    SOURCE_RECORD_INSERT,
    SOURCE_UNIT_NAME,
    STATEMENT_CODES,
    BbkStatementPayloadError,
    StatementRequest,
    StatementResponse,
    StatementSeries,
    build_series_key,
    build_url,
    parse_observations,
    parse_snapshot,
    store_observations,
)

# 2026-08-27에 실제로 받은 응답의 모양이다. 빈도 차원은 `D`인데 값은 금요일에만 있다.
OBSERVATION_DATES = (date(2026, 8, 14), date(2026, 8, 21))
VALUES = {StatementSeries.DE_ASSETS_W: ("2272143", "2265320")}

# 값이 없는 날. 주간 잔액이라 대부분의 날짜가 여기 걸린다.
MISSING_DATE = date(2026, 8, 20)


def header_line(series: tuple[StatementSeries, ...] = tuple(StatementSeries)) -> str:
    columns = ['""']
    for member in series:
        columns.extend((member.column_name, f"{member.column_name}_FLAGS"))
    return ",".join(columns)


def metadata_lines(
    series: tuple[StatementSeries, ...] = tuple(StatementSeries),
    unit_multiplier: str = SOURCE_UNIT_NAME,
) -> tuple[str, ...]:
    """데이터 앞에 붙는 메타데이터 줄. 첫 칸이 날짜가 아니므로 건너뛴다."""
    padding = "," * (2 * len(series))
    return (
        f'"",Total assets / unadjusted / Deutsche Bundesbank{padding[:-1]}',
        f"BBK_UNIT_ENG{padding}",
        f"Decimals,0{padding[:-1]}",
        f"Time format code,P1D{padding[:-1]}",
        f"category,BABA11{padding[:-1]}",
        f"unit multiplier,{unit_multiplier}{padding[:-1]}",
        f"last update,2026-08-26 11:44:47{padding[:-1]}",
    )


def data_line(index: int, values: dict[StatementSeries, tuple[str, ...]] = VALUES) -> str:
    cells = [OBSERVATION_DATES[index].isoformat()]
    for member in StatementSeries:
        cells.extend((values[member][index], ""))
    return ",".join(cells)


def missing_line(day: date = MISSING_DATE) -> str:
    cells = [day.isoformat()]
    for _ in StatementSeries:
        cells.extend((".", "No value available"))
    return ",".join(cells)


def csv_bytes(*lines: str, header: str | None = None, encoding: str = ENCODING) -> bytes:
    body = "\r\n".join((header if header is not None else header_line(), *lines))
    return body.encode(encoding)


BODY = csv_bytes(*metadata_lines(), data_line(0), missing_line(), data_line(1))

# 요청 구간에 고시가 하나도 없으면 메타데이터만 오고 데이터 줄이 없다.
EMPTY_BODY = csv_bytes(*metadata_lines())

SOURCE_RECORD_ID = 1
STARTED_AT = datetime(2026, 8, 24, 0, 20, tzinfo=UTC)
COMPLETED_AT = datetime(2026, 8, 24, 0, 20, 1, tzinfo=UTC)


def request_for(start: date = date(2026, 8, 10), end: date = date(2026, 8, 24)) -> StatementRequest:
    return StatementRequest(observation_start=start, observation_end=end)


def response_for(body: bytes = BODY, request: StatementRequest | None = None) -> StatementResponse:
    return StatementResponse(
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


def test_series_ids_are_readable_and_carry_their_frequency():
    # DB와 대시보드에 남는 값만 보고 무슨 시계열인지 알 수 있어야 한다. `TTA032`는 아무 것도 말하지 않는다.
    assert StatementSeries.DE_ASSETS_W.value == "DEASSETS_W"
    assert StatementSeries.DE_ASSETS_W.bbk_code == "TTA032"
    assert StatementSeries.DE_ASSETS_W.kind == "balance_sheet"
    # 한 테이블에 일별·주간·월간이 섞여 있어 표시가 없으면 조회하는 쪽이 주기를 구분할 수 없다.
    assert all(series.value.endswith("_W") for series in StatementSeries)


def test_the_request_asks_for_english_csv():
    """`lang=en`이 빠지면 구분자가 `;`인 독일어 표기가 와서 파싱이 통째로 어긋난다."""
    url = build_url(request_for())

    assert f"/{DATASET}/{build_series_key()}?" in url
    assert build_series_key() == f"{FREQUENCY}.{'+'.join(STATEMENT_CODES)}"
    assert "lang=en" in url
    assert "format=csv" in url
    assert "startPeriod=2026-08-10" in url
    assert "endPeriod=2026-08-24" in url


def test_parsing_keeps_the_weekly_values_and_drops_the_empty_days():
    observations = parse_observations(BODY, request_for())

    assert [observation.observation_date for observation in observations] == list(OBSERVATION_DATES)
    assert [observation.value for observation in observations] == [Decimal(2272143), Decimal(2265320)]
    assert all(observation.series_id == "DEASSETS_W" for observation in observations)


def test_rows_outside_the_requested_period_are_dropped_but_still_counted():
    """응답이 덮은 구간은 저장 구간과 다르다. 0건이 값 없음인지 구간 밖인지를 가르는 근거다."""
    snapshot = parse_snapshot(BODY, request_for(start=date(2026, 8, 18), end=date(2026, 8, 24)))

    assert [observation.observation_date for observation in snapshot.observations] == [date(2026, 8, 21)]
    assert snapshot.response_first_date == date(2026, 8, 14)
    assert snapshot.response_last_date == date(2026, 8, 21)
    assert snapshot.response_row_count == 3


def test_a_response_without_data_rows_is_not_a_failure():
    snapshot = parse_snapshot(EMPTY_BODY, request_for())

    assert snapshot.observations == ()
    assert snapshot.response_first_date is None
    assert snapshot.response_row_count == 0


def test_a_changed_unit_multiplier_fails_the_collection():
    """`Millions`가 `Billions`가 되면 같은 숫자가 1000배다. 응답 형식은 그대로라 아무 것도 안 터진다."""
    body = csv_bytes(*metadata_lines(unit_multiplier="Billions"), data_line(0))

    with pytest.raises(BbkStatementPayloadError, match="unit multiplier"):
        parse_snapshot(body, request_for())


def test_a_response_without_a_unit_multiplier_row_fails():
    body = csv_bytes('"",Total assets / unadjusted / Deutsche Bundesbank,,', data_line(0))

    with pytest.raises(BbkStatementPayloadError, match="unit multiplier"):
        parse_snapshot(body, request_for())


def test_an_unrequested_column_fails_the_collection():
    # 다른 계정이 섞여 오면 같은 관측일에 값이 두 개 생긴다. 멈추는 편이 낫다.
    header = header_line() + ",BBBK11.D.TTA082,BBBK11.D.TTA082_FLAGS"

    with pytest.raises(BbkStatementPayloadError, match="unrequested series column"):
        parse_snapshot(csv_bytes(*metadata_lines(), header=header), request_for())


def test_a_non_numeric_value_fails_the_collection():
    body = csv_bytes(*metadata_lines(), data_line(0, {StatementSeries.DE_ASSETS_W: ("n/a", "")}))

    with pytest.raises(BbkStatementPayloadError, match="non-numeric"):
        parse_snapshot(body, request_for())


def test_a_period_in_the_wrong_order_is_refused_before_the_call():
    with pytest.raises(ValidationError):
        StatementRequest(observation_start=date(2026, 8, 24), observation_end=date(2026, 8, 10))


def test_storing_writes_one_source_record_and_one_row_per_observation():
    connection = FakeConnection()

    count = store_observations(connection, response_for())

    assert count == len(OBSERVATION_DATES)
    calls = connection.recorded_cursor.calls
    assert len(calls) == 1 + len(OBSERVATION_DATES)

    _, source_record = calls[0]
    assert source_record[1] == SOURCE
    # 수집 단위는 시계열이 아니라 조회 한 번이다.
    assert source_record[2] == SOURCE_KEY
    assert source_record[6] == len(OBSERVATION_DATES)
    # 원본이 CSV인데 payload 컬럼 타입은 jsonb다.
    assert source_record[7] is None

    for statement, parameters in calls[1:]:
        assert statement == OBSERVATION_UPSERT
        assert parameters[0] == SOURCE
        assert parameters[1] == "DEASSETS_W"
        assert parameters[4] == SERIES_UNIT
        assert parameters[5] == SOURCE_RECORD_ID


def test_the_stored_unit_is_not_the_providers_own_wording():
    """제공처는 배수만 말한다(`Millions`). 저장 표기는 통화까지 담아야 비교가 된다."""
    assert SERIES_UNIT == "Millions of Euros"
    assert SERIES_UNIT != SOURCE_UNIT_NAME


def test_the_metadata_records_what_was_asked_and_what_came_back():
    connection = FakeConnection()

    store_observations(connection, response_for())

    _, source_record = connection.recorded_cursor.calls[0]
    metadata = json.loads(source_record[8])

    assert metadata["source_unit_name"] == SOURCE_UNIT_NAME
    assert metadata["observation_start"] == "2026-08-10"
    assert metadata["observation_end"] == "2026-08-24"
    assert metadata["response_first_date"] == "2026-08-14"
    assert metadata["response_last_date"] == "2026-08-21"
    assert metadata["response_row_count"] == 3
    assert metadata["series_codes"] == list(STATEMENT_CODES)
    assert metadata["series_ids"] == list(BALANCE_SHEET_SERIES)
    # 인증이 없어 URL에 비밀이 없다. 그대로 남긴다.
    assert metadata["url"].startswith("https://api.statistiken.bundesbank.de/")


def test_zero_observations_still_leave_a_source_record():
    """조회했지만 값이 없는 구간과 아직 조회하지 않은 구간이 구분돼야 한다."""
    connection = FakeConnection()

    count = store_observations(connection, response_for(body=EMPTY_BODY))

    assert count == 0
    assert len(connection.recorded_cursor.calls) == 1


def test_the_provider_matches_the_yield_curve_collector():
    """같은 기관이 준 값이다. `provider`가 갈리면 조회하는 쪽이 독일을 두 제공처로 본다."""
    from modules.collectors.indicator.bbk import SOURCE as CURVE_SOURCE

    assert SOURCE == CURVE_SOURCE
