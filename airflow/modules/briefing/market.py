"""시장 브리핑이 보여 주는 것 — Slack 블록과 표시 helper.

읽는 쪽은 `market_data.py`다. 이 파일은 그쪽이 만든 `MarketSummary` 하나를 받아 표와
블록으로 편다. **여기에는 SQL도 연결도 없다.**

리포트는 둘인데 렌더러는 하나다. 한국장과 미국장은 같은 표를 다른 시간대에 다른 조합으로
보여 주는 것이라 섹션 구성이 크게 겹친다. 무엇을 어느 리포트에 넣을지는 `MarketScope`가
정한다 — 조회는 그 값을 보지 않으므로 여기 둔다.

**렌더링은 함수다.** 표·블록 조립은 감쌀 상태가 없다. 모델 하나를 받아 dict를 만드는 순수
변환이라 클래스로 묶으면 전부 `@staticmethod`가 된다.

LLM 요약은 넣지 않는다. 2026-08-19까지 표 위에 모델 요약을 붙였지만 표가 이미 말하는
것 이상을 쓰지 못해 뺐다.

**시각은 UTC로 담고 KST 변환은 여기서만 한다.** Slack은 프론트엔드가 없는 출력이라
백엔드가 변환하는 자리다.
"""

from collections.abc import Callable, Iterable, Sequence
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict

from modules.briefing import blocks
from modules.briefing.market_data import (
    MarketSummary,
    QuoteChange,
    RecentSignal,
    StockTradeSnapshot,
    session_state,
    us_session_date,
)
from modules.utility import KST_TIMEZONE

# 한국장 시간에도 값이 움직이는 해외 시장. 미국 현물은 닫혀 있어 넣지 않는다.
ASIA_COUNTRIES = frozenset({"JP", "TW", "HK", "CN"})
INDEX_FUTURE = "index_future"

# 표에 그리는 순서. **정렬을 SQL에 맡기지 않는다.** 이름순으로 두면 코스닥이 코스피 위에
# 오고 통화가 CNY부터 시작한다. 읽는 사람이 먼저 보고 싶은 것과 가나다·알파벳 순서는 다르다.
# 목록에 없는 값은 뒤로 밀리고 자기들끼리는 원래 순서를 지킨다.
KOREA_SYMBOL_ORDER = ("KOSPI", "KOSPI200", "KOSPI200_FUT", "KOSDAQ", "KOSDAQ150_FUT")
OVERSEAS_COUNTRY_ORDER = ("US", "JP", "CN", "HK", "TW")

# 미국장 지수·선물 표의 줄 순서. 현물 옆에 그 선물을 놓는다. 목록에 없는 심볼은 뒤로 밀린다.
US_SYMBOL_ORDER = (
    "SP500",
    "SP500_FUT",
    "NASDAQ",
    "NASDAQ100_FUT",
    "DOW_FUT",
    "RUSSELL2000",
    "RUSSELL2000_FUT",
    "SOX",
    "VIX",
    "US10Y_FUT",
)

# 미국장 리포트의 시세 표. (제목, 그 표에 넣는 kind). 한 표에 섞어 두면 금·나스닥·비트코인이
# 한 덩어리로 보여서(2026-08-22 전) 표를 종류별로 가른다. 빈 표는 그리지 않는다.
# kind의 합집합은 QUOTED_KINDS와 같아야 한다 — 테스트가 대조한다.
US_SECTIONS = (
    ("미국 지수·선물", frozenset({"index", "index_future", "bond_future"})),
    ("원자재", frozenset({"commodity"})),
    ("크립토", frozenset({"crypto"})),
    ("ADR", frozenset({"equity"})),
)

# 실시간 환율 표의 줄 순서. 목록에 없는 fx 심볼(USDJPY 등)은 뒤로 밀린다.
FX_SYMBOL_ORDER = ("USDKRW", "JPYKRW", "DXY")

