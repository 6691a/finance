import pytest

from modules.collectors.kis import DomesticFuture, DomesticIndex
from modules.collectors.yahoo import QuoteSymbol
from tests.helpers import NO_REVISION_REASON, head_sql, revision_files

pytestmark = pytest.mark.skipif(not revision_files(), reason=NO_REVISION_REASON)

# 현물과 선물이 각각 최소 하나는 있어야 한다. 한쪽이 비면 그 대시보드가 통째로 빈다.
EXPECTED_FUTURES = {
    "SP500_FUT", "NASDAQ100_FUT", "DOW_FUT", "RUSSELL2000_FUT",
    "KOSPI200_FUT", "KOSDAQ150_FUT",
}
EXPECTED_INDEXES = {
    "VIX", "SOX", "KOSPI", "KOSPI200", "KOSDAQ",
    "NIKKEI225", "TAIEX", "HSI", "SSE_COMP", "RUSSELL2000",
}
EXPECTED_FX = {"USDKRW", "USDJPY", "DXY", "USDCNH", "JPYKRW"}
EXPECTED_RATES = {"US10Y"}
# 수익률이 아니라 가격이다. 같은 "미 10년물"이라도 US10Y 와 반대로 움직인다.
EXPECTED_BOND_FUTURES = {"US10Y_FUT"}
EXPECTED_COMMODITIES = {"GOLD", "SILVER", "COPPER", "WTI"}
EXPECTED_EQUITIES = {"TSMC_ADR"}
# 주말 48시간에 움직이는 유일한 값이다. 나머지가 전부 멈춰 있어 축을 공유할 수 없다.
EXPECTED_CRYPTO = {"BTC", "ETH"}

ALL_EXPECTED = (
    EXPECTED_FUTURES
    | EXPECTED_INDEXES
    | EXPECTED_FX
    | EXPECTED_RATES
    | EXPECTED_BOND_FUTURES
    | EXPECTED_COMMODITIES
    | EXPECTED_EQUITIES
    | EXPECTED_CRYPTO
)


def collected_symbols() -> set[str]:
    """수집기 전부가 쌓는 심볼. 제공처가 달라도 `quote_bar.symbol` 공간은 하나다."""
    return (
        {symbol.value for symbol in QuoteSymbol}
        | {future.value for future in DomesticFuture}
        | {index.value for index in DomesticIndex}
    )


def test_quote_symbol_master_splits_spot_from_futures(capsys):
    sql = head_sql(capsys)

    assert "CREATE TABLE quote_symbol" in sql
    assert "kind VARCHAR(20) NOT NULL" in sql
    assert "CONSTRAINT uq_quote_symbol_natural_key UNIQUE (provider, symbol)" in sql
    # kind 를 늘릴 때마다 CHECK 를 다시 만든다. PostgreSQL native enum 을 안 쓰는 대신
    # 치르는 비용이고, 대신 값 추가가 트랜잭션 안에서 끝난다.
    assert (
        "kind IN ('index', 'index_future', 'fx', 'rate', 'bond_future', 'commodity', 'equity', 'crypto')"
        in sql
    )


@pytest.mark.parametrize("symbol", sorted(ALL_EXPECTED))
def test_every_collected_symbol_has_a_master_row(symbol, capsys):
    # 봉에서 마스터로 외래키를 걸지 않는다. 수집기 Enum에만 추가하고 마스터를 빠뜨리면
    # 지수·선물 대시보드에서 그 심볼만 조용히 사라지므로 여기서 대조한다.
    sql = head_sql(capsys)

    assert f"'{symbol}'" in sql


@pytest.mark.parametrize("symbol", sorted(EXPECTED_FUTURES))
def test_futures_are_seeded_as_index_future(symbol, capsys):
    sql = head_sql(capsys)

    assert f"'{symbol}', 'index_future'" in sql


@pytest.mark.parametrize("symbol", sorted(EXPECTED_INDEXES))
def test_spot_indexes_are_seeded_as_index(symbol, capsys):
    sql = head_sql(capsys)

    assert f"'{symbol}', 'index'" in sql


@pytest.mark.parametrize("symbol", sorted(EXPECTED_FX))
def test_currencies_are_seeded_as_fx(symbol, capsys):
    sql = head_sql(capsys)

    assert f"'{symbol}', 'fx'" in sql


@pytest.mark.parametrize("symbol", sorted(EXPECTED_RATES))
def test_yields_are_seeded_as_rate(symbol, capsys):
    # 금리는 변화율(%)이 아니라 bp 로 읽는 값이라 지수와 화면을 갈라야 한다.
    sql = head_sql(capsys)

    assert f"'{symbol}', 'rate'" in sql


@pytest.mark.parametrize(
    ("symbol", "kind"),
    sorted(
        [(s, "bond_future") for s in EXPECTED_BOND_FUTURES]
        + [(s, "commodity") for s in EXPECTED_COMMODITIES]
        + [(s, "equity") for s in EXPECTED_EQUITIES]
        + [(s, "crypto") for s in EXPECTED_CRYPTO]
    ),
)
def test_tier2_symbols_carry_their_own_kind(symbol, kind, capsys):
    sql = head_sql(capsys)

    assert f"'{symbol}', '{kind}'" in sql


def test_yield_and_bond_future_are_different_kinds():
    """`US10Y`는 수익률(4.66%)이고 `US10Y_FUT`는 가격(110달러)이다.

    같은 "미 10년물"이라도 서로 반대로 움직인다. 한 패널에 겹치면 읽는 사람이 반드시
    틀리므로 kind로 갈라 둔다.
    """
    assert EXPECTED_RATES & EXPECTED_BOND_FUTURES == set()


def test_every_kind_covers_every_collected_symbol():
    # 심볼을 늘릴 때 위 집합 중 한 곳에 넣는 것을 잊지 않게 한다.
    assert collected_symbols() == ALL_EXPECTED


def test_domestic_symbols_belong_to_the_domestic_collector():
    """국내에서 받을 수 있는 것은 국내를 우선한다.

    코스피는 처음에 Yahoo(`^KS11`)로 받았지만 분봉 품질이 낮아 KIS로 옮겼다(문서 §8.4).
    실수로 되돌아오면 여기서 걸린다.
    """
    yahoo_symbols = {symbol.value for symbol in QuoteSymbol}
    kis_symbols = {future.value for future in DomesticFuture} | {index.value for index in DomesticIndex}

    assert {"KOSPI", "KOSPI200_FUT"} <= kis_symbols
    assert not ({"KOSPI", "KOSPI200_FUT"} & yahoo_symbols)


def test_kospi_was_moved_from_yahoo_to_kis(capsys):
    # 제공처를 옮기는 마이그레이션이 있어야 마스터 행이 kis로 간다. 없으면 대시보드가
    # 코스피를 yahoo 밑에서 찾다가 빈 화면을 보여 준다.
    sql = head_sql(capsys)

    assert "UPDATE quote_symbol SET provider = 'kis'" in sql
    assert "DELETE FROM quote_bar WHERE provider = 'yahoo' AND symbol = 'KOSPI'" in sql
