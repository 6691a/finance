"""당일 분봉 라인 차트 PNG. **계열 하나가 이미지 하나다.**

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
from typing import TYPE_CHECKING

from modules.utility import KST_TIMEZONE

if TYPE_CHECKING:
    from modules.briefing.market import ChartSeries

# 한글 글리프를 가진 폰트 이름 조각. 나눔(운영 이미지), 맑은 고딕(Windows),
# Apple SD Gothic(macOS 로컬 테스트) 순으로 찾는다.
KOREAN_FONT_KEYWORDS = ("nanum", "malgun", "apple sd gothic")

# 국내 관례: 상승 빨강, 하락 파랑.
RISE_COLOR = "#d64545"
FALL_COLOR = "#3b6fd6"


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
    axis.set_title(f"{series.label} {closes[-1]:,.0f}", fontsize=11)
    axis.xaxis.set_major_formatter(DateFormatter("%H:%M"))
    # 기본 offset 표기(1e6)는 값을 읽을 수 없게 한다.
    axis.yaxis.set_major_formatter(StrMethodFormatter("{x:,.0f}"))
    axis.grid(True, linewidth=0.3, alpha=0.5)
    axis.tick_params(labelsize=8)

    figure.tight_layout()
    buffer = io.BytesIO()
    figure.savefig(buffer, format="png")
    return buffer.getvalue()


def _korean_font(font_manager) -> str:
    for font in font_manager.fontManager.ttflist:
        if any(keyword in font.name.lower() for keyword in KOREAN_FONT_KEYWORDS):
            return font.name
    raise ChartError("no Korean-capable font installed; add fonts-nanum to the Airflow image")
