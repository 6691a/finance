"""DAG 객체와 호출 시각 판단만 검증한다.

파싱과 저장 규칙은 `modules/collectors/kis_investor_flow.py`에 있고 `tests/collectors/`가 덮는다.
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


@pytest.mark.parametrize(
    ("hour", "minute", "expected"),
    [
        (9, 35, True),
        (10, 5, True),
        (11, 25, True),
        (13, 25, True),
        (14, 35, True),
        (10, 55, False),
        (9, 30, False),
    ],
)
def test_stock_estimates_are_called_only_on_update_slots(hour, minute, expected):
    """추정치는 하루 몇 차례만 갱신된다. 5분마다 부르면 같은 값만 반복된다."""
    now = datetime(2026, 8, 14, hour, minute, tzinfo=KST_TIMEZONE)

    assert kis_investor_flow_intraday.wants_stock_estimates(now, {}) is expected


@pytest.mark.parametrize("given", [True, False])
def test_a_manual_run_can_override_the_slot_check(given):
    now = datetime(2026, 8, 14, 10, 55, tzinfo=KST_TIMEZONE)

    assert kis_investor_flow_intraday.wants_stock_estimates(now, {"include_stock_estimates": given}) is given


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
