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
from modules.collectors.mof import (
    ENCODING,
    EXPECTED_HEADER,
    JGB_SERIES,
    OBSERVATION_UPSERT,
    SERIES_UNIT,
    SOURCE_RECORD_INSERT,
    SOURCE_UNIT_NAME,
    JgbSeries,
    MofCoverageError,
    MofFile,
    MofPayloadError,
    MofRequest,
    MofResponse,
    build_url,
    fetch_curves,
    file_span,
    parse_curve,
    parse_era_date,
    parse_observations,
    store_observations,
)

# 실제 파일의 한 행. 1년부터 40년까지 열다섯 칸이다.
VALUES = (
    "1.287",
    "1.562",
    "1.714",
    "1.933",
    "2.1",
    "2.241",
    "2.389",
    "2.554",
    "2.689",
    "2.824",
    "3.402",
    "3.695",
    "3.994",
    "3.982",
    "3.948",
)

# 저장 대상 만기의 값. VALUES에서 그 열만 뽑은 것이다.
ISSUED_VALUES = {
    JgbSeries.JGB_2Y: Decimal("1.562"),
    JgbSeries.JGB_5Y: Decimal("2.1"),
    JgbSeries.JGB_10Y: Decimal("2.824"),
    JgbSeries.JGB_20Y: Decimal("3.695"),
    JgbSeries.JGB_30Y: Decimal("3.982"),
    JgbSeries.JGB_40Y: Decimal("3.948"),
}

TITLE_LINE = "国債金利情報 (令和8年8月)" + "," * 14 + ",(単位 : %)"
HEADER_LINE = ",".join(EXPECTED_HEADER)

# `jgbcm.csv`는 데이터 뒤에 빈 줄과 안내 문구를 붙인다. 둘 다 열 수는 열여섯 칸이다.
BLANK_LINE = "," * 15
NOTICE_LINE = "※最新のcsvデータがダウンロードできない場合、キャッシュを削除してください。" + "," * 15


def data_row(observation_date: str = "R8.8.3", values: tuple[str, ...] = VALUES) -> str:
    return ",".join((observation_date, *values))


def with_value(column_label: str, value: str) -> tuple[str, ...]:
    """한 만기 열만 바꾼 값 묶음."""
    index = EXPECTED_HEADER.index(column_label) - 1
    return VALUES[:index] + (value,) + VALUES[index + 1 :]


def csv_bytes(*rows: str, header: str = HEADER_LINE, encoding: str = ENCODING) -> bytes:
    return "\r\n".join((TITLE_LINE, header, *rows)).encode(encoding)


BODY = csv_bytes(data_row(), data_row("R8.8.4"), BLANK_LINE, NOTICE_LINE)

SOURCE_RECORD_ID = 1
STARTED_AT = datetime(2026, 8, 6, 23, 20, tzinfo=UTC)
COMPLETED_AT = datetime(2026, 8, 6, 23, 20, 1, tzinfo=UTC)


def request_for(
    start: date = date(2026, 8, 3),
    end: date = date(2026, 8, 6),
    file: MofFile | None = None,
) -> MofRequest:
    return MofRequest(observation_start=start, observation_end=end, file=file)


