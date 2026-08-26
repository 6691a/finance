"""DAG 객체와 params 해석만 검증한다.

파싱과 저장 규칙은 `modules/collectors/market/kis_investor_flow.py`에 있고 `tests/collectors/`가 덮는다.
"""

from contextlib import nullcontext
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace

import pytest
from airflow.exceptions import AirflowFailException

from dags import kis_investor_trade_daily
from modules.collectors.market.kis_investor_flow import InvestorFlowStock
from modules.utility import KST_TIMEZONE

NOW_KST = datetime(2026, 8, 14, 18, 10, tzinfo=KST_TIMEZONE)
END_DATE = date(2026, 8, 14)
SAMSUNG = InvestorFlowStock.SAMSUNG_ELECTRONICS


def test_the_dag_runs_after_the_session_closes():
    dag = kis_investor_trade_daily.kis_investor_trade_daily

    # 확정값이라 장중에 부를 이유가 없다. KST 평일 18:10 = UTC 평일 09:10.
    assert dag.schedule == "10 18 * * 1-5"
    assert dag.max_active_runs == 1
    assert set(dag.task_dict) == {"collect"}


def test_the_start_date_is_a_kst_midnight():
    """naive start_date 는 배포 환경의 시계를 타서 첫 run 이 하루 어긋난다."""
    start = kis_investor_trade_daily.kis_investor_trade_daily.start_date

    assert start.tzinfo is not None
    # Airflow 가 UTC 로 정규화한다. KST 2026-08-15 00:00 = UTC 2026-08-14 15:00.
    assert start.astimezone(UTC) == datetime(2026, 8, 14, 15, 0, tzinfo=UTC)


def test_an_empty_end_date_means_the_run_day():
    assert kis_investor_trade_daily.requested_end_date(NOW_KST, {}) == date(2026, 8, 14)


def test_a_backfill_end_date_is_read_as_given():
    given = {"end_date": "2026-07-01"}

    assert kis_investor_trade_daily.requested_end_date(NOW_KST, given) == date(2026, 7, 1)


def test_an_unreadable_end_date_fails_before_any_call():
    """조용히 오늘로 되돌리면 운영자가 백필했다고 믿는 구간이 비어 있게 된다."""
    with pytest.raises(AirflowFailException, match="must be YYYY-MM-DD"):
        kis_investor_trade_daily.requested_end_date(NOW_KST, {"end_date": "20260701"})


def test_an_iso_week_end_date_is_rejected():
    """date.fromisoformat 은 2026-W32 를 그 주 월요일로 받는다. 조용히 다른 구간이 된다."""
    with pytest.raises(AirflowFailException, match="must be YYYY-MM-DD"):
        kis_investor_trade_daily.requested_end_date(NOW_KST, {"end_date": "2026-W32"})


@pytest.mark.parametrize(("given", "expected"), [({}, 1), ({"pages": 6}, 6), ({"pages": None}, 1)])
def test_pages_default_to_one(given, expected):
    assert kis_investor_trade_daily.requested_pages(given) == expected


def test_pages_below_one_fail():
    with pytest.raises(AirflowFailException, match="at least 1"):
        kis_investor_trade_daily.requested_pages({"pages": 0})


# ---------------------------------------------------------------------------
# 수정주가 소급 조정 — `walk_back`
# ---------------------------------------------------------------------------


class FakeFetch:
    """`StockTradeDailyFetch` 자리. 걷기가 보는 것은 `rows`의 거래일뿐이다."""

    def __init__(self, days: list[date]) -> None:
        self.rows = [SimpleNamespace(business_date=day) for day in days]


class FakeCollector:
    """호출마다 30 거래일씩 뒤로 가는 응답을 흉내 낸다."""

    def __init__(self, *, span: int = 30) -> None:
        self.span = span
        self.fetched: list[date] = []
        self.stored: list[date] = []

    def fetch_stock_trade_daily(self, stock, end_date: date) -> FakeFetch:
        self.fetched.append(end_date)
        return FakeFetch([end_date - timedelta(days=offset) for offset in range(self.span)])

    def store_stock_trade_daily(self, connection, fetch: FakeFetch) -> int:
        self.stored.append(fetch.rows[0].business_date)
        return len(fetch.rows)


@pytest.fixture
def no_transaction(monkeypatch):
    """`atomic`은 실제 연결을 요구한다. 걷기의 판단만 보므로 통과시킨다."""
    monkeypatch.setattr(kis_investor_trade_daily, "atomic", lambda connection: nullcontext())


def test_a_conflicting_page_is_not_stored(monkeypatch, no_transaction):
    """어긋난 채로 얹으면 한 종목 안에 두 기준이 섞인다. **저장 전에** 멈춰야 한다."""
    monkeypatch.setattr(kis_investor_trade_daily, "close_conflicts", lambda connection, fetch: (date(2026, 8, 13),))
    collector = FakeCollector()

    walk = kis_investor_trade_daily.walk_back(collector, object(), SAMSUNG, END_DATE, pages=3)

    assert walk.conflicts == (date(2026, 8, 13),)
    assert walk.stored == 0
    assert collector.stored == []
    # 첫 장에서 멈춘다. 나머지를 더 받아 봐야 전부 옛 기준과 어긋난다.
    assert len(collector.fetched) == 1


def test_the_recovery_walk_stores_despite_the_disagreement(monkeypatch, no_transaction):
    """복구 걷기는 DB 전체가 옛 기준이라 매 장이 어긋난다. 그때는 검사를 끄고 덮는다."""
    monkeypatch.setattr(kis_investor_trade_daily, "close_conflicts", lambda connection, fetch: (date(2026, 8, 13),))
    collector = FakeCollector()

    walk = kis_investor_trade_daily.walk_back(
        collector, object(), SAMSUNG, END_DATE, pages=3, detect_conflicts=False
    )

    assert walk.conflicts == ()
    assert len(collector.stored) == 3


def test_the_recovery_walk_stops_at_the_backfill_start(monkeypatch, no_transaction):
    """`until`이 걷기의 바닥이다. 없으면 `pages`만큼 계속 걸어 상장 전까지 내려간다."""
    monkeypatch.setattr(kis_investor_trade_daily, "close_conflicts", lambda connection, fetch: ())
    collector = FakeCollector()
    until = END_DATE - timedelta(days=70)

    walk = kis_investor_trade_daily.walk_back(
        collector, object(), SAMSUNG, END_DATE, pages=kis_investor_trade_daily.RECOVERY_MAX_PAGES, until=until
    )

    assert walk.earliest >= until - timedelta(days=30)
    assert all(day >= until - timedelta(days=30) for day in collector.fetched)
    # 200장을 다 돌지 않는다. 바닥에 닿으면 멈춘다.
    assert len(collector.fetched) < kis_investor_trade_daily.RECOVERY_MAX_PAGES


def test_the_backfill_start_matches_the_index_history():
    """지수와 종목의 시작일이 다르면 나중에 둘을 대조할 수 없다."""
    assert kis_investor_trade_daily.BACKFILL_START_DATE == date(2016, 8, 15)
