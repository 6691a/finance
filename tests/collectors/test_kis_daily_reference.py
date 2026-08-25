"""거래소가 준 일봉을 정답지로 고정한다.

`tests/collectors/fixtures/kis_inquire_daily_itemchartprice_005930.json`은 KIS 기간별시세
(`/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice`, `FHKST03010100`)를
2026-08-25에 운영 키로 한 번 받은 응답이다. 삼성전자 2026-03-31~2026-08-25 100 거래일이고,
`FID_ORG_ADJ_PRC=1`(원주가)로 받았다. 같은 구간을 `0`(수정주가)으로도 받아 대조했더니
**100일이 한 칸도 다르지 않았다** — 이 구간에 수정 이벤트가 없다(`mod_yn`이 전부 `N`).

이 파일이 있는 이유는 둘이다.

- **지표 계산이 조용히 바뀌는 것을 막는다.** EMA 초기값을 어디서 잡는지, 조회 봉 수를
  얼마로 두는지에 따라 MACD·RSI가 소수점 아래부터 움직인다. 거래소가 준 종가 시리즈
  하나를 고정해 두면 그 변화가 여기서 먼저 드러난다.
- **증권사 앱 화면과 우리 값이 갈릴 때 어느 쪽이 기준인지 답한다.** 앱 차트는 이 구간의
  최고가를 380,000(2026-06-19)으로 표시하지만, 그날 KIS가 주는 고가는 374,500이다.
  수정주가로 받아도 같다. 앱 라벨은 이 API가 주는 정규장 일봉이 아니다.

**이 테스트는 네트워크를 타지 않는다.** 픽스처를 다시 받아야 할 일이 생기면(구간을 늘리거나
수정 이벤트가 있는 종목을 넣을 때) 위 파라미터로 한 번 받아 파일만 갈아 끼운다.
"""

import json
from datetime import date
from pathlib import Path

import pytest

from modules import technical

FIXTURE = Path(__file__).parent / "fixtures" / "kis_inquire_daily_itemchartprice_005930.json"


def reference_bars() -> list[technical.DailyBar]:
    """정답지를 오름차순 일봉으로 읽는다. 응답은 최신순이라 뒤집는다."""
    payload = json.loads(FIXTURE.read_text())
    rows = sorted(payload["output2"], key=lambda row: row["stck_bsop_date"])
    return [
        technical.DailyBar(
            business_date=date.fromisoformat(
                f"{row['stck_bsop_date'][:4]}-{row['stck_bsop_date'][4:6]}-{row['stck_bsop_date'][6:]}"
            ),
            open=float(row["stck_oprc"]),
            high=float(row["stck_hgpr"]),
            low=float(row["stck_lwpr"]),
            close=float(row["stck_clpr"]),
            volume=int(row["acml_vol"]),
        )
        for row in rows
    ]


def test_the_reference_covers_one_hundred_trading_days():
    bars = reference_bars()

    assert len(bars) == 100
    assert bars[0].business_date == date(2026, 3, 31)
    assert bars[-1].business_date == date(2026, 8, 25)


def test_the_exchange_high_is_the_regular_session_high():
    """앱 차트가 380,000으로 표시하는 2026-06-19의 KIS 고가는 374,500이다.

    증권사 화면과 값이 다르다는 말이 나올 때 되돌아올 자리다. 우리 저장값도 이 수와 같다
    (2026-08-25 운영 DB 대조: 2026-03-01~08-20의 115일 중 어긋난 날 0).
    """
    bars = {bar.business_date: bar for bar in reference_bars()}

    assert bars[date(2026, 6, 19)].high == 374500.0
    assert bars[date(2026, 6, 19)].low == 346250.0
    assert bars[date(2026, 3, 31)].low == 167000.0


def test_indicators_computed_from_the_exchange_series_stay_put():
    """거래소 종가 100봉으로 낸 지표. 이 수가 바뀌면 계산이 바뀐 것이다."""
    snapshot = technical.summarize("005930", "삼성전자", reference_bars())

    assert snapshot is not None
    assert snapshot.as_of_date == date(2026, 8, 25)
    assert snapshot.close == 251500.0
    assert snapshot.sma20 == pytest.approx(246475.0)
    assert snapshot.sma60 == pytest.approx(287225.0)
    assert snapshot.rsi14 == pytest.approx(47.8417747, abs=1e-6)
    assert snapshot.macd == pytest.approx(-1159.93884, abs=1e-4)
    assert snapshot.macd_signal == pytest.approx(-4708.51011, abs=1e-4)
    assert snapshot.macd_histogram == pytest.approx(3548.57126, abs=1e-4)


def test_more_history_moves_the_ema_but_not_the_moving_averages():
    """조회 봉 수가 지표에 남기는 흔적. 표와 차트가 같은 봉 수를 써야 하는 이유다.

    이동평균은 창 안의 종가만 보므로 앞을 잘라도 같고, EMA를 쓰는 MACD는 달라진다.
    브리핑은 `briefing/market_data.INDICATOR_HISTORY_BARS` 하나로 표와 차트를 함께 맞춘다.
    """
    bars = reference_bars()
    short = technical.summarize("005930", "삼성전자", bars[-70:])
    full = technical.summarize("005930", "삼성전자", bars)

    assert short is not None and full is not None
    assert short.sma20 == full.sma20
    assert short.sma60 == full.sma60
    assert short.macd != full.macd
