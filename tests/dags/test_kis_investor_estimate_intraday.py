"""DAG 객체와 갱신 슬롯만 검증한다.

파싱과 저장 규칙은 `modules/collectors/market/kis_investor_flow.py`에 있고 `tests/collectors/`가 덮는다.
"""

from dags import kis_investor_estimate_intraday

# KIS 공식 예제가 밝힌 갱신 시각을 조금 지난 뒤(KST). 시장 누적 쪽 `*/5` 스케줄에 얹혀
# 돌던 값이라, 이 목록이 DAG 스케줄과 어긋나면 추정 조회가 조용히 안 돈다.
EXPECTED_SLOTS_KST = ((9, 35), (10, 5), (11, 25), (13, 25), (14, 35))


def test_the_dag_runs_only_on_update_slots():
    """추정치는 하루 다섯 번만 갱신된다. 5분마다 부르면 같은 값만 반복된다.

    분이 제각각이라 cron 하나가 아니라 다중 cron 타임테이블이다. 주말은 cron이 뺀다.
    """
    timetable = kis_investor_estimate_intraday.kis_investor_estimate_intraday.schedule

    crons = [part.strip().split(" ", 2)[:2] for part in timetable.summary.split(",")]
    assert [(int(hour), int(minute)) for minute, hour in crons] == list(EXPECTED_SLOTS_KST)
    assert all(part.strip().endswith("* * 1-5") for part in timetable.summary.split(","))


def test_one_task_walks_every_stock():
    tasks = kis_investor_estimate_intraday.kis_investor_estimate_intraday.task_dict

    # 호출 하나가 트랜잭션 하나라 태스크를 쪼개지 않아도 앞의 성공이 남는다.
    assert set(tasks) == {"collect"}


def test_a_missed_slot_is_worth_retrying():
    """다음 슬롯이 한두 시간 뒤라 시장 누적(재시도 1회)보다 더 준다."""
    dag = kis_investor_estimate_intraday.kis_investor_estimate_intraday

    assert dag.default_args["retries"] == 2
    assert dag.max_active_runs == 1


def test_the_dag_takes_no_slot_parameter():
    """언제 눌러도 조회한다. 벽시계로 모드를 가르던 파라미터가 없다."""
    assert kis_investor_estimate_intraday.kis_investor_estimate_intraday.params == {}
