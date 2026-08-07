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
from modules.collectors.ecos import (
    MARKET_RATE_SERIES,
    OBSERVATION_UPSERT,
    SERIES_UNIT,
    SOURCE_RECORD_INSERT,
    SOURCE_UNIT_NAME,
    STAT_CODE,
    EcosHTTPError,
    EcosPayloadError,
    EcosRequest,
    EcosResponse,
    EcosResultError,
    MarketRateSeries,
    build_url,
    fetch_series,
    parse_observations,
    store_observations,
)

SERIES = MarketRateSeries.KTB_10Y
SERIES_ID = SERIES.value


def row(time: str = "20260731", value: str | None = "4.261", **overrides: object) -> dict:
    # 실제 응답의 필드 구성을 그대로 쓴다. 수집기가 무시하는 칸도 함께 둬서 extra="ignore"가
    # 계속 유효한지 확인한다.
    return {
        "STAT_CODE": STAT_CODE,
        "STAT_NAME": "1.3.2.1. 시장금리(일별)",
        "ITEM_CODE1": SERIES.item_code,
        "ITEM_NAME1": "국고채(10년)",
        "ITEM_CODE2": None,
        "ITEM_NAME2": None,
        "UNIT_NAME": SOURCE_UNIT_NAME,
        "WGT": None,
        "TIME": time,
        "DATA_VALUE": value,
    } | overrides


def search_payload(*rows: dict, total: int | None = None) -> bytes:
    body = {"StatisticSearch": {"list_total_count": len(rows) if total is None else total, "row": list(rows)}}
    return json.dumps(body, ensure_ascii=False).encode("utf-8")


def result_payload(code: str, message: str = "메시지") -> bytes:
    return json.dumps({"RESULT": {"CODE": code, "MESSAGE": message}}, ensure_ascii=False).encode("utf-8")


PAYLOAD = search_payload(row(), row(time="20260803", value="4.264"))

SOURCE_RECORD_ID = 1
API_KEY = SecretStr("a" * 20)
STARTED_AT = datetime(2026, 8, 6, 23, 0, tzinfo=UTC)
COMPLETED_AT = datetime(2026, 8, 6, 23, 0, 1, tzinfo=UTC)


def request_for(series: MarketRateSeries | str = SERIES) -> EcosRequest:
    return EcosRequest(
        series=series,
        observation_start=date(2026, 7, 31),
        observation_end=date(2026, 8, 6),
    )


