"""추적 종목 마스터 시드가 수집기 대상과 어긋나지 않는지 확인한다.

**관측 테이블에서 이 마스터로 외래키를 걸지 않는다.** 걸면 마스터 행이 없는 종목을 수집기가
저장하지 못해, 수집기 Enum에만 추가하고 시드를 빠뜨린 순간 DAG가 죽는다. 대신 여기서
대조한다. `indicator_series`·`quote_symbol` 카탈로그 테스트와 같은 장치다.
"""

import re

import pytest

from modules.collectors import kis
from modules.collectors.document.dart import DartCompany
from modules.collectors.market.kis_positioning import PositioningStock
from tests.helpers import NO_REVISION_REASON, head_sql, revision_files

pytestmark = pytest.mark.skipif(not revision_files(), reason=NO_REVISION_REASON)


@pytest.mark.parametrize("stock", sorted(PositioningStock, key=lambda member: member.value))
def test_every_collected_stock_has_a_master_row(stock, capsys):
    sql = head_sql(capsys)

    assert f"'{stock.value}'" in sql, f"{stock.label} 시드가 없다"
    assert f"'{stock.label}'" in sql


def test_the_two_collectors_agree_on_the_stock_list():
    # 공시와 포지션이 같은 종목을 봐야 한 키로 이어진다.
    assert {stock.value for stock in PositioningStock} == {company.value for company in DartCompany}


def test_instruments_are_seeded_by_the_migration_not_the_app(capsys):
    sql = head_sql(capsys)

    # 시드는 마이그레이션이 넣는다. 리비전에서 앱 코드를 import하면 나중에 Enum이 바뀔 때
    # 과거 리비전의 결과가 따라 바뀐다.
    assert "INSERT INTO instrument" in sql
    assert "is_watched" in sql


INSTRUMENT_INSERT = re.compile(
    r"INSERT INTO instrument \(ticker, market, name, kind, currency, is_watched\) "
    r"VALUES \('(?P<ticker>[^']+)', '[^']*', '[^']*', '[^']*', '[^']*', (?P<watched>true|false)\)"
)


def test_only_the_collected_stocks_are_watched(capsys):
    """`is_watched`가 참인 종목은 시세를 받는 종목과 정확히 같아야 한다.

    **이 플래그 하나가 여섯을 켠다** — 투자의견 수집, 기술지표 일봉 요청, 주간 인과 그래프
    대상, 그리고 추론 subject다. 시세가 없는 종목이 참이 되면 조회가 조인에서 빠지거나
    baseline 셋이 NULL로 들어가고, `ck_thesis_base_all_or_none`이 그 조합을 허용하므로
    **오류 없이 빈 값이 쌓인다.**

    위의 `test_every_collected_stock_has_a_master_row`는 "수집 종목마다 시드 행이 있는가"
    한 방향만 본다. 반대 방향(참인 행이 수집 목록보다 넓은가)이 여기다.

    문서 태그 후보는 `is_watched`를 안 보고 마스터 전체를 읽는다(`select_taggable.sql`).
    그래서 마스터가 넓어지는 것 자체는 이 테스트를 깨지 않는다.
    """
    sql = head_sql(capsys)
    seeded = {match["ticker"]: match["watched"] == "true" for match in INSTRUMENT_INSERT.finditer(sql)}
    watched = {ticker for ticker, is_watched in seeded.items() if is_watched}

    assert watched == {stock.value for stock in kis.DomesticStock}
    assert watched == {stock.value for stock in PositioningStock}
    # 마스터가 시세 목록보다 넓다는 것이 이 확장의 전제다. 같아지면 태그 후보가 다시 좁아진 것이다.
    assert len(seeded) > len(watched)
