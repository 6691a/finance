"""DAG 객체 자체를 봐야만 알 수 있는 것만 검증한다.

파싱·`since` 절단·저장은 `tests/collectors/test_kis_asia_index.py`가 덮는다.
"""

from datetime import UTC, datetime

from dags import kis_asia_index_intraday, kis_quote_intraday
from modules.collectors.market.kis_overseas_index import MAX_BARS_PER_REQUEST

# 니케이 분봉은 KIS도 15~16분 지연이다(2026-09-04 실측: 10:03:54 KST 조회에 최신 봉 09:48).
KIS_ASIA_DELAY_MINUTES = 16
POLL_MINUTES = 5
# 항셍이 KST 17:00 마감이고 정산 봉이 17:08까지 온다. 지연을 더하면 17:25 KST에 마지막 봉이 보인다.
LAST_BAR_VISIBLE_KST = (17, 25)


def test_the_dag_polls_the_asian_sessions_and_nothing_else():
    dag = kis_asia_index_intraday.kis_asia_index_intraday

    # KST 평일 09:00~17:55 = UTC 평일 00:00~08:55. 도쿄 09:00 개장부터 항셍 정산 봉 + 지연까지.
    assert kis_asia_index_intraday.SCHEDULE == "*/5 9-17 * * 1-5"
    assert dag.schedule == kis_asia_index_intraday.SCHEDULE
    minute, hours, *_ = kis_asia_index_intraday.SCHEDULE.split()
    first, last = (int(hour) for hour in hours.split("-"))
    assert (last, 55) >= LAST_BAR_VISIBLE_KST
    assert first == 9
    assert minute == f"*/{POLL_MINUTES}"


def test_the_lookback_outlives_the_provider_delay():
    """Yahoo 수집이 하루 7봉이 된 이유가 이것이다 — 지연 15분에 lookback 15분이면 정렬된 봉이 전부 잘린다."""
    assert kis_asia_index_intraday.LOOKBACK_MINUTES > KIS_ASIA_DELAY_MINUTES + POLL_MINUTES
    assert kis_asia_index_intraday.LOOKBACK_MINUTES <= MAX_BARS_PER_REQUEST


def test_the_dag_has_one_task_and_no_overlap():
    dag = kis_asia_index_intraday.kis_asia_index_intraday

    assert set(dag.task_dict) == {"collect"}
    assert dag.max_active_runs == 1
    assert dag.catchup is False


def test_the_dag_shares_the_domestic_polling_cadence():
    """같은 5분 주기다. 토큰 캐시를 같이 쓰므로 주기가 어긋나면 발급 횟수만 는다."""
    ours = kis_asia_index_intraday.SCHEDULE.split()[0]
    theirs = kis_quote_intraday.kis_quote_intraday.schedule.split()[0]
    assert ours == theirs


def test_display_metadata_is_filled():
    dag = kis_asia_index_intraday.kis_asia_index_intraday

    assert dag.dag_display_name.endswith("(KIS)")
    assert dag.description
    assert dag.doc_md and "15분" in dag.doc_md
    assert set(dag.params) == {kis_asia_index_intraday.LOOKBACK_MINUTES_PARAM}
    for param in dag.params.values():
        assert param.schema.get("title")
        assert param.description


def test_the_start_date_is_a_kst_midnight():
    start = kis_asia_index_intraday.kis_asia_index_intraday.start_date

    assert start.tzinfo is not None
    assert start.astimezone(UTC) == datetime(2026, 9, 4, 15, 0, tzinfo=UTC)
