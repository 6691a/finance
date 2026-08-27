"""fix bok press release feed

Revision ID: b6f1a92c4d70
Revises: a8c5f207d1e6
Create Date: 2026-08-27 00:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b6f1a92c4d70"
down_revision: str | Sequence[str] | None = "a8c5f207d1e6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 한국은행 피드를 보도자료 게시판으로 바꾼다. 시드에 넣은 주소(`B0000338`)는 화폐박물관
# 소장품 게시판의 영문 RSS였다. 항목 30건이 전부 2024-06-24에 멈춰 있고 그 뒤로 새 문서가
# 한 건도 들어오지 않았다 — DB의 `bok` 문서 8건이 `Sangpyeong Tongbo`, `Hojo Taehwan
# Banknote` 같은 유물 소개다.
#
# **그래서 통화정책방향 결정문을 한 번도 수집하지 못했다.** 2026-08-27 금통위 인상 결정이
# 10:30 KST에 `통화정책방향(2026.8.27)`으로 공표됐는데 그날 정시 수집에 없었다. 국내 언론
# 쪽으로도 못 받는다 — 연합인포맥스 `allArticle` 피드는 최근 50건만 싣고 그 안에 통방 속보가
# 올라오지 않았다(같은 날 실측). 한은 원문이 유일한 경로다.
#
# **두 주소 모두 실제로 요청해 응답을 확인했다**(2026-08-27).
#
# - `P0000559`(보도자료)는 100건을 싣고 2024-03-14까지 거슬러 간다. 통화정책방향, 금융통화
#   위원회 의사록, 일반 보도자료가 한 피드에 함께 온다. RSS 2.0이라 `parse_feed`가 그대로 읽고
#   `pubDate`는 `+0900` offset을 달고 온다.
# - `guid`가 없어 `external_id`는 링크(`.../view.do?nttId=...`)가 된다. 발표마다 `nttId`가
#   달라 자연키가 겹치지 않는다.
# - `description`에 결정문 전문이 HTML로 들어 있어 `feed_content`가 그대로 맞다.
#
# 발행처가 같고 피드만 바꾸는 것이라 `c5f81d3a9b46`(BBC·CNBC)처럼 slug를 그대로 두고
# `feed_url`만 바꾼다. 기존 유물 문서 8건은 지우지 않는다 — 2024년 문서라 조회 창에 걸리지
# 않고, 지우면 그 행을 가리키는 `source_record` 계보까지 함께 정리해야 한다.
#
# 첫 실행에서 100건이 한 번에 들어와 그만큼 LLM 평가가 돈다. 이후에는 새 보도자료만 는다.
BOK_PRESS_FEED = "https://www.bok.or.kr/portal/bbs/P0000559/news.rss?menuNo=200761"
BOK_MUSEUM_FEED = "https://www.bok.or.kr/portal/bbs/B0000338/news.rss?menuNo=200761"


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
    op.execute(f"UPDATE document_source SET feed_url = '{BOK_PRESS_FEED}' WHERE slug = 'bok'")


def downgrade_default() -> None:
    op.execute(f"UPDATE document_source SET feed_url = '{BOK_MUSEUM_FEED}' WHERE slug = 'bok'")
