"""add instrument filing entity id and sector

Revision ID: c8d21f5a09b7
Revises: b7e2d4a91c35
Create Date: 2026-09-04 10:00:00.000000

산업 대표 20사를 **한국 거시 지표의 표본**으로 쓰기 위한 두 칸이다. 설계는
`docs/collection/korea-industry-macro-expansion.md`다.

## `filing_entity_id` — 공시·실적 대상을 시세 대상에서 뗀다

지금까지 DART 대상은 `DartCompany` StrEnum 두 줄이었고, `is_watched`가 참인 종목과
정확히 같아야 한다는 테스트에 묶여 있었다. 그래서 공시를 20사로 늘리면 분봉·수급·실시간
구독까지 함께 끌려온다. **시세와 규제 공시는 대상이 다르다.**

이 칸에 값이 있으면 공시·실적 수집 대상이고 `NULL`이면 아니다. 별도 플래그를 두지 않는
이유는 그 번호가 어차피 어딘가 있어야 하는 값이고, **번호가 있다는 것 자체가 "규제 공시에서
이 회사를 안다"**이기 때문이다. 플래그를 따로 두면 번호는 비었는데 플래그만 참인 조합이
생긴다.

**컬럼 이름에 `dart`를 박지 않는다.** `ck_instrument_market`이 이미 `nyse`·`nasdaq`을
허용하므로 미국 종목이 이 테이블에 오는 것은 가정이 아니다. 발급 기관은 `market`이
정한다 — 한국은 DART 회사 고유번호 8자리, 미국은 SEC EDGAR CIK다. 그래서 읽는 쪽은
`filing_entity_id IS NOT NULL`만 걸지 않고 `market`을 함께 건다.

## `sector` — 대표가 바뀌어도 축은 안 바뀐다

`a2f7c31e9b64`(seed sector instruments)가 업종을 **주석으로만** 남기고 컬럼을 두지 않았다.
그때의 근거는 "우리가 그 값을 쓰는 자리가 아직 없다"였다. **그 자리가 생겨서 뒤집는다** —
거시 지표는 회사가 아니라 산업 단위로 집계해야 하고, 섹터 없이 20사 합계만 쓰면 대표를
교체한 해의 점프가 산업 변화인지 명단 변화인지 가릴 수 없다.

값은 `a2f7c31e9b64`의 주석에 있던 업종 이름을 글자 그대로 이어받고, 반도체 둘을 더한다.

**Enum과 CHECK를 두지 않는다.** 이 값이 바뀌는 것이 컬럼의 전제다 — 화장품이 한국 수출의
얼굴에서 내려가고 소프트웨어가 그 자리에 오면 섹터 이름부터 바뀐다. CHECK를 걸면 그때
마이그레이션이 두 벌(CHECK 변경 + 시드)이 된다.

## 명단 교체는 이 칸의 `UPDATE`다

회사를 뺄 때 **행을 지우지 않는다.** `filing_entity_id`를 `NULL`로 만들면 수집만 멈추고
`earnings_fact`·`disclosure_event`의 과거 행과 문서 태그 후보는 그대로 남는다. 거시
지표에서 과거를 잃는 것은 지표를 잃는 것과 같다.

교체 이력은 별도 테이블이 아니라 **리비전이 갖는다**(`b3f9c72e1d54`가 조선 대표를 바꾼
것이 그 선례다). 교체 리비전 주석에는 뺀 회사, 넣은 회사, 그리고 **왜 그 산업이 더 이상
대표가 아닌가** 셋을 적는다.

DART 회사 고유번호 20개는 `corpCode.xml`에서 20/20 대조했다(2026-09-04).

**수기 리비전이다.** `config.yaml`이 운영 DB를 가리켜 autogenerate를 돌리지 않는다.
모델(`apps/models/reference.py`)과 여기의 컬럼 주석은 **글자 그대로** 같아야 한다.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c8d21f5a09b7"
down_revision: str | Sequence[str] | None = "b7e2d4a91c35"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

FILING_ENTITY_ID_COMMENT = (
    "그 나라 공시 규제기관이 이 회사에 붙인 고유번호. 값이 있으면 규제 공시·실적 수집 대상이고 "
    "NULL이면 아니다. 발급 기관은 market이 정한다(kospi·kosdaq=금융감독원 DART 회사 고유번호 8자리, "
    "nyse·nasdaq=SEC EDGAR CIK). 그래서 읽는 쪽은 market을 함께 건다"
)

SECTOR_COMMENT = (
    "이 종목이 대표하는 산업(예: 반도체, 자동차, 화장품). 한국 거시 지표를 회사가 아니라 "
    "산업 단위로 집계하기 위한 축이며 대표 기업이 교체돼도 이름이 바뀌지 않는다. "
    "값이 바뀌는 것이 전제라 Enum과 CHECK를 두지 않는다"
)

# (종목코드, DART 회사 고유번호, 섹터). 회사 이름은 이미 `instrument.name`에 있다.
# 반도체만 둘이고 나머지는 섹터당 하나다.
FILING_ENTITIES: tuple[tuple[str, str, str], ...] = (
    ("005930", "00126380", "반도체"),
    ("000660", "00164779", "반도체"),
    ("005380", "00164742", "자동차"),
    ("373220", "01515323", "2차전지"),
    ("051910", "00356361", "화학"),
    ("005490", "00155319", "철강"),
    ("329180", "01390344", "조선"),
    ("012450", "00126566", "방산"),
    ("000720", "00164478", "건설"),
    ("090430", "00583424", "화장품"),
    ("207940", "00877059", "바이오"),
    ("035420", "00266961", "인터넷"),
    ("105560", "00688996", "은행"),
    ("006800", "00111722", "증권"),
    ("032830", "00126256", "보험"),
    ("139480", "00872984", "유통"),
    ("003490", "00113526", "항공"),
    ("011200", "00164645", "해운"),
    ("015760", "00159193", "전력"),
    ("017670", "00159023", "통신"),
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
    op.add_column(
        "instrument",
        sa.Column("filing_entity_id", sa.Text(), nullable=True, comment=FILING_ENTITY_ID_COMMENT),
    )
    op.add_column(
        "instrument",
        sa.Column("sector", sa.Text(), nullable=True, comment=SECTOR_COMMENT),
    )
    for ticker, filing_entity_id, sector in FILING_ENTITIES:
        # `market`을 함께 건다. 같은 종목코드가 다른 시장에 생기면 그 행은 다른 기관의
        # 번호를 받아야 한다.
        op.execute(
            "UPDATE instrument "
            f"SET filing_entity_id = '{filing_entity_id}', sector = '{sector}' "
            f"WHERE market = 'kospi' AND ticker = '{ticker}'"
        )


def downgrade_default() -> None:
    op.drop_column("instrument", "sector")
    op.drop_column("instrument", "filing_entity_id")
