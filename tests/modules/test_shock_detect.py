"""급변 판정의 경계값을 잠근다. **DB가 없다** — 봉을 손으로 만들어 넣는다.

`detect.py`가 순수 함수인 이유가 이 파일이다. 1.99%와 2.00%를 가르는 자리를 가짜 연결
없이, 픽스처 없이 확인한다.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from modules.shock.detect import detect, peer_move, window_change_pct
from modules.shock.domain import PEER_SPECS, Bar, Direction, PeerRegion

WINDOW_START = datetime(2026, 9, 3, 4, 46, tzinfo=UTC)  # KST 13:46
WINDOW_END = datetime(2026, 9, 3, 5, 16, tzinfo=UTC)  # KST 14:16
THRESHOLD = Decimal("2.0")


def bar(minute: int, *, open_: str, high: str, low: str, close: str) -> Bar:
    return Bar(
        bar_at=WINDOW_START + timedelta(minutes=minute),
        open=Decimal(open_),
        high=Decimal(high),
        low=Decimal(low),
        close=Decimal(close),
    )


def flat(count: int, price: str = "100") -> list[Bar]:
    return [bar(index, open_=price, high=price, low=price, close=price) for index in range(count)]


def run(bars: list[Bar], threshold: Decimal = THRESHOLD, min_bars: int = 15):
    return detect(
        bars,
        symbol="KOSPI",
        threshold_pct=threshold,
        window_start=WINDOW_START,
        window_end=WINDOW_END,
        min_bars=min_bars,
    )


def test_a_quiet_window_produces_no_event():
    assert run(flat(30)) is None


def test_a_drop_exactly_at_the_threshold_is_captured():
    bars = flat(29)
    # 100의 고점 대비 저가 98.00 = 정확히 -2.00%. 경계는 **안쪽**이다.
    bars.append(bar(29, open_="100", high="100", low="98", close="98"))

    event = run(bars)

    assert event is not None
    assert event.direction is Direction.DROP
    assert event.move_pct == Decimal("-2.0000")
    assert event.extreme_price == Decimal(100)
    assert event.trigger_price == Decimal(98)


def test_a_drop_just_under_the_threshold_is_not_captured():
    bars = flat(29)
    bars.append(bar(29, open_="100", high="100", low="98.01", close="98.01"))

    assert run(bars) is None


def test_a_surge_exactly_at_the_threshold_is_captured():
    bars = flat(29)
    bars.append(bar(29, open_="100", high="102", low="100", close="102"))

    event = run(bars)

    assert event is not None
    assert event.direction is Direction.SURGE
    assert event.move_pct == Decimal("2.0000")
    assert event.extreme_price == Decimal(100)
    assert event.trigger_price == Decimal(102)


def test_one_wide_bar_is_not_both_a_drop_and_a_surge():
    """봉 하나의 고가-저가 폭은 신호가 아니다. **회귀 테스트다.**

    극값에 그 봉 자신을 넣으면 100에서 98로 떨어진 봉이 자기 저가를 최저점으로 삼아
    `98 → 100`을 +2.04% 급등으로도 읽는다. 큰 음봉이 전부 급등으로 잡혔다
    (2026-09-04 구현 중 발견).
    """
    bars = flat(29)
    bars.append(bar(29, open_="100", high="100", low="98", close="98"))

    event = run(bars)

    assert event is not None
    assert event.direction is Direction.DROP


def test_an_intrabar_swing_alone_never_triggers():
    """마지막 봉 하나가 3% 왕복해도 직전 봉들이 조용하면 사건이 아니다."""
    bars = flat(29)
    bars.append(bar(29, open_="100", high="101.5", low="98.5", close="100"))

    assert run(bars) is None


def test_the_extreme_always_precedes_the_trigger_bar():
    """고점이 저점보다 뒤에 온 창은 하락으로 안 읽힌다.

    창 안의 봉이 전부 판정 시점 이하라서 순서가 구조적으로 보장된다. 이 테스트는 그
    보장을 잠근다 — 단순 min/max로 바꾸면 여기서 깨진다.
    """
    bars = flat(15, "100")
    # 먼저 98까지 떨어지고(=하락 아님, 직전 고점이 100이라 -2.00%) 그 뒤 고점이 105로 뛴다.
    bars = flat(14, "100")
    bars.append(bar(14, open_="100", high="100", low="99", close="99"))
    bars += [bar(index, open_="105", high="105", low="105", close="105") for index in range(15, 30)]

    event = run(bars)

    # 99 → 105는 저점 대비 +6.06%라 급등으로 잡힌다. 105 고점을 99에 소급 적용하지 않는다.
    assert event is not None
    assert event.direction is Direction.SURGE
    assert event.extreme_price == Decimal(99)


def test_the_first_bar_that_crosses_wins():
    bars = flat(20)
    bars.append(bar(20, open_="100", high="100", low="97", close="97"))
    bars.append(bar(21, open_="97", high="97", low="90", close="90"))
    bars += [bar(index, open_="90", high="90", low="90", close="90") for index in range(22, 30)]

    event = run(bars)

    assert event is not None
    assert event.detected_at == WINDOW_START + timedelta(minutes=20)
    assert event.move_pct == Decimal("-3.0000")


def test_a_window_with_too_few_bars_is_not_judged():
    """창이 덜 차면 판정하지 않는다. 두 봉으로 '30분 창'이라고 부르면 그 값이 거짓이다."""
    bars = [bar(0, open_="100", high="100", low="100", close="100"), bar(1, open_="100", high="100", low="90", close="90")]

    assert run(bars) is None


def test_window_change_uses_the_first_open_and_the_last_close():
    bars = [
        bar(0, open_="100", high="101", low="99", close="100"),
        bar(1, open_="100", high="105", low="95", close="102"),
    ]

    assert window_change_pct(bars) == Decimal("2.0000")


def test_window_change_is_none_without_bars():
    assert window_change_pct([]) is None


def test_the_measured_2026_09_03_drop_is_captured():
    """실측 사건 하나. 13:51 고점 6,661.04 → 14:16 저가 6,527.70 = -2.00%."""
    bars = [bar(index, open_="6661.04", high="6661.04", low="6650", close="6655") for index in range(29)]
    bars.append(bar(29, open_="6540", high="6545", low="6527.70", close="6530"))

    event = run(bars)

    assert event is not None
    assert event.direction is Direction.DROP
    assert event.move_pct == Decimal("-2.0018")
    assert event.bar_count == 30


def test_a_peer_without_enough_bars_is_reported_as_missing():
    """0%로 채우지 않는다. '안 움직였다'와 '못 봤다'를 섞으면 포착이 거짓말을 한다."""
    peer = peer_move(PEER_SPECS["NIKKEI225"], flat(2), min_bars=15)

    assert peer.available is False
    assert peer.change_pct is None
    assert peer.bars == 2
    assert peer.label == "닛케이225"


def test_a_peer_with_enough_bars_reports_its_window_change():
    bars = [bar(index, open_="100", high="100", low="100", close="100") for index in range(29)]
    bars.append(bar(29, open_="99", high="99", low="99", close="99"))

    peer = peer_move(PEER_SPECS["SSE_COMP"], bars, min_bars=15)

    assert peer.available is True
    assert peer.change_pct == Decimal("-1.0000")
    assert peer.label == "상해종합"


def test_us_futures_are_peers_because_the_us_cash_market_is_closed():
    """한국 장중(09:00~15:30 KST)에 미국 현물장은 닫혀 있다. 그 시간에 움직이는 것이 선물이다."""
    us = [spec for spec in PEER_SPECS.values() if spec.region is PeerRegion.US]

    assert {spec.symbol for spec in us} == {"SP500_FUT", "NASDAQ100_FUT"}
    assert all(spec.table == "index_future_bar" for spec in us)
    # 코스닥은 뺐다 — 같은 나라라 "한국만의 재료인가"에 답이 못 된다.
    assert "KOSDAQ" not in PEER_SPECS
