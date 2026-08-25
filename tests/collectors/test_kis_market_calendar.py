import json
import re
from datetime import UTC, date, datetime
from typing import Self

import pytest
from pydantic import SecretStr
from sqlalchemy import Table

from apps.models.market import MarketSession
from apps.models.raw import SourceRecord
from modules.collectors.calendar import kis_market_calendar
from modules.collectors.calendar.kis_market_calendar import (
    MARKET_SESSION_DOMESTIC_UPSERT,
    MARKET_SESSION_SETTLEMENT_UPDATE,
    MAX_PAGES,
    SOURCE_RECORD_INSERT,
    DomesticFetch,
    KisCursorError,
    KisMarketCalendarCollector,
    OverseasFetch,
    OverseasRow,
    fold_us_settlement,
)
from modules.collectors.kis import KisPayloadError, KisResultError

SOURCE_RECORD_ID = 7
TOKEN = SecretStr("token")
APP_KEY = SecretStr("key")
APP_SECRET = SecretStr("secret")
COLLECTOR = KisMarketCalendarCollector(TOKEN, APP_KEY, APP_SECRET)
STARTED_AT = datetime(2026, 8, 12, 22, 0, tzinfo=UTC)
COMPLETED_AT = datetime(2026, 8, 12, 22, 0, 5, tzinfo=UTC)


def domestic_row(day: str, open_day: str = "Y") -> dict[str, str]:
    return {
        "bass_dt": day,
        "wday_dvsn_cd": "04",
        "bzdy_yn": open_day,
        "tr_day_yn": open_day,
        "opnd_yn": open_day,
        "sttl_day_yn": open_day,
    }


def overseas_row(
    market_code: str,
    market_name: str,
    abbreviation: str = "US",
    local: str = "20260813",
    domestic: str = "20260814",
) -> dict[str, str]:
    return {
        "prdt_type_cd": "512",
        "tr_natn_cd": "840" if abbreviation == "US" else "392",
        "tr_natn_name": "미국" if abbreviation == "US" else "일본",
        "natn_eng_abrv_cd": abbreviation,
        "tr_mket_cd": market_code,
        "tr_mket_name": market_name,
        "acpl_sttl_dt": local,
        "dmst_sttl_dt": domestic,
    }


def body(rows: list[dict[str, str]], cursor: str = "", rt_cd: str = "0", msg1: str = "조회 되었습니다.") -> bytes:
    return json.dumps(
        {
            "rt_cd": rt_cd,
            "msg_cd": "MCA00000",
            "msg1": msg1,
            "ctx_area_fk": "20260812            ",
            "ctx_area_nk": cursor,
            "output": rows,
        },
        ensure_ascii=False,
    ).encode()


class FakeCursor:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple]] = []
        self.results: list[tuple | None] = []

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *args: object) -> bool:
        return False

    def execute(self, statement: str, parameters: tuple) -> None:
        self.calls.append((statement, parameters))
        if "INSERT INTO source_record" in statement:
            self.results.append((SOURCE_RECORD_ID,))
        elif "UPDATE market_session" in statement:
            self.results.append(self.update_result)
        else:
            self.results.append(None)

    def executemany(self, statement: str, parameters) -> None:
        self.calls.extend((statement, tuple(row)) for row in parameters)

    def fetchone(self):
        return self.results.pop(0) if self.results else None

    update_result: tuple | None = (True,)


class FakeConnection:
    def __init__(self) -> None:
        self.recorded_cursor = FakeCursor()

    def cursor(self) -> FakeCursor:
        return self.recorded_cursor


@pytest.fixture(autouse=True)
def without_the_psycopg2_fast_path(monkeypatch):
    """저장 테스트를 PEP 249 경로에 고정한다. `test_kis.py`와 같은 이유다."""
    monkeypatch.setattr("modules.upsert._execute_batch", None)


@pytest.fixture(autouse=True)
def without_page_delay(monkeypatch):
    monkeypatch.setattr(kis_market_calendar.time, "sleep", lambda _: None)


