"""브리핑 차트 PNG. 당일 분봉 라인과 확정 일봉 보조지표 두 종류다. **계열 하나가 이미지 하나다.**

한 장에 서브플롯으로 묶지 않는다. Slack 메시지에서 이미지 하나로 합치면 모바일에서
작게 접혀 읽기 어렵고, 심볼을 늘릴 때마다 그리드 배치가 바뀐다. 계열마다 image 블록을
하나씩 싣는다.

matplotlib은 **함수 안에서** import한다. 운영 Airflow 이미지에 없으면 브리핑 전체가 아니라
차트만 죽어야 한다. 표가 본체이고 차트는 덧붙임이다(요약 실패와 같은 원칙). ImportError는
그대로 올리고 DAG가 실패 종류로 가른다.

라벨이 한글(`quote_symbol.label`)이라 한글 글리프가 있는 폰트가 필요하다. 없으면
`ChartError`로 멈춘다. 두부 글자(□□□)로 조용히 나가는 것보다 낫다. 운영 이미지에는
`fonts-nanum`을 함께 넣는다.

이 모듈은 Airflow를 import하지 않는다. import하면 테스트가 배포 환경 없이 돌지 않는다.
"""

import io
from collections.abc import Sequence
from datetime import date, datetime
from itertools import pairwise
from typing import TYPE_CHECKING

from modules import technical
from modules.briefing import blocks
from modules.utility import KST_TIMEZONE

if TYPE_CHECKING:
    from modules.briefing.market_data import ChartSeries, DailyChartSeries

# 한글 글리프를 가진 폰트 이름 조각. 나눔(운영 이미지), 맑은 고딕(Windows),
# Apple SD Gothic(macOS 로컬 테스트) 순으로 찾는다.
KOREAN_FONT_KEYWORDS = ("nanum", "malgun", "apple sd gothic")

# 국내 관례: 상승 빨강, 하락 파랑.
RISE_COLOR = "#d64545"
FALL_COLOR = "#3b6fd6"

# 이동평균선. (기간, 색)이고 이 순서가 범례 순서다. 한국투자증권 앱 차트와 같은 네 기간이라
# 같은 종목을 두 화면에서 볼 때 눈이 옮겨간다. 색은 봉(빨강·파랑)과 겹치지 않는 것으로 고른다.
SMA_LINES: tuple[tuple[int, str], ...] = (
    (5, "#e08a1e"),
    (20, "#7b5ea7"),
    (60, "#2f8f83"),
    (120, "#5b8f2f"),
)

# 봉을 그리지 않는 계열(환율)의 종가선. 이동평균과 구분되는 무채색이다.
CLOSE_LINE_COLOR = "#333333"

RSI_COLOR = "#3b6fd6"
MACD_COLOR = "#333333"
MACD_SIGNAL_COLOR = "#e08a1e"

# RSI 과매수·과매도 기준선. 판정이 아니라 눈금이다 — 표와 마찬가지로 방향은 말하지 않는다.
RSI_UPPER = 70
RSI_LOWER = 30

# 봉과 RSI·MACD 단을 그리는 종류(`quote_symbol.kind`). **환율은 종가 선 하나로 그린다.**
# 환율은 브리핑에서 매매 시점을 보는 대상이 아니라 수준과 방향만 보면 되고, 봉과 단 셋을
# 얹으면 정작 볼 것이 작아진다. 이동평균선은 환율에도 그린다.
CANDLE_KINDS = frozenset({"index", "equity"})

# 봉 몸통의 폭. x축이 봉 자리(0, 1, 2 …)라 1.0이면 이웃 봉과 맞닿는다.
CANDLE_WIDTH = 0.7

# x축에 찍는 날짜 눈금 수의 상한. 늘리면 8인치 폭에서 라벨이 서로 겹친다.
MAX_DATE_TICKS = 6

# 화면에 그리는 봉 수. **계산은 받은 봉 전부로 하고 표시만 자른다**(`INDICATOR_HISTORY_BARS`).
# 120일선과 MACD는 앞쪽 봉을 다 써야 값이 나오는데, 그 봉까지 그리면 8인치 폭에 1~2년치가
# 들어가 봉 하나가 선이 된다.
#
# 60봉(약 3개월)인 이유는 이 그림이 **매일 아침 Slack에서 한 번 스쳐 보는 것**이기 때문이다.
# 반년치를 넣으면 최근 며칠의 움직임이 화면 오른쪽 끝 몇 픽셀로 뭉개진다. 더 긴 흐름은
# 이동평균선 넷이 대신 말한다 — 60일선·120일선은 창 밖의 봉으로 계산돼 있다.
DISPLAY_BARS = 60


