"""시세 봉 — 분봉·일봉과 거래소 vocabulary.

kind별 물리 테이블이다(2026-08-18 분리). `quote_bar`/`quote_daily` 뷰가 이들을 UNION ALL
하지만 **쓰기는 반드시 이 테이블로 간다.** 공통 컬럼은 `MacroBarColumns`·`MacroDailyColumns`
믹스인이 갖고, 개별 종목(`stock_bar`)만 거래소를 자연키의 한 축으로 둔다.
"""

from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    Text,
    UniqueConstraint,
)
from sqlalchemy import Enum as SqlEnum
from sqlalchemy.orm import Mapped, declared_attr, mapped_column

from apps.core.database import EntityBase, table_options


class StockExchange(StrEnum):
    """개별 종목 봉의 거래소.

    같은 종목이 KRX와 NXT(넥스트레이드)에서 따로 체결되므로 거래소가 자연키의 한 축이다.
    통합(`UN`) 시세는 두 거래소 체결을 섞어 어느 쪽 값도 아니게 되므로 받지 않는다.
    해외 상장 종목(TSMC ADR)은 상장 거래소를 그대로 적는다.
    """

    KRX = "KRX"
    NXT = "NXT"
    NYSE = "NYSE"
    NASDAQ = "NASDAQ"


# ---------------------------------------------------------------------------
# kind별 봉 테이블
#
# 원래 `quote_bar`/`quote_daily` 두 테이블이 8개 kind(지수·지수선물·환율·금리·채권선물·
# 원자재·종목·암호화폐)를 전부 담았다. 자산군마다 거래 시간대·변동폭·키 체계가 달라
# 직접 조회할 때 읽기 어려워 2026-08-18에 kind별 물리 테이블로 갈랐다.
#
# - 행 모양은 전부 같아 mixin 으로 선언한다. 심볼의 성격(라벨·국가)은 여전히
#   `reference.quote_symbol` 마스터가 갖는다.
# - 기존 이름 `quote_bar`/`quote_daily`는 kind 테이블을 UNION ALL 한 **뷰**로 남아
#   브리핑 SQL과 Grafana 대시보드가 그대로 돈다. 뷰는 마이그레이션이 만들고 여기에는
#   매핑하지 않는다. 매핑을 남기면 autogenerate 가 같은 이름의 테이블을 또 만들려 한다.
# - 개별 종목(`stock_bar`/`stock_daily`)만 축이 다르다. 거래소(KRX/NXT)가 자연키에
#   들어가고 종목코드 세계(`instrument.ticker`, 수급·공시 테이블)와 키 체계를 맞춘다.
# ---------------------------------------------------------------------------


class MacroBarColumns:
    """kind별 1분봉 테이블이 공유하는 컬럼.

    `indicator_observation`과 목적이 다르다. 저쪽은 하루 한 값을 쌓는 리포트용이고, 이쪽은
    미국 선물이 지금 빠지고 있으니 한국 반도체가 곧 빠질 수 있다는 **실시간 알림**을 위한
    것이다. 수집은 24시간 돈다.

    `(provider, symbol, bar_at)`이 멱등 키다. 폴링 주기보다 넓은 구간을 받아도 겹치는 봉은
    갱신으로 흡수된다. `symbol`은 제공처 안에서만 고유하므로 키에 `provider`가 함께 들어간다.
    """

    provider: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="데이터 제공처 식별자(예: yahoo 또는 kis). 같은 수집의 source_record.source와 같은 값이다",
    )
    symbol: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="시세 대상 식별자(예: SP500_FUT, SOX). quote_symbol 마스터의 symbol과 같으며 제공처 안에서만 고유하다",
    )
    bar_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        comment="1분봉이 시작하는 시각(UTC). 봉은 이 시각부터 1분간의 거래를 담는다",
    )
    open: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False, comment="봉 구간의 시가")
    high: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False, comment="봉 구간의 고가")
    low: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False, comment="봉 구간의 저가")
    close: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False, comment="봉 구간의 종가")
    volume: Mapped[int | None] = mapped_column(
        BigInteger,
        nullable=True,
        comment=(
            "봉 구간의 거래량. 제공처가 주는 값을 그대로 저장한다. "
            "현물 지수처럼 거래량 개념이 없는 심볼은 제공처가 0을 실어 보내므로 0이 들어간다. "
            "즉 0은 '거래가 없었다'와 '제공처가 거래량을 주지 않는다'를 구분하지 않는다"
        ),
    )
    previous_close: Mapped[Decimal] = mapped_column(
        Numeric(18, 8),
        nullable=False,
        comment=(
            "직전 정규장 종가. 알림이 쓰는 변동률의 분모다. "
            "봉마다 같은 값이 반복되지만 세션 경계 계산을 피하려고 그대로 저장한다"
        ),
    )

    @declared_attr
    def source_record_id(cls) -> Mapped[int]:
        return mapped_column(
            BigInteger,
            ForeignKey("source_record.id", ondelete="RESTRICT"),
            nullable=False,
            comment="근거가 되는 source_record 레코드 ID",
        )


