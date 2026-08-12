"""DAG 객체 자체를 봐야만 알 수 있는 것만 검증한다.

파싱과 저장 규칙은 `modules/collectors/dart.py`에 있고 `tests/collectors/test_dart.py`가 덮는다.
"""

from dags import dart_disclosure_intraday


def test_the_dag_polls_only_while_disclosures_appear():
    # KST 평일 07:00~20:59 = UTC 평일 22:00~11:59. 장 시작 전부터 장 마감 뒤 공시까지 덮는다.
    assert dart_disclosure_intraday.dart_disclosure_intraday.schedule == "*/2 7-20 * * 1-5"


def test_disclosure_events_are_stored_before_earnings_are_extracted():
    tasks = dart_disclosure_intraday.dart_disclosure_intraday.task_dict

    assert set(tasks) == {"collect_disclosures", "extract_earnings"}
    # 공시 이벤트가 먼저다. 실적 추출이 실패해도 이벤트는 이미 저장돼 있다.
    assert tasks["extract_earnings"].upstream_task_ids == {"collect_disclosures"}
