import pytest

from tests.helpers import NO_REVISION_REASON, head_sql, revision_files

pytestmark = pytest.mark.skipif(not revision_files(), reason=NO_REVISION_REASON)


def test_source_record_keeps_lineage_columns_and_state_checks(capsys):
    sql = head_sql(capsys)

    assert "CREATE TABLE source_record" in sql
    assert "source_type VARCHAR(20) NOT NULL" in sql
    assert "source_key TEXT NOT NULL" in sql
    assert "started_at TIMESTAMP WITH TIME ZONE NOT NULL" in sql
    assert "completed_at TIMESTAMP WITH TIME ZONE" in sql
    assert "record_count INTEGER DEFAULT 0 NOT NULL" in sql
    assert "content_sha256" not in sql
    assert "payload JSONB" in sql
    assert "payload_uri TEXT" in sql
    assert "metadata JSONB DEFAULT '{}'::jsonb NOT NULL" in sql
    assert "CONSTRAINT ck_source_record_source_type CHECK (source_type IN ('api', 'crawl', 'websocket'))" in sql
    assert (
        "CONSTRAINT ck_source_record_status CHECK (status IN ('running', 'succeeded', 'failed', 'quarantined'))"
    ) in sql


def test_indicator_observation_keeps_natural_key_and_source_reference(capsys):
    sql = head_sql(capsys)

    assert "CREATE TABLE indicator_observation" in sql
    assert "source_record_id BIGINT NOT NULL" in sql
    assert "realtime_start" not in sql
    assert "realtime_end" not in sql
    assert "quality_status" not in sql
    assert "CONSTRAINT uq_indicator_observation_natural_key UNIQUE (series_id, observation_date)" in sql
    assert "CREATE INDEX ix_indicator_observation_source_record_id" in sql


def test_migrations_never_pin_a_schema(capsys):
    sql = head_sql(capsys)

    # Tables follow the connection's search_path, so nothing qualifies them.
    assert "CREATE SCHEMA" not in sql
    assert "raw." not in sql
    assert "market." not in sql
    assert "reference." not in sql


def test_every_table_carries_common_columns(capsys):
    sql = head_sql(capsys)

    # `alembic_version*` is Alembic's own bookkeeping, and `exchange_rate` copies the
    # DDL of an external table, so neither derives from EntityBase.
    tables = (
        sql.count("CREATE TABLE ") - sql.count("CREATE TABLE alembic_version") - sql.count("CREATE TABLE exchange_rate")
    )
    assert tables >= 3
    assert sql.count("id BIGSERIAL NOT NULL") == tables
    assert sql.count("created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL") == tables
    assert sql.count("updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL") == tables


def test_migrations_document_table_purposes(capsys):
    sql = head_sql(capsys)

    assert "COMMENT ON TABLE source_record IS 'API, 크롤링, 웹소켓 수집 단위의 출처와 상태를 보존하는 테이블'" in sql
    assert (
        "COMMENT ON TABLE indicator_observation IS "
        "'FRED 지표 관측값을 조회 가능한 형태로 정규화하고 원본과 연결하는 테이블'"
    ) in sql
    assert "COMMENT ON COLUMN source_record.payload IS '작은 JSON 원본; 저장하지 않으면 NULL'" in sql
    assert "COMMENT ON COLUMN indicator_observation.source_record_id IS '근거가 되는 source_record 레코드 ID'" in sql
