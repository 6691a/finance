"""add korea export series

Revision ID: e2f7a9c14b05
Revises: c8e1b4f7a209
Create Date: 2026-08-28 11:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e2f7a9c14b05"
down_revision: str | Sequence[str] | None = "c8e1b4f7a209"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 관세청 10일 단위 수출 잠정치. 전체와 10대 품목이다. 리비전에서 앱 코드를 import하지 않는다.
# import하면 나중에 수집기 Enum이 바뀔 때 과거 리비전의 결과가 따라 바뀐다. 대조는 tests/migrations가 한다.
#
# **`kind`는 새로 만들지 않고 `activity`를 쓴다.** 종류는 단위가 아니라 무엇을 재는 값인가이고
# 수출은 실물활동이다. 한국 것만 뽑는 쿼리는 `country='KR'`을 함께 걸면 된다.
#
# **만기가 NULL이다.** 수출에는 만기 개념이 없다. 0으로 채우면 만기별 비교 쿼리가 "0개월물"로 그린다.
#
# 품목 구성과 응답의 열 순서는 2026-08-28에 제공처에 직접 조회해 확인했다.
#
# (provider, series_id, country, country_name, maturity_months, kind, label)
KOREA_EXPORT_SEED: tuple[tuple[str, str, str, str, int | None, str, str], ...] = (
    ("kcs", "KR_EXPORT_MTD", "KR", "대한민국", None, "activity", "한국 수출 전체(월 누계)"),
    ("kcs", "KR_EXPORT_SEMICON_MTD", "KR", "대한민국", None, "activity", "한국 반도체 수출(월 누계)"),
    ("kcs", "KR_EXPORT_STEEL_MTD", "KR", "대한민국", None, "activity", "한국 철강제품 수출(월 누계)"),
    ("kcs", "KR_EXPORT_CAR_MTD", "KR", "대한민국", None, "activity", "한국 승용차 수출(월 누계)"),
    ("kcs", "KR_EXPORT_OILPROD_MTD", "KR", "대한민국", None, "activity", "한국 석유제품 수출(월 누계)"),
    ("kcs", "KR_EXPORT_WIRELESS_MTD", "KR", "대한민국", None, "activity", "한국 무선통신기기 수출(월 누계)"),
    ("kcs", "KR_EXPORT_SHIP_MTD", "KR", "대한민국", None, "activity", "한국 선박 수출(월 누계)"),
    ("kcs", "KR_EXPORT_AUTOPART_MTD", "KR", "대한민국", None, "activity", "한국 자동차부품 수출(월 누계)"),
    ("kcs", "KR_EXPORT_COMPUTER_MTD", "KR", "대한민국", None, "activity", "한국 컴퓨터 주변기기 수출(월 누계)"),
    ("kcs", "KR_EXPORT_PRECISION_MTD", "KR", "대한민국", None, "activity", "한국 정밀기기 수출(월 누계)"),
    ("kcs", "KR_EXPORT_APPLIANCE_MTD", "KR", "대한민국", None, "activity", "한국 가전제품 수출(월 누계)"),
)

SEED_COLUMNS = ("provider", "series_id", "country", "country_name", "maturity_months", "kind", "label")


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
            "indicator_series",
            sa.column("provider", sa.Text),
            sa.column("series_id", sa.Text),
            sa.column("country", sa.Text),
            sa.column("country_name", sa.Text),
            sa.column("maturity_months", sa.Integer),
            sa.column("kind", sa.String),
            sa.column("label", sa.Text),
        ),
        [dict(zip(SEED_COLUMNS, row)) for row in KOREA_EXPORT_SEED],
        # offline(`--sql`)에서는 executemany를 찍을 수 없다. 행마다 INSERT를 내게 한다.
        multiinsert=False,
    )


def downgrade_default() -> None:
    series_ids = ", ".join(f"'{row[1]}'" for row in KOREA_EXPORT_SEED)
    op.execute(f"DELETE FROM indicator_series WHERE provider = 'kcs' AND series_id IN ({series_ids})")


def upgrade_finance() -> None:
    pass


def downgrade_finance() -> None:
    pass
