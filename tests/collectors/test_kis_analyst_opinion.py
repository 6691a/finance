import json
import re
from datetime import UTC, date
from decimal import Decimal
from typing import Self

import pytest
from pydantic import SecretStr
from sqlalchemy import Table

from apps.models.market import StockAnalystOpinion
from apps.models.raw import SourceRecord
from modules.collectors.analyst import kis_opinion
from modules.collectors.analyst.kis_opinion import (
    OPINION_SCREEN,
    OPINION_TR_ID,
    OPINION_UPSERT,
    SOURCE_RECORD_INSERT,
    WATCHED_INSTRUMENTS,
    KisAnalystOpinionCollector,
    watched_stocks,
)
from modules.collectors.kis import KisPayloadError, KisResultError

SOURCE_RECORD_ID = 41
APP_KEY = SecretStr("key")
APP_SECRET = SecretStr("secret")
TOKEN = SecretStr("token")
SAMSUNG = "005930"
START = date(2026, 7, 23)
END = date(2026, 8, 22)

# 2026-08-22 실측 응답이다. 한 응답 안에 BUY 와 매수 가 섞여 있었고, 괴리가 두 벌 온다.
KIWOOM_ROW = {
    "stck_bsop_date": "20260810",
    "invt_opnn": "BUY",
    "invt_opnn_cls_code": "2",
    "rgbf_invt_opnn": "BUY",
    "rgbf_invt_opnn_cls_code": "3",
    "mbcr_name": "키움",
    "hts_goal_prc": "350000",
    "stck_prdy_clpr": "231000",
    "stck_nday_esdg": "-119000",
    "nday_dprt": "-34.00",
    # 조회 시점 현재가 대비. 매일 바뀌는 값이라 저장하지 않는다.
    "stft_esdg": "-68500",
    "dprt": "-19.57",
}

KOREA_INVESTMENT_ROW = {
    "stck_bsop_date": "20260731",
    "invt_opnn": "매수",
    "invt_opnn_cls_code": "2",
    "rgbf_invt_opnn": "매수",
    "rgbf_invt_opnn_cls_code": "3",
    "mbcr_name": "한국투자",
    "hts_goal_prc": "650000",
    "stck_prdy_clpr": "207000",
    "stck_nday_esdg": "-443000",
    "nday_dprt": "-68.15",
    "stft_esdg": "-368500",
    "dprt": "-56.69",
}


def collector() -> KisAnalystOpinionCollector:
    """수집기 하나. 자격 증명과 토큰이 상태라 종목마다 다시 넘기지 않는다."""
    return KisAnalystOpinionCollector(TOKEN, APP_KEY, APP_SECRET)


def body(rt_cd: str = "0", msg1: str = "정상처리 되었습니다.", **outputs) -> bytes:
    payload = {"rt_cd": rt_cd, "msg_cd": "MCA00000", "msg1": msg1, **outputs}
    return json.dumps(payload, ensure_ascii=False).encode()


class FakeCursor:
    def __init__(self, rows: list[tuple] | None = None) -> None:
        self.calls: list[tuple[str, tuple]] = []
        self._rows = rows or []

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *args: object) -> bool:
        return False

    def execute(self, statement: str, parameters=()) -> None:
        self.calls.append((statement, tuple(parameters)))

    def executemany(self, statement: str, parameters) -> None:
        self.calls.extend((statement, tuple(row)) for row in parameters)

    def fetchone(self) -> tuple[int]:
        return (SOURCE_RECORD_ID,)

    def fetchall(self) -> list[tuple]:
        return self._rows


class FakeConnection:
    def __init__(self, rows: list[tuple] | None = None) -> None:
        self.recorded_cursor = FakeCursor(rows)

    def cursor(self) -> FakeCursor:
        return self.recorded_cursor


@pytest.fixture(autouse=True)
def without_the_psycopg2_fast_path(monkeypatch):
    monkeypatch.setattr("modules.upsert._execute_batch", None)


