"""create daily series view

Revision ID: 2029012bafaa
Revises: 400f10fc8e60
Create Date: 2026-08-15 13:39:51.091951

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "2029012bafaa"
down_revision: str | Sequence[str] | None = "400f10fc8e60"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


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


# 흩어져 있는 일별 시계열을 한 이름 공간으로 모은다.
#
# **이 뷰가 있는 이유는 LLM에게 줄 툴을 하나로 만들기 위해서다.** 금리는
# `indicator_observation`, 환율은 `exchange_rate`, 지수·원자재는 `quote_daily`, 종목 종가는
# `stock_investor_trade_daily`에 있다. 툴을 네 개로 나누면 모델이 어느 것을 불러야 할지부터
# 틀리고, 상관 계산도 조합마다 다른 조인을 써야 한다.
#
# `(provider, series_id)`가 좌표다. `series_id`는 제공처 안에서만 고유하므로 provider가 함께
# 들어간다. `indicator_observation`이 쓰는 규칙과 같다.
#
# 환율은 하루에 회차가 1,300건 넘게 온다. 그중 마지막 회차 하나만 그날 값으로 쓴다.
# 매매기준율이 0으로 고시되는 통화가 있어 그건 뺀다.
#
# 뷰라서 마이그레이션이 데이터를 옮기지 않는다. 원본 테이블이 갱신되면 그대로 따라온다.
DAILY_SERIES_VIEW = """
CREATE OR REPLACE VIEW daily_series AS
SELECT provider, symbol AS series_id, business_date, close AS value, 'price' AS kind
FROM quote_daily
UNION ALL
SELECT provider, series_id, observation_date AS business_date, value, 'rate' AS kind
FROM indicator_observation
UNION ALL
SELECT provider, stock_code AS series_id, business_date, close_price AS value, 'price' AS kind
FROM stock_investor_trade_daily
UNION ALL
-- 하위 질의로 감싼다. UNION 안에서 ORDER BY를 쓰면 전체 결과에 붙어 원본 컬럼 이름을
-- 찾지 못한다(`column "currency" does not exist`).
SELECT * FROM (
    SELECT DISTINCT ON (currency, date)
        'hana' AS provider, currency AS series_id, date AS business_date,
        exchange_standard_rate AS value, 'fx' AS kind
    FROM exchange_rate
    WHERE exchange_standard_rate > 0
    ORDER BY currency, date, round DESC
) AS hana_daily
"""


def upgrade_default() -> None:
    op.execute(DAILY_SERIES_VIEW)


def downgrade_default() -> None:
    op.execute("DROP VIEW IF EXISTS daily_series")


def upgrade_market_migration() -> None:
    pass


def downgrade_market_migration() -> None:
    pass