def response_for(
    body: bytes = BODY,
    file: MofFile = MofFile.CURRENT,
    request: MofRequest | None = None,
) -> MofResponse:
    return MofResponse(
        request=request or request_for(),
        file=file,
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


def test_series_map_onto_columns_the_file_actually_has():
    # 열 이름을 잘못 적으면 그 시계열만 조용히 사라진다. 헤더 상수와 대조해 둔다.
    assert {series.column_label for series in JgbSeries} <= set(EXPECTED_HEADER)
    assert len(set(JGB_SERIES)) == len(JGB_SERIES)
    assert len({series.maturity_months for series in JgbSeries}) == len(JgbSeries)
    assert all(series.maturity_months > 0 for series in JgbSeries)


def test_series_ids_are_readable():
    # DB와 대시보드에 남는 값만 보고 무슨 시계열인지 알 수 있어야 한다.
    assert JgbSeries.JGB_10Y.value == "JGB10Y"
    assert JgbSeries.JGB_10Y.label == "일본 10년물"
    assert JgbSeries.JGB_10Y.maturity_months == 120


def test_build_url_points_at_each_published_file():
    # 과거 전체 파일만 data/ 아래에 있다. 경로를 하나로 묶으면 404가 난다.
    assert build_url(MofFile.CURRENT).endswith("/interest_rate/jgbcm.csv")
    assert build_url(MofFile.ALL).endswith("/interest_rate/data/jgbcm_all.csv")
    # source_key에는 경로가 아니라 파일 이름만 남긴다.
    assert (MofFile.CURRENT.filename, MofFile.ALL.filename) == ("jgbcm", "jgbcm_all")


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("S49.9.24", date(1974, 9, 24)),
        ("H1.1.8", date(1989, 1, 8)),
        ("H31.4.30", date(2019, 4, 30)),
        ("R1.5.1", date(2019, 5, 1)),
        ("R8.8.3", date(2026, 8, 3)),
    ],
)
def test_parse_era_date_reads_the_japanese_era(text, expected):
    assert parse_era_date(text) == expected


@pytest.mark.parametrize("text", ["X8.8.3", "2026.8.3", "R8.8", "R8.8.3.1", "R0.8.3", "R8.13.3", "R8.a.3", ""])
def test_parse_era_date_rejects_anything_else(text):
    # 표기가 바뀌면 조용히 엉뚱한 연도로 저장되는 것보다 수집이 멈추는 편이 낫다.
    with pytest.raises(MofPayloadError):
        parse_era_date(text)


def test_parse_curve_reads_only_the_issued_maturities():
    curve = parse_curve(BODY, request_for())

    assert len(curve.observations) == 2 * len(JgbSeries)
    first_day = [observation for observation in curve.observations if observation.observation_date == date(2026, 8, 3)]
    assert {observation.series: observation.value for observation in first_day} == ISSUED_VALUES


def test_parse_curve_reports_the_range_the_file_covers():
    curve = parse_curve(BODY, request_for())

    assert (curve.file_first_date, curve.file_last_date) == (date(2026, 8, 3), date(2026, 8, 4))
    assert curve.file_row_count == 2


def test_parse_curve_skips_maturities_that_are_not_published_yet():
    # 40년물은 오래된 구간에서 `-`로 온다. 결측이지 오류가 아니다.
    body = csv_bytes(data_row(values=with_value("40年", "-")))

    observations = parse_observations(body, request_for())

    assert len(observations) == len(JgbSeries) - 1
    assert JgbSeries.JGB_40Y not in {observation.series for observation in observations}


def test_parse_curve_keeps_rows_outside_the_period_out_of_the_observations():
    body = csv_bytes(data_row("R8.7.31"), data_row("R8.8.3"))

    curve = parse_curve(body, request_for(start=date(2026, 8, 1)))

    assert {observation.observation_date for observation in curve.observations} == {date(2026, 8, 3)}
    # 구간 밖이어도 파일이 그 날짜를 담고 있었다는 사실은 남는다. 어느 파일을 더 받을지 여기서 갈린다.
    assert curve.file_first_date == date(2026, 7, 31)


def test_parse_curve_ignores_the_trailing_blank_and_notice_lines():
    # 안내 문구 줄도 열 수는 열여섯 칸이라 칸 수만으로는 걸러지지 않는다.
    assert len(NOTICE_LINE.split(",")) == len(EXPECTED_HEADER)
    assert parse_curve(BODY, request_for()).file_row_count == 2


def test_parse_curve_rejects_a_changed_header():
    # 재무성이 열을 추가하면 값이 조용히 옆 칸으로 밀린다. 헤더 대조가 먼저 실패해야 그걸 안다.
    body = csv_bytes(data_row(), header=HEADER_LINE + ",50年")

    with pytest.raises(MofPayloadError, match="header"):
        parse_curve(body, request_for())


