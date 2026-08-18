"""포지션 대시보드가 수집 규칙과 어긋나지 않는지 확인한다.

패널 SQL은 코드가 아니라 JSON 문자열이라 리팩터링이 따라오지 않는다. 여기서 고정하는 것은
**단위를 모르는 값을 단위가 있는 척 보여 주지 않는가**와 **미발표 행을 0으로 그리지
않는가** 둘이다. 둘 다 틀려도 화면은 멀쩡해 보인다.
"""

import json
from pathlib import Path

import pytest

DASHBOARD = Path(__file__).resolve().parents[2] / "compose/local/grafana/dashboards/market-positioning.json"


@pytest.fixture(scope="module")
def dashboard() -> dict:
    return json.loads(DASHBOARD.read_text())


def statements(dashboard: dict) -> dict[int, str]:
    return {
        panel["id"]: target["rawSql"]
        for panel in dashboard["panels"]
        for target in panel.get("targets", [])
        if "rawSql" in target
    }


def test_uid_and_variables_stay_put(dashboard):
    # uid를 바꾸면 Grafana가 같은 파일로 두 번째 대시보드를 만들고 갱신이 막힌다.
    assert dashboard["uid"] == "market-positioning"
    assert [variable["name"] for variable in dashboard["templating"]["list"]] == ["stock", "universe"]


def test_every_query_pins_the_provider(dashboard):
    for panel_id, sql in statements(dashboard).items():
        assert "provider = 'kis'" in sql, f"패널 {panel_id}"


def test_the_stock_picker_shows_names_but_queries_codes(dashboard):
    """`005930`은 사람이 읽는 이름이 아니다. 표시와 조회 값을 가른다."""
    variable = next(v for v in dashboard["templating"]["list"] if v["name"] == "stock")

    # Grafana 는 __text/__value 두 칼럼을 보고 표시와 값을 가른다.
    assert "name AS __text" in variable["query"]
    assert "ticker AS __value" in variable["query"]
    assert "FROM instrument" in variable["query"]
    assert variable["multi"] is True


def test_stock_series_are_labelled_with_the_master_name(dashboard):
    """범례에 종목코드를 그대로 쓰지 않는다. 이름은 마스터 한 곳에서만 온다.

    시장 단위 패널은 해당하지 않는다. `KOSPI`·`KOSDAQ`는 그 자체로 읽히는 값이다.
    """
    labelled = [sql for sql in statements(dashboard).values() if "stock_code AS metric" in sql or "named.name" in sql]

    assert len(labelled) == 3
    for sql in labelled:
        assert "COALESCE(named.name," in sql
        assert "FROM instrument AS i" in sql


def test_unpublished_short_sale_rows_are_hidden_but_real_zeros_are_kept(dashboard):
    """당일 행은 0으로 온다(실측). 그건 '공매도가 없었다'가 아니라 '아직 발표 전'이다.

    반대로 진짜 0인 날은 남겨야 한다. 공매도 금지 같은 제도 변화가 그 모양이기 때문이다.
    그래서 조건이 "0이면 숨김"이 아니라 "마지막 영업일이면서 0이면 숨김"이어야 한다.
    """
    short_sale = [sql for sql in statements(dashboard).values() if "krx_stock_short_sale_daily" in sql]

    assert len(short_sale) == 2
    for sql in short_sale:
        assert "short_sale_quantity = 0" in sql
        assert "max(latest.business_date)" in sql
        # 0을 통째로 거르면 공매도 금지 구간이 화면에서 사라진다.
        assert "short_sale_quantity > 0" not in sql


def test_credit_balance_is_charted_on_the_trade_date(dashboard):
    """결제일로 그리면 추이가 2영업일씩 밀린다."""
    credit = [sql for sql in statements(dashboard).values() if "krx_stock_credit_balance_daily" in sql]

    assert credit
    for sql in credit:
        assert "trade_date" in sql
        assert "settlement_date" not in sql


def test_amounts_without_a_confirmed_unit_are_not_dressed_up(dashboard):
    """단위를 모르는 금액에 억원·백만원 같은 라벨을 붙이지 않는다."""
    funds = next(panel for panel in dashboard["panels"] if panel["id"] == 6)

    assert "단위 미확정" in funds["fieldConfig"]["defaults"]["custom"]["axisLabel"]
    # 신용잔고 금액도 단위가 확정되지 않아 화면은 수량을 쓴다.
    quantity_panels = [panel for panel in dashboard["panels"] if panel["id"] in (1, 3)]
    for panel in quantity_panels:
        sql = panel["targets"][0]["rawSql"]
        assert "loan_balance_quantity" in sql
        assert "loan_balance_amount" not in sql


def test_the_ranking_table_shows_one_universe_and_its_latest_snapshot(dashboard):
    """모집단을 고르지 않으면 전체와 코스닥이 한 표에 섞인다. 기준일도 그 안에서 찾아야 한다."""
    ranking = statements(dashboard)[7]

    assert ranking.count("universe_code = '$universe'") == 2
    assert "max(latest.standard_date)" in ranking
    assert "ORDER BY rank" in ranking
    # 증가율은 저장된 값을 그대로 보여 준다. 화면에서 다시 계산하지 않는다.
    assert "loan_balance_growth_rate" in ranking


def test_the_universe_choices_match_what_the_collector_requests(dashboard):
    from modules.collectors.kis_positioning import RANKING_UNIVERSES

    variable = next(v for v in dashboard["templating"]["list"] if v["name"] == "universe")

    for universe, label in RANKING_UNIVERSES:
        assert universe in variable["query"]
        assert label in variable["query"]


def test_market_lending_keeps_the_two_markets_apart(dashboard):
    """제공처가 주는 합계는 저장하지 않는다. 화면도 두 시장을 그대로 그린다."""
    market_lending = statements(dashboard)[8]

    assert "krx_market_securities_lending_daily" in market_lending
    assert "market_code AS metric" in market_lending
    # 합계를 화면에서 만들어 넣지 않는다. 필요하면 두 선을 더해 읽는다.
    assert "sum(" not in market_lending.lower()
