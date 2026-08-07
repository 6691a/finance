"""create indicator series master

Revision ID: a2e57b3c8f41
Revises: f1a6b0c94d73
Create Date: 2026-08-07 15:04:51.226730

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a2e57b3c8f41"
down_revision: str | Sequence[str] | None = "f1a6b0c94d73"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 지금 수집 중인 시계열의 시드. 이 목록은 이 리비전이 만들어진 시점의 값으로 고정한다.
# 시계열을 늘리면 수집기 Enum과 함께 새 리비전에서 INSERT를 추가한다. 여기서 앱 코드를
# import해 오면 나중에 코드가 바뀔 때 과거 리비전의 결과가 따라 바뀐다.
#
# (provider, series_id, country, country_name, maturity_months, kind, label)
SEED: tuple[tuple[str, str, str, str, int, str, str], ...] = (
    ("fred", "DGS3MO", "US", "미국", 3, "government_bond", "미국 3개월물"),
    ("fred", "DGS2", "US", "미국", 24, "government_bond", "미국 2년물"),
    ("fred", "DGS10", "US", "미국", 120, "government_bond", "미국 10년물"),
    ("fred", "DGS30", "US", "미국", 360, "government_bond", "미국 30년물"),
    ("ecos", "KTB2Y", "KR", "한국", 24, "government_bond", "한국 2년물"),
    ("ecos", "KTB3Y", "KR", "한국", 36, "government_bond", "한국 3년물"),
    ("ecos", "KTB10Y", "KR", "한국", 120, "government_bond", "한국 10년물"),
    ("ecos", "KTB30Y", "KR", "한국", 360, "government_bond", "한국 30년물"),
    # CD 91일은 국채가 아니다. 국채 곡선 패널에서 빠지도록 kind로 가른다.
    ("ecos", "CD91D", "KR", "한국", 3, "money_market", "한국 CD 91일"),
)


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
    indicator_series = op.create_table(
        "indicator_series",
        sa.Column(
            "id",
            sa.BigInteger(),
            autoincrement=True,
            nullable=False,
            comment="레코드 고유 식별자",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
            comment="레코드 생성 시각(UTC)",
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
            comment="레코드 최종 수정 시각(UTC)",
        ),
        sa.Column(
            "provider",
            sa.Text(),
            nullable=False,
            comment="데이터 제공처 식별자(예: fred 또는 ecos). indicator_observation.provider와 같은 값이다",
        ),
        sa.Column(
            "series_id",
            sa.Text(),
            nullable=False,
            comment="제공처 안에서 시계열을 가리키는 식별자. indicator_observation.series_id와 같은 값이다",
        ),
        sa.Column(
            "country",
            sa.Text(),
            nullable=False,
            comment="발행 국가(ISO 3166-1 alpha-2, 예: US 또는 KR). 유로존처럼 국가가 아닌 통화권은 XM을 쓴다",
        ),
        sa.Column(
            "country_name",
            sa.Text(),
            nullable=False,
            comment="국가 표시 이름. 국가에 붙는 속성이 더 늘면 country 마스터 테이블로 분리한다",
        ),
        sa.Column(
            "maturity_months",
            sa.Integer(),
            nullable=False,
            comment="만기 개월 수. 만기별 비교와 정렬에 쓴다(3개월=3, 10년=120). 91일물은 3으로 둔다",
        ),
        sa.Column(
            "kind",
            sa.Enum(
                "government_bond",
                "money_market",
                name="serieskind",
                native_enum=False,
                create_constraint=False,
                length=20,
            ),
            nullable=False,
            comment="금리의 종류(government_bond 또는 money_market). 국채 곡선에서 단기 자금시장 금리를 가른다",
        ),
        sa.Column(
            "label",
            sa.Text(),
            nullable=False,
            comment="차트와 표에 쓰는 표시 이름(예: 미국 10년물)",
        ),
        sa.CheckConstraint("kind IN ('government_bond', 'money_market')", name="ck_indicator_series_kind"),
        sa.CheckConstraint("maturity_months > 0", name="ck_indicator_series_maturity_months"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("provider", "series_id", name="uq_indicator_series_natural_key"),
        comment="지표 시계열이 어느 나라 무슨 금리인지 설명하는 마스터",
    )

    op.bulk_insert(
        indicator_series,
        [
            {
                "provider": provider,
                "series_id": series_id,
                "country": country,
                "country_name": country_name,
                "maturity_months": maturity_months,
                "kind": kind,
                "label": label,
            }
            for provider, series_id, country, country_name, maturity_months, kind, label in SEED
        ],
        # offline(`--sql`)에서는 executemany를 찍을 수 없다. 행마다 INSERT를 내게 한다.
        multiinsert=False,
    )


def downgrade_default() -> None:
    op.drop_table("indicator_series")
