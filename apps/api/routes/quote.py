"""시세 봉 라우트.

**`from`·`to`의 축이 라우트마다 다르다.** 분봉은 UTC 시각이고 일봉은 그 시장의 거래일이다.
분봉에 KST 날짜 경계를 쓰면 미국 지수가 밤에 움직이는 것을 하루 어긋나게 자른다.

**`/symbols`는 `/{kind}/{symbol}`보다 먼저 등록한다.** 정적 경로가 뒤에 오면 동적 kind가
먼저 물어 `symbols`를 kind로 파싱하려다 422를 낸다. 이 파일 안의 함수 정의 순서가 그
계약이다.
"""

from datetime import date, datetime, timedelta
from typing import Annotated

from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends, HTTPException, Query

from apps.api.container import ApiContainer
from apps.api.repository import DEFAULT_LIMIT, INTERVALS, MAX_LIMIT
from apps.api.schemas import BarSeries, DailySeries, QuoteSymbolList
from apps.api.service import QuoteReadService
from apps.api.service.quote import ExchangeRequired, TooManyPoints, UnknownSymbol
from apps.core.utility import kst_day_bounds, kst_today
from apps.models.reference import QuoteSymbolKind

router = APIRouter(prefix="/api/quotes", tags=["quote"])

# 분봉 기본 창(일). 하루치가 심볼 하나에 400점 안팎이라 이틀이 기본이다.
DEFAULT_BAR_DAYS = 1

# 일봉 기본 창(일). 상한 5,000점 안에 넉넉히 들어간다.
DEFAULT_DAILY_DAYS = 365

ServiceDep = Annotated[
    QuoteReadService,
    Depends(Provide[ApiContainer.quote_service]),
]

# **행을 주는 라우트는 전부 쪽으로 낸다.** 봉 자체(`/bars`·`/daily`)는 행이 아니라 한
# 심볼의 열 묶음이라 여기 해당하지 않는다 — 그쪽 상한은 `MAX_POINTS`다.
Limit = Annotated[int, Query(ge=1, le=MAX_LIMIT, description="쪽 크기")]
Offset = Annotated[int, Query(ge=0, description="건너뛸 건수")]


def _resolve(kind: str) -> QuoteSymbolKind:
    try:
        return QuoteSymbolKind(kind)
    except ValueError as error:
        raise HTTPException(status_code=404, detail=f"unknown kind {kind!r}") from error


@router.get("/symbols", response_model=QuoteSymbolList)
@inject
async def list_symbols(
    service: ServiceDep, limit: Limit = DEFAULT_LIMIT, offset: Offset = 0
) -> QuoteSymbolList:
    """수집 중인 심볼 전부와 **실제로 쌓인 구간**."""
    return await service.symbols(limit=limit, offset=offset)


@router.get("/bars", response_model=BarSeries)
@inject
async def read_bars(
    service: ServiceDep,
    kind: Annotated[str, Query(description="QuoteSymbolKind 값")],
    symbol: Annotated[str, Query(description="심볼. 종목은 6자리 코드나 저장 심볼")],
    exchange: Annotated[
        str | None,
        Query(description="KRX·NXT·NYSE·NASDAQ. **equity에는 필수다** — 기본값을 두지 않는다"),
    ] = None,
    interval: Annotated[str, Query(description="1m·5m·15m·1h")] = "1m",
    start: Annotated[datetime | None, Query(alias="from", description="시작 시각(UTC, 포함)")] = None,
    end: Annotated[datetime | None, Query(alias="to", description="종료 시각(UTC, 제외)")] = None,
) -> BarSeries:
    """분봉. 기본 창은 KST 오늘 하루다."""
    if interval not in INTERVALS:
        raise HTTPException(status_code=422, detail=f"interval은 {list(INTERVALS)} 중 하나다")
    if start is None or end is None:
        today = kst_today()
        window = kst_day_bounds(today - timedelta(days=DEFAULT_BAR_DAYS - 1), today)
        start, end = start or window[0], end or window[1]
    try:
        return await service.bars(
            kind=_resolve(kind),
            symbol=symbol,
            interval=interval,
            start=start,
            end=end,
            exchange=exchange,
        )
    except UnknownSymbol as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ExchangeRequired as error:
        raise HTTPException(
            status_code=422,
            detail=f"종목 {error}는 거래소를 골라야 한다(KRX·NXT·NYSE·NASDAQ). 통합 시세는 없다.",
        ) from error
    except TooManyPoints as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@router.get("/daily", response_model=DailySeries)
@inject
async def read_daily(
    service: ServiceDep,
    kind: Annotated[str, Query(description="QuoteSymbolKind 값")],
    symbol: Annotated[str, Query(description="심볼")],
    exchange: Annotated[str | None, Query(description="equity에 필수")] = None,
    start: Annotated[date | None, Query(alias="from", description="시작 거래일(포함)")] = None,
    end: Annotated[date | None, Query(alias="to", description="종료 거래일(포함)")] = None,
) -> DailySeries:
    """일봉. 기본 창은 오늘까지 1년이다.

    **국내 종목(KRX) 일봉은 `stock_investor_trade_daily`에서 온다** — 수급과 함께 확정되고
    `stock_daily`는 해외 상장 종목용이다. 부르는 쪽은 그 갈래를 몰라도 된다.
    """
    to_day = end or kst_today()
    from_day = start or to_day - timedelta(days=DEFAULT_DAILY_DAYS)
    try:
        return await service.daily(
            kind=_resolve(kind), symbol=symbol, start=from_day, end=to_day, exchange=exchange
        )
    except UnknownSymbol as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ExchangeRequired as error:
        raise HTTPException(
            status_code=422,
            detail=f"종목 {error}는 거래소를 골라야 한다(KRX·NXT·NYSE·NASDAQ). 통합 시세는 없다.",
        ) from error
    except TooManyPoints as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