# 시세 표에 그리는 종류. 등락을 퍼센트로 그려도 뜻이 통하는 것만 넣는다.
#
# **`rate`를 넣지 않는다.** 금리를 퍼센트 변화로 그리면 4.65 → 4.70이 `+1.08%`가 되어
# 5bp 움직임이 1% 넘게 뛴 것처럼 보인다. 금리는 `indicator_observation` 쪽 표가 bp로 그린다.
# `fx`도 넣지 않는다. 환율은 실시간 환율 표(`_fx_quotes`)가 따로 그린다.
QUOTED_KINDS = frozenset({"index", "index_future", "equity", "commodity", "bond_future", "crypto"})


class MarketScope(StrEnum):
    """어느 리포트인가. 조회는 같고 무엇을 그릴지가 다르다.

    `KOREA_PREOPEN`은 개장 전 발송(08:10·09:00)이다. 08:00 미국장 리포트가 이미 보낸 것
    (미국 지수·선물, 금리·스프레드, 전일 국내 지수·선물, 수급)은 빼고, NXT 프리마켓
    (08:00~08:50)이 만든 종목 시세와 전일 확정치(증시자금·공매도·등락 종목 수)만 그린다.
    """

    KOREA = "korea"
    KOREA_PREOPEN = "korea_preopen"
    US = "us"


def render_blocks(
    summary: MarketSummary,
    scope: MarketScope,
    *,
    chart_files: Sequence[tuple[str, str]] | None = None,
    chart_error: str | None = None,
):
    """Slack 블록. 값은 Slack 기본 `table` 블록에 넣는다(`blocks` 모듈 docstring 참고)."""
    local = summary.generated_at.astimezone(KST_TIMEZONE)
    if scope is MarketScope.KOREA_PREOPEN:
        rendered = [
            blocks.header(f"🌅 한국장 프리마켓 브리핑 · {blocks.timestamp(local)}"),
            *_quote_section("국내 종목(프리마켓)", _domestic_stocks(summary)),
            *_chart_section(chart_files, chart_error),
            *_technical_section(summary),
            *_quote_section("환율(실시간·장외)", _fx_quotes(summary)),
            *_market_funds_section(summary),
            *_short_position_section(summary),
            *_movement_section(summary),
        ]
    elif scope is MarketScope.KOREA:
        rendered = [
            blocks.header(f"📈 한국장 브리핑 · {blocks.timestamp(local)} · {session_state(summary.generated_at)}"),
            *_quote_section("국내 지수·선물", _korea_quotes(summary)),
            *_chart_section(chart_files, chart_error),
            *_technical_section(summary),
            *_quote_section("장중 해외", _intraday_overseas(summary)),
            *_quote_section("환율(실시간·장외)", _fx_quotes(summary)),
            *_flow_section(summary),
            *_stock_flow_section(summary),
            *_stock_trade_sections(summary),
            *_market_funds_section(summary),
            *_short_position_section(summary),
            *_movement_section(summary),
        ]
    else:
        session = us_session_date(summary.generated_at)
        rendered = [
            blocks.header(f"🌙 미국장 마감 · {session:%m/%d}(현지) · {blocks.timestamp(local)}"),
            *[block for title, quotes in _us_quote_sections(summary) for block in _quote_section(title, quotes)],
            *_rate_section(summary),
            *_rate_spread_section(summary),
            *_quote_section("전일 국내", _korea_quotes(summary)),
            *_flow_section(summary),
            *_stock_flow_section(summary),
        ]
    rendered.append(blocks.context(_as_of(summary, scope)))
    return rendered


def render_text(summary: MarketSummary, scope: MarketScope) -> str:
    """블록을 못 그리는 자리에 뜨는 한 줄. 알림 미리보기가 이걸 읽는다."""
    quotes = _scope_quotes(summary, scope)
    parts = [f"{quote.label} {_number(quote.close)} {_percent(quote.change_percent)}" for quote in quotes[:2]]
    if scope is MarketScope.US:
        parts += [f"{rate.label} {_rate(rate.value)}" for rate in summary.rates if rate.country == "US"][:1]
    titles = {
        MarketScope.KOREA: "한국장 브리핑",
        MarketScope.KOREA_PREOPEN: "한국장 프리마켓 브리핑",
        MarketScope.US: "미국장 마감",
    }
    title = titles[scope]
    return f"{title} · " + " · ".join(parts) if parts else f"{title} · 값 없음"


