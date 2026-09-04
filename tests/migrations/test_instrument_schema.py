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


def test_instrument_migration_adds_the_filing_entity_and_sector_columns(capsys):
    sql = head_sql(capsys)

    assert "ADD COLUMN filing_entity_id TEXT" in sql
    assert "ADD COLUMN sector TEXT" in sql
    # 번호가 없다는 것이 "규제 공시 대상이 아니다"라는 뜻이다. NOT NULL이면 그 뜻이 사라진다.
    assert "ADD COLUMN filing_entity_id TEXT NOT NULL" not in sql
    assert "ADD COLUMN sector TEXT NOT NULL" not in sql
    # 섹터는 값이 바뀌는 것이 전제다. CHECK를 걸면 섹터 하나 들일 때 마이그레이션이 두 벌이 된다.
    assert "ck_instrument_sector" not in sql


def test_the_new_instrument_column_comments_match_the_model(capsys):
    """마이그레이션과 모델의 주석이 어긋나면 다음 autogenerate가 매번 COMMENT ON 차이를 낸다."""
    from apps.models.reference import Instrument

    sql = head_sql(capsys)
    columns = Instrument.__table__.c

    for name in ("filing_entity_id", "sector"):
        comment = columns[name].comment
        assert comment
        assert f"COMMENT ON COLUMN instrument.{name} IS '{comment}'" in sql
