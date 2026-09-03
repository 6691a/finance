"""기술적 신호."""

from datetime import date
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import (
    CheckConstraint,
    Numeric,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from apps.core.database import EntityBase, table_options
from apps.models.analysis._columns import _enum_column


class SignalDirection(StrEnum):
    """신호가 어느 쪽으로 일어났나.

    **판정이 아니라 사건의 방향이다.** 골든크로스·MACD 상향·RSI 과매도 탈출이 `UP`이다.

    전에는 옛 추론의 `ThesisDirection`을 빌려 썼고 그쪽에는 `FLAT`이 있었다. 여기서는 값이
    둘뿐이라 쓴 적이 없고 `ck_technical_signal_direction`도 둘만 받았다. 추론을 지우면서
    이 표가 자기 enum을 갖는다.
    """

    UP = "up"
    DOWN = "down"


class TechnicalSignalKind(StrEnum):
    """매매 신호(사건)의 종류. `modules/technical/indicators.py`의 `SignalKind`와 값이 같아야 한다.

    셋을 고른 기준은 서로 다른 렌즈다 — 추세추종(`sma_cross`), 모멘텀(`macd_cross`),
    역추세(`rsi_reversal`). 같은 렌즈를 여럿 두면 적중률 비교가 서로를 설명하지 못한다.
    """

    SMA_CROSS = "sma_cross"
    MACD_CROSS = "macd_cross"
    RSI_REVERSAL = "rsi_reversal"


class TechnicalSignal(EntityBase):
    """확정 일봉에서 검출한 매매 신호 한 건. **사건이지 판정이 아니다.**

    지표값(SMA·RSI·MACD)은 원천 OHLCV에서 언제든 다시 계산되므로 저장하지 않는다. 신호는
    다르다 — "언제 교차했는지"는 시점이 지나면 값에서 되살릴 수 없고, 그 사건 뒤 실제로
    어떻게 움직였는지를 채점하려면 사건이 행으로 남아야 한다.

    **덮어쓴다.** LLM이 낸 값은 재호출마다 답이 달라 첫 성공본을 지키지만, 이것은 결정적
    계산이라 원천 봉이 수정되면 값이 따라가는 편이 맞다. 덮어써도 "최초 판단"이 사라지는
    게 아니다.

    `source_record`를 남기지 않는다. 외부 응답이 아니라 파생 사건이고, 원천 계보는
    `index_daily`·`stock_investor_trade_daily`의 `source_record_id`가 이미 갖는다.
    """

    __tablename__ = "technical_signal"
    __table_args__ = (
        UniqueConstraint(
            "provider",
            "symbol",
            "signal_date",
            "kind",
            name="uq_technical_signal_natural_key",
        ),
        CheckConstraint(
            "kind IN ('sma_cross', 'macd_cross', 'rsi_reversal')",
            name="ck_technical_signal_kind",
        ),
        # 신호에 횡보는 없다. 교차는 위로 아니면 아래로 일어난다.
        CheckConstraint("direction IN ('up', 'down')", name="ck_technical_signal_direction"),
        table_options(
            comment="확정 일봉에서 검출한 기술적 매매 신호를 사후 채점용으로 누적하는 테이블",
            database="default",
        ),
    )

    provider: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="원천 일봉을 준 제공처. 현재는 kis뿐이다",
    )
    symbol: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="대상 식별자(지수는 KOSPI·KOSDAQ, 종목은 6자리 코드). 마스터로 외래키를 걸지 않는다",
    )
    signal_date: Mapped[date] = mapped_column(
        nullable=False,
        comment="사건이 일어난 KRX 거래일. 그날 확정 종가로 계산한 지표가 직전 거래일과 교차했다",
    )
    kind: Mapped[TechnicalSignalKind] = mapped_column(
        _enum_column(TechnicalSignalKind),
        nullable=False,
        comment=(
            "신호 종류(sma_cross는 SMA20/SMA60 교차, macd_cross는 MACD와 시그널 라인 교차, "
            "rsi_reversal은 RSI14의 30·70 재돌파)"
        ),
    )
    direction: Mapped[SignalDirection] = mapped_column(
        _enum_column(SignalDirection),
        nullable=False,
        comment=(
            "사건의 방향(up 또는 down). 골든크로스·MACD 상향·과매도 탈출이 up이다. "
            "매수·매도 판정이 아니라 사건이 어느 쪽으로 일어났는지다"
        ),
    )
    close: Mapped[Decimal] = mapped_column(
        Numeric(18, 4), nullable=False, comment="사건일 종가. 사후 수익률 계산의 기준가다"
    )
    sma20: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False, comment="사건일의 20거래일 단순이동평균")
    sma60: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False, comment="사건일의 60거래일 단순이동평균")
    rsi14: Mapped[Decimal] = mapped_column(
        Numeric(6, 2), nullable=False, comment="사건일의 14일 RSI(Wilder 평활). 0~100"
    )
    macd: Mapped[Decimal] = mapped_column(
        Numeric(18, 4), nullable=False, comment="사건일의 MACD 라인(EMA12 - EMA26)"
    )
    macd_signal: Mapped[Decimal] = mapped_column(
        Numeric(18, 4), nullable=False, comment="사건일의 시그널 라인(MACD의 EMA9)"
    )
    volume_ratio20: Mapped[Decimal | None] = mapped_column(
        Numeric(10, 4),
        nullable=True,
        comment=(
            "사건일 거래량 / 직전 20거래일 평균 거래량. 거래량이 없거나 직전 평균이 0이면 NULL이다 — "
            "1로 채우지 않는다"
        ),
    )
    rule_version: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="검출 규칙 버전(modules/technical/indicators.py의 RULE_VERSION)",
    )