def _scope_quotes(summary: MarketSummary, scope: MarketScope) -> tuple[QuoteChange, ...]:
    """리포트 첫 표에 실리는 시세. 미리보기 한 줄과 footer의 '가장 오래된 값'이 같은 것을 본다."""
    if scope is MarketScope.US:
        return _us_quotes(summary)
    if scope is MarketScope.KOREA_PREOPEN:
        return _domestic_stocks(summary)
    return _korea_quotes(summary)


def _us_listed_adr(quote: QuoteChange) -> bool:
    """미국 상장 ADR인가. Yahoo로 받는 종목(equity)은 전부 미국 상장 ADR이다(수집기 주석 참고).

    `country`는 회사 국적(TSMC=TW, SK하이닉스=KR)이라 거래 세션을 말해 주지 않는다.
    국적으로 거르면 ADR이 국내 표나 아시아 표에 섞여 뉴욕 마감값이 장중 값처럼 보인다.
    """
    return quote.provider == "yahoo" and quote.kind == "equity"


def _korea_quotes(summary: MarketSummary) -> tuple[QuoteChange, ...]:
    return _ordered(
        (
            quote
            for quote in summary.quotes
            if quote.country == "KR" and quote.kind in QUOTED_KINDS and not _us_listed_adr(quote)
        ),
        lambda quote: quote.symbol,
        KOREA_SYMBOL_ORDER,
    )


def _us_quote_sections(summary: MarketSummary) -> tuple[tuple[str, tuple[QuoteChange, ...]], ...]:
    """미국장 리포트의 시세 표들. `US_SECTIONS` 순서로 (제목, 줄)을 돌려준다.

    미국 심볼·크립토·미국 상장 ADR이 대상이다. 크립토는 country가 `XX`(나라 없음)라 종류로
    넣는다. 지수·선물 표만 `US_SYMBOL_ORDER`로 줄을 세우고 나머지는 SQL 순서(심볼 이름순)다.
    """
    candidates = [
        quote
        for quote in summary.quotes
        if quote.kind in QUOTED_KINDS and (quote.country == "US" or quote.kind == "crypto" or _us_listed_adr(quote))
    ]
    return tuple(
        (
            title,
            _ordered(
                (quote for quote in candidates if quote.kind in kinds),
                lambda quote: quote.symbol,
                US_SYMBOL_ORDER,
            ),
        )
        for title, kinds in US_SECTIONS
    )


def _us_quotes(summary: MarketSummary) -> tuple[QuoteChange, ...]:
    """미국장 표 전부를 표 순서대로 편 것. 미리보기 한 줄과 footer가 이걸 본다."""
    return tuple(quote for _title, quotes in _us_quote_sections(summary) for quote in quotes)


def _intraday_overseas(summary: MarketSummary) -> tuple[QuoteChange, ...]:
    """한국장 시간에도 값이 움직이는 해외 심볼.

    미국은 선물만 넣는다. 현물 지수는 이 시간에 닫혀 있어 어제 종가를 오늘 값처럼 보이게 한다.
    미국 상장 ADR도 같은 이유로 뺀다. country가 아시아(TW·KR)라도 거래는 뉴욕이다.
    크립토는 24시간 거래라 항상 실시간이다. country가 나라가 아닌 `XX`라 뒤로 밀린다.
    """
    return _ordered(
        (
            quote
            for quote in summary.quotes
            if quote.kind in QUOTED_KINDS
            and not _us_listed_adr(quote)
            and (
                (quote.country == "US" and quote.kind == INDEX_FUTURE)
                or quote.country in ASIA_COUNTRIES
                or quote.kind == "crypto"
            )
        ),
        lambda quote: quote.country,
        OVERSEAS_COUNTRY_ORDER,
    )


def _domestic_stocks(summary: MarketSummary) -> tuple[QuoteChange, ...]:
    """국내 개별 종목만. `collect_summary`가 stock_bar 직접 조회로 바꿔 둔 행이라 NXT 봉을 담는다."""
    return tuple(quote for quote in summary.quotes if quote.provider == "kis" and quote.kind == "equity")


