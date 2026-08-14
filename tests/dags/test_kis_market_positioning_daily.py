"""DAG 객체 자체를 봐야만 알 수 있는 것만 검증한다.

수집 규칙은 `modules/collectors/kis_positioning.py`에 있고 `tests/collectors/`가 덮는다.
"""

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