def fake_send_get(payload: bytes, headers: dict[str, str] | None = None):
    sent: list[dict] = []

    def send(token, app_key, app_secret, path, tr_id, query, tr_cont=""):
        sent.append({"path": path, "tr_id": tr_id, "query": dict(query)})
        return payload, 200, dict(headers or {})

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


def rows_for(cursor: FakeCursor, table: str) -> list[tuple]:
    return [parameters for statement, parameters in cursor.calls if f"INSERT INTO {table}" in statement]


@pytest.mark.parametrize(
    ("statement", "model"),
    [(OPINION_UPSERT, StockAnalystOpinion), (SOURCE_RECORD_INSERT, SourceRecord)],
)
def test_every_upsert_matches_its_model(statement, model):
    table = model.__table__
    columns = inserted_columns(statement)

    assert set(columns) <= {column.name for column in table.columns}
    assert required_columns(table) <= set(columns)
    if statement is not SOURCE_RECORD_INSERT:
        # provider 만 SQL 에 리터럴로 박혀 있다.
        assert placeholder_count(statement) == len(columns) - 1


def test_the_upsert_key_is_the_broker_on_the_publication_day():
    assert "ON CONFLICT (provider, stock_code, business_date, broker_name)" in OPINION_UPSERT


def test_fetch_asks_for_the_stock_and_the_window(monkeypatch):
    send = fake_send_get(body(output=[KIWOOM_ROW]))
    monkeypatch.setattr(kis_opinion, "send_get", send)

    fetch = collector().fetch(SAMSUNG, START, END)

    request = send.sent[0]
    assert request["tr_id"] == OPINION_TR_ID
    assert request["query"]["FID_COND_SCR_DIV_CODE"] == OPINION_SCREEN
    assert request["query"]["FID_INPUT_ISCD"] == SAMSUNG
    assert request["query"]["FID_INPUT_DATE_1"] == "20260723"
    assert request["query"]["FID_INPUT_DATE_2"] == "20260822"
    assert fetch.stock_code == SAMSUNG
    assert fetch.metadata == {
        "stock_code": SAMSUNG,
        "observation_start": "2026-07-23",
        "observation_end": "2026-08-22",
        "returned": 1,
    }
    assert fetch.started_at.tzinfo is UTC
    assert fetch.completed_at.tzinfo is UTC


def test_a_row_keeps_the_broker_and_the_publication_day_gap_only(monkeypatch):
    monkeypatch.setattr(kis_opinion, "send_get", fake_send_get(body(output=[KIWOOM_ROW])))

    (row,) = collector().fetch(SAMSUNG, START, END).rows

    assert row.business_date == date(2026, 8, 10)
    assert row.broker_name == "키움"
    assert row.opinion == "BUY"
    assert row.opinion_code == "2"
    assert row.previous_opinion_code == "3"
    assert row.target_price == Decimal(350000)
    assert row.previous_close == Decimal(231000)
    # 발표 전일 종가 대비다. 조회 시점 현재가 대비(stft_esdg, dprt)는 모델에 칸이 없다.
    assert row.gap_amount == Decimal(-119000)
    assert row.gap_rate == Decimal("-34.00")
    assert not hasattr(row, "dprt")


def test_opinion_wording_is_stored_as_the_broker_wrote_it(monkeypatch):
    """BUY 와 매수 를 하나로 접지 않는다. 기계 판독은 구분코드가 한다."""
    monkeypatch.setattr(kis_opinion, "send_get", fake_send_get(body(output=[KIWOOM_ROW, KOREA_INVESTMENT_ROW])))

    rows = collector().fetch(SAMSUNG, START, END).rows

    assert [row.opinion for row in rows] == ["BUY", "매수"]
    assert {row.opinion_code for row in rows} == {"2"}


