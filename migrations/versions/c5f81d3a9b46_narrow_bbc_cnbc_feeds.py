"""narrow bbc cnbc feeds

Revision ID: c5f81d3a9b46
Revises: a9e4b72c5d18
Create Date: 2026-08-20 00:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c5f81d3a9b46"
down_revision: str | Sequence[str] | None = "a9e4b72c5d18"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# BBC와 CNBC를 일반 뉴스 피드에서 같은 발행사의 경제 전문 피드로 좁힌다. LLM 평가는
# 문서 1건 = 호출 1회라 저점수 문서가 덜 들어와야 비용이 준다.
#
# **운영 DB 실측**(2026-08-20, 최근 7일): bbc_business는 하루 20건에 평균 2.1점(0~8),
# cnbc는 하루 20건에 평균 3.0점이었다. 소비자·기업 가십이 대부분이라 5점 이상이 각각
# 6%·24%뿐이다. 연합뉴스(평균 2.8) → 연합인포맥스(평균 5.6) 교체가 효과를 입증한 것과
# 같은 방법이다.
#
# **두 피드 모두 실제로 요청해 응답을 확인했다**(2026-08-20). 둘 다 RSS 2.0이고 기존
# `parse_feed`가 그대로 읽는다.
#
# - BBC Economy 토픽 피드는 약 1~2건/일. 물가·금리·국채 같은 거시 기사만 싣는다.
# - CNBC Economy(20910258)는 약 2~3건/일. 연준·재정·물가 기사만 싣는다.
#
# 발행사가 같고 피드만 좁히는 것이라 einfomax 때처럼 slug를 바꾸지 않고 feed_url만
# 바꾼다. external_id(기사 URL) 형태가 같아 기존 행과 자연키가 충돌하지 않는다.
BBC_ECONOMY_FEED = "https://feeds.bbci.co.uk/news/business/economy/rss.xml"
BBC_BUSINESS_FEED = "https://feeds.bbci.co.uk/news/business/rss.xml"
CNBC_ECONOMY_FEED = "https://www.cnbc.com/id/20910258/device/rss/rss.html"
CNBC_TOP_NEWS_FEED = "https://www.cnbc.com/id/100003114/device/rss/rss.html"


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
    op.execute(f"UPDATE document_source SET feed_url = '{BBC_ECONOMY_FEED}' WHERE slug = 'bbc_business'")
    op.execute(f"UPDATE document_source SET feed_url = '{CNBC_ECONOMY_FEED}' WHERE slug = 'cnbc'")


def downgrade_default() -> None:
    op.execute(f"UPDATE document_source SET feed_url = '{BBC_BUSINESS_FEED}' WHERE slug = 'bbc_business'")
    op.execute(f"UPDATE document_source SET feed_url = '{CNBC_TOP_NEWS_FEED}' WHERE slug = 'cnbc'")
