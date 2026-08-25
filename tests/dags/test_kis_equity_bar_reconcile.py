"""DAG 객체와 거래소 창 판정만 검증한다.

봉을 걷고 거르는 규칙(`until`·`max_calls`)은 `modules/collectors/kis.py`에 있고
`tests/collectors/test_kis.py`가 덮는다.
"""

from datetime import UTC, datetime

import pytest

from dags import kis_equity_bar_reconcile
from modules.collectors import kis
from modules.collectors.kis import StockExchange
from modules.utility import KST_TIMEZONE


def test_the_dag_backs_up_the_websocket_without_racing_it():
    """WebSocket 이 원천이고 이 DAG 는 백업이다. 한 호출이 최근 두 시간을 덮으므로 30분이면 겹친다.

    틱을 정각이 아니라 05·35분에 두는 이유는 KRX 마감(15:30) 봉이다. 15:30 에 부르면 그 분이
    아직 완결되지 않아 그날 마지막 봉만 잠정으로 남는다.
    """
    dag = kis_equity_bar_reconcile.kis_equity_bar_reconcile

    # KST 평일 08:05~19:35. NXT 마지막 봉(20:00)은 20:05 확정 DAG 의 몫이라 20시대가 없다.
    assert dag.schedule == "5,35 8-19 * * 1-5"
    assert dag.max_active_runs == 1
    assert set(dag.task_dict) == {"reconcile"}


def test_the_display_metadata_is_filled():
    dag = kis_equity_bar_reconcile.kis_equity_bar_reconcile

    assert dag.dag_display_name
    assert dag.description
    assert dag.doc_md


def test_the_start_date_is_a_kst_midnight():
    start = kis_equity_bar_reconcile.kis_equity_bar_reconcile.start_date

    assert start.tzinfo is not None
    assert start.astimezone(UTC) == datetime(2026, 8, 24, 15, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    ("moment", "expected"),
    [
        # NXT 프리마켓은 08:00에 열리고 KRX 는 아직 봉이 없다.
        ((8, 30), (StockExchange.NXT,)),
        ((10, 0), (StockExchange.KRX, StockExchange.NXT)),
        # KRX 마감(15:30) 봉을 확정하려면 마감 뒤 첫 틱이 한 번 더 불러야 한다.
        ((15, 35), (StockExchange.KRX, StockExchange.NXT)),
        ((16, 5), (StockExchange.NXT,)),
        ((19, 35), (StockExchange.NXT,)),
        # 두 거래소 다 닫혔다. 20:05 확정 DAG 가 마지막 봉을 맡는다.
        ((20, 10), ()),
        ((7, 35), ()),
    ],
)
def test_only_the_exchanges_with_a_live_or_just_closed_session_are_called(moment, expected):
    """휴지 구간에 부르면 KIS 호출만 늘고 새 봉은 없다."""
    now_kst = datetime(2026, 8, 25, *moment, tzinfo=KST_TIMEZONE)

    assert kis_equity_bar_reconcile.active_exchanges(now_kst) == expected


def test_the_nxt_flag_drops_nxt_here_too(monkeypatch):
    """마감 확정 DAG와 같은 손잡이를 본다. 한쪽만 NXT를 계속 부르면 손잡이가 거짓이 된다."""
    monkeypatch.setenv(kis.NXT_REST_FLAG, "false")
    now_kst = datetime(2026, 8, 25, 10, 0, tzinfo=KST_TIMEZONE)

    assert kis_equity_bar_reconcile.active_exchanges(now_kst) == (StockExchange.KRX,)


def test_a_dropped_nxt_leaves_its_pre_market_window_empty(monkeypatch):
    """08:30은 NXT만 열리는 시각이다. 손잡이를 내리면 부를 거래소가 없어 태스크는 skip 한다."""
    monkeypatch.setenv(kis.NXT_REST_FLAG, "false")
    now_kst = datetime(2026, 8, 25, 8, 30, tzinfo=KST_TIMEZONE)

    assert kis_equity_bar_reconcile.active_exchanges(now_kst) == ()
