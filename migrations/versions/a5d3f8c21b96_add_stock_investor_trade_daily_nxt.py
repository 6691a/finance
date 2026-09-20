"""add stock_investor_trade_daily_nxt

Revision ID: a5d3f8c21b96
Revises: e2a7c5d19b04
Create Date: 2026-09-20

종목별 투자자 매매동향 확정 일별값의 NXT 판. KIS 투자자 일별 API를 시장 코드 `NX`로 불러 받은
NXT 체결만의 일봉과 수급을 KRX 표(`stock_investor_trade_daily`)와 **같은 모양의 별도 표**에 쌓는다
(2026-09-20 실측, 설계 `docs/collection/kis-stock-daily-nxt.md`). 같은 표에 거래소 열을 더하지 않은
것은 그 표를 읽는 SQL 열둘이 하나라도 KRX 조건을 빠뜨리면 같은 날짜가 두 행으로 나와서다.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a5d3f8c21b96"
down_revision: str | Sequence[str] | None = "e2a7c5d19b04"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade(engine_name: str) -> None:
    globals().get(f"upgrade_{engine_name}", lambda: None)()


def downgrade(engine_name: str) -> None:
    globals().get(f"downgrade_{engine_name}", lambda: None)()


def upgrade_default() -> None:
    op.create_table(
        "stock_investor_trade_daily_nxt",
        sa.Column("provider", sa.Text(), nullable=False, comment="데이터 제공처 식별자(kis)"),
        sa.Column(
            "stock_code",
            sa.Text(),
            nullable=False,
            comment="6자리 종목코드(005930, 000660). 종목 이름은 instrument 마스터가 갖는다",
        ),
        sa.Column(
            "business_date",
            sa.Date(),
            nullable=False,
            comment="거래일(stck_bsop_date). NXT 영업일 기준이며 시각은 담지 않는다",
        ),
        sa.Column(
            "open_price",
            sa.Numeric(precision=18, scale=4),
            nullable=False,
            comment="시가(stck_oprc). NXT 체결 기준. 단위는 원",
        ),
        sa.Column(
            "high_price",
            sa.Numeric(precision=18, scale=4),
            nullable=False,
            comment="고가(stck_hgpr). NXT 체결 기준. 단위는 원",
        ),
        sa.Column(
            "low_price",
            sa.Numeric(precision=18, scale=4),
            nullable=False,
            comment="저가(stck_lwpr). NXT 체결 기준. 단위는 원",
        ),
        sa.Column(
            "close_price",
            sa.Numeric(precision=18, scale=4),
            nullable=False,
            comment="종가(stck_clpr). NXT 마감가(마지막 체결가). 단위는 원. 20:00 뒤에 받아야 확정이다",
        ),
        sa.Column(
            "accumulated_volume",
            sa.BigInteger(),
            nullable=False,
            comment="누적 거래량(acml_vol). NXT 체결만의 거래량. 단위는 주",
        ),
        sa.Column(
            "accumulated_trade_amount",
            sa.Numeric(precision=24, scale=2),
            nullable=False,
            comment="누적 거래대금(acml_tr_pbmn). **단위는 원이다.** 투자자별 대금만 백만원이라 섞어 쓰면 안 된다",
        ),
        sa.Column(
            "foreign_net_buy_qty",
            sa.BigInteger(),
            nullable=False,
            comment="외국인 순매수 수량(frgn_ntby_qty). 단위는 주. 등록+미등록과 일치하는지 검증한다",
        ),
        sa.Column(
            "foreign_registered_net_buy_qty",
            sa.BigInteger(),
            nullable=False,
            comment="외국인 등록분 순매수 수량(frgn_reg_ntby_qty). 단위는 주",
        ),
        sa.Column(
            "foreign_unregistered_net_buy_qty",
            sa.BigInteger(),
            nullable=False,
            comment="외국인 미등록분 순매수 수량(frgn_nreg_ntby_qty). 단위는 주",
        ),
        sa.Column(
            "individual_net_buy_qty",
            sa.BigInteger(),
            nullable=False,
            comment="개인 순매수 수량(prsn_ntby_qty). 단위는 주. 장중 추정 API에는 없는 값이다",
        ),
        sa.Column(
            "institution_net_buy_qty",
            sa.BigInteger(),
            nullable=False,
            comment="기관계 순매수 수량(orgn_ntby_qty). 단위는 주. 세부 일곱의 합과 일치하는지 검증한다",
        ),
        sa.Column(
            "securities_net_buy_qty",
            sa.BigInteger(),
            nullable=False,
            comment="금융투자 순매수 수량(scrt_ntby_qty). 기관계의 부분집합이다",
        ),
        sa.Column(
            "investment_trust_net_buy_qty",
            sa.BigInteger(),
            nullable=False,
            comment="투자신탁 순매수 수량(ivtr_ntby_qty). 기관계의 부분집합이다",
        ),
        sa.Column(
            "private_equity_net_buy_qty",
            sa.BigInteger(),
            nullable=False,
            comment="사모펀드 순매수 수량(pe_fund_ntby_vol). 이 분류만 접미사가 _vol이다",
        ),
        sa.Column(
            "bank_net_buy_qty",
            sa.BigInteger(),
            nullable=False,
            comment="은행 순매수 수량(bank_ntby_qty). 기관계의 부분집합이다",
        ),
        sa.Column(
            "insurance_net_buy_qty",
            sa.BigInteger(),
            nullable=False,
            comment="보험 순매수 수량(insu_ntby_qty). 기관계의 부분집합이다",
        ),
        sa.Column(
            "merchant_bank_net_buy_qty",
            sa.BigInteger(),
            nullable=False,
            comment="종금 순매수 수량(mrbn_ntby_qty). 기관계의 부분집합이다",
        ),
        sa.Column(
            "pension_fund_net_buy_qty",
            sa.BigInteger(),
            nullable=False,
            comment="기금 순매수 수량(fund_ntby_qty). 기관계의 부분집합이다",
        ),
        sa.Column(
            "other_corporation_net_buy_qty",
            sa.BigInteger(),
            nullable=False,
            comment="기타법인 순매수 수량(etc_corp_ntby_vol). 기관계 밖이며 접미사가 _vol이다",
        ),
        sa.Column(
            "other_organization_net_buy_qty",
            sa.BigInteger(),
            nullable=False,
            comment="기타단체 순매수 수량(etc_orgt_ntby_vol). 기관계 밖이며 접미사가 _vol이다",
        ),
        sa.Column(
            "foreign_net_buy_amount",
            sa.Numeric(precision=24, scale=2),
            nullable=False,
            comment="외국인 순매수 대금(frgn_ntby_tr_pbmn). **단위는 백만원이다**",
        ),
        sa.Column(
            "institution_net_buy_amount",
            sa.Numeric(precision=24, scale=2),
            nullable=False,
            comment="기관계 순매수 대금(orgn_ntby_tr_pbmn). 단위는 백만원",
        ),
        sa.Column(
            "individual_net_buy_amount",
            sa.Numeric(precision=24, scale=2),
            nullable=False,
            comment="개인 순매수 대금(prsn_ntby_tr_pbmn). 단위는 백만원",
        ),
        sa.Column(
            "source_record_id",
            sa.BigInteger(),
            nullable=False,
            comment="이 행을 마지막으로 갱신한 수집의 source_record 레코드 ID",
        ),
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
        sa.ForeignKeyConstraint(["source_record_id"], ["source_record.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "provider", "stock_code", "business_date", name="uq_stock_investor_trade_daily_nxt_natural_key"
        ),
        comment="종목별 투자자 매매동향의 NXT 확정 일별값을 누적하는 테이블. KRX 표와 컬럼이 같다",
        info={"database": "default", "managed": True},
    )
    op.create_index(
        "ix_stock_investor_trade_daily_nxt_business_date",
        "stock_investor_trade_daily_nxt",
        ["business_date"],
        unique=False,
    )
    op.create_index(
        "ix_stock_investor_trade_daily_nxt_source_record_id",
        "stock_investor_trade_daily_nxt",
        ["source_record_id"],
        unique=False,
    )


def downgrade_default() -> None:
    op.drop_index("ix_stock_investor_trade_daily_nxt_source_record_id", table_name="stock_investor_trade_daily_nxt")
    op.drop_index("ix_stock_investor_trade_daily_nxt_business_date", table_name="stock_investor_trade_daily_nxt")
    op.drop_table("stock_investor_trade_daily_nxt")
