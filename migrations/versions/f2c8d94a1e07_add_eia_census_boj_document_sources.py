"""add eia census boj document sources

Revision ID: f2c8d94a1e07
Revises: e3a91c7d0f24
Create Date: 2026-08-20 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f2c8d94a1e07"
down_revision: str | Sequence[str] | None = "e3a91c7d0f24"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 원자재(EIA)·미국 실물지표(Census)·일본 통화정책(BOJ)의 1차 발표를 원문으로 받는다.
# 지금까지 이 세 영역은 언론이 기사로 옮겨 준 것만 들어왔다.
#
# **세 주소 모두 실제로 요청해 응답을 확인했다**(2026-08-20). 확인 결과를 함께 남긴다.
#
# - `eia`는 보도자료 RSS 2.0이다. 링크와 guid가 `/pressroom/releases/press591.php`처럼
#   상대 경로라 수집기 `parse_feed`가 피드 URL을 기준으로 절대 URL로 푼다. `pubDate`가
#   `EST` 표기인데 `parsedate_to_datetime`이 RFC 822 시간대 이름을 읽는다.
# - `census`는 경제지표 브리핑룸 피드다. **guid가 발표가 아니라 시계열을 가리킨다**
#   (`housing_starts`가 매달 그대로). 수집기 `SERIES_GUID_SOURCES`가 발표일을 붙여
#   발표마다 문서를 만든다. 뉴스룸 피드(`newsroom/press-releases.xml`)도 200이지만
#   소매판매·주택착공 같은 지표 발표가 안 실려 이쪽을 쓴다.
# - `boj`는 영문 What's New RSS 2.0이다. `pubDate`가 `+0900`이고 `description`이 빈
#   값이라 요약 없이 제목만 쌓인다. 정책 발표·통계 공표가 한 피드에 온다.
#
# 넣지 않은 것도 남긴다. KRX 보도자료(`open.krx.co.kr`)는 피드가 없고 페이지가 `.cmd`
# JSON 엔드포인트로 동적 렌더된다 — HTML·JSON 목록 수집은 아직 만들지 않았다.
# 금융감독원은 개편된 홈페이지에 RSS가 없다(메인의 `rss` 문자열 매치는 민원 페이지
# `ombdsmnDstrss`의 오탐). 정책브리핑 korea.kr의 부처별 RSS도 전부 404다.
#
# `collection_mode`는 기존 출처와 같은 `feed_content`다. 원문 본문 추출을 아직 만들지
# 않았고 이용조건 확인(`terms_checked_at`)도 하지 않았다.
#
# (slug, name, source_kind, country, language, feed_url, collection_mode, enabled)
NEW_SOURCE_SEED: tuple[tuple[str, str, str, str | None, str, str, str, bool], ...] = (
    (
        "eia",
        "U.S. Energy Information Administration",
        "official",
        "US",
        "en",
        "https://www.eia.gov/rss/press_rss.xml",
        "feed_content",
        True,
    ),
    (
        "census",
        "U.S. Census Bureau 경제지표",
        "official",
        "US",
        "en",
        "https://www.census.gov/economic-indicators/indicator.xml",
        "feed_content",
        True,
    ),
    (
        "boj",
        "Bank of Japan",
        "official",
        "JP",
        "en",
        "https://www.boj.or.jp/en/rss/whatsnew.xml",
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
        [dict(zip(SEED_COLUMNS, row)) for row in NEW_SOURCE_SEED],
        # offline(`--sql`)에서는 executemany를 찍을 수 없다. 행마다 INSERT를 내게 한다.
        multiinsert=False,
    )


def downgrade_default() -> None:
    # 이 리비전이 넣은 행만 지운다. 문서는 `document.source_slug`로만 이어져 있어
    # 외래키가 막지 않는다. 이미 수집한 문서는 남는다.
    slugs = ", ".join(f"'{row[0]}'" for row in NEW_SOURCE_SEED)
    op.execute(f"DELETE FROM document_source WHERE slug IN ({slugs})")
