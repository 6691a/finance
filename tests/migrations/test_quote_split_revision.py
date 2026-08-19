"""quote_bar/quote_daily를 kind별 테이블로 가른 리비전의 테이블 단위 사실.

특정 리비전 ID나 전체 문자열에 고정하지 않는다. offline SQL에 다음이 있으면 충분하다:
16개 물리 테이블 생성, 원본 두 테이블 DROP, 같은 이름의 호환 뷰 생성.
"""

import pytest

from tests.helpers import NO_REVISION_REASON, head_sql, revision_files

pytestmark = pytest.mark.skipif(not revision_files(), reason=NO_REVISION_REASON)

MACRO_TABLES = (
    "index_bar",
    "index_future_bar",
    "fx_bar",
    "rate_bar",
    "bond_future_bar",
    "commodity_bar",
    "crypto_bar",
    "index_daily",
    "index_future_daily",
    "fx_daily",
    "rate_daily",
    "bond_future_daily",
    "commodity_daily",
    "crypto_daily",
)


@pytest.mark.parametrize("table", MACRO_TABLES)
def test_each_kind_gets_its_own_table(table, capsys):
    sql = head_sql(capsys)

    assert f"CREATE TABLE {table}" in sql
    key = "bar_at" if table.endswith("_bar") else "business_date"
    assert f"CONSTRAINT uq_{table}_natural_key UNIQUE (provider, symbol, {key})" in sql


def test_stock_tables_carry_the_exchange_axis(capsys):
    sql = head_sql(capsys)

    assert "CREATE TABLE stock_bar" in sql
    assert "CONSTRAINT uq_stock_bar_natural_key UNIQUE (provider, stock_code, exchange, bar_at)" in sql
    assert "CREATE TABLE stock_daily" in sql
    assert "CONSTRAINT uq_stock_daily_natural_key UNIQUE (provider, stock_code, exchange, business_date)" in sql
    # 통합(UN)이 끼어들 수 없게 CHECK가 거래소를 제한한다.
    assert "exchange IN ('KRX', 'NXT', 'NYSE')" in sql


def test_the_contract_code_lives_only_in_the_future_table(capsys):
    sql = head_sql(capsys)

    create_future = sql.split("CREATE TABLE index_future_bar")[1].split(";")[0]
    assert "contract_code" in create_future
    create_index = sql.split("CREATE TABLE index_bar")[1].split(";")[0]
    assert "contract_code" not in create_index


def test_the_old_tables_become_compatibility_views(capsys):
    sql = head_sql(capsys)

    assert "DROP TABLE quote_bar" in sql
    assert "DROP TABLE quote_daily" in sql
    assert "CREATE VIEW quote_bar" in sql
    assert "CREATE VIEW quote_daily" in sql
    # 뷰에 NXT를 태우면 같은 종목·같은 분에 두 줄이 생긴다.
    assert "WHERE exchange IN ('KRX', 'NYSE')" in sql


def test_nasdaq_joins_the_exchange_axis(capsys):
    # SK하이닉스 ADR(SKHY)은 나스닥 상장이다. CHECK와 호환 뷰가 NASDAQ을 태워야
    # 봉이 저장되고 브리핑·Grafana 조회(뷰)에 보인다.
    sql = head_sql(capsys)

    assert "exchange IN ('KRX', 'NXT', 'NYSE', 'NASDAQ')" in sql
    assert "WHERE exchange IN ('KRX', 'NYSE', 'NASDAQ')" in sql