def test_a_missing_broker_name_fails_instead_of_breaking_the_key(monkeypatch):
    monkeypatch.setattr(kis_opinion, "send_get", fake_send_get(body(output=[KIWOOM_ROW | {"mbcr_name": " "}])))

    with pytest.raises(KisPayloadError, match="mbcr_name"):
        collector().fetch(SAMSUNG, START, END)


def test_bad_numbers_fail_instead_of_being_stored(monkeypatch):
    monkeypatch.setattr(kis_opinion, "send_get", fake_send_get(body(output=[KIWOOM_ROW | {"hts_goal_prc": "abc"}])))

    with pytest.raises(KisPayloadError, match="hts_goal_prc"):
        collector().fetch(SAMSUNG, START, END)


def test_rows_after_the_requested_end_are_rejected(monkeypatch):
    monkeypatch.setattr(
        kis_opinion, "send_get", fake_send_get(body(output=[KIWOOM_ROW | {"stck_bsop_date": "20260901"}]))
    )

    with pytest.raises(KisPayloadError, match="returned rows after"):
        collector().fetch(SAMSUNG, START, END)


@pytest.mark.parametrize("marker", ["M", "F"])
def test_a_truncated_response_is_a_failure_not_a_partial_save(monkeypatch, marker):
    monkeypatch.setattr(kis_opinion, "send_get", fake_send_get(body(output=[KIWOOM_ROW]), headers={"tr_cont": marker}))

    with pytest.raises(KisPayloadError, match="truncated"):
        collector().fetch(SAMSUNG, START, END)


def test_result_code_failures_are_raised(monkeypatch):
    monkeypatch.setattr(kis_opinion, "send_get", fake_send_get(body(rt_cd="1", msg1="권한이 없습니다")))

    with pytest.raises(KisResultError) as error:
        collector().fetch(SAMSUNG, START, END)

    assert error.value.code == "1"


def test_store_writes_the_rows_and_their_lineage(monkeypatch):
    monkeypatch.setattr(kis_opinion, "send_get", fake_send_get(body(output=[KIWOOM_ROW, KOREA_INVESTMENT_ROW])))
    fetch = collector().fetch(SAMSUNG, START, END)
    connection = FakeConnection()

    assert collector().store(connection, fetch) == 2

    statement, parameters = connection.recorded_cursor.calls[0]
    assert "INSERT INTO source_record" in statement
    assert parameters[1:3] == ("kis", "invest_opinion")
    assert parameters[6] == 2
    # API 키가 질의 문자열에 있으므로 원본 요청을 남기지 않는다.
    assert parameters[7] is None

    upserts = rows_for(connection.recorded_cursor, "stock_analyst_opinion")
    assert [row[:3] for row in upserts] == [
        (SAMSUNG, date(2026, 8, 10), "키움"),
        (SAMSUNG, date(2026, 7, 31), "한국투자"),
    ]
    assert all(row[-1] == SOURCE_RECORD_ID for row in upserts)


def test_an_empty_window_leaves_only_a_lineage_record(monkeypatch):
    """조회했지만 없던 구간과 아직 조회하지 않은 구간이 구분돼야 한다."""
    monkeypatch.setattr(kis_opinion, "send_get", fake_send_get(body(output=[])))
    fetch = collector().fetch(SAMSUNG, START, END)
    connection = FakeConnection()

    assert collector().store(connection, fetch) == 0
    assert len(connection.recorded_cursor.calls) == 1
    assert "INSERT INTO source_record" in connection.recorded_cursor.calls[0][0]


def test_watched_stocks_come_from_the_instrument_master():
    """추론 대상과 같은 SQL이다. 추적 종목이 늘 때 수집기를 고치지 않는다."""
    connection = FakeConnection(rows=[("005930", "삼성전자"), ("000660", "SK하이닉스")])

    assert watched_stocks(connection) == (("005930", "삼성전자"), ("000660", "SK하이닉스"))
    statement, _ = connection.recorded_cursor.calls[0]
    assert statement is WATCHED_INSTRUMENTS
    assert "WHERE is_watched" in statement
