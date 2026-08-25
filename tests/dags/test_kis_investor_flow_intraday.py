"""DAG 객체와 첫 집계 가드만 검증한다.

파싱과 저장 규칙은 `modules/collectors/market/kis_investor_flow.py`에 있고 `tests/collectors/`가 덮는다.
종목 추정은 `kis_investor_estimate_intraday`가 따로 갖는다.
"""

from datetime import datetime

import pytest

from dags import kis_investor_flow_intraday
from modules.utility import KST_TIMEZONE


def test_the_dag_runs_only_during_the_regular_session():
    dag = kis_investor_flow_intraday.kis_investor_flow_intraday

    # KST 평일 09:00~15:59 = UTC 평일 00:00~06:59.
    assert dag.schedule == "*/5 9-15 * * 1-5"
    assert dag.max_active_runs == 1


def test_one_task_walks_every_target():
    tasks = kis_investor_flow_intraday.kis_investor_flow_intraday.task_dict

    # 호출 하나가 트랜잭션 하나라 태스크를 쪼개지 않아도 앞의 성공이 남는다.
    assert set(tasks) == {"collect"}


def test_the_dag_has_no_mode_switch():
    """종목 추정을 가르던 벽시계 분기와 파라미터가 남아 있지 않다.

    갱신 시각이 아닐 때 Trigger 를 누르면 추정이 조용히 빠진 채 성공하던 형태다.
    `kis_investor_estimate_intraday`로 갈랐다.
    """
    dag = kis_investor_flow_intraday.kis_investor_flow_intraday

    assert "include_stock_estimates" not in dag.params
    assert not hasattr(kis_investor_flow_intraday, "wants_stock_estimates")


@pytest.mark.parametrize(
    ("hour", "minute", "expected"),
    [
        # 전일 15:55 슬롯이 다음 날 09:00 정각에 실행된다. 그 시각에는 KIS가 첫 집계를
        # 아직 안 내서 응답이 전부 0이고, all-zero 가드가 시장 코드 오류로 오탐한다.
        (9, 0, True),
        (9, 4, True),
        (9, 5, False),
        (9, 10, False),
        (20, 41, False),  # 장 마감 후 수동 실행은 최종 누적값이 있다
    ],
)
def test_runs_before_the_first_aggregation_are_skipped(hour, minute, expected):
    now = datetime(2026, 8, 19, hour, minute, tzinfo=KST_TIMEZONE)

    assert kis_investor_flow_intraday.before_first_aggregation(now) is expected
