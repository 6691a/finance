import pytest
from sqlalchemy import CheckConstraint

from apps.models.content import Document, DocumentSource
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
    assert declared >= {"ck_document_type", "ck_document_direction", "ck_document_body_status"}
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


def test_the_body_status_column_is_nullable_because_null_is_the_collection_queue(capsys):
    """NULL이 "아직 시도하지 않았다"이고 그 집합이 곧 본문 수집 큐다.

    NOT NULL로 두면 기본값을 정해야 하고, 어떤 값을 골라도 "안 해 봤다"와 "해 봤는데
    이랬다"가 같아 보인다.
    """
    sql = head_sql(capsys)

    assert Document.__table__.columns["body_status"].nullable
    assert "ALTER TABLE document ADD COLUMN body_status" in sql
    assert (
        "CONSTRAINT ck_document_body_status CHECK "
        "(body_status IN ('ok', 'empty', 'attachment_only', 'unavailable'))" in sql
    )


def test_document_attachment_keys_on_the_url_not_the_position(capsys):
    """페이지 마크업이 바뀌면 순서가 흔들려 같은 파일이 새 행이 된다."""
    sql = head_sql(capsys)

    assert "CREATE TABLE document_attachment" in sql
    assert "CONSTRAINT uq_document_attachment_natural_key UNIQUE (document_id, url)" in sql
    assert "FOREIGN KEY(document_id) REFERENCES document (id) ON DELETE CASCADE" in sql


def test_a_video_attachment_may_not_carry_a_storage_path(capsys):
    """영상은 내려받지 않는다. 경로가 붙어 있으면 코드가 규칙을 어긴 것이라 DB가 막는다."""
    sql = head_sql(capsys)

    assert (
        "CONSTRAINT ck_document_attachment_video_has_no_path CHECK "
        "(kind <> 'video' OR storage_path IS NULL)" in sql
    )


def test_enabled_sources_are_raised_to_full_text(capsys):
    """본문을 받는 것은 정책 변경이다. 코드가 아니라 `collection_mode`가 그것을 연다."""
    sql = head_sql(capsys)

    assert "UPDATE document_source" in sql
    assert "SET collection_mode = 'full_text'" in sql


def test_content_level_is_gone(capsys):
    """읽는 코드가 없는데 CHECK만 걸려 운영 태스크를 죽였다(2026-08-30).

    "그 문서에 본문이 있나"는 `body_status`가 더 정확히 답한다. 정책은 원래부터
    `document_source.collection_mode`가 원본이다.
    """
    sql = head_sql(capsys)

    assert "content_level" not in Document.__table__.columns
    assert "ALTER TABLE document DROP COLUMN content_level" in sql
    assert "DROP CONSTRAINT ck_document_metadata_only_has_no_body" in sql
    # 정책 컬럼은 그대로다. 둘은 다른 것이다.
    assert "collection_mode" in DocumentSource.__table__.columns
