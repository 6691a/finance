"""DAG 객체 자체를 봐야만 알 수 있는 것만 검증한다.

수집 규칙은 `modules/collectors/analyst/kis_opinion.py`에 있고 `tests/collectors/`가 덮는다.
"""

from datetime import timedelta

from dags import kis_analyst_opinion_daily


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


def test_one_task_walks_every_watched_stock():
    tasks = kis_analyst_opinion_daily.kis_analyst_opinion_daily.task_dict

    # 호출 하나가 트랜잭션 하나라 태스크를 쪼개지 않아도 앞의 성공이 남는다.
    assert set(tasks) == {"collect_opinions"}
