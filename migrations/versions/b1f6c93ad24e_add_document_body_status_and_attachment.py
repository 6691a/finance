"""add document body_status and document_attachment

Revision ID: b1f6c93ad24e
Revises: d51c9a7be402
Create Date: 2026-08-30 18:20:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b1f6c93ad24e"
down_revision: str | Sequence[str] | None = "d51c9a7be402"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 설계는 `docs/collection/document-body-collection.md`다.
#
# 본문을 채우려면 셋이 먼저 있어야 한다. "이 문서는 본문을 받아 봤나"를 남길 자리
# (`body_status`), 본문이 첨부 PDF에만 있는 출처의 파일을 둘 자리(`document_attachment`),
# 그리고 본문을 받아도 된다는 정책(`collection_mode`)이다.
#
# 2026-08-30 실측: 문서 3,598건 중 `body`가 채워진 것은 0건이고, 본문이 첨부에만 있는
# 출처(bok·fss·boj·naver_research)가 772건이다.

BODY_COMMENT = "정규화한 본문. 길이 상한을 두지 않는다. metadata_only 출처는 NULL이며 CHECK 제약이 이를 강제한다"
OLD_BODY_COMMENT = "정규화한 본문. metadata_only 출처는 NULL이며 CHECK 제약이 이를 강제한다"

BODY_STATUS_COMMENT = (
    "본문 수집 결과(ok, empty, attachment_only 또는 unavailable). "
    "NULL은 아직 시도하지 않았다는 뜻이고 **그 집합이 곧 수집 큐다.** "
    "연결 실패와 5xx는 상태를 남기지 않아 다음 실행이 다시 집는다"
)
BODY_STATUS_CHECK = "ck_document_body_status"

# 본문을 해시에서 뺀다. 평가가 제목과 요약만 보므로 본문이 바뀌었다고 다시 평가할 이유가
# 없고, 넣어 두면 본문 백필이 문서 전체의 재평가를 부른다. **컬럼 값 자체는 건드리지
# 않는다** — 본문이 전부 NULL이던 시절의 해시가 새 정의와 글자 그대로 같기 때문이다
# (수집기가 세 조각 중 본문 자리를 빈 문자열로 남긴다).
CONTENT_HASH_COMMENT = (
    "정규화한 제목·요약의 SHA-256. 재평가 여부와 완전 중복 판정의 기준이다. "
    "**본문은 넣지 않는다** — 평가가 제목과 요약만 보므로 본문이 바뀌었다고 다시 평가할 "
    "이유가 없고, 넣으면 본문 백필이 전체 문서의 재평가를 부른다. "
    "정규화 규칙이 흔들리면 이 값이 매번 바뀌므로 규칙을 먼저 고정한다"
)
OLD_CONTENT_HASH_COMMENT = (
    "정규화한 제목·요약·본문의 SHA-256. 재평가 여부와 완전 중복 판정의 기준이다. "
    "정규화 규칙이 흔들리면 이 값이 매번 바뀌므로 규칙을 먼저 고정한다"
)

CONTENT_LEVEL_COMMENT = (
    "이 문서에 실제로 담긴 수준. 출처 정책(document_source.collection_mode)과 다르다 — "
    "발견 시점에는 feed_content이고 본문이 들어온 뒤에야 full_text로 오른다"
)
OLD_CONTENT_LEVEL_COMMENT = "이 문서에 실제로 담긴 수준. 출처 정책과 같지만 본문 수집이 실패하면 낮아질 수 있다"

# 켜진 출처 전부에서 본문을 받는다(2026-08-30 사용자 결정). 이용조건이 문제가 되면 코드가
# 아니라 그 출처의 `collection_mode`나 `enabled`를 내리는 것으로 끝나야 한다.
#
# 되돌릴 때를 위해 지금 값을 여기 적어 둔다. 켜진 22개 중 fss만 metadata_only이고
# 나머지는 feed_content다.
METADATA_ONLY_SLUGS = ("fss",)


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
    op.add_column(
        "document",
        sa.Column(
            "body_status",
            sa.Enum(
                "ok",
                "empty",
                "attachment_only",
                "unavailable",
                name="bodystatus",
                native_enum=False,
                length=20,
            ),
            nullable=True,
            comment=BODY_STATUS_COMMENT,
        ),
    )
    op.create_check_constraint(
        BODY_STATUS_CHECK,
        "document",
        "body_status IN ('ok', 'empty', 'attachment_only', 'unavailable')",
    )
    op.alter_column("document", "body", existing_type=sa.Text(), existing_nullable=True, comment=BODY_COMMENT)
    op.alter_column(
        "document",
        "content_hash",
        existing_type=sa.Text(),
        existing_nullable=False,
        comment=CONTENT_HASH_COMMENT,
    )
    op.alter_column(
        "document",
        "content_level",
        existing_type=sa.Enum(
            "metadata_only",
            "feed_content",
            "full_text",
            name="collectionmode",
            native_enum=False,
            length=20,
        ),
        existing_nullable=False,
        comment=CONTENT_LEVEL_COMMENT,
    )

    op.create_table(
        "document_attachment",
        sa.Column(
            "document_id", sa.BigInteger(), nullable=False, comment="문서 ID. 문서가 지워지면 첨부도 함께 지운다"
        ),
        sa.Column(
            "position",
            sa.Integer(),
            nullable=False,
            comment="문서 안에서의 순서(0부터). 페이지에 나온 차례일 뿐 자연키가 아니다",
        ),
        sa.Column(
            "kind",
            sa.Enum("file", "video", name="attachmentkind", native_enum=False, length=20),
            nullable=False,
            comment="내려받은 파일(file)인지 링크만 남긴 영상(video)인지",
        ),
        sa.Column("url", sa.Text(), nullable=False, comment="첨부 원본 URL. 영상은 이 값이 전부다"),
        sa.Column(
            "storage_path",
            sa.Text(),
            nullable=True,
            comment=(
                "파일을 둔 자리의 상대경로(예: documents/boj/2026/08/1234-0.pdf). "
                "마운트 지점을 빼고 남기므로 마운트가 바뀌어도 행을 고치지 않는다. 영상은 NULL이다"
            ),
        ),
        sa.Column(
            "filename", sa.Text(), nullable=True, comment="제공처가 준 파일 이름. 알 수 없으면 NULL이다"
        ),
        sa.Column(
            "media_type",
            sa.Text(),
            nullable=True,
            comment="응답의 Content-Type(예: application/pdf). 제공처가 주지 않으면 NULL이다",
        ),
        sa.Column(
            "byte_size",
            sa.BigInteger(),
            nullable=True,
            comment="받은 파일의 바이트 수. 크기 상한은 두지 않는다. 영상은 NULL이다",
        ),
        sa.Column(
            "sha256",
            sa.Text(),
            nullable=True,
            comment="받은 파일 내용의 SHA-256. 서로 다른 문서가 같은 파일을 가리키는지 보는 근거다. 영상은 NULL이다",
        ),
        sa.Column(
            "fetched_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="파일을 내려받은 시각(UTC). 영상은 NULL이다",
        ),
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False, comment="레코드 고유 식별자"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
            comment="레코드 생성 시각(UTC)",
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
            comment="레코드 최종 수정 시각(UTC)",
        ),
        sa.CheckConstraint("kind IN ('file', 'video')", name="ck_document_attachment_kind"),
        sa.CheckConstraint(
            "kind <> 'video' OR storage_path IS NULL",
            name="ck_document_attachment_video_has_no_path",
        ),
        sa.ForeignKeyConstraint(["document_id"], ["document.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("document_id", "url", name="uq_document_attachment_natural_key"),
        comment="문서에 붙은 첨부 파일과 영상 링크",
        info={"database": "default", "managed": True},
    )
    op.create_index("ix_document_attachment_document_id", "document_attachment", ["document_id"], unique=False)

    op.execute(
        """
        UPDATE document_source
        SET collection_mode = 'full_text',
            updated_at = now()
        WHERE enabled
          AND collection_mode <> 'full_text'
        """
    )


def downgrade_default() -> None:
    metadata_only = ", ".join(f"'{slug}'" for slug in METADATA_ONLY_SLUGS)
    op.execute(
        f"""
        UPDATE document_source
        SET collection_mode = CASE WHEN slug IN ({metadata_only}) THEN 'metadata_only' ELSE 'feed_content' END,
            updated_at = now()
        WHERE enabled
          AND collection_mode = 'full_text'
        """
    )

    op.drop_index("ix_document_attachment_document_id", table_name="document_attachment")
    op.drop_table("document_attachment")

    op.alter_column(
        "document",
        "content_level",
        existing_type=sa.Enum(
            "metadata_only",
            "feed_content",
            "full_text",
            name="collectionmode",
            native_enum=False,
            length=20,
        ),
        existing_nullable=False,
        comment=OLD_CONTENT_LEVEL_COMMENT,
    )
    op.alter_column(
        "document",
        "content_hash",
        existing_type=sa.Text(),
        existing_nullable=False,
        comment=OLD_CONTENT_HASH_COMMENT,
    )
    op.alter_column("document", "body", existing_type=sa.Text(), existing_nullable=True, comment=OLD_BODY_COMMENT)
    op.drop_constraint(BODY_STATUS_CHECK, "document", type_="check")
    op.drop_column("document", "body_status")
