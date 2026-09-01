"""DAG 객체와 그 안의 캘린더 판정만 검증한다.

파싱과 저장 규칙은 `modules/`에 있고 `tests/collectors/`가 덮는다. 여기 남은 것은 스케줄,
태스크 의존, 그리고 "휴장일에 무엇을 요청하지 않는가"다.
"""

from datetime import UTC, date, datetime
from typing import Self

import pytest
from airflow.sdk.exceptions import AirflowFailException

from dags import kis_quote_intraday, market_calendar_daily, yahoo_quote_intraday
from modules.collectors.market.yahoo import US_EQUITY_SYMBOLS, QuoteSymbol


class FakeCursor:
    def __init__(self, row: tuple | None) -> None:
        self.row = row

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *args: object) -> bool:
        return False

    def execute(self, statement: str, parameters: tuple) -> None:
        self.statement = statement
        self.parameters = parameters

    def fetchone(self) -> tuple | None:
        return self.row


class FakeConnection:
    def __init__(self, row: tuple | None) -> None:
        self.recorded_cursor = FakeCursor(row)
        self.closed = False

    def cursor(self) -> FakeCursor:
        return self.recorded_cursor

    def close(self) -> None:
        self.closed = True


def fake_hook(module, monkeypatch, row: tuple | None) -> FakeConnection:
    connection = FakeConnection(row)

    class Hook:
        def __init__(self, postgres_conn_id: str) -> None:
            self.postgres_conn_id = postgres_conn_id

        def get_conn(self) -> FakeConnection:
            return connection

    monkeypatch.setattr(module, "PostgresHook", Hook)
    return connection


def test_the_calendar_dag_runs_once_a_morning():
    # KST 매일 07:00 = UTC 전날 22:00. 국내 정규장(09:00)과 미국 마감 뒤 사이다.
    assert market_calendar_daily.market_calendar_daily.schedule == "0 7 * * *"


def test_nyse_creates_the_us_rows_before_kis_fills_the_settlement():
    tasks = market_calendar_daily.market_calendar_daily.task_dict

    assert set(tasks) == {"domestic_holiday", "nyse_calendar", "overseas_settlement"}
    # NYSE가 먼저 행을 만들어야 결제일 UPDATE가 붙을 곳이 생긴다.
    assert tasks["overseas_settlement"].upstream_task_ids == {"nyse_calendar"}
    # 국내는 미국 경로와 무관하다. 한쪽 실패가 다른 쪽을 막지 않는다.
    assert tasks["domestic_holiday"].upstream_task_ids == set()


@pytest.mark.parametrize(
    ("row", "closed"),
    [
        ((False,), True),
        ((True,), False),
        # 아직 판정하지 않았거나 캘린더가 그 날짜를 못 채웠다. 수집을 계속한다.
        ((None,), False),
        (None, False),
    ],
)
def test_kis_skips_only_on_a_confirmed_krx_holiday(monkeypatch, row, closed):
    connection = fake_hook(kis_quote_intraday, monkeypatch, row)

    assert kis_quote_intraday._closed_today(date(2026, 8, 17)) is closed
    assert connection.recorded_cursor.parameters == ("KRX", date(2026, 8, 17))
    assert connection.closed


def test_yahoo_drops_only_the_us_spot_symbols_when_us_equities_are_closed(monkeypatch):
    fake_hook(yahoo_quote_intraday, monkeypatch, (False,))

    symbols = yahoo_quote_intraday.polling_symbols()

    values = {symbol.value for symbol in symbols}
    assert values & US_EQUITY_SYMBOLS == set()
    # 선물·환율·원자재·아시아 지수·암호화폐는 미국 달력과 무관하므로 그대로 받는다.
    assert {"SP500_FUT", "USDKRW", "GOLD", "NIKKEI225", "BTC"} <= values
    assert len(values) == len(QuoteSymbol) - len(US_EQUITY_SYMBOLS)


@pytest.mark.parametrize("row", [(True,), (None,), None])
def test_yahoo_keeps_every_symbol_unless_the_holiday_is_confirmed(monkeypatch, row):
    fake_hook(yahoo_quote_intraday, monkeypatch, row)

    assert yahoo_quote_intraday.polling_symbols() == tuple(QuoteSymbol)


def test_yahoo_asks_with_the_new_york_date(monkeypatch):
    connection = fake_hook(yahoo_quote_intraday, monkeypatch, (True,))

    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            # KST 2026-08-13 06:00, 즉 뉴욕은 아직 2026-08-12 17:00이다.
            return datetime(2026, 8, 12, 21, 0, tzinfo=UTC)

    monkeypatch.setattr(yahoo_quote_intraday, "datetime", FrozenDatetime)
    yahoo_quote_intraday.polling_symbols()

    # KST 날짜(8/13)로 물으면 미국 세션의 절반이 엉뚱한 날을 본다.
    assert connection.recorded_cursor.parameters == ("US_EQUITY", date(2026, 8, 12))


def test_an_empty_krx_calendar_fails_the_task():
    """수집기는 0행을 `failed`로 적는데 DAG이 그 값을 안 보고 성공했다(G-40). 캘린더가 늙으면
    `krx_open_day`가 `None`이 되어 휴장일 skip이 전부 사라진다."""
    with pytest.raises(AirflowFailException, match="2026-09-01"):
        market_calendar_daily.require_session_days(0, date(2026, 9, 1))


def test_a_filled_krx_calendar_passes_through():
    assert market_calendar_daily.require_session_days(250, date(2026, 9, 1)) == 250
