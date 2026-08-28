"""add korea import and country trade series

Revision ID: f3b8d2e05a17
Revises: e2f7a9c14b05
Create Date: 2026-08-28 14:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f3b8d2e05a17"
down_revision: str | Sequence[str] | None = "e2f7a9c14b05"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 관세청 10일 단위 데이터셋 넷 중 나머지 셋. 수출 품목별은 앞 리비전(e2f7a9c14b05)이 넣었다.
# 리비전에서 앱 코드를 import하지 않는다. import하면 나중에 수집기가 바뀔 때 과거 리비전의
# 결과가 따라 바뀐다. 대조는 tests/migrations가 한다.
#
# **`country`는 전부 `KR`이다.** 대중국 수출은 한국의 지표이지 중국의 지표가 아니다. 상대국은
# 라벨이 말한다.
#
# **국가별 데이터셋의 전체 금액은 여기 없다.** 품목별의 `KR_EXPORT_MTD`·`KR_IMPORT_MTD`와 같은
# 값이라(2026-07 1~10일 실측으로 일치) 자연키가 겹친다.
#
# 항목 목록과 순서는 2026-08-28에 제공처 명세와 실제 응답으로 확인했다. **수출과 수입의 나라
# 순서가 다르다** — 한쪽을 복사하면 값이 통째로 다른 나라에 붙는다.
#
# (provider, series_id, country, country_name, maturity_months, kind, label)
KOREA_TRADE_SEED: tuple[tuple[str, str, str, str, int | None, str, str], ...] = (
    # 수입 주요품목별
    ("kcs", "KR_IMPORT_MTD", "KR", "대한민국", None, "activity", "한국 수입 전체(월 누계)"),
    ("kcs", "KR_IMPORT_SEMICON_MTD", "KR", "대한민국", None, "activity", "한국 반도체 수입(월 누계)"),
    ("kcs", "KR_IMPORT_CRUDE_MTD", "KR", "대한민국", None, "activity", "한국 원유 수입(월 누계)"),
    ("kcs", "KR_IMPORT_MACHINERY_MTD", "KR", "대한민국", None, "activity", "한국 기계류 수입(월 누계)"),
    ("kcs", "KR_IMPORT_GAS_MTD", "KR", "대한민국", None, "activity", "한국 가스 수입(월 누계)"),
    ("kcs", "KR_IMPORT_CHIPEQUIP_MTD", "KR", "대한민국", None, "activity", "한국 반도체 제조용장비 수입(월 누계)"),
    ("kcs", "KR_IMPORT_PRECISION_MTD", "KR", "대한민국", None, "activity", "한국 정밀기기 수입(월 누계)"),
    ("kcs", "KR_IMPORT_OILPROD_MTD", "KR", "대한민국", None, "activity", "한국 석유제품 수입(월 누계)"),
    ("kcs", "KR_IMPORT_WIRELESS_MTD", "KR", "대한민국", None, "activity", "한국 무선통신기기 수입(월 누계)"),
    ("kcs", "KR_IMPORT_CAR_MTD", "KR", "대한민국", None, "activity", "한국 승용차 수입(월 누계)"),
    ("kcs", "KR_IMPORT_COAL_MTD", "KR", "대한민국", None, "activity", "한국 석탄 수입(월 누계)"),
    # 수출 주요국가별
    ("kcs", "KR_EXPORT_CN_MTD", "KR", "대한민국", None, "activity", "한국 대중국 수출(월 누계)"),
    ("kcs", "KR_EXPORT_US_MTD", "KR", "대한민국", None, "activity", "한국 대미국 수출(월 누계)"),
    ("kcs", "KR_EXPORT_EU_MTD", "KR", "대한민국", None, "activity", "한국 대유럽연합 수출(월 누계)"),
    ("kcs", "KR_EXPORT_VN_MTD", "KR", "대한민국", None, "activity", "한국 대베트남 수출(월 누계)"),
    ("kcs", "KR_EXPORT_HK_MTD", "KR", "대한민국", None, "activity", "한국 대홍콩 수출(월 누계)"),
    ("kcs", "KR_EXPORT_JP_MTD", "KR", "대한민국", None, "activity", "한국 대일본 수출(월 누계)"),
    ("kcs", "KR_EXPORT_TW_MTD", "KR", "대한민국", None, "activity", "한국 대대만 수출(월 누계)"),
    ("kcs", "KR_EXPORT_IN_MTD", "KR", "대한민국", None, "activity", "한국 대인도 수출(월 누계)"),
    ("kcs", "KR_EXPORT_SG_MTD", "KR", "대한민국", None, "activity", "한국 대싱가포르 수출(월 누계)"),
    ("kcs", "KR_EXPORT_MY_MTD", "KR", "대한민국", None, "activity", "한국 대말레이시아 수출(월 누계)"),
    # 수입 주요국가별
    ("kcs", "KR_IMPORT_CN_MTD", "KR", "대한민국", None, "activity", "한국 대중국 수입(월 누계)"),
    ("kcs", "KR_IMPORT_US_MTD", "KR", "대한민국", None, "activity", "한국 대미국 수입(월 누계)"),
    ("kcs", "KR_IMPORT_EU_MTD", "KR", "대한민국", None, "activity", "한국 대유럽연합 수입(월 누계)"),
    ("kcs", "KR_IMPORT_JP_MTD", "KR", "대한민국", None, "activity", "한국 대일본 수입(월 누계)"),
    ("kcs", "KR_IMPORT_VN_MTD", "KR", "대한민국", None, "activity", "한국 대베트남 수입(월 누계)"),
    ("kcs", "KR_IMPORT_AU_MTD", "KR", "대한민국", None, "activity", "한국 대호주 수입(월 누계)"),
    ("kcs", "KR_IMPORT_TW_MTD", "KR", "대한민국", None, "activity", "한국 대대만 수입(월 누계)"),
    ("kcs", "KR_IMPORT_SA_MTD", "KR", "대한민국", None, "activity", "한국 대사우디아라비아 수입(월 누계)"),
    ("kcs", "KR_IMPORT_RU_MTD", "KR", "대한민국", None, "activity", "한국 대러시아연방 수입(월 누계)"),
    ("kcs", "KR_IMPORT_MY_MTD", "KR", "대한민국", None, "activity", "한국 대말레이시아 수입(월 누계)"),
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
        [dict(zip(SEED_COLUMNS, row)) for row in KOREA_TRADE_SEED],
        # offline(`--sql`)에서는 executemany를 찍을 수 없다. 행마다 INSERT를 내게 한다.
        multiinsert=False,
    )


def downgrade_default() -> None:
    series_ids = ", ".join(f"'{row[1]}'" for row in KOREA_TRADE_SEED)
    op.execute(f"DELETE FROM indicator_series WHERE provider = 'kcs' AND series_id IN ({series_ids})")


def upgrade_finance() -> None:
    pass


def downgrade_finance() -> None:
    pass
