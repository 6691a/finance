"""seed sector instruments

Revision ID: a2f7c31e9b64
Revises: d9a41c7e05b3
Create Date: 2026-08-31 12:00:00.000000

문서 태그 후보를 KOSPI 업종 대표 종목으로 넓힌다. 설계는
`docs/analysis/economic-document-archive-design.md` 6.6이다.

- **`is_watched`는 전부 `False`다.** 이 플래그는 "시세까지 받는 종목"이라, 참으로 넣으면
  기술지표 조회·주간 인과 그래프 대상·추론 subject가 함께 켜진다. 봉이 없는 종목이 거기
  들어가면 오류 없이 빈 결과가 된다(추론 baseline 셋은 CHECK가 전부 NULL을 허용한다).
- 여기 넣은 행이 하는 일은 **문서 태그 후보와 네이버 기업 리포트 통과**뿐이다. 그 둘은
  `instrument` 전체를 읽는 `select_taggable.sql`을 본다.
- 업종마다 종목 하나다. 여럿 넣으면 한 리포트가 같은 업종 여럿을 함께 다뤄 태그가 늘
  함께 붙는다 — 지금 삼성전자·SK하이닉스가 92% 함께 붙는 것과 같은 형태다.
- 코드와 약명은 KIS 상품기본조회(`CTPF1002R`)로 대조했다(2026-08-31). `name`은 그 응답의
  `prdt_abrv_name`이고, 열여덟 전부 `mket_id_cd = STK`(유가증권)다.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a2f7c31e9b64"
down_revision: str | Sequence[str] | None = "d9a41c7e05b3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# (업종, 종목코드, 약명). 업종은 이 목록을 고른 이유라 주석으로만 남긴다 — 테이블에 업종
# 컬럼을 두지 않는다. 종목이 어느 업종인지는 KIS와 KRX가 각자 다르게 분류하고, 우리가
# 그 값을 쓰는 자리가 아직 없다.
SECTOR_INSTRUMENTS: tuple[tuple[str, str, str], ...] = (
    ("자동차", "005380", "현대차"),
    ("2차전지", "373220", "LG에너지솔루션"),
    ("화학", "051910", "LG화학"),
    ("철강", "005490", "POSCO홀딩스"),
    ("조선", "009540", "HD한국조선해양"),
    ("방산", "012450", "한화에어로스페이스"),
    ("건설", "000720", "현대건설"),
    ("화장품", "090430", "아모레퍼시픽"),
    ("바이오", "207940", "삼성바이오로직스"),
    ("인터넷", "035420", "NAVER"),
    ("은행", "105560", "KB금융"),
    ("증권", "006800", "미래에셋증권"),
    ("보험", "032830", "삼성생명"),
    ("유통", "139480", "이마트"),
    ("항공", "003490", "대한항공"),
    ("해운", "011200", "HMM"),
    ("전력", "015760", "한국전력"),
    ("통신", "017670", "SK텔레콤"),
)


TABLE_COMMENT = "우리가 이름을 아는 종목의 마스터. is_watched가 참인 종목만 시세를 받는다"
IS_WATCHED_COMMENT = "시세를 수집할 대상 여부. 거짓이면 문서 태그 후보로만 쓴다"
PREVIOUS_TABLE_COMMENT = "시세·뉴스·시그널이 참조하는 추적 종목 마스터"
PREVIOUS_IS_WATCHED_COMMENT = "신규 데이터 수집과 분석을 수행할 추적 대상 여부"


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
                "ticker": ticker,
                "market": "kospi",
                "name": name,
                "kind": "equity",
                "currency": "KRW",
                "is_watched": False,
            }
            for _, ticker, name in SECTOR_INSTRUMENTS
        ],
        multiinsert=False,
    )
    # 주석은 모델과 글자 그대로 같아야 한다. 다르면 다음 autogenerate가 매번 COMMENT ON
    # 차이를 만든다. 뜻이 바뀐 것을 스키마에도 남긴다 — 이 테이블을 "우리 종목"으로 읽고
    # 조인하는 코드가 앞으로 생기지 않게.
    op.execute(f"COMMENT ON TABLE instrument IS '{TABLE_COMMENT}'")
    op.execute(f"COMMENT ON COLUMN instrument.is_watched IS '{IS_WATCHED_COMMENT}'")


def downgrade_default() -> None:
    tickers = ", ".join(f"'{ticker}'" for _, ticker, _ in SECTOR_INSTRUMENTS)
    # `is_watched`를 함께 거른다. 되돌리는 사이에 어떤 종목이 시세 대상으로 승격됐다면
    # 그것은 이 리비전이 넣은 행이 아니라 운영이 만든 상태다.
    op.execute(f"DELETE FROM instrument WHERE market = 'kospi' AND NOT is_watched AND ticker IN ({tickers})")
    op.execute(f"COMMENT ON TABLE instrument IS '{PREVIOUS_TABLE_COMMENT}'")
    op.execute(f"COMMENT ON COLUMN instrument.is_watched IS '{PREVIOUS_IS_WATCHED_COMMENT}'")