def _fx_quotes(summary: MarketSummary) -> tuple[QuoteChange, ...]:
    """장외 실시간 환율. 은행 고시가 아니라 장중에 움직이는 시장 값이다."""
    return _ordered(
        (quote for quote in summary.quotes if quote.kind == "fx"),
        lambda quote: quote.symbol,
        FX_SYMBOL_ORDER,
    )


def _quote_section(title: str, quotes: Sequence[QuoteChange]) -> list[dict[str, Any]]:
    """시세 표.

    **기준 시각을 행마다 적는다.** 심볼마다 마지막 봉 시각이 다르다. 국내는 KRX 마감,
    해외 선물은 몇 분 전 값이라 한 표 안에서 며칠 차이가 나기도 한다. 표 밖에 대표 시각
    하나만 두면 묵은 줄이 최신처럼 보인다.

    **거래소를 아는 행이 하나라도 있으면 거래소 열을 넣는다.** 국내 종목은 같은 분에도
    KRX·NXT 값이 다르므로 어느 거래소 봉인지 행에 보여야 한다(차트 라벨과 같은 이유).
    거래소 개념이 없는 지수·환율·해외 표는 열 자체가 없다.
    """
    if not quotes:
        return []
    # 전일 종가는 봉에 실려 오는 값이라 그 자체에 날짜가 없다. 그래서 `직전 기준` 열을 두지
    # 않고 열 이름으로 뜻을 닫는다 — 다른 표의 `직전`이 직전 **수집일**인 것과 다르다.
    if any(quote.exchange for quote in quotes):
        rows = [
            (
                quote.label,
                _number(quote.close),
                _number(quote.previous_close),
                _percent(quote.change_percent),
                quote.exchange or "-",
                _day_stamp(quote.bar_at),
            )
            for quote in quotes
        ]
        return blocks.table_section(title, ("구분", "종가", "전일 종가", "등락", "거래소", "기준"), rows)
    rows = [
        (
            quote.label,
            _number(quote.close),
            _number(quote.previous_close),
            _percent(quote.change_percent),
            _day_stamp(quote.bar_at),
        )
        for quote in quotes
    ]
    return blocks.table_section(title, ("구분", "종가", "전일 종가", "등락", "기준"), rows)


def _chart_section(files: Sequence[tuple[str, str]] | None, error: str | None) -> list[dict[str, Any]]:
    """차트 이미지. 계열마다 image 블록 하나다(`(file_id, label)`). **실패는 채널에 남긴다.**

    당일 분봉과 확정 일봉 보조지표가 이 목록에 섞여 온다. 어느 쪽인지는 부르는 쪽이 라벨에
    담는다 — 블록 모양이 같아 여기서 가를 것이 없다.

    조용히 빠지면 차트가 원래 없는 리포트와 구분되지 않는다(요약 실패와 같은 원칙).
    둘 다 없으면 개장 전처럼 그릴 봉이 없는 정상 흐름이라 아무 것도 그리지 않는다.
    """
    if files:
        return [blocks.image(file_id, f"{label} 차트") for file_id, label in files]
    if error:
        return [blocks.context([f"⚠️ 차트 생성 실패: {error}"])]
    return []


def _rate_section(summary: MarketSummary) -> list[dict[str, Any]]:
    if not summary.rates:
        return []
    # 금리는 등락률(%)을 쓰지 않는다. 4.65 → 4.70이 `+1.08%`가 되어 5bp 움직임이 1% 넘게
    # 뛴 것처럼 보인다(QUOTED_KINDS 주석과 같은 이유). 대신 직전 값과 그 관측일을 적는다.
    rows = [
        (
            rate.label,
            _rate(rate.value),
            "-" if rate.previous_value is None else _rate(rate.previous_value),
            _basis_points(rate.change_bp),
            "-" if rate.previous_observation_date is None else f"{rate.previous_observation_date:%m/%d}",
            f"{rate.observation_date:%m/%d}",
        )
        for rate in summary.rates
    ]
    return blocks.table_section("주요국 10년 금리", ("국가", "금리", "직전", "직전 대비", "직전 기준", "기준"), rows)


