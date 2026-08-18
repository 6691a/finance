"""수급 대시보드가 수집 규칙과 어긋나지 않는지 확인한다.

여기서 고정하는 것은 셋이다. **누적값을 거래일로 나눠 빼는가**, **슬롯을 시각처럼 쓰지
않는가**, **단위를 모르는 값에 단위를 붙이지 않는가**. 셋 다 틀려도 화면은 멀쩡해 보인다.
"""

import json
from pathlib import Path

import pytest

DASHBOARD = Path(__file__).resolve().parents[2] / "compose/local/grafana/dashboards/investor-flow.json"


@pytest.fixture(scope="module")
def dashboard() -> dict:
    return json.loads(DASHBOARD.read_text())


def statements(dashboard: dict) -> dict[str, str]:
    found = {"annotation": dashboard["annotations"]["list"][0]["target"]["rawSql"]}
    for panel in dashboard["panels"]:
        for target in panel["targets"]:
            found[f"{panel['id']}{target['refId']}"] = target["rawSql"]
    return found


def test_uid_and_variables_stay_put(dashboard):
    # uid를 바꾸면 Grafana가 같은 파일로 두 번째 대시보드를 만들고 갱신이 막힌다.
    assert dashboard["uid"] == "investor-flow"
    assert [variable["name"] for variable in dashboard["templating"]["list"]] == ["market", "stock"]


def test_every_query_pins_the_provider(dashboard):
    for key, sql in statements(dashboard).items():
        tables = (
            "market_investor_flow_snapshot",
            "stock_investor_estimate_snapshot",
            "stock_investor_trade_daily",
            "quote_bar",
        )
        if any(table in sql for table in tables):
            assert "provider = 'kis'" in sql, key


def test_the_delta_is_computed_per_trading_day(dashboard):
    """누적값은 장마다 0에서 다시 시작한다.

    거래일로 나누지 않고 빼면 하루 첫 스냅샷이 전날 마지막값만큼의 가짜 급변으로 보인다.
    """
    delta = statements(dashboard)["3A"]

    assert delta.count("lag(") == 3
    assert delta.count("PARTITION BY session_day ORDER BY observed_at") == 3
    assert "AT TIME ZONE 'Asia/Seoul')::date AS session_day" in delta


def test_no_delta_is_read_from_a_column(dashboard):
    """델타는 저장하지 않는다. 화면이 계산한다."""
    for key, sql in statements(dashboard).items():
        assert "_delta" not in sql, key


def test_the_slot_is_shown_as_a_time_only_on_screen(dashboard):
    """저장은 회차 코드, 표시는 시각이다. 종목코드를 이름으로 보여 주는 것과 같은 층이다.

    **모르는 회차는 시각을 지어내지 않는다.** 제공처가 회차를 늘리면 `슬롯 N`으로 그대로
    나와야 한다. 그러지 않으면 틀린 시각이 맞는 것처럼 보인다.
    """
    table = statements(dashboard)["5A"]

    assert 'END AS "갱신 시각"' in table
    assert "ELSE '슬롯 ' || e.source_time_code" in table
    # 정렬은 시각 문자열이 아니라 회차 코드로 한다.
    assert 'ORDER BY e.business_date DESC, e.source_time_code DESC, "종목"' in table


def test_the_estimate_slot_is_never_used_as_a_time_axis(dashboard):
    """응답에는 원천 시각이 없다. 가짜 시각을 데이터 점으로 만들지 않는다."""
    daily = statements(dashboard)["6A"]
    # 추이 패널은 거래일을 축으로 쓰고 회차는 그날 마지막 값을 고르는 데만 쓴다.
    assert "DISTINCT ON (e.stock_code, e.business_date)" in daily
    assert "ORDER BY e.stock_code, e.business_date, e.source_time_code DESC" in daily
    assert "source_time_code + " not in daily


def test_the_institution_breakdown_shows_all_seven_parts(dashboard):
    """일곱을 다 그려야 합이 기관계와 맞는지 화면에서 읽힌다.

    하나라도 빠지면 선 몇 개가 위아래로 움직이는 그림일 뿐이고, 기관계와 대조할 수 없다.
    """
    sql = statements(dashboard)["7A"]

    for column in (
        "securities",
        "investment_trust",
        "private_equity",
        "bank",
        "insurance",
        "merchant_bank",
        "pension_fund",
    ):
        assert f"{column}_net_buy_qty" in sql, column
    # 기관이 아닌 둘은 이 패널에 없다. 합쳐 그리면 기관계와 맞지 않는다.
    assert "other_corporation" not in sql
    assert "other_organization" not in sql