def test_parse_curve_rejects_a_row_with_a_missing_cell():
    body = csv_bytes(",".join(("R8.8.3", *VALUES[:-1])))

    with pytest.raises(MofPayloadError, match="cells"):
        parse_curve(body, request_for())


@pytest.mark.parametrize("value", ["NaN", "Infinity", "abc", "1.2.3"])
def test_parse_curve_rejects_values_that_are_not_finite_numbers(value):
    body = csv_bytes(data_row(values=with_value("10年", value)))

    with pytest.raises(MofPayloadError):
        parse_curve(body, request_for())


def test_parse_curve_rejects_a_body_that_is_not_cp932():
    # UTF-8로 받은 본문은 헤더부터 어긋난다. 그대로 저장하면 열 대응이 무너진다.
    with pytest.raises(MofPayloadError):
        parse_curve(csv_bytes(data_row(), encoding="utf-8"), request_for())


def test_parse_curve_rejects_a_file_without_data_rows():
    with pytest.raises(MofPayloadError, match="no data rows"):
        parse_curve(csv_bytes(BLANK_LINE, NOTICE_LINE), request_for())


def test_file_span_reads_only_the_dates():
    assert file_span(BODY) == (date(2026, 8, 3), date(2026, 8, 4))


def test_request_rejects_a_reversed_period():
    with pytest.raises(ValidationError, match="observation_start"):
        MofRequest(observation_start=date(2026, 8, 6), observation_end=date(2026, 8, 3))


def test_request_and_response_are_frozen_so_a_retry_cannot_mutate_them():
    response = response_for()

    with pytest.raises(ValidationError):
        response.request.observation_start = date(2026, 1, 1)
    with pytest.raises(ValidationError):
        response.status = 500


def test_response_normalizes_timestamps_to_utc():
    response = MofResponse(
        request=request_for(),
        file=MofFile.CURRENT,
        body=BODY,
        status=200,
        started_at=datetime(2026, 8, 7, 8, 20, tzinfo=timezone(timedelta(hours=9))),
        completed_at=COMPLETED_AT,
    )

    assert response.started_at == STARTED_AT
    assert response.started_at.utcoffset() == timedelta(0)


def test_response_rejects_naive_timestamps():
    with pytest.raises(ValidationError):
        MofResponse(
            request=request_for(),
            file=MofFile.CURRENT,
            body=BODY,
            status=200,
            started_at=datetime(2026, 8, 6, 23, 20),  # noqa: DTZ001
            completed_at=COMPLETED_AT,
        )


HISTORY_BODY = csv_bytes(data_row("S49.9.24"), data_row("R8.7.31"))


def fake_fetch(monkeypatch, bodies: dict[MofFile, bytes]) -> list[MofFile]:
    """받은 파일을 순서대로 기록한다."""
    fetched: list[MofFile] = []

    def _fetch(file: MofFile, request: MofRequest) -> MofResponse:
        fetched.append(file)
        return response_for(body=bodies[file], file=file, request=request)

    monkeypatch.setattr("modules.collectors.mof._fetch", _fetch)
    return fetched


def test_fetch_curves_takes_only_the_current_file_when_it_covers_the_period(monkeypatch):
    fetched = fake_fetch(monkeypatch, {MofFile.CURRENT: BODY, MofFile.ALL: HISTORY_BODY})

    responses = fetch_curves(request_for(start=date(2026, 8, 3)))

    assert fetched == [MofFile.CURRENT]
    assert [response.file for response in responses] == [MofFile.CURRENT]


def test_fetch_curves_adds_the_history_file_when_the_period_starts_earlier(monkeypatch):
    # `jgbcm.csv`는 이번 달치만 담는다. 매달 초 며칠은 되돌아본 구간이 지난달로 넘어간다.
    fetched = fake_fetch(monkeypatch, {MofFile.CURRENT: BODY, MofFile.ALL: HISTORY_BODY})

    responses = fetch_curves(request_for(start=date(2026, 7, 28)))

    assert fetched == [MofFile.CURRENT, MofFile.ALL]
    # 오래된 파일이 앞에 온다. 저장 순서가 관측일 순서와 같아진다.
    assert [response.file for response in responses] == [MofFile.ALL, MofFile.CURRENT]


