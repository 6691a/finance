"""시세 봉 라우트.

주제 넷: ① kind가 곧 물리 테이블이고 **종목은 뷰를 타지 않는다** ② 거래소를 안 고르면
거절한다 ③ 상한 초과가 400이다 ④ 빈 값이 0이 되지 않는다.
"""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import httpx
import pytest
from dependency_injector import providers

from apps.api.app import create_app
from apps.api.repository import MAX_POINTS, SymbolRows
from apps.api.repository.quote import QuoteReadRepository
from apps.api.service.quote import build_bars
from apps.models.reference import QuoteSymbol, QuoteSymbolKind
from tests.api.conftest import container

BAR_AT = datetime(2026, 8, 27, 0, 30, tzinfo=UTC)


def symbol_row(
    kind: QuoteSymbolKind = QuoteSymbolKind.INDEX,
    symbol: str = "KOSPI",
    provider: str = "kis",
) -> QuoteSymbol:
    return QuoteSymbol(
        provider=provider,
        symbol=symbol,
        kind=kind,
        country="KR",
        country_name="대한민국",
        label="코스피",
    )


def bar(offset: int = 0, volume: int | None = 1000) -> tuple[Any, ...]:
    """분봉 한 행. `offset`은 분 단위로 밀어 준다."""
    return (
        BAR_AT + timedelta(minutes=offset),
        Decimal("3200.10"),
        Decimal("3210.00"),
        Decimal("3199.00"),
        Decimal("3205.50"),
        None if volume is None else Decimal(volume),
    )


class FakeRepository:
    def __init__(self, **rows: Any) -> None:
        self.rows = rows
        self.calls: list[dict[str, Any]] = []

    async def symbol_rows(self, **kwargs: Any) -> SymbolRows:
        self.calls.append(kwargs)
        return SymbolRows(
            symbols=tuple(self.rows.get("symbols", [])),
            bars=self.rows.get("bars", {}),
            daily=self.rows.get("daily", {}),
            exchanges=self.rows.get("exchanges", {}),
        )

    async def symbol(self, kind: QuoteSymbolKind, symbol: str) -> QuoteSymbol | None:
        return next(
            (
                row
                for row in self.rows.get("symbols", [])
                if row.kind is kind and row.symbol == symbol
            ),
            None,
        )

    async def bar_rows(self, **kwargs: Any) -> tuple[tuple[Any, ...], ...]:
        self.calls.append(kwargs)
        return tuple(self.rows.get("bar_rows", []))

    async def daily_rows(self, **kwargs: Any) -> tuple[tuple[Any, ...], ...]:
        self.calls.append(kwargs)
        return tuple(self.rows.get("daily_rows", []))


def app_with(fake: FakeRepository):
    built = container()
    built.quote_repository.override(providers.Object(fake))
    return create_app(built)


def client(**rows: Any) -> httpx.AsyncClient:
    app = app_with(FakeRepository(**rows))
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


@pytest.mark.asyncio
async def test_the_symbol_list_carries_what_actually_landed():
    """마스터에만 있고 0건인 심볼이 실제로 있다(`rate`가 그렇다). 화면이 그것을 알아야 한다."""
    rows = {
        "symbols": [symbol_row()],
        "bars": {("index", "KOSPI"): (3120, BAR_AT, BAR_AT)},
        "daily": {("index", "KOSPI"): (2500, date(2016, 8, 15), date(2026, 8, 26))},
    }
    async with client(**rows) as http:
        item = (await http.get("/api/quotes/symbols")).json()["items"][0]

    assert item["bar_rows"] == 3120
    assert item["daily_from"] == "2016-08-15"
    assert item["bar_to"].endswith("Z")


@pytest.mark.asyncio
async def test_a_symbol_with_nothing_collected_reads_as_zero_not_missing():
    async with client(symbols=[symbol_row(QuoteSymbolKind.RATE, "US10Y")]) as http:
        item = (await http.get("/api/quotes/symbols")).json()["items"][0]

    assert item["bar_rows"] == 0
    assert item["bar_from"] is None


@pytest.mark.asyncio
async def test_the_equity_symbol_lists_its_exchanges():
    """같은 종목이 KRX와 NXT에서 따로 체결된다. 화면이 하나를 골라야 한다."""
    rows = {
        "symbols": [symbol_row(QuoteSymbolKind.EQUITY, "005930")],
        "exchanges": {("equity", "005930"): ("KRX", "NXT")},
    }
    async with client(**rows) as http:
        item = (await http.get("/api/quotes/symbols")).json()["items"][0]

    assert item["exchanges"] == ["KRX", "NXT"]


@pytest.mark.asyncio
async def test_the_macro_symbol_has_no_exchange():
    async with client(symbols=[symbol_row()]) as http:
        item = (await http.get("/api/quotes/symbols")).json()["items"][0]

    assert item["exchanges"] == []


