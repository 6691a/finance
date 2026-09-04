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


FILING_ENTITY_UPDATE = re.compile(
    r"UPDATE instrument SET filing_entity_id = '(?P<entity_id>\d+)', sector = '(?P<sector>[^']+)' "
    r"WHERE market = 'kospi' AND ticker = '(?P<ticker>[^']+)'"
)


def seeded_filing_entities(sql: str) -> dict[str, tuple[str, str]]:
    return {match["ticker"]: (match["entity_id"], match["sector"]) for match in FILING_ENTITY_UPDATE.finditer(sql)}


def test_every_filing_entity_has_a_sector(capsys):
    """공시·실적 대상 스무 곳에 회사 번호와 섹터가 함께 들어간다.

    **섹터가 비면 거시 집계가 회사 단위로 떨어진다.** 그러면 대표를 교체한 해의 점프가
    산업 변화인지 명단 변화인지 가릴 수 없다.
    """
    seeded = seeded_filing_entities(head_sql(capsys))

    assert len(seeded) == 20
    assert all(entity_id and sector for entity_id, sector in seeded.values())
    # 반도체만 둘이고 나머지는 섹터당 하나다.
    sectors = [sector for _, sector in seeded.values()]
    assert len(set(sectors)) == 19
    assert sectors.count("반도체") == 2


def test_filing_entities_cover_the_disclosure_collector(capsys):
    """공시 수집기가 아는 회사는 전부 마스터에 번호가 있어야 한다.

    반대 방향(번호가 있는데 수집기 Enum에 없는 것)은 **정상이다.** 이 칸이 `is_watched`와
    다른 축이라는 것이 그 뜻이고, 대상을 넓히는 동안 Enum이 뒤따라온다.
    """
    seeded = seeded_filing_entities(head_sql(capsys))

    for company in DartCompany:
        assert company.value in seeded, f"{company.label} 회사 번호가 시드에 없다"
        assert seeded[company.value][0] == company.corp_code


def test_filing_entity_ids_are_dart_corp_codes(capsys):
    """한국 시장의 번호는 DART 회사 고유번호 8자리다.

    발급 기관은 `market`이 정한다. 미국 종목이 들어오면 같은 칸에 SEC CIK가 들어가므로,
    자릿수를 여기서 잠가 두면 그때 이 테스트가 갈릴 자리를 알려 준다.
    """
    for entity_id, _ in seeded_filing_entities(head_sql(capsys)).values():
        assert len(entity_id) == 8 and entity_id.isdigit()


def test_watched_stays_narrower_than_the_filing_entities(capsys):
    """시세 대상은 늘지 않는다.

    이 칸을 만든 이유가 그것이다 — 공시를 스무 곳으로 넓히면서 분봉·수급·실시간 구독까지
    함께 끌고 가지 않으려고 축을 나눴다.
    """
    sql = head_sql(capsys)
    seeded = {match["ticker"]: match["watched"] == "true" for match in INSTRUMENT_INSERT.finditer(sql)}
    watched = {ticker for ticker, is_watched in seeded.items() if is_watched}

    assert watched < set(seeded_filing_entities(sql))