def fake_send_get(pages: list[tuple[bytes, str]]):
    """`(본문, 헤더 tr_cont)` 목록을 차례로 돌려주는 `send_get` 대역."""
    sent: list[dict] = []

    def send(token, app_key, app_secret, path, tr_id, query, tr_cont=""):
        sent.append({"query": dict(query), "tr_cont": tr_cont})
        payload, flag = pages[len(sent) - 1]
        return payload, 200, {"tr_cont": flag}

    send.sent = sent  # type: ignore[attr-defined]
    return send


def inserted_columns(statement: str) -> tuple[str, ...]:
    columns = re.search(r"INSERT INTO \w+ \(([^)]+)\)", statement, re.DOTALL)
    assert columns is not None
    names = re.sub(r"--[^\n]*", "", columns.group(1))
    return tuple(name.strip() for name in names.split(",") if name.strip())


def placeholder_count(statement: str) -> int:
    values = re.search(r"VALUES \(([^)]+)\)", statement, re.DOTALL)
    assert values is not None
    return values.group(1).count("%s")


def required_columns(table: Table) -> set[str]:
    return {
        column.name
        for column in table.columns
        if not column.nullable and column.server_default is None and not column.primary_key
    }


def test_domestic_upsert_matches_the_model_and_its_natural_key():
    table = MarketSession.__table__
    columns = inserted_columns(MARKET_SESSION_DOMESTIC_UPSERT)

    assert set(columns) <= {column.name for column in table.columns}
    assert required_columns(table) <= set(columns)
    # market_code·market_name·country_code·verified_by는 SQL에 리터럴로 박혀 있다.
    literals = 4
    assert placeholder_count(MARKET_SESSION_DOMESTIC_UPSERT) == len(columns) - literals
    assert "ON CONFLICT (market_code, session_date)" in MARKET_SESSION_DOMESTIC_UPSERT


def test_settlement_update_only_touches_the_settlement_columns():
    assigned = set(re.findall(r"^\s{4}(\w+) =", MARKET_SESSION_SETTLEMENT_UPDATE, re.MULTILINE))

    assert assigned == {
        "local_settlement_date",
        "domestic_settlement_date",
        "verification_source_record_id",
        "updated_at",
    }
    # 개장 판정은 NYSE 소유다. 여기서 건드리면 매일 덮어써진다.
    assert "effective_open_day =" not in MARKET_SESSION_SETTLEMENT_UPDATE
    assert "verified_by =" not in MARKET_SESSION_SETTLEMENT_UPDATE
    assert "INSERT" not in MARKET_SESSION_SETTLEMENT_UPDATE


def test_source_record_insert_matches_the_model():
    table = SourceRecord.__table__
    columns = inserted_columns(SOURCE_RECORD_INSERT)

    assert set(columns) <= {column.name for column in table.columns}
    assert required_columns(table) <= set(columns)


def test_domestic_fetch_converts_flags_and_dates(monkeypatch):
    pages = [(body([domestic_row("20260812"), domestic_row("20260815", open_day="N")]), "D")]
    monkeypatch.setattr(kis_market_calendar, "send_get", fake_send_get(pages))

    fetch = COLLECTOR.fetch_domestic_calendar(date(2026, 8, 12))

    assert fetch.page_count == 1
    assert [day.session_date for day in fetch.days] == [date(2026, 8, 12), date(2026, 8, 15)]
    assert [day.open_day for day in fetch.days] == [True, False]
    assert fetch.days[0].weekday_code == "04"


def test_domestic_fetch_joins_every_page(monkeypatch):
    pages = [
        (body([domestic_row("20260812")], cursor="20260904"), "M"),
        (body([domestic_row("20260904")], cursor="20260928"), "M"),
        (body([domestic_row("20260928")]), "D"),
    ]
    send = fake_send_get(pages)
    monkeypatch.setattr(kis_market_calendar, "send_get", send)

    fetch = COLLECTOR.fetch_domestic_calendar(date(2026, 8, 12))

    assert fetch.page_count == 3
    assert len(fetch.days) == 3
    # 첫 장은 빈 tr_cont, 다음 장부터 N이고 커서가 되먹여진다.
    assert [call["tr_cont"] for call in send.sent] == ["", "N", "N"]
    assert send.sent[1]["query"]["CTX_AREA_NK"] == "20260904"


