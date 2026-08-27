"""DAG 객체 자체를 봐야만 알 수 있는 것만 검증한다.

수집 규칙은 `modules/collectors/analyst/kis_opinion.py`에 있고 `tests/collectors/`가 덮는다.
"""

import contextlib
from datetime import date, timedelta

import pytest
from airflow.sdk.exceptions import AirflowFailException

from dags import kis_analyst_opinion_daily
from modules.collectors.analyst.kis_opinion import OPINION_LOOKBACK_DAYS
from modules.period import LOOKBACK_DAYS


def test_the_dag_runs_on_weekday_mornings_before_the_forecast():
    dag = kis_analyst_opinion_daily.kis_analyst_opinion_daily

    # KST 평일 08:20 = UTC 일~목 23:20. 투자의견은 당일 아침 사건이라 월요일에도 돈다.
    # 포지션 DAG(화~토 08:10)와 달리 전 영업일 확정치가 아니다.
    assert dag.schedule == "20 8 * * 1-5"
    assert dag.max_active_runs == 1


def test_retries_fit_before_the_forecast_slot():
    dag = kis_analyst_opinion_daily.kis_analyst_opinion_daily

    # 08:20 + 5분 × 2 < 08:35. 한 시간을 기다리면 장전 추론을 넘긴다.
    assert dag.default_args["retries"] == 2
    assert dag.default_args["retry_delay"] == timedelta(minutes=5)


def test_the_dag_takes_the_shared_period_parameters():
    params = kis_analyst_opinion_daily.kis_analyst_opinion_daily.params

    assert set(params) == {"observation_start", "observation_end", "lookback_days"}


def test_the_window_default_is_the_collectors_not_the_shared_one():
    """공유 기본값(7일)은 의견 갱신 주기보다 짧다.

    수집기가 자기 창을 갖고 DAG가 그것을 쓴다 — 두 곳에 숫자를 적으면 Param 표시값과
    실제 조회 구간이 어긋난다.
    """
    params = kis_analyst_opinion_daily.kis_analyst_opinion_daily.params

    assert params["lookback_days"] == OPINION_LOOKBACK_DAYS
    assert OPINION_LOOKBACK_DAYS != LOOKBACK_DAYS


def test_one_task_walks_every_watched_stock():
    tasks = kis_analyst_opinion_daily.kis_analyst_opinion_daily.task_dict

    # 호출 하나가 트랜잭션 하나라 태스크를 쪼개지 않아도 앞의 성공이 남는다.
    assert set(tasks) == {"collect_opinions"}


def _run_task(monkeypatch, *, rows_per_stock: int):
    """수집기를 가짜로 끼우고 태스크 본문을 부른다. 외부 호출도 DB도 없다."""
    module = kis_analyst_opinion_daily

    class FakeCollector:
        def __init__(self, *args) -> None:
            pass

        def fetch(self, stock_code, start, end):
            return object()

        def store(self, connection, fetch):
            return rows_per_stock

    monkeypatch.setattr(module, "_credentials", lambda: ("key", "secret"))
    monkeypatch.setattr(module, "access_token", lambda *args: "token")
    class FakeConnection:
        def close(self) -> None:
            pass

    monkeypatch.setattr(module, "_connection", FakeConnection)
    monkeypatch.setattr(module, "_skip_when_closed", lambda connection, today: None)
    monkeypatch.setattr(module, "watched_stocks", lambda connection: (("005930", "삼성전자"),))
    monkeypatch.setattr(module, "KisAnalystOpinionCollector", FakeCollector)
    monkeypatch.setattr(module, "atomic", lambda connection: contextlib.nullcontext())
    monkeypatch.setattr(
        module,
        "resolve_observation_period",
        lambda context, default_lookback_days: (date(2026, 7, 29), date(2026, 8, 27)),
    )
    monkeypatch.setattr(module, "get_current_context", dict)

    return module.kis_analyst_opinion_daily.task_dict["collect_opinions"].python_callable()


def test_storing_nothing_fails_instead_of_reporting_success(monkeypatch):
    """창이 한 달인데 0건이면 의견이 없는 것이 아니라 조회가 깨진 것이다.

    7일 창이던 때 이 판정이 없어서 아홉 날 동안 매일 "성공, 0건"으로 끝났고
    `stock_analyst_opinion`이 빈 채로 남았다(2026-08-27).
    """
    with pytest.raises(AirflowFailException, match="no analyst opinions"):
        _run_task(monkeypatch, rows_per_stock=0)


def test_storing_rows_succeeds(monkeypatch):
    """판정이 정상 실행을 막지 않는다."""
    assert _run_task(monkeypatch, rows_per_stock=5) == 5
