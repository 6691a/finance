import csv
import io
import json
import re
import zipfile
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Self

import pytest

from apps.models.market import IndicatorObservation
from apps.models.raw import SourceRecord
from modules.collectors.indicator.dtcc_cds import (
    CDS_SERIES,
    EXPECTED_HEADER,
    OBSERVATION_UPSERT,
    SERIES_UNIT,
    SOURCE_RECORD_INSERT,
    CdsCompany,
    CdsTenor,
    DtccPayloadError,
    DtccResponse,
    build_url,
    match_company,
    parse_day,
    standard_maturity,
    store_day,
)

TRADE_DATE = date(2026, 10, 1)
FIVE_YEAR = "2031-12-20"
STARTED_AT = datetime(2026, 10, 2, 1, 0, tzinfo=UTC)
COMPLETED_AT = datetime(2026, 10, 2, 1, 0, 5, tzinfo=UTC)
SOURCE_RECORD_ID = 41


def trade(
    dissemination_id: str,
    *,
    name: str = "Oracle Corporation",
    spread: str = "0.0249",
    action: str = "NEWT",
    original: str = "",
    executed: str = "2026-10-01T18:00:01Z",
    expiration: str = FIVE_YEAR,
    platform: str = "GSBS",
    notation: str = "3",
) -> dict[str, str]:
    return {
        "Dissemination Identifier": dissemination_id,
        "Original Dissemination Identifier": original,
        "Action type": action,
        "Event timestamp": executed,
        "Execution Timestamp": executed,
        "Expiration Date": expiration,
        "Spread-Leg 1": spread,
        "Spread notation-Leg 1": notation if spread else "",
        "Platform identifier": platform,
        "Underlying Asset Name": name,
    }


def zipped(
    rows: list[dict[str, str]], *, header: tuple[str, ...] = EXPECTED_HEADER, member: str | None = None
) -> bytes:
    text = io.StringIO()
    writer = csv.writer(text, quoting=csv.QUOTE_ALL)
    writer.writerow(header)
    for row in rows:
        writer.writerow([row.get(column, "") for column in header])
    body = io.BytesIO()
    with zipfile.ZipFile(body, "w") as archive:
        archive.writestr(member or f"SEC_CUMULATIVE_CREDITS_{TRADE_DATE:%Y_%m_%d}.csv", text.getvalue())
    return body.getvalue()


def values(rows: list[dict[str, str]]) -> dict[str, Decimal]:
    return {
        observation.series_id: observation.value for observation in parse_day(zipped(rows), TRADE_DATE).observations
    }


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


def response_for(rows: list[dict[str, str]]) -> DtccResponse:
    return DtccResponse(
        trade_date=TRADE_DATE, body=zipped(rows), status=200, started_at=STARTED_AT, completed_at=COMPLETED_AT
    )


def inserted_columns(statement: str) -> tuple[str, ...]:
    columns = re.search(r"INSERT INTO \w+ \(([^)]+)\)", statement, re.DOTALL)
    assert columns is not None
    names = re.sub(r"--[^\n]*", "", columns.group(1))
    return tuple(name.strip() for name in names.split(",") if name.strip())


def test_the_sql_matches_the_models():
    # 수집기는 ORM 없이 문자열 SQL을 쓴다. 컬럼 이름이 어긋나면 실행 시점에야 드러난다.
    assert set(inserted_columns(SOURCE_RECORD_INSERT)) <= {column.name for column in SourceRecord.__table__.columns}
    assert set(inserted_columns(OBSERVATION_UPSERT)) <= {
        column.name for column in IndicatorObservation.__table__.columns
    }
    assert "ON CONFLICT (provider, series_id, observation_date) DO UPDATE" in OBSERVATION_UPSERT


def test_series_ids_are_readable_and_cover_every_company_and_tenor():
    assert len(CDS_SERIES) == len(CdsCompany) * len(CdsTenor) == 30
    assert "CDS_ORCL_5Y" in CDS_SERIES