def _rate_spread_section(summary: MarketSummary) -> list[dict[str, Any]]:
    """금리 스프레드. 역전(음수)이 그대로 보이도록 부호를 항상 붙인다."""
    if not summary.spreads:
        return []
    rows = [
        (
            spread.label,
            f"{spread.spread_bp:+,.0f}bp",
            _basis_points(spread.change_bp),
            f"{spread.observed_on:%m/%d}",
        )
        for spread in summary.spreads
    ]
    return blocks.table_section("금리 스프레드", ("구분", "스프레드", "전일 대비", "기준"), rows)


def _market_funds_section(summary: MarketSummary) -> list[dict[str, Any]]:
    """증시자금. 단위는 KIS 표기 그대로 억원이다. 전일 확정치라 기준일을 행마다 적는다."""
    if summary.funds is None:
        return []
    funds = summary.funds
    stamp = f"{funds.business_date:%m/%d}"
    previous_day = funds.previous_business_date
    entries = (
        ("고객예탁금", funds.customer_deposit, funds.customer_deposit_change),
        ("신용융자 잔고", funds.credit_loan_balance, funds.credit_loan_change),
        ("미수금", funds.unsettled_amount, funds.unsettled_change),
    )
    rows = []
    for name, value, change in entries:
        delta = _delta(value, _previous(value, change), previous_day)
        rows.append((name, f"{value:,.0f}", delta.previous, delta.change, delta.rate, delta.stamp, stamp))
    return blocks.table_section(
        "증시자금(억원)", ("구분", "잔고", "직전", "직전 대비", "등락률", "직전 기준", "기준"), rows
    )


def _technical_section(summary: MarketSummary) -> list[dict[str, Any]]:
    """확정 일봉 기술지표. **수치와 기준일만 말한다.**

    상승·하락·매수·매도 같은 판정 열을 두지 않는다. 방향은 `thesis`가 확률로 내고 채점을
    받는다. 여기서 판정을 흉내 내면 채점 없는 신호가 브리핑에 실린다.
    """
    if not summary.technicals:
        return []
    latest = {signal.symbol: signal for signal in summary.signals}
    rows = [
        (
            snapshot.label,
            _ratio_percent(snapshot.close, snapshot.sma20),
            _ratio_percent(snapshot.sma20, snapshot.sma60),
            f"{snapshot.rsi14:.1f}",
            f"{snapshot.macd_histogram:+,.2f}",
            "-" if snapshot.volume_ratio20 is None else f"{snapshot.volume_ratio20:.2f}x",
            _signal_label(latest.get(snapshot.subject_code)),
            f"{snapshot.as_of_date:%m/%d}",
        )
        for snapshot in summary.technicals
    ]
    return blocks.table_section(
        "기술적 관측(확정 일봉·KRX)",
        ("대상", "종가/20일선", "20일선/60일선", "RSI(14일)", "MACD 히스토그램", "거래량/20일평균", "신호", "기준"),
        rows,
    )


def _signal_label(signal: RecentSignal | None) -> str:
    """사건 이름과 발생일. 최근 창에 아무 것도 없으면 `-`다."""
    if signal is None:
        return "-"
    return f"{signal.label} {signal.signal_date:%m/%d}"


def _ratio_percent(numerator: float, denominator: float) -> str:
    """`(왼쪽 / 오른쪽 - 1) × 100`. 이동평균 위인지 아래인지를 한 칸으로 읽는다."""
    if denominator == 0:
        return "-"
    return f"{(numerator / denominator - 1) * 100:+.2f}%"


