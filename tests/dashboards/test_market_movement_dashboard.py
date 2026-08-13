"""분포 대시보드가 수집 규칙과 어긋나지 않는지 확인한다.

패널 SQL은 코드가 아니라 JSON 문자열이라 리팩터링이 따라오지 않는다. 특히 **전체 종목 수를
어떻게 세는가**가 이 화면의 유일한 계산 규칙이라 여기서 고정한다.
"""

import json
import re
from pathlib import Path

import pytest

from apps.models.market import MovementMarket

DASHBOARD = Path(__file__).resolve().parents[2] / "compose/local/grafana/dashboards/market-movement.json"


@pytest.fixture(scope="module")
def dashboard() -> dict:
    return json.loads(DASHBOARD.read_text())


def statements(dashboard: dict) -> dict[str, str]:
    found = {"annotation": dashboard["annotations"]["list"][0]["target"]["rawSql"]}
    for panel in dashboard["panels"]:
        for target in panel["targets"]:
            found[f"{panel['id']}{target['refId']}"] = target["rawSql"]
    return found


def movement_statements(dashboard: dict) -> dict[str, str]:
    return {key: sql for key, sql in statements(dashboard).items() if "market_movement_snapshot" in sql}


def test_uid_and_variables_stay_put(dashboard):
    # uid를 바꾸면 Grafana가 같은 파일로 두 번째 대시보드를 만들고 갱신이 막힌다.
    assert dashboard["uid"] == "market-movement"
    assert [variable["name"] for variable in dashboard["templating"]["list"]] == ["ds", "symbol"]


def test_every_panel_reads_the_selected_datasource(dashboard):
    for panel in dashboard["panels"]:
        assert panel["datasource"] == {"type": "postgres", "uid": "${ds}"}
        for target in panel["targets"]:
            assert target["datasource"] == {"type": "postgres", "uid": "${ds}"}


def test_the_total_is_the_three_way_sum_never_the_five(dashboard):
    """**상한가는 상승에 포함된다**(실측). 다섯 값을 더하면 상·하한가가 이중 계산된다."""
    ratios = [sql for sql in movement_statements(dashboard).values() if "rising_count /" in sql]

    assert ratios, "상승 비율을 계산하는 패널이 없다"
    for sql in ratios:
        assert "nullif(rising_count + unchanged_count + falling_count, 0)" in sql
        assert "upper_limit_count +" not in sql
        assert "+ lower_limit_count" not in sql


def test_the_symbol_variable_matches_the_stored_vocabulary(dashboard):
    variable = next(v for v in dashboard["templating"]["list"] if v["name"] == "symbol")

    # 값을 손으로 적지 않고 저장된 것에서 읽는다. 시장이 늘면 화면이 따라온다.
    assert "SELECT DISTINCT symbol FROM market_movement_snapshot" in variable["query"]
    assert variable["multi"] is True
    assert {member.value for member in MovementMarket} == {"KOSPI", "KOSDAQ"}


def test_the_index_panel_joins_on_the_same_symbol(dashboard):
    """분포와 지수 봉을 잇는 것이 이 화면의 핵심이다. 두 테이블의 symbol 값이 같아야 한다."""
    divergence = [sql for sql in statements(dashboard).values() if "quote_bar" in sql]

    assert len(divergence) == 1
    sql = divergence[0]
    assert "symbol = ${symbol:sqlstring}" in sql
    # 지수 변동률은 저장하지 않고 여기서 계산한다.
    assert "(close - previous_close) / nullif(previous_close, 0)" in sql


def test_every_query_pins_the_provider(dashboard):
    """`symbol`은 제공처 안에서만 고유하다. provider 없는 조회는 제공처가 늘면 조용히 틀린다."""
    for key, sql in statements(dashboard).items():
        if "market_movement_snapshot" in sql or "quote_bar" in sql:
            assert "provider = 'kis'" in sql, key


def test_the_session_band_follows_the_trading_calendar(dashboard):
    """정규장 띠를 요일로 그리지 않는다. 공휴일에 띠가 서면 화면이 거짓말을 한다."""
    annotation = statements(dashboard)["annotation"]

    assert "FROM market_session" in annotation
    assert "market_code = 'KRX'" in annotation
    assert "effective_open_day" in annotation
    assert "EXTRACT(DOW" not in annotation


def test_the_latest_snapshot_shows_one_row_per_market(dashboard):
    table = statements(dashboard)["5A"]

    assert "DISTINCT ON (symbol)" in table
    assert "ORDER BY symbol, observed_at DESC" in table
    # 상·하한가는 상승·하락 안의 부분집합이라 따로 보여 준다.
    assert '"상한가"' in table
    assert '"하한가"' in table


def test_panels_do_not_invent_a_reset_row(dashboard):
    """개장 전·마감 후의 all-zero 응답은 저장되지 않는다. 화면이 0을 만들어 채우지 않는다."""
    for key, sql in movement_statements(dashboard).items():
        assert not re.search(r"COALESCE\([^)]*rising_count", sql), key
        assert "generate_series" not in sql, key
