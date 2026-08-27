"""add contract code to index future daily

Revision ID: f8a2c6d9e104
Revises: b7f4c2a91d38
Create Date: 2026-08-27 23:40:00.000000

지수선물 일봉이 실제 월물을 행마다 남긴다.

분봉 `index_future_bar`는 이 칸을 처음부터 갖고 있었다. 일봉에 없었던 이유는 그 테이블을
Yahoo 연속 심볼(`ES=F`)만 채웠기 때문이다. 연속 심볼에는 실제 월물이 없어 남길 것이 없었다.
KIS 국내선물은 다르다. `KOSPI200_FUT` 한 시계열이 분기마다 다른 계약(`A01609` → `A01612`)의
값으로 이어지므로, **이 칸이 없으면 월물이 바뀐 날의 갭이 시장 급변인지 롤오버인지 구분되지 않는다.**

**nullable이고 server_default가 없다.** Yahoo 연속 심볼은 앞으로도 NULL이고, 그것이 "안 남겼다"가
아니라 "남길 월물이 없다"는 뜻이다. 빈 문자열로 메우면 둘이 같아진다.

자연키는 `(provider, symbol, business_date)` 그대로다. 최근월물은 날짜마다 하나뿐이라 월물을
키에 넣을 이유가 없고, 넣으면 롤 하는 날 같은 날짜에 두 행이 생긴다.

`quote_daily` 뷰는 건드리지 않는다. 그 뷰는 kind별 테이블의 **공통** 읽기 모양이고 월물은
선물에만 있다. 월물 경계는 물리 테이블을 직접 조회한다.

**수기 리비전이다.** `config.yaml`이 운영 DB를 가리켜 autogenerate를 돌리지 않는다.
모델(`apps/models/market/series.py`)과 여기의 컬럼 주석은 **글자 그대로** 같아야 한다.
다르면 다음 autogenerate가 매번 차이를 만든다.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f8a2c6d9e104"
down_revision: str | Sequence[str] | None = "b7f4c2a91d38"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CONTRACT_CODE_COMMENT = (
    "선물의 실제 월물 코드(예: A01609). Yahoo 연속 심볼(ES=F)은 NULL이다. "
    "월물이 바뀌면 가격에 갭이 생기는데, 이 값이 없으면 그 갭이 시장 급변인지 "
    "롤오버인지 구분할 수 없다"
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
        "index_future_daily",
        sa.Column("contract_code", sa.Text(), nullable=True, comment=CONTRACT_CODE_COMMENT),
    )


def downgrade_default() -> None:
    op.drop_column("index_future_daily", "contract_code")