def _short_position_section(summary: MarketSummary) -> list[dict[str, Any]]:
    """종목 공매도와 대차 잔고. 대차는 공매도의 재고라 한 표에 그린다. 단위는 주다."""
    if not summary.short_positions:
        return []
    rows = []
    for position in summary.short_positions:
        short = _delta(
            position.short_sale_quantity, position.previous_short_sale_quantity, position.previous_business_date
        )
        lending = _delta(
            position.lending_balance_quantity,
            position.previous_lending_balance_quantity,
            position.previous_business_date,
        )
        rows.append(
            (
                position.label,
                f"{position.short_sale_volume_ratio:.2f}%",
                f"{position.short_sale_quantity:,}",
                short.previous,
                short.rate,
                f"{position.lending_balance_quantity:,}" if position.lending_balance_quantity is not None else "-",
                lending.previous,
                lending.rate,
                short.stamp,
                f"{position.business_date:%m/%d}",
            )
        )
    return blocks.table_section(
        "공매도·대차(주·KRX)",
        (
            "종목",
            "공매도 비중",
            "공매도 수량",
            "직전 공매도",
            "공매도 등락률",
            "대차 잔고",
            "직전 대차",
            "대차 등락률",
            "직전 기준",
            "기준",
        ),
        rows,
    )


# `market_investor_flow_snapshot`의 순매수 대금은 **백만원 단위**다(KIS `*_ntby_tr_pbmn`).
# 억원으로 줄이려면 1억이 아니라 100으로 나눈다. 원 단위로 착각해 1억으로 나누면 수천억짜리
# 값이 전부 `-0`이 되어 표와 요약 입력이 동시에 거짓말을 한다.
MILLIONS_PER_HUNDRED_MILLION = 100


def _flow_section(summary: MarketSummary) -> list[dict[str, Any]]:
    if not summary.flows:
        return []
    rows = [
        (
            flow.market_code,
            _amount(flow.foreign_net_buy_amount),
            _amount(flow.previous_foreign_net_buy_amount),
            _amount(flow.institution_net_buy_amount),
            _amount(flow.previous_institution_net_buy_amount),
            _amount(flow.individual_net_buy_amount),
            _amount(flow.previous_individual_net_buy_amount),
            _session_stamp(flow.previous_session_date),
            _day_stamp(flow.observed_at),
        )
        for flow in summary.flows
    ]
    return blocks.table_section(
        "투자자 순매수(억원)",
        ("시장", "외국인", "직전 외국인", "기관", "직전 기관", "개인", "직전 개인", "직전 기준", "기준"),
        rows,
    )


def _stock_flow_section(summary: MarketSummary) -> list[dict[str, Any]]:
    """추적 종목의 추정 순매수.

    시장 수급과 표를 나눈다. 저쪽은 억원이고 이쪽은 주 수라 한 표에 넣으면 자릿수가 뜻을
    잃는다. **추정치라는 것도 제목에 적는다.** 확정 수급은 장 마감 뒤에야 나온다.

    기준은 날짜가 아니라 수집 시각이다. KIS가 장중 몇 차례 갱신하는 값이라 날짜만 적으면
    아침 추정과 마감 추정이 같은 줄로 보인다(시장 수급 표와 같은 이유).
    """
    if not summary.stock_flows:
        return []
    rows = [
        (
            flow.label,
            f"{flow.foreign_net_buy_qty:+,}",
            _quantity(flow.previous_foreign_net_buy_qty),
            f"{flow.institution_net_buy_qty:+,}",
            _quantity(flow.previous_institution_net_buy_qty),
            f"{flow.total_net_buy_qty:+,}",
            _quantity(flow.previous_total_net_buy_qty),
            _session_stamp(flow.previous_business_date),
            _day_stamp(flow.collected_at),
        )
        for flow in summary.stock_flows
    ]
    return blocks.table_section(
        "종목 추정 순매수(주)",
        ("종목", "외국인", "직전 외국인", "기관", "직전 기관", "합계", "직전 합계", "직전 기준", "기준"),
        rows,
    )


def _closed_trades(summary: MarketSummary) -> tuple[StockTradeSnapshot, ...]:
    """오늘(KST) 거래일의 확정값만 고른다.

    조회 창은 연휴를 건너려고 열흘이지만, 그대로 그리면 12:30 발송에 어제 마감이 오늘
    것처럼 실린다. 확정값은 KST 18:10에 오므로 이 섹션은 저녁 발송에만 나타난다.
    """
    today = summary.generated_at.astimezone(KST_TIMEZONE).date()
    return tuple(trade for trade in summary.stock_trades if trade.business_date == today)