class ChartError(RuntimeError):
    """차트를 그릴 수 없고 다시 그려도 같은 결과다."""


def render_series_png(series: "ChartSeries") -> bytes:
    """계열 하나를 이미지 하나로 그린다. 봉이 없는 계열은 부르는 쪽이 걸러야 한다."""
    if not series.points:
        raise ChartError(f"no points to draw: {series.symbol}")

    import matplotlib

    matplotlib.use("Agg")
    from matplotlib import font_manager
    from matplotlib.dates import DateFormatter
    from matplotlib.figure import Figure
    from matplotlib.ticker import StrMethodFormatter

    matplotlib.rcParams["font.family"] = _korean_font(font_manager)
    matplotlib.rcParams["axes.unicode_minus"] = False

    figure = Figure(figsize=(8, 2.8), dpi=150)
    axis = figure.subplots()
    # matplotlib은 aware datetime을 UTC로 되돌리므로 KST naive로 바꿔 그린다.
    times = [moment.astimezone(KST_TIMEZONE).replace(tzinfo=None) for moment, _ in series.points]
    closes = [float(close) for _, close in series.points]
    color = RISE_COLOR if closes[-1] >= closes[0] else FALL_COLOR
    axis.plot(times, closes, color=color, linewidth=1.2)
    # 등락률은 넣지 않는다. 계열의 첫 봉이 결측으로 늦게 시작하면 시가 대비가 아니게 되고,
    # 전일 대비 등락은 표가 이미 갖고 있다.
    # 날짜는 마지막 봉의 KST 날짜다. 요일은 blocks.timestamp와 같은 표를 쓴다 —
    # strftime("%a")는 실행 환경 로케일을 탄다.
    day = times[-1]
    # 제목에 시장·봉 간격·날짜를 함께 적는다. 값만 있으면 NXT 애프터마켓 봉이 KRX 마감값처럼
    # 읽히고, 간격이 없으면 이 선이 1분봉인지 5분봉인지 그림만 보고는 알 수 없다.
    interval = interval_minutes(times)
    stamp = f"{day:%m/%d}({blocks.WEEKDAY_NAMES[day.weekday()]})"
    axis.set_title(
        f"{series.label} {closes[-1]:,.0f} · {series.venue} · {interval}분봉 · {stamp}"
        if interval
        else f"{series.label} {closes[-1]:,.0f} · {series.venue} · {stamp}",
        fontsize=11,
    )
    axis.xaxis.set_major_formatter(DateFormatter("%H:%M"))
    # 기본 offset 표기(1e6)는 값을 읽을 수 없게 한다.
    axis.yaxis.set_major_formatter(StrMethodFormatter("{x:,.0f}"))
    axis.grid(True, linewidth=0.3, alpha=0.5)
    axis.tick_params(labelsize=8)

    figure.tight_layout()
    buffer = io.BytesIO()
    figure.savefig(buffer, format="png")
    return buffer.getvalue()


def interval_minutes(times: Sequence[datetime]) -> int:
    """봉 간격(분). 알 수 없으면 0이다.

    **상수로 적지 않고 봉에서 읽는다.** 지금 수집이 1분봉이라 `1분봉`을 상수로 두면, 나중에
    5분으로 바꾼 날 그림만 조용히 거짓말을 한다.

    간격은 **가장 짧은 사이**다. 점심 공백이나 결측이 있으면 그쪽이 더 벌어지므로 평균이나
    최빈값보다 최소가 실제 간격에 가깝다.
    """
    gaps = [round((later - earlier).total_seconds() / 60) for earlier, later in pairwise(times) if later > earlier]
    return min(gaps) if gaps else 0


