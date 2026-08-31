"""swap shipbuilding to operating company

Revision ID: b3f9c72e1d54
Revises: a2f7c31e9b64
Create Date: 2026-08-31 17:10:00.000000

조선 대표 종목을 HD한국조선해양(009540)에서 HD현대중공업(329180)으로 바꾼다.

**순수지주회사는 KRX 업종이 `금융`이다.** 자회사 주식을 보유·관리하는 것이 그 회사의
사업이라 표준산업분류가 `기타 금융업`이고, 업종지수도 그것을 따른다. 실측에서
HD한국조선해양·HD현대·LG·SK·CJ가 전부 그랬다(2026-08-31). 배를 만드는 것은 자회사다.

그대로 두면 조선 기사가 금융 업종에 걸린다 — 오류 없이, 그럴듯하게. 6.7의 업종 축이
그 매핑 위에 서므로 축을 붙이기 전에 종목을 바꾼다.

HD현대중공업은 표준산업분류가 `선박 및 보트 건조업`, KRX 업종이 `운송장비·부품`이다.
약명과 시장은 KIS 상품기본조회(`CTPF1002R`)로 대조했다 — `HD현대중공업`, `mket_id_cd=STK`.

**사업지주는 이 규칙에 걸리지 않는다.** 한화(000880)는 `화학`, 두산(000150)은 `전기·전자`,
POSCO홀딩스는 `금속`이다. 판별은 이름이 아니라 표준산업분류가 `기타 금융업`인지로 한다.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b3f9c72e1d54"
down_revision: str | Sequence[str] | None = "a2f7c31e9b64"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

HOLDING_COMPANY = ("009540", "HD한국조선해양")
OPERATING_COMPANY = ("329180", "HD현대중공업")


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


def _swap(remove: tuple[str, str], add: tuple[str, str]) -> None:
    """마스터에서 한 종목을 빼고 다른 하나를 넣는다.

    **`is_watched`가 거짓인 행만 지운다.** 그 사이에 어떤 종목이 시세 대상으로 승격됐다면
    그것은 이 리비전이 넣은 상태가 아니므로 건드리지 않는다.

    이미 붙은 `document_instrument` 태그는 남는다. 외래키가 없어 DB가 막지 않고, 지난
    문서가 그때 무엇으로 평가됐는지는 사실 그대로 남아야 한다.
    """
    op.execute(f"DELETE FROM instrument WHERE ticker = '{remove[0]}' AND market = 'kospi' AND NOT is_watched")
    op.bulk_insert(
        sa.table(
            "instrument",
            sa.column("ticker", sa.Text),
            sa.column("market", sa.String),
            sa.column("name", sa.Text),
            sa.column("kind", sa.String),
            sa.column("currency", sa.Text),
            sa.column("is_watched", sa.Boolean),
        ),
        [
            {
                "ticker": add[0],
                "market": "kospi",
                "name": add[1],
                "kind": "equity",
                "currency": "KRW",
                "is_watched": False,
            }
        ],
        multiinsert=False,
    )


def upgrade_default() -> None:
    _swap(HOLDING_COMPANY, OPERATING_COMPANY)


def downgrade_default() -> None:
    _swap(OPERATING_COMPANY, HOLDING_COMPANY)