def test_fetch_curves_fails_when_no_published_file_reaches_back_that_far(monkeypatch):
    fake_fetch(monkeypatch, {MofFile.CURRENT: BODY, MofFile.ALL: HISTORY_BODY})

    with pytest.raises(MofCoverageError):
        fetch_curves(request_for(start=date(1970, 1, 1)))


def test_fetch_curves_takes_only_the_pinned_file(monkeypatch):
    fetched = fake_fetch(monkeypatch, {MofFile.CURRENT: BODY, MofFile.ALL: HISTORY_BODY})

    responses = fetch_curves(request_for(start=date(1974, 9, 24), file=MofFile.ALL))

    assert fetched == [MofFile.ALL]
    assert [response.file for response in responses] == [MofFile.ALL]


def test_store_writes_the_file_once_and_upserts_every_observation():
    connection = FakeConnection()

    assert store_observations(connection, response_for()) == 2 * len(JgbSeries)

    statements = [statement for statement, _ in connection.recorded_cursor.calls]
    assert len(statements) == 1 + 2 * len(JgbSeries)
    assert "INSERT INTO source_record" in statements[0]
    assert "INSERT INTO indicator_observation" in statements[1]
    assert "ON CONFLICT (provider, series_id, observation_date) DO UPDATE" in statements[1]


def test_store_records_the_file_as_the_collection_unit():
    connection = FakeConnection()

    store_observations(connection, response_for())

    source_type, source, source_key, started_at, completed_at, status, record_count, payload, metadata = (
        connection.recorded_cursor.calls[0][1]
    )

    # 수집 단위가 시계열이 아니라 파일이다. fred, ecos와 갈리는 지점이다.
    assert (source_type, source, source_key, status) == ("api", "mof", "jgbcm", "succeeded")
    assert (started_at, completed_at) == (STARTED_AT, COMPLETED_AT)
    assert started_at.tzinfo is not None
    assert record_count == 2 * len(JgbSeries)
    # 원본이 CSV라 jsonb 컬럼에 넣지 않는다. 대신 어느 파일의 어느 구간이었는지를 metadata가 남긴다.
    assert payload is None
    assert json.loads(metadata) == {
        "http_status": 200,
        "url": build_url(MofFile.CURRENT),
        "file": "jgbcm",
        "source_unit_name": SOURCE_UNIT_NAME,
        "observation_start": "2026-08-03",
        "observation_end": "2026-08-06",
        "file_first_date": "2026-08-03",
        "file_last_date": "2026-08-04",
        "file_row_count": 2,
        "series_ids": list(JGB_SERIES),
    }


def test_store_links_each_observation_to_the_stored_source_record():
    connection = FakeConnection()

    store_observations(connection, response_for())

    provider, series_id, observation_date, value, unit, source_record_id = connection.recorded_cursor.calls[1][1]

    assert (series_id, observation_date, value) == ("JGB2Y", date(2026, 8, 3), Decimal("1.562"))
    # 저장 단위는 제공처 표기 `%`가 아니라 fred, ecos와 맞춘 표기다. 세 나라 금리를 같이 조회한다.
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


def test_store_writes_nothing_when_the_file_is_broken():
    connection = FakeConnection()

    with pytest.raises(MofPayloadError):
        store_observations(connection, response_for(body=csv_bytes(data_row(), header=HEADER_LINE + ",50年")))

    assert connection.recorded_cursor.calls == []


def test_store_repeats_the_same_upsert_for_a_rerun_of_the_same_period():
    first, second = FakeConnection(), FakeConnection()

    assert store_observations(first, response_for()) == store_observations(second, response_for())
    assert [statement for statement, _ in first.recorded_cursor.calls] == [
        statement for statement, _ in second.recorded_cursor.calls
    ]
