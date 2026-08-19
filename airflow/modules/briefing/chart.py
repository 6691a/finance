"""당일 분봉 라인 차트 PNG.

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
from datetime import datetime
from math import ceil
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

SUBPLOT_COLUMNS = 2


class ChartError(RuntimeError):
    """차트를 그릴 수 없고 다시 그려도 같은 결과다."""


def render_chart_png(series: Sequence["ChartSeries"], generated_at: datetime) -> bytes:
    """계열마다 서브플롯 하나. 계열이 없으면 부르는 쪽이 생략해야 한다."""
    if not series:
        raise ChartError("no series to draw")

    import matplotlib

    matplotlib.use("Agg")
    from matplotlib import font_manager
    from matplotlib.dates import DateFormatter
    from matplotlib.figure import Figure
    from matplotlib.ticker import StrMethodFormatter

    matplotlib.rcParams["font.family"] = _korean_font(font_manager)
    matplotlib.rcParams["axes.unicode_minus"] = False

    rows = ceil(len(series) / SUBPLOT_COLUMNS)
    figure = Figure(figsize=(5 * SUBPLOT_COLUMNS, 2.6 * rows), dpi=150)
    local = generated_at.astimezone(KST_TIMEZONE)
    figure.suptitle(f"당일 흐름 · {local:%m/%d %H:%M} KST", fontsize=11)

    axes = figure.subplots(rows, SUBPLOT_COLUMNS, squeeze=False)
    for index, one in enumerate(series):
        axis = axes[index // SUBPLOT_COLUMNS][index % SUBPLOT_COLUMNS]
        # matplotlib은 aware datetime을 UTC로 되돌리므로 KST naive로 바꿔 그린다.
        times = [moment.astimezone(KST_TIMEZONE).replace(tzinfo=None) for moment, _ in one.points]
        closes = [float(close) for _, close in one.points]
        color = RISE_COLOR if closes[-1] >= closes[0] else FALL_COLOR
        axis.plot(times, closes, color=color, linewidth=1.2)
        # 등락률은 넣지 않는다. 계열의 첫 봉이 결측으로 늦게 시작하면 시가 대비가 아니게 되고,
        # 전일 대비 등락은 표가 이미 갖고 있다.
        axis.set_title(f"{one.label} {closes[-1]:,.0f}", fontsize=10)
        axis.xaxis.set_major_formatter(DateFormatter("%H:%M"))
        # 기본 offset 표기(1e6)는 값을 읽을 수 없게 한다.
        axis.yaxis.set_major_formatter(StrMethodFormatter("{x:,.0f}"))
        axis.grid(True, linewidth=0.3, alpha=0.5)
        axis.tick_params(labelsize=8)
    for index in range(len(series), rows * SUBPLOT_COLUMNS):
        axes[index // SUBPLOT_COLUMNS][index % SUBPLOT_COLUMNS].set_visible(False)

    figure.tight_layout(rect=(0, 0, 1, 0.95))
    buffer = io.BytesIO()
    figure.savefig(buffer, format="png")
    return buffer.getvalue()


def _korean_font(font_manager) -> str:
    for font in font_manager.fontManager.ttflist:
        if any(keyword in font.name.lower() for keyword in KOREAN_FONT_KEYWORDS):
            return font.name
    raise ChartError("no Korean-capable font installed; add fonts-nanum to the Airflow image")
