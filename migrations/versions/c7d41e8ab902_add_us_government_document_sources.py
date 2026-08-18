"""add us government document sources

Revision ID: c7d41e8ab902
Revises: b6de981cb250
Create Date: 2026-08-18 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c7d41e8ab902"
down_revision: str | Sequence[str] | None = "b6de981cb250"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 미국 행정부의 1차 발표를 원문으로 받는다. 지금까지 미국 공식 출처는 연준·BLS·BEA뿐이라
# 대통령 행정명령, 관세 조치, 백악관 성명은 언론이 기사로 옮겨 준 것만 들어왔다.
#
# **두 주소 모두 실제로 요청해 응답을 확인했다**(2026-08-18). 확인 결과를 함께 남긴다.
#
# - `whitehouse`는 `/news/feed/`다. 이 피드가 `/presidential-actions/`(행정명령·포고문),
#   `/fact-sheets/`, `/briefings-statements/`, `/releases/`를 모두 싣는 상위 피드라 카테고리별
#   피드를 따로 켜지 않는다. 문서 루트인 `/feed/`는 404다. `/remarks/feed/`는 200이지만
#   2025-01-20 취임사 한 건뿐이라 죽은 카테고리로 보고 뺀다.
# - `ustr`은 관세·무역 조치의 1차 출처다. 발표가 시장에 바로 닿는데 백악관 피드에는
#   안 실리는 건이 많다.
#
# 넣지 않은 것도 남긴다. Federal Register의 대통령 문서 피드
# (`documents.rss?conditions[type][]=PRESDOCU`)는 200이고 관보 원문이라 안정적이지만,
# 백악관 발표와 제목까지 겹치면서 며칠 늦게 실린다. 같은 문서가 두 벌 쌓이면 LLM 태깅
# 비용이 그만큼 두 배가 된다. 백악관 피드가 흔들리면 그때 이 주소로 갈아탄다.
# 재무부는 알려진 RSS 주소가 전부 404다(`/rss/press.xml`, `/news/press-releases/feed`).
# 국무부(`/rss-feed/press-releases/feed/`)는 200이지만 최근 10건이 대부분 각국 국경일
# 축전이라 거시 아카이브에 넣을 값어치가 없다.
#
# `collection_mode`는 기존 출처와 같은 `feed_content`다. 원문 본문 추출을 아직 만들지 않았고
# 이용조건 확인(`terms_checked_at`)도 하지 않았다.
#
# (slug, name, source_kind, country, language, feed_url, collection_mode, enabled)
US_GOVERNMENT_SOURCE_SEED: tuple[tuple[str, str, str, str | None, str, str, str, bool], ...] = (
    (
        "whitehouse",
        "The White House",
        "official",
        "US",
        "en",
        "https://www.whitehouse.gov/news/feed/",
        "feed_content",
        True,
    ),
    (
        "ustr",
        "Office of the U.S. Trade Representative",
        "official",
        "US",
        "en",
        "https://ustr.gov/rss.xml",
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
        [dict(zip(SEED_COLUMNS, row)) for row in US_GOVERNMENT_SOURCE_SEED],
        # offline(`--sql`)에서는 executemany를 찍을 수 없다. 행마다 INSERT를 내게 한다.
        multiinsert=False,
    )


def downgrade_default() -> None:
    # 이 리비전이 넣은 행만 지운다. 문서는 `document.source_slug`로만 이어져 있어
    # 외래키가 막지 않는다. 이미 수집한 문서는 남는다.
    slugs = ", ".join(f"'{row[0]}'" for row in US_GOVERNMENT_SOURCE_SEED)
    op.execute(f"DELETE FROM document_source WHERE slug IN ({slugs})")