def _bar_table_args(table: str, comment: str) -> tuple:
    """1분봉 테이블의 공통 제약. 이름에 테이블이 들어가야 해서 함수로 만든다."""
    return (
        UniqueConstraint("provider", "symbol", "bar_at", name=f"uq_{table}_natural_key"),
        Index(f"ix_{table}_source_record_id", "source_record_id"),
        table_options(comment=comment, database="default"),
    )


class IndexBar(MacroBarColumns, EntityBase):
    """현물 지수 1분봉. KIS(코스피·코스피200·코스닥, 해외지수 S&P500·나스닥 종합)와 Yahoo(해외 지수)가 채운다."""

    __tablename__ = "index_bar"
    __table_args__ = _bar_table_args("index_bar", "현물 지수의 1분봉을 장중 알림 판단용으로 누적하는 테이블")


class IndexFutureBar(MacroBarColumns, EntityBase):
    """지수선물 1분봉. 현물이 멈춘 시간대에도 움직여 실시간 알림의 본체다."""

    __tablename__ = "index_future_bar"
    __table_args__ = _bar_table_args("index_future_bar", "지수선물의 1분봉을 장중 알림 판단용으로 누적하는 테이블")

    contract_code: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment=(
            "선물의 실제 월물 코드(예: A01609). Yahoo 연속 심볼(ES=F)은 NULL이다. "
            "월물이 바뀌면 가격에 갭이 생기는데, 이 값이 없으면 그 갭이 시장 급변인지 "
            "롤오버인지 구분할 수 없다"
        ),
    )


class FxBar(MacroBarColumns, EntityBase):
    """환율 1분봉. 하나은행 고시(`exchange_rate`)와 달리 장외 시장 환율이다."""

    __tablename__ = "fx_bar"
    __table_args__ = _bar_table_args("fx_bar", "장외 시장 환율의 1분봉을 장중 알림 판단용으로 누적하는 테이블")


class RateBar(MacroBarColumns, EntityBase):
    """수익률 1분봉(US10Y). 값이 가격이 아니라 퍼센트라 **변화율이 아니라 bp로 읽는다.**"""

    __tablename__ = "rate_bar"
    __table_args__ = _bar_table_args("rate_bar", "금리 수익률의 1분봉을 장중 알림 판단용으로 누적하는 테이블")


class BondFutureBar(MacroBarColumns, EntityBase):
    """채권선물 1분봉. `rate`와 달리 **가격**이라 금리와 반대로 움직인다."""

    __tablename__ = "bond_future_bar"
    __table_args__ = _bar_table_args("bond_future_bar", "채권선물 가격의 1분봉을 장중 알림 판단용으로 누적하는 테이블")