def _stock_trade_sections(summary: MarketSummary) -> list[dict[str, Any]]:
    """종목 마감 확정: 종가·등락과 확정 수급, 그리고 기관 세부.

    추정(장중) 표와 나눈다. 하나는 장중 스냅샷이고 하나는 마감 확정치라 같은 표에 섞으면
    어느 쪽인지 알 수 없다. 기관 세부 일곱은 열이 많아 표를 따로 그린다.

    제목에 KRX를 밝힌다. 시세 표가 15:30 이후 NXT 봉을 보이므로, 여기 종가가 그와 다른
    이유(KRX 정규장 확정치이고 NXT 체결은 이 집계에 없음)가 제목에서 보여야 한다.
    """
    trades = _closed_trades(summary)
    if not trades:
        return []
    closing_rows = [
        (
            trade.label,
            _number(trade.close),
            "-" if trade.previous_close is None else _number(trade.previous_close),
            _percent(trade.change_percent),
            f"{trade.foreign_net_buy_qty:+,}",
            _quantity(trade.previous_foreign_net_buy_qty),
            f"{trade.institution_net_buy_qty:+,}",
            _quantity(trade.previous_institution_net_buy_qty),
            f"{trade.individual_net_buy_qty:+,}",
            _quantity(trade.previous_individual_net_buy_qty),
            _session_stamp(trade.previous_business_date),
            f"{trade.business_date:%m/%d}",
        )
        for trade in trades
    ]
    # 모든 표에는 기준이 있어야 한다. 확정 일별 수급이라 시각이 아니라 거래일이다.
    detail_rows = [
        (
            trade.label,
            f"{trade.securities_net_buy_qty:+,}",
            f"{trade.investment_trust_net_buy_qty:+,}",
            f"{trade.private_equity_net_buy_qty:+,}",
            f"{trade.bank_net_buy_qty:+,}",
            f"{trade.insurance_net_buy_qty:+,}",
            f"{trade.merchant_bank_net_buy_qty:+,}",
            f"{trade.pension_fund_net_buy_qty:+,}",
            f"{trade.business_date:%m/%d}",
        )
        for trade in trades
    ]
    return [
        *blocks.table_section(
            "종목 마감 확정(주·KRX)",
            (
                "종목",
                "종가",
                "직전 종가",
                "등락",
                "외국인",
                "직전 외국인",
                "기관",
                "직전 기관",
                "개인",
                "직전 개인",
                "직전 기준",
                "기준",
            ),
            closing_rows,
        ),
        *blocks.table_section(
            "기관 세부(주·KRX)",
            ("종목", "금융투자", "투신", "사모", "은행", "보험", "종금", "연기금", "기준"),
            detail_rows,
        ),
    ]


def _ordered[T](items: Iterable[T], key: Callable[[T], str], order: Sequence[str]) -> tuple[T, ...]:
    """`order`에 적힌 차례로 줄을 세운다. 목록에 없는 값은 뒤로 밀린다."""
    rank = {value: index for index, value in enumerate(order)}
    return tuple(sorted(items, key=lambda item: rank.get(key(item), len(rank))))


def _movement_section(summary: MarketSummary) -> list[dict[str, Any]]:
    if not summary.movements:
        return []
    rows = [
        (
            movement.market_code,
            f"{movement.rising_count:,}",
            _count(movement.previous_rising_count),
            f"{movement.unchanged_count:,}",
            _count(movement.previous_unchanged_count),
            f"{movement.falling_count:,}",
            _count(movement.previous_falling_count),
            _session_stamp(movement.previous_session_date),
            _day_stamp(movement.observed_at),
        )
        for movement in summary.movements
    ]
    return blocks.table_section(
        "등락 종목 수",
        ("시장", "상승", "직전 상승", "보합", "직전 보합", "하락", "직전 하락", "직전 기준", "기준"),
        rows,
    )


