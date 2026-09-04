"""split einfomax into section document sources

Revision ID: d4c7f1a9e206
Revises: e4a91c07b6d3
Create Date: 2026-09-04 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d4c7f1a9e206"
down_revision: str | Sequence[str] | None = "e4a91c07b6d3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 연합인포맥스 전체기사 피드 하나를 섹션 피드 여덟으로 쪼갠다. 설계는
# `docs/collection/einfomax-section-split.md`다.
#
# **여덟 피드는 실제로 요청해 응답을 확인했다**(2026-09-04). 전부 200이고 `allArticle`과 같은
# RSS 2.0이다 — title/link/description(CDATA 요약)/pubDate를 주고 `<guid>`가 없어
# `external_id`는 canonical URL이 된다. 항목은 각 20건이고 가장 빠른 섹션(정책/금융,
# 채권/외환)이 20건에 약 5시간 분량이라 매시 수집으로 충분하다. `pubDate`는
# `2026-09-04 15:03:24` 모양의 시간대 없는 KST라 수집기의 `NAIVE_FEED_TIMEZONES`가 KST로
# 읽는다 — **여덟 slug가 거기 다 있어야 한다.**
#
# 섹션끼리 기사가 겹치지 않는다(같은 시각 160건이 모두 서로 다른 기사였다). 그래서 쪼개도
# 같은 기사가 두 행으로 들어오지 않는다.
#
# 물량이 줄어서 쪼개는 것이 아니다 — 전체기사의 96%가 이미 이 여덟 섹션 안이다. 값어치는
# **섹션이 행이 되어 `enabled` 한 칸으로 끄고 켜진다는 것**이다. 지금은 여덟을 다 켠다.
#
# `einfomax` 행은 지우지 않고 `enabled=false`로 남긴다 — 지우면 왜 뺐는지가 사라진다.
# 그 행으로 들어온 문서도 지우지 않는다(`document.source_slug`는 외래키가 아니다).
#
# (slug, name, source_kind, country, language, feed_url, collection_mode, enabled)
EINFOMAX_SECTION_SEED: tuple[tuple[str, str, str, str | None, str, str, str, bool], ...] = (
    (
        "einfomax_stock",
        "연합인포맥스 증권",
        "media",
        "KR",
        "ko",
        "https://news.einfomax.co.kr/rss/S1N2.xml",
        "feed_content",
        True,
    ),
    (
        "einfomax_ib",
        "연합인포맥스 IB/기업",
        "media",
        "KR",
        "ko",
        "https://news.einfomax.co.kr/rss/S1N7.xml",
        "feed_content",
        True,
    ),
    (
        "einfomax_column",
        "연합인포맥스 칼럼/이슈",
        "media",
        "KR",
        "ko",
        "https://news.einfomax.co.kr/rss/S1N9.xml",
        "feed_content",
        True,
    ),
    (
        "einfomax_policy",
        "연합인포맥스 정책/금융",
        "media",
        "KR",
        "ko",
        "https://news.einfomax.co.kr/rss/S1N15.xml",
        "feed_content",
        True,
    ),
    (
        "einfomax_bond_fx",
        "연합인포맥스 채권/외환",
        "media",
        "KR",
        "ko",
        "https://news.einfomax.co.kr/rss/S1N16.xml",
        "feed_content",
        True,
    ),
    (
        "einfomax_realestate",
        "연합인포맥스 부동산",
        "media",
        "KR",
        "ko",
        "https://news.einfomax.co.kr/rss/S1N17.xml",
        "feed_content",
        True,
    ),
    (
        "einfomax_global_stock",
        "연합인포맥스 해외주식",
        "media",
        "KR",
        "ko",
        "https://news.einfomax.co.kr/rss/S1N21.xml",
        "feed_content",
        True,
    ),
    (
        "einfomax_world",
        "연합인포맥스 국제뉴스",
        "media",
        "KR",
        "ko",
        "https://news.einfomax.co.kr/rss/S1N23.xml",
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
        [dict(zip(SEED_COLUMNS, row)) for row in EINFOMAX_SECTION_SEED],
        # offline(`--sql`)에서는 executemany를 찍을 수 없다. 행마다 INSERT를 내게 한다.
        multiinsert=False,
    )
    op.execute("UPDATE document_source SET enabled = false WHERE slug = 'einfomax'")


def downgrade_default() -> None:
    # 이 리비전이 넣은 행만 지운다. 문서는 `document.source_slug`로만 이어져 있어
    # 외래키가 막지 않는다. 이미 수집한 문서는 남는다.
    slugs = ", ".join(f"'{row[0]}'" for row in EINFOMAX_SECTION_SEED)
    op.execute(f"DELETE FROM document_source WHERE slug IN ({slugs})")
    op.execute("UPDATE document_source SET enabled = true WHERE slug = 'einfomax'")