def _date_ticks(days: Sequence[date]) -> tuple[list[int], list[str]]:
    """봉 자리에 찍을 날짜 눈금. 마지막 봉은 반드시 들어간다 — 기준일이 축에도 보여야 한다."""
    if not days:
        return [], []
    step = max(1, (len(days) - 1) // max(1, MAX_DATE_TICKS - 1))
    positions = list(range(len(days) - 1, -1, -step))[::-1]
    return positions, [f"{days[spot]:%m/%d}" for spot in positions]


def draws_candles(kind: str) -> bool:
    """이 종류를 봉과 RSI·MACD 단으로 그릴지. 아니면 종가 선 하나에 이동평균선만 얹는다."""
    return kind in CANDLE_KINDS


def render_daily_png(series: "DailyChartSeries") -> bytes:
    """확정 일봉 한 계열을 그린다. 이동평균선은 언제나 그리고, 봉과 RSI14·MACD는 종류가 정한다.

    **분봉 차트에 얹지 않고 따로 그린다.** 이동평균선은 당일 가격 범위에서 멀리 떨어져
    있을 수 있어 한 축에 두면 분봉이 납작해진다. 시간 축도 다르다.

    지표를 못 내는 구간은 `None`이라 선이 늦게 시작한다. 0으로 채우지 않는다 — 표에
    줄이 없는 것과 같은 이유다. 봉이 모자라 한 점도 못 내는 기간(예: 120봉이 안 될 때의
    120일선)은 범례에서도 뺀다. 그리지 않은 선을 범례가 있다고 말하면 안 된다.
    """
    if len(series.bars) < technical.TECHNICAL_MIN_BARS:
        raise ChartError(f"not enough bars to draw indicators: {series.subject_code}")

    import matplotlib

    matplotlib.use("Agg")
    from matplotlib import font_manager
    from matplotlib.figure import Figure
    from matplotlib.ticker import StrMethodFormatter

    matplotlib.rcParams["font.family"] = _korean_font(font_manager)
    matplotlib.rcParams["axes.unicode_minus"] = False

    closes = [bar.close for bar in series.bars]
    indicators = technical.compute_series(closes)

    # 여기서부터는 그릴 구간만 본다. 지표는 위에서 전 구간으로 이미 계산했다.
    shown = series.bars[-DISPLAY_BARS:]
    window = slice(len(series.bars) - len(shown), None)
    closes = closes[window]
    days = [bar.business_date for bar in shown]
    # **x축은 날짜가 아니라 봉 자리다.** 날짜를 그대로 쓰면 주말·휴장일이 빈칸으로 남아
    # 연휴마다 차트에 구멍이 생긴다. 증권사 차트가 거래일을 붙여 그리는 것과 같다.
    # 날짜는 눈금 라벨로만 돌아온다(`_date_ticks`).
    spots = list(range(len(shown)))

    with_indicators = draws_candles(series.kind)
    if with_indicators:
        figure = Figure(figsize=(8, 5.4), dpi=150)
        price, momentum, trend = figure.subplots(3, 1, sharex=True, height_ratios=(3, 1.1, 1.4))
        panels = (price, momentum, trend)
    else:
        figure = Figure(figsize=(8, 3.4), dpi=150)
        price = figure.subplots()
        panels = (price,)

    if with_indicators:
        # 봉은 직접 그린다. 캔들은 matplotlib 기본 차트에 없고 mplfinance는 운영 이미지에 없다.
        # 꼬리는 고가~저가 세로선, 몸통은 시가~종가 막대다. 시가와 종가가 같은 날(도지)은
        # 높이가 0이라 테두리만 남는데, 그 한 줄이 곧 봉이라 따로 손대지 않는다.
        # 국내 관례대로 종가가 시가 이상이면 양봉(빨강)이다. 전일 종가 대비가 아니다.
        candle_colors = [RISE_COLOR if bar.close >= bar.open else FALL_COLOR for bar in shown]
        price.vlines(
            spots,
            [bar.low for bar in shown],
            [bar.high for bar in shown],
            color=candle_colors,
            linewidth=0.6,
        )
        price.bar(
            spots,
            [bar.close - bar.open for bar in shown],
            bottom=[bar.open for bar in shown],
            color=candle_colors,
            edgecolor=candle_colors,
            linewidth=0.6,
            width=CANDLE_WIDTH,
        )
    else:
        # **종가선에는 상승·하락 색을 쓰지 않는다.** 이동평균 넷과 한 축에 겹치는데 빨강·파랑을
        # 쓰면 60일선(청록)과 색이 붙어 어느 선이 값인지 눈으로 못 가른다. 방향은 이동평균과의
        # 위아래로 읽는다.
        price.plot(spots, closes, color=CLOSE_LINE_COLOR, linewidth=1.4, label="종가", zorder=3)

    for period, color in SMA_LINES:
        # 이동평균도 전 구간으로 계산하고 그릴 구간만 자른다. 잘라 놓고 계산하면 화면 왼쪽
        # 끝에서 선이 늦게 시작해, 그 구간에 값이 없는 것처럼 보인다.
        line = technical.sma_series([bar.close for bar in series.bars], period)[window]
        if all(value is None for value in line):
            continue
        price.plot(spots, line, color=color, linewidth=0.9, label=f"{period}일선", zorder=2)
    price.set_title(f"{series.label} {closes[-1]:,.0f} · {series.venue} · {days[-1]:%m/%d} 확정 일봉", fontsize=11)
    # 선이 다섯이라 한 줄로 눕힌다. 세로로 쌓으면 왼쪽 위 값이 범례에 가린다.
    price.legend(fontsize=7, loc="upper left", framealpha=0.7, ncol=len(SMA_LINES) + 1, columnspacing=1.0)
    price.yaxis.set_major_formatter(StrMethodFormatter("{x:,.0f}"))

    if with_indicators:
        momentum.plot(spots, indicators.rsi14[window], color=RSI_COLOR, linewidth=1.0)
        momentum.axhline(RSI_UPPER, color=RISE_COLOR, linewidth=0.6, linestyle="--")
        momentum.axhline(RSI_LOWER, color=FALL_COLOR, linewidth=0.6, linestyle="--")
        momentum.set_ylim(0, 100)
        momentum.set_ylabel(f"RSI({technical.RSI_BARS})", fontsize=8)

        histogram = [
            None if macd is None or signal is None else macd - signal
            for macd, signal in zip(indicators.macd[window], indicators.macd_signal[window], strict=True)
        ]
        bar_colors = [RISE_COLOR if (value or 0) >= 0 else FALL_COLOR for value in histogram]
        trend.bar(spots, [value or 0 for value in histogram], color=bar_colors, width=CANDLE_WIDTH, label="OSC")
        trend.plot(
            spots,
            indicators.macd[window],
            color=MACD_COLOR,
            linewidth=0.8,
            label=f"MACD({technical.MACD_FAST_BARS},{technical.MACD_SLOW_BARS})",
        )
        trend.plot(
            spots,
            indicators.macd_signal[window],
            color=MACD_SIGNAL_COLOR,
            linewidth=0.8,
            label=f"시그널({technical.MACD_SIGNAL_BARS})",
        )
        trend.legend(fontsize=7, loc="upper left", framealpha=0.6)
        trend.set_ylabel("MACD", fontsize=8)
        trend.yaxis.set_major_formatter(StrMethodFormatter("{x:,.0f}"))

    positions, labels = _date_ticks(days)
    panels[-1].set_xticks(positions, labels)
    panels[-1].set_xlim(-1, len(spots))
    for axis in panels:
        axis.grid(True, linewidth=0.3, alpha=0.5)
        axis.tick_params(labelsize=8)

    figure.tight_layout()
    # 무엇으로 낸 값인지 그림 안에 남긴다. 지표는 계산에 쓴 봉 수에 따라 소수점 아래가
    # 달라져서, 창만 보고는 증권사 앱 값과 왜 다른지 가릴 수 없다.
    figure.text(
        0.995,
        0.005,
        f"{series.venue} 확정 일봉 · 계산 {len(series.bars)}봉 · 표시 {len(shown)}봉",
        ha="right",
        va="bottom",
        fontsize=7,
        color="#777777",
    )
    buffer = io.BytesIO()
    figure.savefig(buffer, format="png")
    return buffer.getvalue()


def _korean_font(font_manager) -> str:
    for font in font_manager.fontManager.ttflist:
        if any(keyword in font.name.lower() for keyword in KOREAN_FONT_KEYWORDS):
            return font.name
    raise ChartError("no Korean-capable font installed; add fonts-nanum to the Airflow image")
