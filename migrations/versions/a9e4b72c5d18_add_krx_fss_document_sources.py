"""add krx fss document sources

Revision ID: a9e4b72c5d18
Revises: f2c8d94a1e07
Create Date: 2026-08-20 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a9e4b72c5d18"
down_revision: str | Sequence[str] | None = "f2c8d94a1e07"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 국내 증시의 1차 발표 두 곳을 붙인다. 거래소 조치(사이드카 발동, 신규상장, 결산실적
# 집계)와 금융감독 조치(검사 결과, 제재, 가이드라인)는 지금까지 언론 피드로만 들어왔다.
#
# **두 곳 다 피드가 없어 게시판 목록을 직접 읽는다.** 수집 방법은
# `modules.collectors.document_listings.LISTING_SOURCES`가 slug로 들고 있고, DAG이
# 거기 있는 slug를 목록 수집으로 보낸다. 이 행의 `feed_url`은 그 수집이 부를 엔드포인트다.
#
# **두 채널 모두 실제로 요청해 응답을 확인했다**(2026-08-20).
#
# - `krx`는 보도자료 화면(OPN05000000T1.jsp)이 표를 그릴 때 부르는 JSON 서블릿이다.
#   POST 파라미터(bld, 조회 구간)는 코드 레지스트리가 만든다. 문서별 GET 딥링크가 없어
#   canonical_url은 화면 URL에 noti_no를 붙인 합성 URL이다.
# - `fss`는 eGov 게시판 목록 HTML이다. 개편된 홈페이지에 RSS가 없다(korea.kr 부처별
#   RSS도 전부 404). 목록에 요약이 없어 `collection_mode`는 `metadata_only`다 —
#   제목·링크·게시일만 저장한다.
#
# `krx`의 `collection_mode`는 기존 출처와 같은 `feed_content`다(목록 JSON의 `contn`이
# 요약 노릇을 한다. 대부분 "첨부 참조"라 짧다). 이용조건 확인(`terms_checked_at`)은
# 두 곳 다 아직 하지 않았다.
#
# (slug, name, source_kind, country, language, feed_url, collection_mode, enabled)
NEW_SOURCE_SEED: tuple[tuple[str, str, str, str | None, str, str, str, bool], ...] = (
    (
        "krx",
        "한국거래소 보도자료",
        "official",
        "KR",
        "ko",
        "https://open.krx.co.kr/contents/OPN/99/OPN99000001.jspx",
        "feed_content",
        True,
    ),
    (
        "fss",
        "금융감독원 보도자료",
        "official",
        "KR",
        "ko",
        "https://www.fss.or.kr/fss/bbs/B0000188/list.do?menuNo=200218",
        "metadata_only",
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