def response_for(series: MarketRateSeries | str = SERIES, body: bytes = PAYLOAD) -> EcosResponse:
    return EcosResponse(
        request=request_for(series),
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


def test_market_rate_series_cover_short_and_long_maturities():
    assert MarketRateSeries.KTB_10Y.value in MARKET_RATE_SERIES
    assert MarketRateSeries.CD_91D.value in MARKET_RATE_SERIES
    assert len(set(MARKET_RATE_SERIES)) == len(MARKET_RATE_SERIES)


def test_stored_series_id_is_readable_and_keeps_the_item_code_beside_it():
    # DB와 대시보드에 남는 값이 `010210000`이면 무슨 시계열인지 읽을 수 없다.
    assert request_for().series_id == "KTB10Y"
    assert not SERIES_ID.isdigit()
    # 항목코드는 버리지 않는다. 요청과 응답 대조에 쓰고 metadata로 남긴다.
    assert request_for().item_code == "010210000"
    assert SERIES.label == "국고채 10년"


def test_every_series_declares_a_distinct_item_code_and_label():
    assert len({series.item_code for series in MarketRateSeries}) == len(MarketRateSeries)
    assert len({series.label for series in MarketRateSeries}) == len(MarketRateSeries)
    assert all(series.item_code.isdigit() for series in MarketRateSeries)


def test_build_url_puts_every_argument_in_the_path_in_order():
    url = build_url(API_KEY, request_for())
    segments = url.removeprefix("https://ecos.bok.or.kr/api/StatisticSearch/").split("/")

    assert segments[0] == API_KEY.get_secret_value()
    assert segments[1:4] == ["json", "kr", "1"]
    # URL에는 저장 식별자가 아니라 ECOS 항목코드가 들어가야 한다.
    assert segments[5:] == [STAT_CODE, "D", "20260731", "20260806", SERIES.item_code]
    assert SERIES_ID not in url
    # 질의 문자열을 쓰는 API가 아니다. `?`가 붙으면 경로 조립이 어긋난 것이다.
    assert "?" not in url


def test_request_rejects_an_unlisted_series_and_a_reversed_period():
    # ECOS는 없는 항목코드에도 데이터 없음으로 답한다. 오타가 조용한 0건이 되면 안 된다.
    with pytest.raises(ValidationError):
        request_for("010195001")

    with pytest.raises(ValidationError, match="observation_start"):
        EcosRequest(series=SERIES, observation_start=date(2026, 8, 6), observation_end=date(2026, 7, 31))


def test_build_url_requires_an_api_key():
    with pytest.raises(ValueError, match="API key"):
        build_url(SecretStr(""), request_for())


def test_request_and_response_are_frozen_so_a_retry_cannot_mutate_them():
    response = response_for()

    with pytest.raises(ValidationError):
        response.request.series = MarketRateSeries.KTB_3Y
    with pytest.raises(ValidationError):
        response.status = 500


def test_response_normalizes_timestamps_to_utc():
    response = EcosResponse(
        request=request_for(),
        body=PAYLOAD,
        status=200,
        started_at=datetime(2026, 8, 7, 8, 0, tzinfo=timezone(timedelta(hours=9))),
        completed_at=COMPLETED_AT,
    )

    assert response.started_at == STARTED_AT
    assert response.started_at.utcoffset() == timedelta(0)


def test_response_rejects_naive_timestamps():
    with pytest.raises(ValidationError):
        EcosResponse(
            request=request_for(),
            body=PAYLOAD,
            status=200,
            started_at=datetime(2026, 8, 6, 23, 0),  # noqa: DTZ001
            completed_at=COMPLETED_AT,
        )


def test_parse_observations_reads_the_compact_daily_time():
    observations = parse_observations(PAYLOAD, request_for())

    assert len(observations) == 2
    assert observations[0].observation_date == date(2026, 7, 31)
    assert observations[0].value == Decimal("4.261")


def test_parse_observations_treats_no_data_as_an_empty_result():
    # 휴장일만 걸린 구간이다. 재시도할 일도, 실패시킬 일도 아니다.
    assert parse_observations(result_payload("INFO-200", "해당하는 데이터가 없습니다."), request_for()) == ()


def test_parse_observations_raises_the_result_code_for_a_rejected_request():
    with pytest.raises(EcosResultError) as raised:
        parse_observations(result_payload("INFO-100", "인증키가 유효하지 않습니다."), request_for())

    # 재시도 여부는 DAG가 코드로 판단한다. 수집기는 판단하지 않는다.
    assert raised.value.code == "INFO-100"


def test_parse_observations_rejects_a_truncated_page():
    # 요청 건수를 넘으면 ECOS는 경고 없이 앞부분만 준다. 그대로 저장하면 구간에 구멍이 남는다.
    with pytest.raises(EcosPayloadError, match="of 26 rows"):
        parse_observations(search_payload(row(), total=26), request_for())


def test_parse_observations_rejects_rows_of_another_item():
    with pytest.raises(EcosPayloadError, match="010200000"):
        parse_observations(search_payload(row(ITEM_CODE1=MarketRateSeries.KTB_3Y.item_code)), request_for())


def test_parse_observations_rejects_a_changed_unit():
    with pytest.raises(EcosPayloadError, match="unit"):
        parse_observations(search_payload(row(UNIT_NAME="%")), request_for())


def test_parse_observations_skips_rows_without_a_value():
    observations = parse_observations(search_payload(row(), row(time="20260803", value="")), request_for())

    assert len(observations) == 1


@pytest.mark.parametrize("time", ["2026-07-31", "202607", "20261331"])
def test_parse_observations_rejects_a_time_that_is_not_a_daily_date(time):
    with pytest.raises(EcosPayloadError):
        parse_observations(search_payload(row(time=time)), request_for())


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-", "4,261"])
def test_parse_observations_rejects_values_that_are_not_finite_numbers(value):
    with pytest.raises(EcosPayloadError):
        parse_observations(search_payload(row(value=value)), request_for())


def test_parse_observations_rejects_a_response_without_a_known_envelope():
    with pytest.raises(EcosPayloadError):
        parse_observations(b'{"unexpected": 1}', request_for())

    with pytest.raises(EcosPayloadError):
        parse_observations(b"not json", request_for())


def test_fetch_preserves_retry_after_for_rate_limit(monkeypatch):
    def raise_429(url, timeout):
        raise HTTPError(url, 429, "rate limited", {"Retry-After": "60"}, None)

    monkeypatch.setattr("modules.collectors.ecos.urlopen", raise_429)

    with pytest.raises(EcosHTTPError) as raised:
        fetch_series(API_KEY, request_for())

    assert raised.value.status == 429
    # URL 경로에 인증키가 들어 있다. 예외 메시지가 그걸 실어 나르면 안 된다.
    assert API_KEY.get_secret_value() not in str(raised.value)


def test_store_writes_the_raw_response_once_and_upserts_every_observation():
    connection = FakeConnection()

    assert store_observations(connection, response_for()) == 2

    statements = [statement for statement, _ in connection.recorded_cursor.calls]
    assert len(statements) == 3
    assert "INSERT INTO source_record" in statements[0]
    assert "INSERT INTO indicator_observation" in statements[1]
    assert "ON CONFLICT (provider, series_id, observation_date) DO UPDATE" in statements[1]


def test_store_records_lineage_and_collection_state_on_the_source_record():
    connection = FakeConnection()

    store_observations(connection, response_for())

    source_type, source, source_key, started_at, completed_at, status, record_count, payload, metadata = (
        connection.recorded_cursor.calls[0][1]
    )

    assert (source_type, source, source_key, status) == ("api", "ecos", SERIES_ID, "succeeded")
    assert (started_at, completed_at) == (STARTED_AT, COMPLETED_AT)
    assert started_at.tzinfo is not None
    assert record_count == 2
    assert json.loads(payload) == json.loads(PAYLOAD)
    # 저장하는 series_id는 읽을 수 있는 ID이므로, 원본 좌표는 metadata가 지고 있어야 한다.
    assert json.loads(metadata) == {
        "http_status": 200,
        "stat_code": STAT_CODE,
        "item_code": SERIES.item_code,
        "item_name": SERIES.label,
        "cycle": "D",
        "source_unit_name": SOURCE_UNIT_NAME,
        "observation_start": "2026-07-31",
        "observation_end": "2026-08-06",
    }


def test_store_links_each_observation_to_the_stored_source_record():
    connection = FakeConnection()

    store_observations(connection, response_for())

    provider, series_id, observation_date, value, unit, source_record_id = connection.recorded_cursor.calls[1][1]

    assert (series_id, observation_date, value) == (SERIES_ID, date(2026, 7, 31), Decimal("4.261"))
    # 저장 단위는 응답의 `연%`가 아니라 FRED와 맞춘 표기다. 두 나라 금리를 같이 조회한다.
    assert unit == SERIES_UNIT != SOURCE_UNIT_NAME
    assert source_record_id == SOURCE_RECORD_ID
    # 멱등 키가 제공처까지 포함하므로 관측값의 provider는 원본 레코드의 source와 같아야 한다.
    assert provider == connection.recorded_cursor.calls[0][1][1]


def test_store_keeps_the_source_record_for_a_period_without_observations():
    # 조회했지만 값이 없는 구간과 아직 조회하지 않은 구간은 구분돼야 한다.
    connection = FakeConnection()

    assert store_observations(connection, response_for(body=result_payload("INFO-200"))) == 0

    statements = [statement for statement, _ in connection.recorded_cursor.calls]
    assert len(statements) == 1
    assert "INSERT INTO source_record" in statements[0]
    assert connection.recorded_cursor.calls[0][1][6] == 0


def test_store_writes_nothing_when_the_payload_is_broken():
    connection = FakeConnection()

    with pytest.raises(EcosPayloadError):
        store_observations(connection, response_for(body=b'{"StatisticSearch": "nope"}'))

    assert connection.recorded_cursor.calls == []


def test_store_writes_nothing_when_ecos_rejects_the_request():
    connection = FakeConnection()

    with pytest.raises(EcosResultError):
        store_observations(connection, response_for(body=result_payload("ERROR-600", "DB 연결 오류")))

    assert connection.recorded_cursor.calls == []


def test_store_repeats_the_same_upsert_for_a_rerun_of_the_same_period():
    first, second = FakeConnection(), FakeConnection()

    assert store_observations(first, response_for()) == store_observations(second, response_for()) == 2
    assert [statement for statement, _ in first.recorded_cursor.calls] == [
        statement for statement, _ in second.recorded_cursor.calls
    ]