class CommodityBar(MacroBarColumns, EntityBase):
    """원자재 최근월물 1분봉. 금·은·구리·유가."""

    __tablename__ = "commodity_bar"
    __table_args__ = _bar_table_args("commodity_bar", "원자재 선물의 1분봉을 장중 알림 판단용으로 누적하는 테이블")


class CryptoBar(MacroBarColumns, EntityBase):
    """암호화폐 1분봉. 다른 모든 값이 멈추는 주말 48시간을 채우는 유일한 시세다."""

    __tablename__ = "crypto_bar"
    __table_args__ = _bar_table_args("crypto_bar", "암호화폐의 1분봉을 장중 알림 판단용으로 누적하는 테이블")


class MacroDailyColumns:
    """kind별 일봉 테이블이 공유하는 컬럼.

    분봉과 목적도 보존 기간도 다르다. 분봉은 장중 알림용이고 제공처가 30일치만 준다.
    **30일 표본으로는 두 시계열의 상관을 낼 수 없다.** 상관 분석에는 몇 년치 일별 수익률이
    필요하고, Yahoo는 `interval=1d`로 심볼당 한 번에 십수 년을 준다.

    분봉과 한 테이블에 섞지 않는 이유는 축이 다르기 때문이다. 분봉은 `bar_at` timestamptz가
    키이고 일봉은 거래일 날짜가 키다.

    전일 종가 컬럼을 두지 않는다. 일봉은 직전 행이 곧 전일이라 `lag()` 하나로 나온다.
    """

    provider: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="데이터 제공처 식별자(yahoo). 같은 수집의 source_record.source와 같은 값이다",
    )
    symbol: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="시세 대상 식별자(예: USDKRW, SOX). quote_symbol 마스터의 symbol과 같으며 제공처 안에서만 고유하다",
    )
    business_date: Mapped[date] = mapped_column(
        Date,
        nullable=False,
        comment=(
            "봉이 담는 거래일. 제공처가 준 봉 시작 시각을 그 시장의 현지 날짜로 바꾼 값이다. "
            "심볼마다 기준 시장이 달라 UTC 날짜와 어긋날 수 있다"
        ),
    )
    open: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False, comment="그 거래일의 시가")
    high: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False, comment="그 거래일의 고가")
    low: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False, comment="그 거래일의 저가")
    close: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False, comment="그 거래일의 종가")
    volume: Mapped[int | None] = mapped_column(
        BigInteger,
        nullable=True,
        comment=(
            "그 거래일의 거래량. 제공처가 주는 값을 그대로 저장한다. "
            "현물 지수와 환율처럼 거래량 개념이 없는 심볼은 제공처가 0을 실어 보내므로 0이 들어간다"
        ),
    )

    @declared_attr
    def source_record_id(cls) -> Mapped[int]:
        return mapped_column(
            BigInteger,
            ForeignKey("source_record.id", ondelete="RESTRICT"),
            nullable=False,
            comment="근거가 되는 source_record 레코드 ID",
        )


def _daily_table_args(table: str, comment: str) -> tuple:
    """일봉 테이블의 공통 제약. 분봉과 달리 거래일 인덱스가 붙는다."""
    return (
        UniqueConstraint("provider", "symbol", "business_date", name=f"uq_{table}_natural_key"),
        Index(f"ix_{table}_business_date", "business_date"),
        Index(f"ix_{table}_source_record_id", "source_record_id"),
        table_options(comment=comment, database="default"),
    )


class IndexDaily(MacroDailyColumns, EntityBase):
    """현물 지수 일봉."""

    __tablename__ = "index_daily"
    __table_args__ = _daily_table_args("index_daily", "현물 지수의 일봉을 상관 분석용으로 누적하는 테이블")