def _as_of(summary: MarketSummary, scope: MarketScope) -> list[str]:
    """리포트 시각과 **가장 묵은 값**.

    값마다의 기준 시각은 이제 표 안에 있다. 여기서는 한눈에 볼 것 하나만 남긴다 —
    이 리포트에서 제일 오래된 값이 언제 것인가. 그게 최신이면 전체가 최신이다.
    """
    lines = [f"작성 {blocks.timestamp(summary.generated_at.astimezone(KST_TIMEZONE))}"]
    quotes = _scope_quotes(summary, scope)
    observed = [quote.bar_at for quote in quotes]
    observed += [flow.observed_at for flow in summary.flows]
    observed += [movement.observed_at for movement in summary.movements]
    if observed:
        lines.append(f"가장 오래된 값 {_day_stamp(min(observed))}")
    return lines


def _day_stamp(moment: datetime) -> str:
    local = moment.astimezone(KST_TIMEZONE)
    return f"{local:%m/%d} {local:%H:%M}"


def _number(value: Decimal) -> str:
    return f"{value:,.2f}"


def _rate(value: Decimal) -> str:
    """금리는 소수 셋째 자리까지다. `Numeric(18,8)`을 그대로 찍으면 `4.68000000%`가 나온다."""
    return f"{value:,.3f}%"


def _percent(change: float | None) -> str:
    if change is None:
        return "-"
    return f"{_arrow(change)} {change:+.2f}%"


def _basis_points(change: float | None) -> str:
    if change is None:
        return "-"
    return f"{_arrow(change)} {change:+.1f}bp"


def _amount(value: Decimal | None) -> str:
    """수급 대금을 억원으로 줄인다. 조 단위 숫자를 그대로 두면 표가 화면을 넘는다."""
    if value is None:
        return "-"
    return f"{value / MILLIONS_PER_HUNDRED_MILLION:+,.0f}"


def _quantity(value: int | None) -> str:
    """순매수 수량. 부호가 곧 방향이라 항상 붙인다."""
    return "-" if value is None else f"{value:+,}"


def _count(value: int | None) -> str:
    """종목 수처럼 부호가 없는 값."""
    return "-" if value is None else f"{value:,}"


def _session_stamp(session_date: date | None) -> str:
    """직전 세션 날짜. 직전 세션이 없으면 `-`다."""
    return "-" if session_date is None else f"{session_date:%m/%d}"


def _previous(value: Decimal | int | None, change: Decimal | int | None) -> Decimal | int | None:
    """증감만 있는 값의 전일 잔고를 역산한다. 증감이 없으면 전일 행이 없다는 뜻이다."""
    if value is None or change is None:
        return None
    return value - change


class Delta(BaseModel):
    """직전 값과의 비교를 표의 칸으로 편 것. 값이 없으면 네 칸이 모두 `-`다."""

    model_config = ConfigDict(frozen=True)

    previous: str
    stamp: str
    change: str
    rate: str


def _delta(value: Decimal | int | None, previous: Decimal | int | None, previous_date: date | None = None) -> Delta:
    """`직전`, `직전 기준`, `직전 대비`, `등락률`.

    **날짜는 값과 같은 칸에 넣지 않고 따로 돌려준다.** 숫자 칸은 우측 정렬이라 뒤에 날짜가
    붙으면 자릿수가 세로로 맞지 않고, 정렬해서 읽는 이점이 사라진다.

    직전 기준일을 함께 내는 이유는 이 표들의 원천이 매일 도는 수집이 아니어서다. 날짜가
    없으면 사흘 전 값과의 차이를 전일 대비로 읽는다.

    등락률은 직전 값의 절대값으로 나눈다. 나누는 쪽의 부호가 등락률의 부호를 뒤집는 일이
    없어야 한다. 직전이 0이면 등락률은 `-`다 — 0에서 늘어난 비율은 정의되지 않는다.
    """
    if value is None or previous is None:
        return Delta(previous="-", stamp="-", change="-", rate="-")
    stamp = "-" if previous_date is None else f"{previous_date:%m/%d}"
    change = value - previous
    rate = "-" if previous == 0 else f"{float(change) / abs(float(previous)) * 100:+.2f}%"
    return Delta(previous=f"{previous:,.0f}", stamp=stamp, change=f"{change:+,.0f}", rate=rate)


def _arrow(change: float) -> str:
    if change > 0:
        return "▲"
    return "▼" if change < 0 else "－"
