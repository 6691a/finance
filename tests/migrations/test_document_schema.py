import pytest
from sqlalchemy import CheckConstraint

from apps.models.content import Document
from tests.helpers import NO_REVISION_REASON, head_sql, revision_files

pytestmark = pytest.mark.skipif(not revision_files(), reason=NO_REVISION_REASON)


def check_constraint_names(table) -> set[str]:
    return {
        constraint.name
        for constraint in table.constraints
        if isinstance(constraint, CheckConstraint) and constraint.name is not None
    }


def test_document_migration_creates_the_table_and_its_natural_key(capsys):
    sql = head_sql(capsys)

    assert "CREATE TABLE document" in sql
    # `content_hash`는 자연키에 넣지 않는다. 넣으면 본문이 조금만 달라져도 같은 기사가 매시간 쌓인다.
    assert "CONSTRAINT uq_document_natural_key UNIQUE (source_slug, external_id)" in sql


def test_every_enum_column_of_document_has_a_check_constraint(capsys):
    """모델이 선언한 CHECK가 실제 DDL에도 전부 나와야 한다.

    Alembic autogenerate는 CHECK 제약을 비교하지 않는다. 모델에만 넣고 리비전을 빠뜨려도
    아무도 알려 주지 않으므로 여기서 대조한다. `direction`이 실제로 그렇게 빠져 있었다.
    """
    sql = head_sql(capsys)

    declared = check_constraint_names(Document.__table__)
    assert declared >= {"ck_document_type", "ck_document_content_level", "ck_document_direction"}
    for name in declared:
        assert f"CONSTRAINT {name} CHECK" in sql


def test_the_source_kind_check_admits_research_after_the_naver_revision(capsys):
    """6단계가 `research`를 더했다. 모델의 CHECK 문자열과 마지막 리비전이 글자 그대로 같아야 한다."""
    sql = head_sql(capsys)

    assert "CONSTRAINT ck_document_source_kind CHECK (source_kind IN ('official', 'media', 'research'))" in sql
    # 초판 CHECK를 떼고 다시 건다. 떼지 않으면 `research` 행 INSERT가 막힌다.
    assert "ALTER TABLE document_source DROP CONSTRAINT ck_document_source_kind" in sql
    assert sql.count("INSERT INTO document_source") >= 6
    assert "naver_research_company" in sql
    assert "naver_research_debenture" in sql


def test_the_direction_check_allows_the_unassessed_document(capsys):
    # 평가 전이면 `direction`이 NULL이다. SQL에서 NULL은 CHECK를 통과하므로 조건에
    # `IS NULL`을 따로 넣지 않는다. 대신 NULL을 허용하는 컬럼이라는 사실을 고정한다.
    sql = head_sql(capsys)

    assert Document.__table__.columns["direction"].nullable
    assert "CONSTRAINT ck_document_direction CHECK (direction IN ('positive', 'negative', 'neutral'))" in sql