class IndexFutureDaily(MacroDailyColumns, EntityBase):
    """지수선물 일봉. 논리 심볼과 실제 월물을 함께 갖는다."""

    __tablename__ = "index_future_daily"
    __table_args__ = _daily_table_args("index_future_daily", "지수선물의 일봉을 상관 분석용으로 누적하는 테이블")

    contract_code: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment=(
            "선물의 실제 월물 코드(예: A01609). Yahoo 연속 심볼(ES=F)은 NULL이다. "
            "월물이 바뀌면 가격에 갭이 생기는데, 이 값이 없으면 그 갭이 시장 급변인지 "
            "롤오버인지 구분할 수 없다"
        ),
    )


class FxDaily(MacroDailyColumns, EntityBase):
    """환율 일봉."""

    __tablename__ = "fx_daily"
    __table_args__ = _daily_table_args("fx_daily", "장외 시장 환율의 일봉을 상관 분석용으로 누적하는 테이블")


class RateDaily(MacroDailyColumns, EntityBase):
    """수익률 일봉(US10Y). 변화율이 아니라 bp로 읽는다."""

    __tablename__ = "rate_daily"
    __table_args__ = _daily_table_args("rate_daily", "금리 수익률의 일봉을 상관 분석용으로 누적하는 테이블")


class BondFutureDaily(MacroDailyColumns, EntityBase):
    """채권선물 일봉."""

    __tablename__ = "bond_future_daily"
    __table_args__ = _daily_table_args("bond_future_daily", "채권선물 가격의 일봉을 상관 분석용으로 누적하는 테이블")


class CommodityDaily(MacroDailyColumns, EntityBase):
    """원자재 일봉."""

    __tablename__ = "commodity_daily"
    __table_args__ = _daily_table_args("commodity_daily", "원자재 선물의 일봉을 상관 분석용으로 누적하는 테이블")


class CryptoDaily(MacroDailyColumns, EntityBase):
    """암호화폐 일봉."""

    __tablename__ = "crypto_daily"
    __table_args__ = _daily_table_args("crypto_daily", "암호화폐의 일봉을 상관 분석용으로 누적하는 테이블")


class StockBar(EntityBase):
    """개별 종목 1분봉. 매크로 봉과 달리 거래소(KRX/NXT)가 자연키의 한 축이다.

    같은 종목이 두 거래소에서 따로 체결되므로 거래소 없이 시각만 키로 쓰면 서로를
    덮어쓴다. 국내는 `kis_stock_minute_bars_daily`가 KRX 한 번, NXT 한 번 받는다.

    `instrument`로 외래키를 걸지 않는다. 걸면 마스터 행이 없는 종목을 수집기가 저장하지
    못해, 수집기 Enum에만 추가하고 마스터를 빠뜨린 순간 DAG가 죽는다. 테스트가 대조한다.
    """

    __tablename__ = "stock_bar"
    __table_args__ = (
        UniqueConstraint("provider", "stock_code", "exchange", "bar_at", name="uq_stock_bar_natural_key"),
        Index("ix_stock_bar_source_record_id", "source_record_id"),
        CheckConstraint("exchange IN ('KRX', 'NXT', 'NYSE', 'NASDAQ')", name="ck_stock_bar_exchange"),
        CheckConstraint("ingest_method IN ('websocket', 'rest')", name="ck_stock_bar_ingest_method"),
        table_options(
            comment="개별 종목의 1분봉을 거래소 단위로 누적하는 테이블",
            database="default",
        ),
    )

    provider: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="데이터 제공처 식별자(kis 또는 yahoo). 같은 수집의 source_record.source와 같은 값이다",
    )
    stock_code: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment=(
            "국내는 한국거래소 6자리 종목코드(005930), 해외 상장 종목은 저장 심볼(TSMC_ADR)이다. "
            "국내 코드는 instrument.ticker, 수급·공시 테이블과 같은 체계라 한 화면에서 조인된다"
        ),
    )
    exchange: Mapped[StockExchange] = mapped_column(
        SqlEnum(
            StockExchange,
            native_enum=False,
            length=20,
            values_callable=lambda enum: [member.value for member in enum],
        ),
        nullable=False,
        comment="체결이 일어난 거래소(KRX, NXT, NYSE, NASDAQ). 통합(UN) 시세는 받지 않는다",
    )
    bar_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        comment="1분봉이 시작하는 시각(UTC). 봉은 이 시각부터 1분간의 거래를 담는다",
    )
    open: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False, comment="봉 구간의 시가. 국내는 원 단위")
    high: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False, comment="봉 구간의 고가. 국내는 원 단위")
    low: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False, comment="봉 구간의 저가. 국내는 원 단위")
    close: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False, comment="봉 구간의 종가. 국내는 원 단위")
    volume: Mapped[int | None] = mapped_column(
        BigInteger,
        nullable=True,
        comment="봉 구간의 거래량(주). 제공처가 주는 값을 그대로 저장한다",
    )
    previous_close: Mapped[Decimal] = mapped_column(
        Numeric(18, 4),
        nullable=False,
        comment=(
            "직전 거래일 확정 종가. 변동률의 분모다. NXT 봉도 KRX 확정 종가를 쓴다 — "
            "전일 기준가가 거래소마다 따로 있지 않기 때문이다"
        ),
    )
    ingest_method: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="이 행을 마지막으로 쓴 수집 경로(websocket 또는 rest). REST 확정이 WebSocket 잠정을 이긴다",
    )
    is_final: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        comment="REST가 완료 봉을 확정했는지. WebSocket 잠정 봉은 false이고 REST upsert만 true로 바꾼다",
    )
    source_record_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("source_record.id", ondelete="RESTRICT"),
        nullable=False,
        comment="근거가 되는 source_record 레코드 ID",
    )


