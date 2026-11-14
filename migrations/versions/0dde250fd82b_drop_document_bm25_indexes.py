"""drop document bm25 indexes

Revision ID: 0dde250fd82b
Revises: d7c3a9e15b42
Create Date: 2026-11-14 23:30:00.000000

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0dde250fd82b"
down_revision: str | Sequence[str] | None = "d7c3a9e15b42"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# BM25 색인 둘(`f1a47d0c62b8`)을 내린다(2026-11-14). 저장소 어디에도 이 색인으로 검색하는
# 질의(`@@@`·`pdb.match`)가 없고, 쓰기마다 `pdb.lindera('korean')` 형태소 분석 비용만 문다.
#
# k3s로 옮기며 덤프를 복원했을 때 `document_attachment_bm25` 하나를 새로 짓는 데 5,780건 중
# 2,015건까지 55분이 걸렸다(첨부 평균 2만 자, 최대 89만 자). 색인은 쓰는 곳이 생길 때 다시 짓는다.
#
# 첨부 텍스트(`document_attachment.extracted_text`)와 그것을 채우는 DAG는 그대로 둔다.
# 확장(pg_search)도 지우지 않는다.
DOCUMENT_INDEX = "document_bm25"
ATTACHMENT_INDEX = "document_attachment_bm25"


def upgrade(engine_name: str) -> None:
    _run(f"upgrade_{engine_name}")


def downgrade(engine_name: str) -> None:
    _run(f"downgrade_{engine_name}")


def _run(name: str) -> None:
    # A revision written before an alias existed has no section for it, and
    # there is nothing for that alias to do. Adding an alias must not force a
    # no-op edit to every past revision.
    operations = globals().get(name)
    if operations is not None:
        operations()


def upgrade_default() -> None:
    # 쓰기를 막지 않는다. 이미 손으로 지운 운영 DB에서는 IF EXISTS로 아무것도 안 한다.
    with op.get_context().autocommit_block():
        op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {ATTACHMENT_INDEX}")
        op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {DOCUMENT_INDEX}")


def downgrade_default() -> None:
    # `f1a47d0c62b8`의 정의에 `70e8e9ce64d3`의 mutable_segment_rows=0을 더한 모양으로 되짓는다.
    with op.get_context().autocommit_block():
        op.execute(
            f"""
            CREATE INDEX CONCURRENTLY IF NOT EXISTS {DOCUMENT_INDEX} ON document
            USING bm25 (id, (title::pdb.lindera('korean')), (body::pdb.lindera('korean')))
            WITH (key_field='id', mutable_segment_rows=0)
            """
        )
        op.execute(
            f"""
            CREATE INDEX CONCURRENTLY IF NOT EXISTS {ATTACHMENT_INDEX} ON document_attachment
            USING bm25 (id, (extracted_text::pdb.lindera('korean')))
            WITH (key_field='id', mutable_segment_rows=0)
            """
        )
