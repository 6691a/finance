"""drop document content_level

Revision ID: c4e28b71fa09
Revises: b1f6c93ad24e
Create Date: 2026-08-30 20:40:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c4e28b71fa09"
down_revision: str | Sequence[str] | None = "b1f6c93ad24e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# `content_level`을 **읽는 코드가 없었다.** 저장소 전체에서 쓰는 자리는 발견 시 INSERT,
# 본문 UPDATE, 그리고 CHECK 제약 둘뿐이고 조회하는 SQL이 하나도 없다.
#
# 그런데 그 CHECK가 운영 태스크를 죽였다(2026-08-30). `ck_document_metadata_only_has_no_body`가
# "metadata_only에는 본문이 없어야 한다"인데, fss 정책이 metadata_only이던 시절의 옛 행 34건에
# 본문이 들어가면서 걸렸다. 아무도 안 읽는 값이 사고만 낸 셈이다.
#
# 이 컬럼이 하려던 일은 `body_status`가 더 정확히 한다 — "그 문서에 본문이 있나"는
# `body_status = 'ok'`이고 "왜 없나"까지 그 컬럼이 갖는다. 정책은 원래부터
# `document_source.collection_mode`가 원본이다.
#
# 되돌릴 때를 위해 옛 정의를 여기 남긴다. `CollectionMode` Enum 자체는
# `document_source.collection_mode`가 계속 쓰므로 코드에 그대로 있다.
CONTENT_LEVEL = sa.Enum(
    "metadata_only",
    "feed_content",
    "full_text",
    name="collectionmode",
    native_enum=False,
    length=20,
)
CONTENT_LEVEL_COMMENT = (
    "이 문서에 실제로 담긴 수준. 출처 정책(document_source.collection_mode)과 다르다 — "
    "발견 시점에는 feed_content이고 본문이 들어온 뒤에야 full_text로 오른다"
)
LEVEL_CHECK = "ck_document_content_level"
BODY_CHECK = "ck_document_metadata_only_has_no_body"

BODY_COMMENT = "정규화한 본문. 길이 상한을 두지 않는다. 아직 못 받았으면 NULL이고 그 사유는 body_status가 갖는다"
OLD_BODY_COMMENT = "정규화한 본문. 길이 상한을 두지 않는다. metadata_only 출처는 NULL이며 CHECK 제약이 이를 강제한다"


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
    op.drop_constraint(BODY_CHECK, "document", type_="check")
    op.drop_constraint(LEVEL_CHECK, "document", type_="check")
    op.drop_column("document", "content_level")
    op.alter_column("document", "body", existing_type=sa.Text(), existing_nullable=True, comment=BODY_COMMENT)


def downgrade_default() -> None:
    op.alter_column("document", "body", existing_type=sa.Text(), existing_nullable=True, comment=OLD_BODY_COMMENT)
    # 되돌릴 때 기존 행에 값이 필요하다. 본문이 있으면 full_text, 없으면 feed_content로 둔다 —
    # metadata_only는 어느 출처도 더 이상 쓰지 않으므로 그 값으로는 복원하지 않는다.
    op.add_column(
        "document",
        sa.Column("content_level", CONTENT_LEVEL, nullable=True, comment=CONTENT_LEVEL_COMMENT),
    )
    op.execute(
        """
        UPDATE document
        SET content_level = CASE WHEN body IS NOT NULL THEN 'full_text' ELSE 'feed_content' END
        """
    )
    op.alter_column("document", "content_level", existing_type=CONTENT_LEVEL, nullable=False)
    op.create_check_constraint(
        LEVEL_CHECK,
        "document",
        "content_level IN ('metadata_only', 'feed_content', 'full_text')",
    )
    op.create_check_constraint(BODY_CHECK, "document", "content_level <> 'metadata_only' OR body IS NULL")
