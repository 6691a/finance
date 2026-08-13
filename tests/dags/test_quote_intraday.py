"""DAG 객체 자체를 봐야만 알 수 있는 것만 검증한다.

백필 구간 파싱과 토큰 캐시는 `modules/`로 옮겼고 `tests/collectors/`가 덮는다. 여기 남은
스케줄 검증은 `@dag`가 만든 객체를 읽어야 해서 `airflow.sdk` import가 필요하다. Windows
에서는 `tests/conftest.py`의 shim이 그걸 가능하게 한다.
"""

from dags import kis_quote_intraday, yahoo_quote_intraday


def test_the_dags_stay_on_their_intended_schedules():
    # 이 둘은 주석으로만 지켜지던 값이다. Yahoo 는 한국 장중의 미국 선물이 목적이라
    # 24시간이어야 하고, KIS 는 국내 정규장만 감싸면 된다.
    assert yahoo_quote_intraday.yahoo_quote_intraday.schedule == "*/5 * * * *"
    assert kis_quote_intraday.kis_quote_intraday.schedule == "*/5 8-16 * * 1-5"


def test_the_kis_dag_separates_bars_from_the_movement_snapshot():
    tasks = kis_quote_intraday.kis_quote_intraday.task_dict

    # 분포 실패가 분봉 저장을 막지 않도록 태스크를 나눈다. 서로 의존하지 않는다.
    assert set(tasks) == {"collect", "collect_movement"}
    assert tasks["collect"].upstream_task_ids == set()
    assert tasks["collect_movement"].upstream_task_ids == set()
