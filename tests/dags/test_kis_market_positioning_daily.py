"""DAG 객체 자체를 봐야만 알 수 있는 것만 검증한다.

수집 규칙은 `modules/collectors/market/kis_positioning.py`에 있고 `tests/collectors/`가 덮는다.
"""

from datetime import UTC, date, datetime

import pytest
from airflow.sdk.exceptions import AirflowSkipException

from dags import kis_market_positioning_daily


def test_the_dag_runs_once_on_the_next_business_morning():
    dag = kis_market_positioning_daily.kis_market_positioning_daily

    # KST 화~토 08:10 = UTC 월~금 23:10. 장중에 여러 번 불러도 판단력이 생기지 않는다.
    assert dag.schedule == "10 8 * * 2-6"
    assert dag.max_active_runs == 1


def test_the_dag_takes_the_shared_period_parameters():
    params = kis_market_positioning_daily.kis_market_positioning_daily.params

    assert set(params) == {"observation_start", "observation_end", "lookback_days"}


def test_one_task_walks_every_dataset():
    tasks = kis_market_positioning_daily.kis_market_positioning_daily.task_dict

    # 호출 하나가 트랜잭션 하나라 태스크를 쪼개지 않아도 앞의 성공이 남는다.
    assert set(tasks) == {"collect_krx"}


def test_the_guard_asks_about_yesterday_so_the_saturday_run_is_not_skipped(monkeypatch):
    """토요일 08:10 run이 보는 장은 금요일이다.

    실행일로 물으면 KRX가 닫힌 토요일이라 매주 건너뛰고, 금요일 공매도가 확정 전 0인 채로
    화요일까지 남는다(2026-08-29 실측 — 브리핑 표에 "0주, -100.00" 이 실렸다).
    """
    module = kis_market_positioning_daily
    asked: list[date] = []

    class FakeDatetime:
        @staticmethod
        def now(tz):
            return datetime(2026, 8, 28, 23, 10, tzinfo=UTC)  # KST 토 2026-08-29 08:10

    def fake_skip(session_kst: date) -> None:
        asked.append(session_kst)
        raise AirflowSkipException("여기서 멈춘다 — 뒤는 외부 호출이다")

    monkeypatch.setattr(module, "datetime", FakeDatetime)
    monkeypatch.setattr(module, "_skip_when_closed", fake_skip)
    monkeypatch.setattr(module, "resolve_observation_period", lambda context: (date(2026, 8, 22), date(2026, 8, 29)))
    monkeypatch.setattr(module, "get_current_context", dict)

    with pytest.raises(AirflowSkipException):
        module.kis_market_positioning_daily.task_dict["collect_krx"].python_callable()

    assert asked == [date(2026, 8, 28)]