def test_domestic_fetch_fails_when_the_cursor_stops_moving(monkeypatch):
    pages = [(body([domestic_row("20260812")], cursor="20260904"), "M")] * 3
    monkeypatch.setattr(kis_market_calendar, "send_get", fake_send_get(pages))

    with pytest.raises(KisCursorError, match="same continuation cursor"):
        COLLECTOR.fetch_domestic_calendar(date(2026, 8, 12))


def test_domestic_fetch_stops_at_the_page_cap(monkeypatch):
    # KIS는 미래를 끝없이 준다(실측). 상한은 정지 조건이지 오류가 아니다.
    pages = [(body([domestic_row("20260812")], cursor=f"cursor-{page}"), "M") for page in range(MAX_PAGES + 1)]
    send = fake_send_get(pages)
    monkeypatch.setattr(kis_market_calendar, "send_get", send)

    fetch = COLLECTOR.fetch_domestic_calendar(date(2026, 8, 12))

    assert fetch.page_count == MAX_PAGES
    assert len(send.sent) == MAX_PAGES


def test_unknown_flag_fails_instead_of_being_stored(monkeypatch):
    row = domestic_row("20260812") | {"opnd_yn": "X"}
    monkeypatch.setattr(kis_market_calendar, "send_get", fake_send_get([(body([row]), "D")]))

    with pytest.raises(KisPayloadError, match="opnd_yn"):
        COLLECTOR.fetch_domestic_calendar(date(2026, 8, 12))


def test_result_code_failure_is_raised(monkeypatch):
    payload = body([], rt_cd="1", msg1="조회할 수 없습니다")
    monkeypatch.setattr(kis_market_calendar, "send_get", fake_send_get([(payload, "D")]))

    with pytest.raises(KisResultError) as error:
        COLLECTOR.fetch_domestic_calendar(date(2026, 8, 12))

    assert error.value.code == "1"


def domestic_fetch(days: tuple[str, ...] = ("20260812",), open_day: str = "Y") -> DomesticFetch:
    return DomesticFetch(
        base_date=date(2026, 8, 12),
        days=tuple(kis_market_calendar.DomesticDay.from_payload(domestic_row(day, open_day=open_day)) for day in days),
        page_count=1,
        started_at=STARTED_AT,
        completed_at=COMPLETED_AT,
    )


def test_store_domestic_writes_the_kis_verdict():
    connection = FakeConnection()

    count = COLLECTOR.store_domestic(connection, domestic_fetch(("20260812", "20260815"), open_day="N"))

    assert count == 2
    upserts = [
        parameters
        for statement, parameters in connection.recorded_cursor.calls
        if "INSERT INTO market_session" in statement
    ]
    assert len(upserts) == 2
    session_date, weekday, business, trading, open_day, settlement, effective, verified_at, record_id = upserts[0]
    assert session_date == date(2026, 8, 12)
    assert (business, trading, open_day, settlement) == (False, False, False, False)
    # 국내는 KIS 판정을 그대로 최종 판정으로 쓴다.
    assert effective is open_day
    assert verified_at == COMPLETED_AT
    assert record_id == SOURCE_RECORD_ID
    assert weekday == "04"


def test_store_domestic_keeps_the_raw_payload_out_of_the_lineage():
    connection = FakeConnection()

    COLLECTOR.store_domestic(connection, domestic_fetch(("20260812",)))

    statement, parameters = connection.recorded_cursor.calls[0]
    assert "INSERT INTO source_record" in statement
    source_type, source, source_key, _, _, status, record_count, payload, metadata = parameters
    assert (source_type, source, source_key) == ("api", "kis", "domestic_holiday")
    assert status == "succeeded"
    assert record_count == 1
    # 하루에 수백 행이라 payload에 넣지 않는다. 구간만 metadata에 남는다.
    assert payload is None
    assert json.loads(metadata)["page_count"] == 1


