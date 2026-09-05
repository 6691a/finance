"""add market shock search hit

Revision ID: c7e41d9b3a02
Revises: d5b02f8a91c4
Create Date: 2026-09-04 18:00:00.000000

원인 분석이 외부 검색으로 만난 기사를 영구 보관하는 표와, 그 답이 검색을 썼는지 표시하는
칸 하나. 설계는 `docs/analysis/market-shock-capture.md` §5.2다.

**밖의 페이지는 바뀌고 사라진다.** 우리 `document`는 원본이 우리 DB에 있어 id만 남기면
되지만, 검색 결과는 보관하지 않으면 근거가 증발한다.

**수기 리비전이다.** `config.yaml`이 운영 DB를 가리켜 autogenerate를 돌리지 않는다.

모델(`apps/models/market/shock.py`)과 여기의 CHECK 문자열·컬럼 주석은 **글자 그대로**
같아야 한다.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c7e41d9b3a02"
down_revision: str | Sequence[str] | None = "d5b02f8a91c4"
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


def upgrade_default() -> None:
    op.add_column(
        "market_shock_event",
        sa.Column(
            "cause_search_used",
            sa.Boolean(),
            server_default="false",
            nullable=False,
            comment=(
                "그 답이 외부 검색 결과를 근거로 썼나. 우리 문서만으로 푼 건과 갈라야 "
                "'검색이 몇 %를 풀었나'를 셀 수 있고, 그 숫자가 소스를 늘릴지 검색을 끌지 정한다"
            ),
        ),
    )
    op.create_table(
        "market_shock_search_hit",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False, comment="레코드 고유 식별자"),
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
            "shock_event_id",
            sa.BigInteger(),
            nullable=False,
            comment="이 결과를 찾게 만든 급변(market_shock_event.id)",
        ),
        sa.Column(
            "provider",
            sa.Text(),
            nullable=False,
            comment="검색 제공처 식별자(tavily). 제공처를 갈아 끼울 때 옛 행과 섞이지 않게 남긴다",
        ),
        sa.Column(
            "query",
            sa.Text(),
            nullable=False,
            comment="이 결과를 처음 물어온 질의 전문. 질의 형태가 맞았는지를 뒤에서 이 칸으로 본다",
        ),
        sa.Column(
            "attempt",
            sa.Integer(),
            nullable=False,
            comment="몇 번째 원인 분석 시도에서 처음 봤나(1부터)",
        ),
        sa.Column(
            "rank",
            sa.Integer(),
            nullable=False,
            comment="그 질의 결과에서 몇 번째였나(1부터). 상위에서 답이 나오는지가 제공처 평가의 축이다",
        ),
        sa.Column("title", sa.Text(), nullable=False, comment="기사 제목"),
        sa.Column(
            "url",
            sa.Text(),
            nullable=False,
            comment="기사 원문 URL. 자연키의 절반이라 같은 기사가 여러 질의에서 나와도 한 행이다",
        ),
        sa.Column(
            "publisher",
            sa.Text(),
            nullable=False,
            comment="URL의 호스트(www.ebn.co.kr). 어느 매체를 document_source로 승격할지 이 값으로 센다",
        ),
        sa.Column(
            "published_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="제공처가 준 발행 시각(UTC). 안 주는 결과가 있어 nullable이고, 그때는 창 필터를 못 건다",
        ),
        sa.Column(
            "snippet",
            sa.Text(),
            nullable=False,
            comment="제공처가 준 발췌 전문. 본문을 따로 긁지 않으므로 이 칸이 우리가 본 것의 전부다",
        ),
        sa.Column(
            "relevance",
            sa.Numeric(precision=6, scale=4),
            nullable=True,
            comment=(
                "제공처가 매긴 관련도(0~1). **우리가 만든 점수가 아니라 받은 값 그대로다** — "
                "제공처가 정한 눈금이라 정규화 규칙 밖이다"
            ),
        ),
        sa.Column(
            "cited",
            sa.Boolean(),
            server_default="false",
            nullable=False,
            comment="모델이 근거로 들었나. 검증이 끝난 뒤에 찍는다",
        ),
        sa.Column(
            "retrieved_at",
            sa.DateTime(timezone=True),
            nullable=False,
            comment="우리가 이 스냅샷을 받은 시각(UTC). 밖의 페이지가 바뀌어도 그때 본 것이 이 행이다",
        ),
        sa.CheckConstraint("attempt >= 1 AND rank >= 1", name="ck_market_shock_search_hit_positions"),
        sa.CheckConstraint(
            "relevance IS NULL OR (relevance >= 0 AND relevance <= 1)",
            name="ck_market_shock_search_hit_relevance",
        ),
        sa.ForeignKeyConstraint(["shock_event_id"], ["market_shock_event.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("shock_event_id", "url", name="uq_market_shock_search_hit_natural_key"),
        comment="급변의 원인을 찾다가 외부 검색으로 만난 기사를 영구 보관하는 테이블",
    )
    op.create_index(
        "ix_market_shock_search_hit_event",
        "market_shock_search_hit",
        ["shock_event_id"],
        unique=False,
    )
    op.create_index(
        "ix_market_shock_search_hit_publisher",
        "market_shock_search_hit",
        ["publisher", "cited"],
        unique=False,
    )


def downgrade_default() -> None:
    op.drop_index("ix_market_shock_search_hit_publisher", table_name="market_shock_search_hit")
    op.drop_index("ix_market_shock_search_hit_event", table_name="market_shock_search_hit")
    op.drop_table("market_shock_search_hit")
    op.drop_column("market_shock_event", "cause_search_used")