class StockDaily(EntityBase):
    """개별 종목 일봉. 해외 상장 종목(TSMC ADR)용이다.

    **국내 종목 일봉은 여기 넣지 않는다.** `stock_investor_trade_daily`가 이미 시가·고가·
    저가·종가를 수급과 함께 갖고 있어 다시 받을 이유가 없다.
    """

    __tablename__ = "stock_daily"
    __table_args__ = (
        UniqueConstraint("provider", "stock_code", "exchange", "business_date", name="uq_stock_daily_natural_key"),
        Index("ix_stock_daily_business_date", "business_date"),
        Index("ix_stock_daily_source_record_id", "source_record_id"),
        CheckConstraint("exchange IN ('KRX', 'NXT', 'NYSE', 'NASDAQ')", name="ck_stock_daily_exchange"),
        table_options(
            comment="개별 종목의 일봉을 거래소 단위로 누적하는 테이블",
            database="default",
        ),
    )

    provider: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="데이터 제공처 식별자(yahoo). 같은 수집의 source_record.source와 같은 값이다",
    )
    stock_code: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="국내는 한국거래소 6자리 종목코드, 해외 상장 종목은 저장 심볼(TSMC_ADR)이다",
    )
    exchange: Mapped[StockExchange] = mapped_column(
        SqlEnum(
            StockExchange,
            native_enum=False,
            length=20,
            values_callable=lambda enum: [member.value for member in enum],
        ),
        nullable=False,
        comment="체결이 일어난 거래소(KRX, NXT, NYSE, NASDAQ). 통합(UN) 시세는 받지 않는다",
    )
    business_date: Mapped[date] = mapped_column(
        Date,
        nullable=False,
        comment="봉이 담는 거래일. 그 시장의 현지 날짜라 UTC 날짜와 어긋날 수 있다",
    )
    open: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False, comment="그 거래일의 시가")
    high: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False, comment="그 거래일의 고가")
    low: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False, comment="그 거래일의 저가")
    close: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False, comment="그 거래일의 종가")
    volume: Mapped[int | None] = mapped_column(
        BigInteger,
        nullable=True,
        comment="그 거래일의 거래량(주). 제공처가 주는 값을 그대로 저장한다",
    )
    source_record_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("source_record.id", ondelete="RESTRICT"),
        nullable=False,
        comment="근거가 되는 source_record 레코드 ID",
    )
