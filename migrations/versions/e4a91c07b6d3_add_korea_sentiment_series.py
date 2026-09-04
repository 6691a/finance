"""add korea sentiment series

Revision ID: e4a91c07b6d3
Revises: c8d21f5a09b7
Create Date: 2026-09-04 11:00:00.000000

한국 매크로에 심리·경기 축을 더한다. 설계는
`docs/collection/korea-industry-macro-expansion.md` 3단계다.

지금 한국 지표는 금리(ECOS)와 수출입(관세청)뿐이다. 지수가 빠진 날 "금리가 올라서"는
말할 수 있고 "체감 경기가 꺾여서"는 말할 근거가 없었다.

## `sentiment` 종류를 더한다

**설문이 만드는 값과 실측이 만드는 값은 틀릴 때 틀리는 방식이 다르다.** 소비자심리지수와
기업경기실사지수를 `activity`에 넣으면 "실물이 어떤가"를 묻는 쿼리가 심리를 함께 그린다.

반대로 **선행종합지수는 `activity`다.** 설문이 아니라 실물 지표의 합성이고, `activity`는
이미 단위가 갈려 있다(달러·퍼센트·명·건).

## 좌표

2026-09-04에 `StatisticItemList`와 실제 응답으로 확인했다.

| 계열 | 통계표 | 항목코드 | 단위 |
| --- | --- | --- | --- |
| `CSI_M` | `511Y002` 소비자동향조사 | `FME` + `99988`(전체) | 없음(`null`) |
| `BSI_M` | `512Y015` 기업경기조사(실적) | `99988`(전산업) + `AA`(업황실적BSI) | 없음(`null`) |
| `LEADING_M` | `901Y067` 경기종합지수 | `I16A` | `2020=100` |

**소비자동향조사와 기업경기조사는 축이 둘이고 순서가 서로 다르다.** 하나만 넘기면 ECOS가
오류가 아니라 데이터 없음(`INFO-200`)으로 답해 조용한 0건이 된다.

**기업경기조사는 전망(`512Y014`)과 실적(`512Y015`)이 다른 통계표다.** 실적을 쓴다 — 전망은
응답자가 미래를 말한 값이라 실물과 어긋난 구간을 그 자체로 해석해야 하고, 지금 필요한 것은
"지금 어떤가"다.

**만기가 NULL이다.** 심리지수와 경기지수에는 만기 개념이 없다. 0으로 채우면 만기별 비교
쿼리가 "0개월물"로 그린다.

**월간이라 저장 식별자가 `_M`으로 끝난다.** 한 테이블에 일별과 월간이 섞여 있어 표시가 없으면
조회하는 쪽이 주기를 구분할 수 없다.

**수기 리비전이다.** 리비전에서 앱 코드를 import하지 않는다 — 나중에 수집기 Enum이 바뀔 때
과거 리비전의 결과가 따라 바뀐다. 대조는 `tests/migrations/test_indicator_series_catalog.py`가 한다.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e4a91c07b6d3"
down_revision: str | Sequence[str] | None = "c8d21f5a09b7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SEED_COLUMNS = ("provider", "series_id", "country", "country_name", "maturity_months", "kind", "label")

SENTIMENT_SEED: tuple[tuple[str, str, str, str, int | None, str, str], ...] = (
    ("ecos", "CSI_M", "KR", "대한민국", None, "sentiment", "소비자심리지수(월별)"),
    ("ecos", "BSI_M", "KR", "대한민국", None, "sentiment", "전산업 업황실적BSI(월별)"),
    ("ecos", "LEADING_M", "KR", "대한민국", None, "activity", "선행종합지수(월별)"),
)

KIND_CHECK = "ck_indicator_series_kind"

KIND_VALUES = (
    "'government_bond', 'money_market', 'policy_rate', 'tips_rate', 'credit_spread', "
    "'price_index', 'activity', 'balance_sheet', 'balance_sheet_item', 'sentiment'"
)
PREVIOUS_KIND_VALUES = (
    "'government_bond', 'money_market', 'policy_rate', 'tips_rate', 'credit_spread', "
    "'price_index', 'activity', 'balance_sheet', 'balance_sheet_item'"
)

KIND_COMMENT = (
    "시계열의 종류(government_bond, money_market, policy_rate, tips_rate, credit_spread, "
    "price_index, activity, balance_sheet, balance_sheet_item 또는 sentiment). 국채 곡선에서 "
    "단기 자금시장 금리·정책금리·실질금리·신용스프레드를 가르고, 단위가 다른 거시지표와 "
    "대차대조표 잔액과 설문이 만드는 심리지수를 그 곡선에서 뺀다"
)
PREVIOUS_KIND_COMMENT = (
    "시계열의 종류(government_bond, money_market, policy_rate, tips_rate, credit_spread, "
    "price_index, activity, balance_sheet 또는 balance_sheet_item). 국채 곡선에서 단기 자금시장 "
    "금리·정책금리·실질금리·신용스프레드를 가르고, 단위가 다른 거시지표와 대차대조표 잔액을 "
    "그 곡선에서 뺀다"
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
    # autogenerate는 CHECK 제약의 변경을 만들지 않는다. 손으로 갈아 끼운다.
    op.drop_constraint(KIND_CHECK, "indicator_series", type_="check")
    op.create_check_constraint(KIND_CHECK, "indicator_series", f"kind IN ({KIND_VALUES})")

    # 컬럼 주석에도 종류가 나열돼 있다. 모델과 어긋나면 autogenerate가 매번 COMMENT ON 차이를 낸다.
    op.alter_column(
        "indicator_series",
        "kind",
        existing_type=sa.VARCHAR(length=20),
        comment=KIND_COMMENT,
        existing_comment=PREVIOUS_KIND_COMMENT,
        existing_nullable=False,
    )

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
        [dict(zip(SEED_COLUMNS, row, strict=True)) for row in SENTIMENT_SEED],
        # offline(`--sql`)에서는 executemany를 찍을 수 없다. 행마다 INSERT를 내게 한다.
        multiinsert=False,
    )


def downgrade_default() -> None:
    # 시드를 먼저 지운다. 새 kind인 행이 남아 있으면 제약을 되돌릴 수 없다.
    series_ids = ", ".join(f"'{row[1]}'" for row in SENTIMENT_SEED)
    op.execute(f"DELETE FROM indicator_series WHERE provider = 'ecos' AND series_id IN ({series_ids})")

    op.alter_column(
        "indicator_series",
        "kind",
        existing_type=sa.VARCHAR(length=20),
        comment=PREVIOUS_KIND_COMMENT,
        existing_comment=KIND_COMMENT,
        existing_nullable=False,
    )
    op.drop_constraint(KIND_CHECK, "indicator_series", type_="check")
    op.create_check_constraint(KIND_CHECK, "indicator_series", f"kind IN ({PREVIOUS_KIND_VALUES})")
