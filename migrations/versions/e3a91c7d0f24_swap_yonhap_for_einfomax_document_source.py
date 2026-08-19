"""swap yonhap for einfomax document source

Revision ID: e3a91c7d0f24
Revises: b91f4e2a6c53
Create Date: 2026-08-19 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e3a91c7d0f24"
down_revision: str | Sequence[str] | None = "b91f4e2a6c53"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 국내 언론 출처를 연합뉴스 경제에서 연합인포맥스로 교체한다. 인포맥스가 금융·경제에
# 특화돼 있고, 연합 경제기사(nsid `AKR...`)를 그대로 실어 두 피드를 병행하면 교차 중복이
# 생긴다. dedup은 같은 출처 안에서만 묶으므로 병행이 아니라 교체다.
#
# **피드는 실제로 요청해 응답을 확인했다**(2026-08-19). RSS 2.0이고 title/link/
# description(CDATA 요약)/author/pubDate를 준다. 50건이 약 2.6시간 분량(시간당 ~18건)이라
# 매시 수집으로 충분하다. 항목에 `<guid>`가 없어 `external_id`는 canonical URL이 된다.
# `pubDate`는 `2026-08-19 17:01:32` 형태의 시간대 없는 KST다 — 수집기의
# `NAIVE_FEED_TIMEZONES`가 KST로 읽는다.
#
# `collection_mode`는 yonhap과 같은 `feed_content`다. 이용조건 확인(`terms_checked_at`)은
# 아직 하지 않았다. yonhap 행은 지우지 않고 `enabled=false`로 남긴다 — 지우면 왜 뺐는지가
# 사라진다.
#
# (slug, name, source_kind, country, language, feed_url, collection_mode, enabled)
EINFOMAX_SOURCE_SEED: tuple[tuple[str, str, str, str | None, str, str, str, bool], ...] = (
    (
        "einfomax",
        "연합인포맥스",
        "media",
        "KR",
        "ko",
        "https://news.einfomax.co.kr/rss/allArticle.xml",
        "feed_content",
        True,
    ),
)

SEED_COLUMNS = ("slug", "name", "source_kind", "country", "language", "feed_url", "collection_mode", "enabled")


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
    op.bulk_insert(
        sa.table(
            "document_source",
            sa.column("slug", sa.Text),
            sa.column("name", sa.Text),
            sa.column("source_kind", sa.String),
            sa.column("country", sa.Text),
            sa.column("language", sa.Text),
            sa.column("feed_url", sa.Text),
            sa.column("collection_mode", sa.String),
            sa.column("enabled", sa.Boolean),
        ),
        [dict(zip(SEED_COLUMNS, row)) for row in EINFOMAX_SOURCE_SEED],
        # offline(`--sql`)에서는 executemany를 찍을 수 없다. 행마다 INSERT를 내게 한다.
        multiinsert=False,
    )
    op.execute("UPDATE document_source SET enabled = false WHERE slug = 'yonhap'")


def downgrade_default() -> None:
    # 이 리비전이 넣은 행만 지운다. 문서는 `document.source_slug`로만 이어져 있어
    # 외래키가 막지 않는다. 이미 수집한 문서는 남는다.
    op.execute("DELETE FROM document_source WHERE slug = 'einfomax'")
    op.execute("UPDATE document_source SET enabled = true WHERE slug = 'yonhap'")
