"""DAG 객체와 params 해석만 검증한다.

파싱과 저장 규칙은 `modules/collectors/market/kis_investor_flow.py`에 있고 `tests/collectors/`가 덮는다.
"""

from contextlib import nullcontext
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace

import pytest
from airflow.sdk.exceptions import AirflowFailException

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

    def fetch_stock_trade_daily(self, stock, end_date: date, *, since: date | None = None) -> FakeFetch:
        """`since`를 실제 수집기처럼 **응답 안에서** 자른다.

        부르는 쪽은 구간의 끝만 정하고 응답이 어디까지 거슬러 올라갈지는 못 정한다. 가짜가
        그 사실을 흉내 내지 않으면 바닥 검사가 통과하는 것처럼 보인다(2026-08-26에 실제로
        그렇게 통과했고 운영에서 죽었다).
        """
        self.fetched.append(end_date)
        days = [end_date - timedelta(days=offset) for offset in range(self.span)]
        if since is not None:
            days = [day for day in days if day >= since]
        return FakeFetch(days)

    def store_stock_trade_daily(self, connection, fetch: FakeFetch) -> int:
        self.stored.append(fetch.rows[0].business_date)
        return len(fetch.rows)


@pytest.fixture
def no_transaction(monkeypatch):
    """`atomic`은 실제 연결을 요구한다. 걷기의 판단만 보므로 통과시킨다.

    장 사이 대기도 함께 끈다. 실제로 쉬면 테스트가 걷는 장 수만큼 느려진다. 대기가 걸리는지는
    `test_the_walk_waits_between_pages`가 따로 본다.
    """
    monkeypatch.setattr(kis_investor_trade_daily, "atomic", lambda connection: nullcontext())
    monkeypatch.setattr(kis_investor_trade_daily, "wait_seconds", lambda seconds: None)


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


def test_the_backfill_stops_where_the_identities_start_holding():
    """제공처가 정한 경계다. 2018-12-07까지는 투자자 항등식 셋이 전부 깨진다(2026-08-26 실측).

    이 값을 앞으로 당기면 못 믿는 세부 수급이 DB에 들어간다. 항등식을 완화해서 받는 것보다
    안 받는 편이 낫다는 판단이 이 상수다.
    """
    assert kis_investor_trade_daily.BACKFILL_START_DATE == date(2018, 12, 10)


def test_the_walk_never_stores_a_row_past_the_backfill_start(monkeypatch, no_transaction):
    """`pages`를 크게 줘도 항등식이 깨지는 구간의 **행**이 들어오지 않는다.

    구간의 끝만 막는 것으로는 모자란다 — 경계를 끝으로 불러도 그 앞 29 거래일이 함께 오고,
    검증이 그 행들에서 먼저 죽는다(2026-08-26 운영 실측). 그래서 `since`가 응답 안을 자른다.
    """
    monkeypatch.setattr(kis_investor_trade_daily, "close_conflicts", lambda connection, fetch: ())
    collector = FakeCollector()

    walk = kis_investor_trade_daily.walk_back(collector, object(), SAMSUNG, END_DATE, pages=500)

    epoch = kis_investor_trade_daily.BACKFILL_START_DATE
    assert all(day >= epoch for day in collector.fetched)
    assert all(day >= epoch for day in collector.stored)
    assert walk.earliest >= epoch
    assert len(collector.fetched) < 500


def test_the_floor_reaches_the_collector_not_just_the_cursor(monkeypatch, no_transaction):
    """바닥을 `since`로도 넘겨야 한다. 안 넘기면 응답 안의 옛 행이 검증에서 죽는다."""
    monkeypatch.setattr(kis_investor_trade_daily, "close_conflicts", lambda connection, fetch: ())
    seen: list[date | None] = []

    class RecordingCollector(FakeCollector):
        def fetch_stock_trade_daily(self, stock, end_date, *, since=None):
            seen.append(since)
            return super().fetch_stock_trade_daily(stock, end_date, since=since)

    kis_investor_trade_daily.walk_back(RecordingCollector(), object(), SAMSUNG, END_DATE, pages=2)

    assert seen == [kis_investor_trade_daily.BACKFILL_START_DATE] * len(seen)
    assert seen


def test_the_walk_waits_between_pages(monkeypatch, no_transaction):
    """무대기 백필은 초당 거래건수 제한에 걸린다(2026-08-26 실측: EGW00201)."""
    monkeypatch.setattr(kis_investor_trade_daily, "close_conflicts", lambda connection, fetch: ())
    waits: list[float] = []
    monkeypatch.setattr(kis_investor_trade_daily, "wait_seconds", waits.append)
    collector = FakeCollector()

    kis_investor_trade_daily.walk_back(collector, object(), SAMSUNG, END_DATE, pages=3)

    # 장이 셋이면 사이가 둘이다. 마지막 장 뒤에는 쉬지 않는다.
    assert waits == [kis_investor_trade_daily.PAGE_DELAY_SECONDS] * 2


def test_a_single_page_run_does_not_wait():
    """일상 실행은 종목당 한 장이라 대기가 붙으면 안 된다."""
    assert kis_investor_trade_daily.requested_pages({}) == 1