def test_build_url_points_at_the_daily_file():
    assert build_url(TRADE_DATE) == (
        "https://kgc0418-tdw-data-0.s3.amazonaws.com/sec/eod/SEC_CUMULATIVE_CREDITS_2026_10_01.zip"
    )


@pytest.mark.parametrize(
    ("trade_date", "tenor", "expected"),
    [
        # 실측: 2026-09-22의 5·3·1년물 만기.
        (date(2026, 9, 22), CdsTenor.Y5, date(2031, 12, 20)),
        (date(2026, 9, 22), CdsTenor.Y3, date(2029, 12, 20)),
        (date(2026, 9, 22), CdsTenor.Y1, date(2027, 12, 20)),
        # 3/20과 9/20에 반년씩 밀린다. 그 전날은 아직 옛 만기다.
        (date(2026, 9, 19), CdsTenor.Y5, date(2031, 6, 20)),
        (date(2026, 3, 20), CdsTenor.Y5, date(2031, 6, 20)),
        (date(2026, 3, 19), CdsTenor.Y5, date(2030, 12, 20)),
        (date(2026, 1, 5), CdsTenor.Y5, date(2030, 12, 20)),
    ],
)
def test_standard_maturity_rolls_on_march_and_september_20(trade_date, tenor, expected):
    assert standard_maturity(trade_date, tenor) == expected


@pytest.mark.parametrize(
    ("name", "company"),
    [
        ("ORACLE CORPORATION", CdsCompany.ORCL),
        ("Oracle Cop", CdsCompany.ORCL),
        ("AMAZON.COM;INC.", CdsCompany.AMZN),
        ("Amazon.com Inc", CdsCompany.AMZN),
        ("ADVANCED MICRO DEVICES;INC.", CdsCompany.AMD),
        ("META PLATFORMS, INC.", CdsCompany.META),
        # 낱말 단위라 META가 METLEN·METAL에 걸리지 않는다.
        ("Metlen Energy & Metals S.A.", None),
        ("SCM METAL PRODUCTS, INC.", None),
        ("SK HYNIX INC.", None),
    ],
)
def test_match_company_reads_the_spellings_seen_in_the_files(name, company):
    assert match_company(name) is company


def test_match_company_fails_on_an_unknown_spelling_of_a_target():
    # 조용히 빠지면 그 회사 값만 사라진다. 새 철자는 허용 목록에 더할 때까지 멈춘다.
    with pytest.raises(DtccPayloadError, match="ORCL"):
        match_company("Oracle Corp Holdings")


def test_parse_day_takes_the_median_of_the_day_in_percent():
    rows = [trade("1", spread="0.0243"), trade("2", spread="0.0249"), trade("3", spread="0.0249")]

    day = parse_day(zipped(rows), TRADE_DATE)

    assert [(o.series_id, o.value, o.print_count) for o in day.observations] == [("CDS_ORCL_5Y", Decimal("2.4900"), 3)]
    assert SERIES_UNIT == "Percent"


def test_parse_day_keeps_only_standard_tenors():
    rows = [
        trade("1", expiration="2029-12-20", spread="0.019"),
        trade("2", expiration="2027-12-20", spread="0.010"),
        # 반년 전 5년물. 만기가 짧아 값이 낮다 — 5년물과 섞지 않는다.
        trade("3", expiration="2031-06-20", spread="0.0230"),
    ]

    day = parse_day(zipped(rows), TRADE_DATE)

    assert {o.series_id: o.value for o in day.observations} == {
        "CDS_ORCL_3Y": Decimal("1.9000"),
        "CDS_ORCL_1Y": Decimal("1.0000"),
    }
    assert day.off_tenor_print_count == 1


def test_parse_day_uses_only_venues_with_a_consistent_unit():
    # 실측: 같은 날 오라클 5년물을 GSBS는 0.0249, BILT는 0.000223으로 적었다.
    rows = [trade("1", spread="0.0249"), trade("2", spread="0.000223", platform="BILT"), trade("3", platform="XOFF")]

    day = parse_day(zipped(rows), TRADE_DATE)

    assert {o.series_id: o.value for o in day.observations} == {"CDS_ORCL_5Y": Decimal("2.4900")}
    assert day.off_venue_print_count == 2


