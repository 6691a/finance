"""add naver research document sources

Revision ID: c2d9e4f1a7b3
Revises: a1f3c7e9b2d4
Create Date: 2026-08-22 00:00:00.000000

"""

from collections.abc import Sequence
from datetime import date

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c2d9e4f1a7b3"
down_revision: str | Sequence[str] | None = "a1f3c7e9b2d4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 증권사 리서치 리포트를 문서로 흡수한다(`docs/analysis/market-thesis/6-analyst.md` 3절). 뉴스가
# "무슨 일이 있었다"까지라면 리포트는 그 사건이 종목·시장에 어떤 뜻인지를 쓴 것이고,
# 지금까지는 뉴스에 섞여 들어오는 만큼만 잡혔다.
#
# **출처 종류가 하나 는다.** `source_kind`에 `research`를 더하고 CHECK를 다시 건다.
# 공식기관도 언론도 아니고, `document_type`이 `report`로 갈리는 근거가 이 값이다.
#
# **여섯 행 모두 네이버 모바일 증권의 내부 JSON API다**(실측 2026-08-21, UTF-8 JSON).
# 네이버 공식 Open API에는 증권·리서치가 없다. 목록 `…/api/research/{category}`가
# `researchId, title, brokerName, writeDate, endUrl`(종목분석은 `itemCode, itemName`)을 주고,
# 상세 `…/api/research/{category}/{researchId}`가 요약 문단(`content`)과 종목분석의
# 투자의견·목표가를 준다. 수집 방법은 `modules.collectors.document_listings.LISTING_SOURCES`가
# slug로 들고 있다. slug 접미는 API 경로 이름 그대로라 코드가 slug에서 카테고리를 뽑는다.
#
# **robots.txt가 막는다.** `finance.naver.com`·`m.stock.naver.com` 모두 `User-agent: *`에
# `Disallow: /`이고 리서치 경로는 네이버 자체 봇에만 열려 있다. **사용자가 감수하기로
# 결정했다(2026-08-21).** `terms_url`·`terms_checked_at`이 그 확인을 남긴다. 이용조건이
# 문제가 되면 코드가 아니라 `enabled`를 내리는 것으로 끝나야 한다.
#
# `pageSize=30`이다. 매시간 도는데 시간당 신규가 30을 넘지 않고(하루 약 74건), 첫 실행
# 백로그가 뉴스 평가(시간당 50건)를 밀어내지 않게 한다. 더 받으려면 이 숫자만 바꾼다.
#
# (slug, name, source_kind, country, language, feed_url, collection_mode, enabled, terms_url, terms_checked_at)
NAVER_RESEARCH_API = "https://m.stock.naver.com/api/research"
TERMS_URL = "https://finance.naver.com/robots.txt"
TERMS_CHECKED_AT = date(2026, 8, 21)

CATEGORIES: tuple[tuple[str, str], ...] = (
    ("company", "종목분석"),
    ("industry", "산업분석"),
    ("market", "시황정보"),
    ("invest", "투자전략"),
    ("economy", "경제분석"),
    ("debenture", "채권분석"),
)

NEW_SOURCE_SEED: tuple[tuple[str, str, str, str | None, str, str, str, bool, str, date], ...] = tuple(
    (
        f"naver_research_{category}",
        f"네이버 증권 리서치 · {label}",
        "research",
        "KR",
        "ko",
        f"{NAVER_RESEARCH_API}/{category}?pageSize=30&page=1",
        "feed_content",
        True,
        TERMS_URL,
        TERMS_CHECKED_AT,
    )
    for category, label in CATEGORIES
)

SEED_COLUMNS = (
    "slug",
    "name",
    "source_kind",
    "country",
    "language",
    "feed_url",
    "collection_mode",
    "enabled",
    "terms_url",
    "terms_checked_at",
)

SOURCE_KIND_CHECK = "ck_document_source_kind"
SOURCE_KINDS_BEFORE = "source_kind IN ('official', 'media')"
SOURCE_KINDS_AFTER = "source_kind IN ('official', 'media', 'research')"


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
    # autogenerate는 CHECK 제약을 비교하지 않는다. 모델의 문자열과 글자 그대로 같아야 한다.
    op.drop_constraint(SOURCE_KIND_CHECK, "document_source", type_="check")
    op.create_check_constraint(SOURCE_KIND_CHECK, "document_source", SOURCE_KINDS_AFTER)
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
            sa.column("terms_url", sa.Text),
            sa.column("terms_checked_at", sa.Date),
        ),
        [dict(zip(SEED_COLUMNS, row)) for row in NEW_SOURCE_SEED],
        # offline(`--sql`)에서는 executemany를 찍을 수 없다. 행마다 INSERT를 내게 한다.
        multiinsert=False,
    )


def downgrade_default() -> None:
    # 이 리비전이 넣은 행만 지운다. 문서는 `document.source_slug`로만 이어져 있어
    # 외래키가 막지 않는다. 이미 수집한 문서는 남는다.
    slugs = ", ".join(f"'{row[0]}'" for row in NEW_SOURCE_SEED)
    op.execute(f"DELETE FROM document_source WHERE slug IN ({slugs})")
    op.drop_constraint(SOURCE_KIND_CHECK, "document_source", type_="check")
    op.create_check_constraint(SOURCE_KIND_CHECK, "document_source", SOURCE_KINDS_BEFORE)