@pytest.mark.asyncio
async def test_the_static_symbols_route_wins_over_the_bar_route():
    """`/symbols`가 kind로 파싱되면 422가 된다. 실제 요청을 보내야 확인된다."""
    async with client(symbols=[]) as http:
        assert (await http.get("/api/quotes/symbols")).status_code == 200


@pytest.mark.asyncio
async def test_bars_come_back_as_columns_not_rows():
    """5,000점이면 키 이름이 3만 번 반복된다. 차트가 먹는 모양도 이쪽이다."""
    rows = {"symbols": [symbol_row()], "bar_rows": [bar(0), bar(1)]}
    async with client(**rows) as http:
        payload = (await http.get("/api/quotes/bars", params={"kind": "index", "symbol": "KOSPI"})).json()

    assert payload["points"] == 2
    assert len(payload["times"]) == len(payload["close"]) == 2
    assert isinstance(payload["close"][0], float)
    assert payload["times"][0].endswith("Z")


@pytest.mark.asyncio
async def test_a_symbol_without_volume_keeps_null_and_never_becomes_zero():
    """0으로 채우면 차트가 바닥으로 떨어지는 거짓 급락을 그린다."""
    rows = {"symbols": [symbol_row()], "bar_rows": [bar(0, volume=None)]}
    async with client(**rows) as http:
        payload = (await http.get("/api/quotes/bars", params={"kind": "index", "symbol": "KOSPI"})).json()

    assert payload["volume"] == [None]


@pytest.mark.asyncio
async def test_an_equity_without_an_exchange_is_refused():
    """말없이 한쪽을 고르면 화면이 어느 거래소 값인지 밝히지 못한 채 선을 그린다."""
    rows = {"symbols": [symbol_row(QuoteSymbolKind.EQUITY, "005930")], "bar_rows": [bar()]}
    async with client(**rows) as http:
        reply = await http.get("/api/quotes/bars", params={"kind": "equity", "symbol": "005930"})

    assert reply.status_code == 422
    assert "거래소" in reply.json()["detail"]


@pytest.mark.asyncio
async def test_an_equity_with_an_exchange_passes_it_down():
    rows = {"symbols": [symbol_row(QuoteSymbolKind.EQUITY, "005930")], "bar_rows": [bar()]}
    fake = FakeRepository(**rows)
    app = app_with(fake)

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as http:
        payload = (
            await http.get(
                "/api/quotes/bars",
                params={"kind": "equity", "symbol": "005930", "exchange": "NXT"},
            )
        ).json()

    assert fake.calls[0]["exchange"] == "NXT"
    assert payload["exchange"] == "NXT"


@pytest.mark.asyncio
async def test_an_unknown_symbol_is_a_404_and_an_unknown_kind_too():
    async with client(symbols=[]) as http:
        assert (await http.get("/api/quotes/bars", params={"kind": "index", "symbol": "NOPE"})).status_code == 404
        assert (await http.get("/api/quotes/bars", params={"kind": "nope", "symbol": "KOSPI"})).status_code == 404


@pytest.mark.asyncio
async def test_an_unknown_interval_is_refused_before_the_query():
    async with client(symbols=[symbol_row()]) as http:
        reply = await http.get(
            "/api/quotes/bars", params={"kind": "index", "symbol": "KOSPI", "interval": "7m"}
        )

    assert reply.status_code == 422


@pytest.mark.asyncio
async def test_too_many_points_is_a_400_that_says_how_to_fix_it():
    """조용히 솎지 않는다. 상한만 알려 주면 부르는 쪽이 다음 수를 못 정한다."""
    rows = {"symbols": [symbol_row()], "bar_rows": [bar(index) for index in range(MAX_POINTS + 1)]}
    async with client(**rows) as http:
        reply = await http.get(
            "/api/quotes/bars", params={"kind": "index", "symbol": "KOSPI", "interval": "1m"}
        )

    assert reply.status_code == 400
    detail = reply.json()["detail"]
    assert str(MAX_POINTS) in detail
    assert "5m" in detail


@pytest.mark.asyncio
async def test_the_daily_route_uses_business_dates_not_timestamps():
    rows = {
        "symbols": [symbol_row()],
        "daily_rows": [
            (date(2026, 8, 26), Decimal(1), Decimal(2), Decimal("0.5"), Decimal("1.5"), 100)
        ],
    }
    async with client(**rows) as http:
        payload = (await http.get("/api/quotes/daily", params={"kind": "index", "symbol": "KOSPI"})).json()

    assert payload["dates"] == ["2026-08-26"]
    assert "times" not in payload


def test_the_bar_statement_reads_one_more_row_than_the_cap():
    compiled = str(
        QuoteReadRepository.bar_statement(
            kind=QuoteSymbolKind.INDEX,
            symbol="KOSPI",
            interval="5m",
            start=datetime(2026, 8, 27, tzinfo=UTC),
            end=datetime(2026, 8, 28, tzinfo=UTC),
        ).compile(compile_kwargs={"literal_binds": True})
    )

    assert f"LIMIT {MAX_POINTS + 1}" in compiled
    assert "date_bin" in compiled
    assert "interval '5 minutes'" in compiled