def test_units_are_not_invented(dashboard):
    """두 API의 배율이 다르고 아직 확정하지 못했다."""
    for panel_id in (2, 3, 7):
        panel = next(p for p in dashboard["panels"] if p["id"] == panel_id)
        assert "단위 미확정" in panel["fieldConfig"]["defaults"]["custom"]["axisLabel"]


def test_the_estimate_panels_say_they_are_estimates(dashboard):
    """확정 수급과 합치면 안 되는 값이다."""
    for panel_id in (5, 6):
        panel = next(p for p in dashboard["panels"] if p["id"] == panel_id)
        assert "추정" in panel["title"] or "추정" in panel["description"]


def test_stock_names_come_from_the_master(dashboard):
    for key in ("5A", "6A"):
        sql = statements(dashboard)[key]
        assert "COALESCE(named.name," in sql
        assert "FROM instrument AS i" in sql


def test_the_session_band_follows_the_trading_calendar(dashboard):
    """정규장 띠를 요일로 그리면 공휴일에 띠가 서서 화면이 거짓말을 한다."""
    annotation = statements(dashboard)["annotation"]

    assert "FROM market_session" in annotation
    assert "effective_open_day" in annotation
    assert "EXTRACT(DOW" not in annotation


def test_the_index_panel_joins_on_the_market_symbol(dashboard):
    """수급과 지수를 겹치는 것이 이 화면의 핵심이다."""
    index_sql = statements(dashboard)["4A"]

    assert "FROM quote_bar" in index_sql
    assert "symbol = '$market'" in index_sql


def test_the_confirmed_panels_are_not_labelled_as_estimates(dashboard):
    """추정과 확정을 합치면 안 된다. 값이 다르고 분류 수도 다르다."""
    for panel_id in (8, 9):
        panel = next(p for p in dashboard["panels"] if p["id"] == panel_id)
        text = panel["title"] + panel["description"]
        assert "확정" in text
        sql = statements(dashboard)[f"{panel_id}A"]
        # 한 패널이 두 테이블을 섞어 읽지 않는다.
        assert "stock_investor_trade_daily" in sql
        assert "stock_investor_estimate_snapshot" not in sql


def test_the_confirmed_panels_say_the_unit(dashboard):
    """이 API에서 단위가 확정됐다. 수량은 주다."""
    for panel_id in (8, 9):
        panel = next(p for p in dashboard["panels"] if p["id"] == panel_id)
        assert "주" in panel["fieldConfig"]["defaults"]["custom"]["axisLabel"]


def test_the_confirmed_breakdown_shows_all_seven_parts(dashboard):
    sql = statements(dashboard)["9A"]

    for label in ("금융투자", "투자신탁", "사모펀드", "은행", "보험", "종금", "기금"):
        assert label in sql, label


def test_the_confirmed_panels_follow_the_stock_variable(dashboard):
    for panel_id in (8, 9):
        sql = statements(dashboard)[f"{panel_id}A"]
        assert "t.stock_code IN (${stock:sqlstring})" in sql
        assert "COALESCE(named.name," in sql


def test_the_candle_panel_is_repeated_per_stock(dashboard):
    """캔들은 한 패널에 한 종목만 그릴 수 있다.

    여러 종목을 IN 으로 넣으면 행이 섞여 캔들이 뒤엉킨다. 패널을 복제해야 한다.
    """
    panel = next(p for p in dashboard["panels"] if p["id"] == 10)
    sql = statements(dashboard)["10A"]

    assert panel["type"] == "candlestick"
    assert panel["repeat"] == "stock"
    assert "t.stock_code = '$stock'" in sql
    assert "IN (${stock:sqlstring})" not in sql


def test_the_candle_panel_reads_all_four_prices(dashboard):
    sql = statements(dashboard)["10A"]

    for column, alias in (
        ("open_price", "open"),
        ("high_price", "high"),
        ("low_price", "low"),
        ("close_price", "close"),
    ):
        assert f't.{column} AS "{alias}"' in sql, column
