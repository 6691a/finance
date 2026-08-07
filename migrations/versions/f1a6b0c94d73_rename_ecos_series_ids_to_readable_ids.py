"""rename ecos series ids to readable ids

Revision ID: f1a6b0c94d73
Revises: c7d41a9f38b2
Create Date: 2026-08-07 14:38:05.612904

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f1a6b0c94d73"
down_revision: str | Sequence[str] | None = "c7d41a9f38b2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PROVIDER = "ecos"

# 처음에는 ECOS의 원본 좌표(통계표 코드 + 항목코드)를 그대로 저장했다. 숫자만 남아 DB나
# 대시보드에서 무슨 시계열인지 읽을 수 없어 읽을 수 있는 ID로 바꾼다. 원본 항목코드는
# `modules.collectors.ecos.MarketRateSeries`가 들고 있고 수집 때마다
# `source_record.metadata`에도 남는다.
RENAMES: tuple[tuple[str, str], ...] = (
    ("817Y002.010195000", "KTB2Y"),
    ("817Y002.010200000", "KTB3Y"),
    ("817Y002.010210000", "KTB10Y"),
    ("817Y002.010230000", "KTB30Y"),
    ("817Y002.010502000", "CD91D"),
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


def _rename(pairs: tuple[tuple[str, str], ...]) -> None:
    for old, new in pairs:
        # 관측값과 그 근거 레코드가 같은 식별자를 쓰므로 둘을 함께 옮긴다.
        op.execute(
            f"UPDATE indicator_observation SET series_id = '{new}'"
            f" WHERE provider = '{PROVIDER}' AND series_id = '{old}'"
        )
        op.execute(
            f"UPDATE source_record SET source_key = '{new}' WHERE source = '{PROVIDER}' AND source_key = '{old}'"
        )


def upgrade_default() -> None:
    _rename(RENAMES)


def downgrade_default() -> None:
    _rename(tuple((new, old) for old, new in RENAMES))