def test_the_equity_bar_statement_never_touches_the_union_view():
    """뷰의 종목 갈래는 KRX·NYSE·NASDAQ만 태운다 — NXT 337,079행이 조용히 빠진다."""
    compiled = str(
        QuoteReadRepository.bar_statement(
            kind=QuoteSymbolKind.EQUITY,
            symbol="005930",
            interval="1m",
            start=datetime(2026, 8, 27, tzinfo=UTC),
            end=datetime(2026, 8, 28, tzinfo=UTC),
            exchange="NXT",
        ).compile(compile_kwargs={"literal_binds": True})
    )

    assert "FROM stock_bar" in compiled
    assert "quote_bar" not in compiled
    assert "stock_bar.exchange" in compiled


def test_every_symbol_kind_has_a_table_on_both_axes():
    """마스터에 kind가 늘면 이 테스트가 먼저 깨져야 한다."""
    from apps.api.repository.quote import BAR_TABLES, DAILY_TABLES

    # `equity`만 매핑 밖이다. 축이 달라 조회문이 갈라져 있다.
    covered = set(BAR_TABLES) | {QuoteSymbolKind.EQUITY}
    assert covered == set(QuoteSymbolKind)
    assert set(DAILY_TABLES) | {QuoteSymbolKind.EQUITY} == set(QuoteSymbolKind)


def test_only_the_equity_bar_reads_whether_it_is_settled():
    """`is_final`은 종목 분봉에만 있는 칸이다. 다른 kind 테이블에는 그 컬럼이 없다.

    **`bool_and`다.** 재집계 버킷 안에 잠정이 하나라도 있으면 고가·저가가 아직 바뀔 수
    있으므로 그 버킷은 잠정이다 — `bool_or`면 잠정을 확정으로 읽는다.
    """
    equity = str(
        QuoteReadRepository.bar_statement(
            kind=QuoteSymbolKind.EQUITY,
            symbol="005930",
            interval="5m",
            start=datetime(2026, 8, 27, tzinfo=UTC),
            end=datetime(2026, 8, 28, tzinfo=UTC),
            exchange="KRX",
        ).compile()
    )
    index = str(
        QuoteReadRepository.bar_statement(
            kind=QuoteSymbolKind.INDEX,
            symbol="KOSPI",
            interval="5m",
            start=datetime(2026, 8, 27, tzinfo=UTC),
            end=datetime(2026, 8, 28, tzinfo=UTC),
        ).compile()
    )

    assert "bool_and(stock_bar.is_final)" in equity
    assert "is_final" not in index


def test_the_settled_flag_lands_on_the_bar_not_the_daily():
    """**자리를 한 번 틀렸다**(2026-08-31) — 확정 칸이 일봉 쪽으로 가서 분봉 응답에 빈 배열이
    나갔다. Pydantic이 모르는 칸을 조용히 버려서 테스트도 응답도 아무 말을 안 했다."""
    rows = ((datetime(2026, 8, 27, tzinfo=UTC), 1, 2, 0, 1, 100, False),)
    series = build_bars(
        rows, kind="equity", symbol="005930", exchange="KRX", provider="kis", interval="5m"
    )

    assert series.settled == (False,)


def test_only_the_index_future_daily_reads_the_contract_code():
    """월물은 지수선물에만 있는 칸이다. 다른 kind에 넣으면 조회 자체가 죽는다.

    빈 배열이 "월물 개념이 없다"이므로 응답 모양은 kind마다 다르다.
    """
    future = str(
        QuoteReadRepository.daily_statement(
            kind=QuoteSymbolKind.INDEX_FUTURE,
            symbol="KOSPI200_FUT",
            start=date(2026, 1, 1),
            end=date(2026, 8, 27),
        ).compile()
    )
    index = str(
        QuoteReadRepository.daily_statement(
            kind=QuoteSymbolKind.INDEX,
            symbol="KOSPI",
            start=date(2026, 1, 1),
            end=date(2026, 8, 27),
        ).compile()
    )

    assert "contract_code" in future
    assert "contract_code" not in index


def test_the_domestic_daily_comes_from_the_investor_trade_table():
    """`stock_daily`는 해외 상장 종목용이고 국내 일봉은 수급 테이블이 갖는다."""
    krx = str(
        QuoteReadRepository.daily_statement(
            kind=QuoteSymbolKind.EQUITY,
            symbol="005930",
            start=date(2026, 1, 1),
            end=date(2026, 8, 27),
            exchange="KRX",
        ).compile()
    )
    adr = str(
        QuoteReadRepository.daily_statement(
            kind=QuoteSymbolKind.EQUITY,
            symbol="TSMC_ADR",
            start=date(2026, 1, 1),
            end=date(2026, 8, 27),
            exchange="NYSE",
        ).compile()
    )

    assert "stock_investor_trade_daily" in krx
    assert "stock_daily" in adr
    assert "stock_investor_trade_daily" not in adr
