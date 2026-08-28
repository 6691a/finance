"""시세 봉의 응답 계약.

**컬럼 지향이다.** 행 지향(`[{time, open, ...}, ...]`)으로 내면 5,000점에 키 이름이 3만 번
반복돼 payload가 두 배가 되고, 화면의 차트가 먹는 모양이 바로 컬럼 지향이라 프런트가
전치할 일도 없다. 계약은 여전히 Pydantic 모델이고 `list[float]` 여섯 칸도 모델이다.

**빈 구간을 0으로 채우지 않는다.** 거래가 없던 분은 행 자체가 없다. 0으로 채우면 차트가
바닥으로 떨어지는 거짓 급락을 그린다.
"""

from datetime import date

from pydantic import Field

from apps.api.schemas.common import ApiModel, Page, UtcDatetime


class QuoteSymbolItem(ApiModel):
    """수집 중인 심볼 하나. 마스터에 **실제 쌓인 것**을 붙여 준다."""

    kind: str = Field(
        description=(
            "값의 종류(index·index_future·fx·rate·bond_future·commodity·equity·crypto). "
            "**화면을 가르는 기준이다** — 거래 시간대와 정상 변동폭의 자릿수가 달라 "
            "한 축에 겹치면 읽을 수 없다."
        )
    )
    symbol: str = Field(description="제공처 안에서 고유한 식별자. 종목은 6자리 코드나 저장 심볼이다.")
    provider: str = Field(description="이 값을 준 제공처(kis·yahoo). 심볼은 제공처 안에서만 고유하다.")
    label: str = Field(description="차트와 표에 쓰는 표시 이름.")
    country: str = Field(description="기초 시장의 국가(ISO 3166-1 alpha-2).")
    country_name: str = Field(description="국가 표시 이름.")
    exchanges: tuple[str, ...] = Field(
        default=(),
        description=(
            "이 심볼의 봉이 쌓인 거래소. **`equity`에만 있다** — 같은 종목이 KRX와 NXT에서 "
            "따로 체결되므로 화면이 반드시 하나를 골라야 한다. 매크로 kind는 빈 배열이다."
        ),
    )
    bar_rows: int = Field(default=0, description="쌓인 분봉 행 수. 0이면 수집이 안 돌고 있다는 뜻이다.")
    bar_from: UtcDatetime | None = Field(default=None, description="가장 오래된 분봉의 시각(UTC).")
    bar_to: UtcDatetime | None = Field(default=None, description="가장 최근 분봉의 시각(UTC). 최신성이 여기 보인다.")
    daily_rows: int = Field(default=0, description="쌓인 일봉 행 수.")
    daily_from: date | None = Field(default=None, description="가장 오래된 거래일.")
    daily_to: date | None = Field(default=None, description="가장 최근 거래일.")


QuoteSymbolList = Page[QuoteSymbolItem]


class BarSeries(ApiModel):
    """분봉 한 묶음. **여섯 배열의 길이가 모두 같다.**"""

    kind: str = Field(description="요청한 kind.")
    symbol: str = Field(description="요청한 심볼.")
    exchange: str | None = Field(
        default=None,
        description="`equity`일 때의 거래소(KRX·NXT·NYSE·NASDAQ). 매크로 kind는 null이다.",
    )
    provider: str = Field(description="이 봉을 준 제공처.")
    interval: str = Field(
        description=(
            "봉 간격(1m·5m·15m·1h). **재집계는 서버가 `date_bin`으로 한다** — "
            "open은 구간의 첫 값, close는 마지막 값, high·low는 극값, volume은 합이다. "
            "일봉은 UTC 하루와 거래일이 어긋나므로 여기 없고 `/api/quotes/daily`가 준다."
        )
    )
    points: int = Field(description="배열 하나의 길이.")
    times: tuple[UtcDatetime, ...] = Field(
        default=(), description="각 봉이 시작하는 시각(UTC). 오름차순이고 **빈 구간은 건너뛴다.**"
    )
    open: tuple[float, ...] = Field(default=(), description="구간의 첫 체결가.")
    high: tuple[float, ...] = Field(default=(), description="구간의 최고가.")
    low: tuple[float, ...] = Field(default=(), description="구간의 최저가.")
    close: tuple[float, ...] = Field(default=(), description="구간의 마지막 체결가.")
    volume: tuple[float | None, ...] = Field(
        default=(),
        description=(
            "구간의 거래량 합. **거래량 개념이 없는 심볼(현물 지수·환율)은 null이거나 0이다** — "
            "제공처가 0을 실어 보낸다."
        ),
    )


class DailySeries(ApiModel):
    """일봉 한 묶음. 축이 시각이 아니라 **거래일**이다.

    거래일은 그 시장의 현지 날짜라 UTC 날짜와 어긋날 수 있다. 그래서 분봉과 한 응답에
    섞지 않는다.
    """

    kind: str = Field(description="요청한 kind.")
    symbol: str = Field(description="요청한 심볼.")
    exchange: str | None = Field(default=None, description="`equity`일 때의 거래소.")
    provider: str = Field(description="이 봉을 준 제공처.")
    points: int = Field(description="배열 하나의 길이.")
    dates: tuple[date, ...] = Field(default=(), description="거래일. 오름차순이고 휴장일은 건너뛴다.")
    open: tuple[float, ...] = Field(default=(), description="그 거래일의 시가.")
    high: tuple[float, ...] = Field(default=(), description="그 거래일의 고가.")
    low: tuple[float, ...] = Field(default=(), description="그 거래일의 저가.")
    close: tuple[float, ...] = Field(default=(), description="그 거래일의 종가.")
    volume: tuple[float | None, ...] = Field(default=(), description="그 거래일의 거래량.")
