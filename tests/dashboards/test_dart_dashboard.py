"""DART 대시보드가 수집 규칙과 어긋나지 않는지 확인한다.

패널 SQL은 코드가 아니라 JSON 문자열이라 리팩터링이 따라오지 않는다. 그래서 **모델이
정한 값 집합과 수집기가 정한 규칙**을 여기서 대조한다. Enum에 값을 추가하고 화면을
빠뜨리면 그 값만 조용히 안 보인다.
"""

import json
from pathlib import Path

import pytest

from apps.models.market import AmountBasis, EarningsMetric, EarningsReleaseType, StatementScope

DASHBOARD = Path(__file__).resolve().parents[2] / "compose/local/grafana/dashboards/dart-disclosure.json"


@pytest.fixture(scope="module")
def dashboard() -> dict:
    return json.loads(DASHBOARD.read_text())


def panel_sql(dashboard: dict) -> dict[int, str]:
    return {
        panel["id"]: target["rawSql"]
        for panel in dashboard["panels"]
        for target in panel.get("targets", [])
        if "rawSql" in target
    }


def test_uid_and_variables_stay_put(dashboard):
    # uid를 바꾸면 Grafana가 같은 파일로 두 번째 대시보드를 만들고 갱신이 막힌다.
    assert dashboard["uid"] == "dart-disclosure"
    assert [variable["name"] for variable in dashboard["templating"]["list"]] == ["company", "basis", "scope"]


def test_the_two_companies_match_the_collector(dashboard):
    from modules.collectors.dart import DartCompany

    company = next(v for v in dashboard["templating"]["list"] if v["name"] == "company")

    for target in DartCompany:
        assert target.value in company["query"]
        assert target.label in company["query"]


@pytest.mark.parametrize(
    ("variable", "enum"),
    [("basis", AmountBasis), ("scope", StatementScope)],
)
def test_choice_variables_offer_every_stored_value(dashboard, variable, enum):
    # 값을 늘리고 화면을 빠뜨리면 그 값의 행만 조용히 안 보인다.
    query = next(v for v in dashboard["templating"]["list"] if v["name"] == variable)["query"]

    for member in enum:
        assert member.value in query


def test_every_metric_has_a_label(dashboard):
    labelled = "\n".join(sql for sql in panel_sql(dashboard).values() if "WHEN 'revenue'" in sql)

    assert labelled, "지표 이름을 붙이는 패널이 없다"
    for member in EarningsMetric:
        assert f"WHEN '{member.value}'" in labelled


def test_earnings_panels_pick_the_latest_receipt_number(dashboard):
    """정정 공시는 이전 행을 덮지 않고 새 행으로 쌓인다. 고르는 일은 조회 쪽 몫이다."""
    earnings = [sql for sql in panel_sql(dashboard).values() if "earnings_fact" in sql and "DISTINCT ON" in sql]

    assert len(earnings) == 2
    for sql in earnings:
        assert "DISTINCT ON (f.stock_code, f.period_end, f.metric)" in sql
        # 접수번호는 시간순으로 커진다. 내림차순 첫 행이 최신 정정이자 확정치다.
        assert "ORDER BY f.stock_code, f.period_end, f.metric, f.rcept_no DESC" in sql


def test_every_query_pins_the_provider(dashboard):
    """`rcept_no`는 제공처 안에서만 고유하다. provider 없는 조회는 제공처가 늘면 조용히 틀린다."""
    for panel_id, sql in panel_sql(dashboard).items():
        if "disclosure_event" in sql or "earnings_fact" in sql:
            assert "provider = 'dart'" in sql, f"패널 {panel_id}"
        if "source_record" in sql:
            assert "source = 'dart'" in sql, f"패널 {panel_id}"


def test_the_timeline_never_dresses_a_date_up_as_a_time(dashboard):
    """접수일에는 시·분이 없다. 최초 감지는 공시 시각이 아니라 그 상한이다."""
    timeline = panel_sql(dashboard)[5]

    assert 'receipt_date AS "접수일"' in timeline
    assert 'detected_at AS "최초 감지"' in timeline
    # 분 단위 접수 시각은 저장하지 않는다. 빈칸을 자정으로 채우지도 않는다.
    assert "published_at" not in timeline
    assert "TIME '00:00'" not in timeline


def test_growth_is_computed_not_stored(dashboard):
    """전년 대비 증감률은 저장하지 않는다. 0으로 나누지도 않는다."""
    table = panel_sql(dashboard)[7]

    assert "prior_year_amount IS NULL OR latest.prior_year_amount = 0 THEN NULL" in table
    assert "abs(latest.prior_year_amount)" in table


def test_release_types_are_shown_as_stored(dashboard):
    """잠정치와 확정치를 화면에서 구분할 수 있어야 한다."""
    table = panel_sql(dashboard)[7]

    assert 'release_type AS "출처"' in table
    assert {member.value for member in EarningsReleaseType} == {"provisional", "periodic"}