def test_parse_day_applies_cancellations_and_corrections():
    rows = [
        trade("1", spread="0.0249"),
        trade("2", spread="0.0300"),
        trade("3", action="EROR", original="2", spread=""),
        trade("4", spread="0.0100"),
        trade("5", action="CORR", original="4", spread="0.0251"),
    ]

    assert values(rows) == {"CDS_ORCL_5Y": Decimal("2.5000")}


def test_parse_day_skips_prints_without_a_spread_and_other_companies():
    rows = [trade("1"), trade("2", spread=""), trade("3", name="SK HYNIX INC.", spread="0.0050")]

    assert values(rows) == {"CDS_ORCL_5Y": Decimal("2.4900")}


def test_parse_day_does_not_let_a_late_print_overwrite_an_earlier_day():
    rows = [trade("1"), trade("2", executed="2026-09-30T21:20:22Z", spread="0.0300")]

    day = parse_day(zipped(rows), TRADE_DATE)

    assert {o.series_id: o.value for o in day.observations} == {"CDS_ORCL_5Y": Decimal("2.4900")}
    assert day.late_print_count == 1


def test_parse_day_skips_short_product_names():
    rows = [trade("1"), trade("2", name="ORACLE CDS USD SR 5.25Y D14", spread="0.0300")]

    day = parse_day(zipped(rows), TRADE_DATE)

    assert {o.series_id: o.value for o in day.observations} == {"CDS_ORCL_5Y": Decimal("2.4900")}
    assert day.short_name_row_count == 1


def test_a_quiet_day_is_not_a_failure():
    assert parse_day(zipped([trade("1", name="SOME OTHER CO")]), TRADE_DATE).observations == ()


@pytest.mark.parametrize(
    ("body", "message"),
    [
        (zipped([]), "no rows"),
        (zipped([trade("1")], header=EXPECTED_HEADER + ("New column",)), "header"),
        (zipped([trade("1")], member="other.csv"), "zip holds"),
        (b"<Error>AccessDenied</Error>", "not a zip"),
        (zipped([trade("1", notation="4")]), "notation"),
        (zipped([trade("1", spread="abc")]), "non-numeric"),
        (zipped([trade("1", action="WHAT")]), "action type"),
    ],
)
def test_parse_day_fails_on_a_broken_file(body, message):
    with pytest.raises(DtccPayloadError, match=message):
        parse_day(body, TRADE_DATE)


def test_store_writes_the_file_once_and_upserts_each_observation():
    connection = FakeConnection()

    assert store_day(connection, response_for([trade("1"), trade("2", name="NVIDIA CORP", spread="0.0086")])) == 2

    calls = connection.recorded_cursor.calls
    source_type, source, source_key, _, _, status, record_count, payload, metadata = calls[0][1]
    assert (source_type, source, source_key, status, record_count, payload) == (
        "api",
        "dtcc",
        "SEC_CUMULATIVE_CREDITS_2026_10_01",
        "succeeded",
        2,
        None,
    )
    assert json.loads(metadata)["print_counts"] == {"CDS_NVDA_5Y": 1, "CDS_ORCL_5Y": 1}
    assert [call[1] for call in calls[1:]] == [
        ("dtcc", "CDS_NVDA_5Y", TRADE_DATE, Decimal("0.8600"), "Percent", SOURCE_RECORD_ID),
        ("dtcc", "CDS_ORCL_5Y", TRADE_DATE, Decimal("2.4900"), "Percent", SOURCE_RECORD_ID),
    ]


def test_store_keeps_the_source_record_on_a_quiet_day():
    # "받았는데 없었다"와 "안 받았다"가 갈려야 한다.
    connection = FakeConnection()

    assert store_day(connection, response_for([trade("1", name="SOME OTHER CO")])) == 0
    assert len(connection.recorded_cursor.calls) == 1


def test_store_writes_nothing_when_the_file_is_broken():
    connection = FakeConnection()

    with pytest.raises(DtccPayloadError):
        store_day(connection, response_for([trade("1", notation="4")]))
    assert connection.recorded_cursor.calls == []
