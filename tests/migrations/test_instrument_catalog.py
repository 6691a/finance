"""추적 종목 마스터 시드가 수집기 대상과 어긋나지 않는지 확인한다.

**관측 테이블에서 이 마스터로 외래키를 걸지 않는다.** 걸면 마스터 행이 없는 종목을 수집기가
저장하지 못해, 수집기 Enum에만 추가하고 시드를 빠뜨린 순간 DAG가 죽는다. 대신 여기서
대조한다. `indicator_series`·`quote_symbol` 카탈로그 테스트와 같은 장치다.
"""

import pytest

from modules.collectors.document.dart import DartCompany
from modules.collectors.kis_positioning import PositioningStock
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
