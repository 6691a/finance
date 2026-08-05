import pytest

from tests.helpers import NO_REVISION_REASON, head_sql, revision_files

pytestmark = pytest.mark.skipif(not revision_files(), reason=NO_REVISION_REASON)


def test_instrument_migration_emits_master_table(capsys):
    sql = head_sql(capsys)

    assert "CREATE TABLE instrument" in sql
    assert "ticker TEXT NOT NULL" in sql
    assert "market VARCHAR(20) NOT NULL" in sql
    assert "kind VARCHAR(20) NOT NULL" in sql
    assert "currency TEXT NOT NULL" in sql
    assert "source_symbol TEXT" in sql
    assert "is_watched BOOLEAN DEFAULT true NOT NULL" in sql


def test_instrument_migration_constrains_natural_key_and_enum_domains(capsys):
    sql = head_sql(capsys)

    assert "CONSTRAINT uq_instrument_ticker_market UNIQUE (ticker, market)" in sql
    assert "CONSTRAINT ck_instrument_market CHECK (market IN ('kospi', 'kosdaq', 'nyse', 'nasdaq'))" in sql
    assert "CONSTRAINT ck_instrument_kind CHECK (kind IN ('equity', 'etf', 'index'))" in sql


def test_instrument_migration_documents_table_and_columns(capsys):
    sql = head_sql(capsys)

    assert "COMMENT ON TABLE instrument IS '시세·뉴스·시그널이 참조하는 추적 종목 마스터'" in sql
    assert (
        "COMMENT ON COLUMN instrument.source_symbol IS "
        "'수집 소스에서 쓰는 심볼. 티커와 다를 때만 채운다(예: KOSPI → ^KS11)'"
    ) in sql
