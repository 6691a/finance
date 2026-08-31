"""add document_attachment parse columns

Revision ID: e5c93b18ad7f
Revises: a2f7c31e9b64
Create Date: 2026-08-31 21:10:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e5c93b18ad7f"
down_revision: str | Sequence[str] | None = "a2f7c31e9b64"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 첨부 PDF에서 뽑은 텍스트를 첨부 행에 붙인다. 설계는 `docs/analysis/pdf-parsing-bm25.md` 6절이다.
#
# **컬럼을 지금 더하는 이유는 읽고 쓰는 코드가 같은 변경에 함께 들어오기 때문이다.**
# `modules/collectors/document/pdf.py`가 쓰고 `document_attachment_parse_hourly`가 부른다.
# 읽는 코드 없이 NULL 컬럼만 먼저 넣지 않는다 — `content_level`이 그렇게 남았다가 운영
# 태스크를 죽인 적이 있다(c4e28b71fa09).
#
# `parse_status`가 NULL이면 "아직 해 보지 않았다"이고 그 집합이 곧 파싱 큐다. `body_status`와
# 같은 규칙이라 연결·I/O 실패에는 상태를 남기지 않는다.
PARSE_STATUS = sa.Enum(
    "ok",
    "partial",
    "failed",
    "unsupported",
    name="attachmentparsestatus",
    native_enum=False,
    length=20,
)
PARSE_STATUS_CHECK = "ck_document_attachment_parse_status"

COLUMNS = (
    (
        "parse_status",
        PARSE_STATUS,
        "첨부 파일에서 텍스트를 뽑아 본 결과. NULL은 아직 해 보지 않았다는 뜻이고 그 집합이 파싱 큐다",
    ),
    (
        "extracted_text",
        sa.Text(),
        "첨부에서 뽑은 본문. 페이지 표식(<!-- page:n -->)을 유지하고 길이 상한을 두지 않는다",
    ),
    (
        "parsed_sha256",
        sa.Text(),
        "이 텍스트를 만든 파일의 SHA-256. sha256과 다르면 파일이 바뀐 것이라 다시 파싱한다",
    ),
    (
        "parser_version",
        sa.Text(),
        "텍스트를 만든 파서의 이름과 판(예: pymupdf/1). 규칙이 바뀌면 올라가고 재처리 대상 판정에 쓴다",
    ),
    (
        "parsed_at",
        sa.DateTime(timezone=True),
        "파싱한 시각(UTC)",
    ),
    (
        "page_count",
        sa.Integer(),
        "PDF의 전체 페이지 수. 아래 값의 분모다",
    ),
    (
        "unreadable_page_count",
        sa.Integer(),
        (
            "글자가 나오지 않은 페이지 수(스캔·이미지 페이지). 이 비율이 외부 Vision을 켤지 정하는 "
            "유일한 근거다 — docs/analysis/pdf-vision-analysis.md 5절"
        ),
    ),
)


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
    for name, column_type, comment in COLUMNS:
        op.add_column(
            "document_attachment",
            sa.Column(name, column_type, nullable=True, comment=comment),
        )
    op.create_check_constraint(
        PARSE_STATUS_CHECK,
        "document_attachment",
        "parse_status IS NULL OR parse_status IN ('ok', 'partial', 'failed', 'unsupported')",
    )


def downgrade_default() -> None:
    op.drop_constraint(PARSE_STATUS_CHECK, "document_attachment", type_="check")
    for name, _, _ in reversed(COLUMNS):
        op.drop_column("document_attachment", name)
