"""DAG 객체 자체를 봐야만 알 수 있는 것만 검증한다.

파싱과 저장 규칙은 `modules/collectors/document/dart.py`에 있고 `tests/collectors/test_dart.py`가 덮는다.
"""

from datetime import date

import pytest

from dags import dart_disclosure_intraday
from modules.collectors.document.dart import DartPayloadError, Disclosure


def test_the_dag_polls_only_while_disclosures_appear():
    # KST 평일 07:00~20:59 = UTC 평일 22:00~11:59. 장 시작 전부터 장 마감 뒤 공시까지 덮는다.
    assert dart_disclosure_intraday.dart_disclosure_intraday.schedule == "*/2 7-20 * * 1-5"


def test_disclosure_events_are_stored_before_earnings_are_extracted():
    tasks = dart_disclosure_intraday.dart_disclosure_intraday.task_dict

    assert set(tasks) == {"collect_disclosures", "extract_earnings", "collect_bodies"}
    # 공시 이벤트가 먼저다. 실적 추출이 실패해도 이벤트는 이미 저장돼 있다.
    assert tasks["extract_earnings"].upstream_task_ids == {"collect_disclosures"}
    assert tasks["collect_bodies"].upstream_task_ids == {"extract_earnings"}


def _disclosure(report_name: str = "연결재무제표기준영업(잠정)실적(공정공시)") -> Disclosure:
    return Disclosure(
        corp_code="00126380",
        corp_name="삼성전자",
        stock_code="005930",
        corp_class="Y",
        report_name=report_name,
        rcept_no="20260730800077",
        filer_name="삼성전자",
        receipt_date=date(2026, 7, 30),
        remarks=None,
    )


class _FailingCollector:
    def fetch_provisional(self, disclosure):
        raise DartPayloadError("summary table is gone")


def test_extraction_failure_is_raised_not_folded_into_not_a_target():
    """예전에는 warning 뒤 `None`이라 "대상 아님"과 "실패"가 같아 보였다.

    그래서 대기 공시 전부가 실패해도 태스크가 `stored=0`으로 성공했다.
    """
    with pytest.raises(DartPayloadError):
        dart_disclosure_intraday._extract(_FailingCollector(), _disclosure())


def test_a_report_we_do_not_read_is_still_none():
    """실패가 아니라 대상이 아닌 것은 그대로 `None`이다."""
    assert dart_disclosure_intraday._extract(_FailingCollector(), _disclosure("최대주주변경")) is None