def overseas_fetch(rows: list[dict[str, str]], trade_date: date = date(2026, 8, 12)) -> OverseasFetch:
    return OverseasFetch(
        trade_date=trade_date,
        rows=tuple(OverseasRow.from_payload(row) for row in rows),
        payload=json.dumps(rows, ensure_ascii=False),
        page_count=1,
        started_at=STARTED_AT,
        completed_at=COMPLETED_AT,
    )


def test_us_markets_fold_into_one_row():
    fetch = overseas_fetch(
        [
            overseas_row("01", "나스닥"),
            overseas_row("02", "뉴욕거래소"),
            overseas_row("05", "아멕스"),
            overseas_row("01", "일본", abbreviation="JP", local="20260814", domestic="20260817"),
        ]
    )

    settlement = fold_us_settlement(fetch)

    assert settlement is not None
    assert settlement.market_count == 3
    assert settlement.local_settlement_date == date(2026, 8, 13)
    assert settlement.domestic_settlement_date == date(2026, 8, 14)


def test_disagreeing_us_markets_fail():
    fetch = overseas_fetch(
        [
            overseas_row("01", "나스닥"),
            overseas_row("02", "뉴욕거래소", local="20260814"),
        ]
    )

    with pytest.raises(KisPayloadError, match="differ across markets"):
        fold_us_settlement(fetch)


def test_missing_us_rows_leave_the_verdict_alone(caplog):
    connection = FakeConnection()
    fetch = overseas_fetch([overseas_row("01", "일본", abbreviation="JP")])

    settlement = COLLECTOR.store_overseas(connection, fetch)

    assert settlement is None
    statements = [statement for statement, _ in connection.recorded_cursor.calls]
    assert not any("UPDATE market_session" in statement for statement in statements)
    assert any("INSERT INTO source_record" in statement for statement in statements)


def test_empty_overseas_response_updates_nothing():
    connection = FakeConnection()

    settlement = COLLECTOR.store_overseas(connection, overseas_fetch([]))

    assert settlement is None
    assert [statement for statement, _ in connection.recorded_cursor.calls if "UPDATE" in statement] == []


def test_store_overseas_updates_only_the_settlement_columns():
    connection = FakeConnection()
    fetch = overseas_fetch([overseas_row("01", "나스닥"), overseas_row("02", "뉴욕거래소")])

    settlement = COLLECTOR.store_overseas(connection, fetch)

    assert settlement is not None
    updates = [
        parameters for statement, parameters in connection.recorded_cursor.calls if "UPDATE market_session" in statement
    ]
    assert updates == [(date(2026, 8, 13), date(2026, 8, 14), SOURCE_RECORD_ID, date(2026, 8, 12))]
    # 원본은 3.5KB 남짓이라 그대로 남긴다. 미국 외 나라를 나중에 되살릴 근거다.
    payload = next(
        parameters[7] for statement, parameters in connection.recorded_cursor.calls if "source_record" in statement
    )
    assert json.loads(payload)[0]["tr_mket_name"] == "나스닥"


def test_an_update_that_matched_no_row_fails_instead_of_looking_stored(monkeypatch):
    """0행 UPDATE는 아무 것도 쓰지 않았다는 뜻이다. 경고만 남기면 DAG가 성공으로 끝난다."""
    connection = FakeConnection()
    monkeypatch.setattr(connection.recorded_cursor, "update_result", None)
    fetch = overseas_fetch([overseas_row("01", "나스닥"), overseas_row("02", "뉴욕거래소")])

    with pytest.raises(kis_market_calendar.SettlementTargetMissing) as error:
        COLLECTOR.store_overseas(connection, fetch)

    assert "2026-08-12" in str(error.value)


def test_overseas_fetch_keeps_the_country_name(monkeypatch):
    rows = [overseas_row("02", "뉴욕거래소")]
    monkeypatch.setattr(kis_market_calendar, "send_get", fake_send_get([(body(rows), "D")]))

    fetch = COLLECTOR.fetch_overseas_settlement(date(2026, 8, 12))

    assert fetch.rows[0].country_name == "미국"
    assert fetch.rows[0].local_settlement_date == date(2026, 8, 13)
