"""프롬프트에 실리는 관측 상태의 모양.

**전부 코드가 SQL·Cypher로 만든다.** 모델이 만드는 것은 여기 하나도 없다. 이 모델들이
그대로 `kospi_forecast.input_state` JSONB가 되므로, 나중에 "그때 무엇을 보고 그렇게 답했나"를
행 하나로 되짚을 수 있다.

**`dict[str, Any]`를 쓰지 않는다.** 키 오타가 프롬프트나 JSONB로 흘러가면 잡아 줄 자리가
없다. JSON으로 바꾸는 것은 경계에서 한 번뿐이다(`model_dump(mode="json")`).

이 모듈은 `domain`만 import한다. LangChain도 Airflow도 보지 않아 노트북과 테스트가
가볍게 읽는다.
"""

from datetime import date
from decimal import Decimal

from pydantic import BaseModel, ConfigDict

from modules.kospi.domain import Direction, Factor, ObservationSign, RunSlot


class _State(BaseModel):
    """관측 상태는 전부 불변이다. 재시도 경로에서 바뀌면 저장된 것과 모델이 본 것이 어긋난다."""

    model_config = ConfigDict(frozen=True)


class DailyBar(_State):
    """확정 일봉 하루. `index_daily`의 한 행이다."""

    business_date: date
    open: Decimal
    close: Decimal
    # 그 날의 등락률. 전일 종가 대비이고 SQL이 계산한다.
    change_pct: Decimal | None = None


class MoveBaseline(_State):
    """최근 일봉이 실제로 얼마나 움직였나. **모델이 크기를 부를 때 딛고 설 자리다.**

    프롬프트가 "최근 진폭에서 출발하라"고만 말하면 모델은 창 안의 열다섯 봉을 눈대중한다.
    2026-09-03 실측에서 그 결과가 중앙값 2.27퍼센트인 시장에 폭 1.00퍼센트포인트였다 —
    구조적으로 못 맞히는 값이라 채점이 뜻을 잃는다.

    **표본이 모자라면 칸이 전부 `None`이다.** 0으로 채우지 않는다.
    """

    observations: int = 0
    # |등락|의 분위수. 폭(`band_pct`)의 출발점이다.
    abs_p25: Decimal | None = None
    abs_p50: Decimal | None = None
    abs_p75: Decimal | None = None
    abs_p90: Decimal | None = None
    # 방향별 조건부 크기. 프롬프트가 요구하는 것이 "오른다면 얼마"라서 나눈다.
    up_median: Decimal | None = None
    down_median: Decimal | None = None
    # 그 창에서 오른 날의 비율. **기저율이지 오늘의 근거가 아니다** — 프롬프트가 그것을 밝힌다.
    up_day_ratio: Decimal | None = None


class RelationRow(_State):
    """관계 표의 한 줄. 가중치는 코드가 관측에서 계산한 값이다.

    **`recent_signs`가 가중치와 함께 가는 이유**는 가중치 하나로 "오래 일관된 -0.5"와
    "막 뒤집히는 중인 -0.15"가 구분되지 않기 때문이다. 프롬프트가 그 뜻을 밝힌다.
    """

    factor: Factor
    label: str
    weight: float
    n_obs: int
    last_date: date | None = None
    last_note: str = ""
    recent_signs: tuple[ObservationSign, ...] = ()


class MemoryRow(_State):
    """활성 메모 하나. 전망이 읽고 관찰이 판정한다.

    **사실이 아니라 지난 관찰의 메모다.** 프롬프트가 그것을 밝히고, 전망이 이것을 근거로
    쓰면 `memory_id`로 인용한다.
    """

    id: int
    created_on: date
    text: str
    factor: Factor | None = None
    verify_count: int = 0


class FlowRow(_State):
    """오늘 그 시각까지의 투자자별 누적 순매수(주). 장중 슬롯만 본다.

    **수량이다.** `market_investor_flow_snapshot`의 금액 칸은 모델 주석이 "단위 미확정"이라
    원이라고 부르면 거짓이 된다.

    **확정 일별 표가 아니다** — 시장 단위는 장중 누적 스냅샷뿐이라 그 시각 최신 행을 쓴다.
    """

    observed_at: str
    foreign_net_buy_qty: float | None = None
    institution_net_buy_qty: float | None = None
    individual_net_buy_qty: float | None = None


class IntradayState(_State):
    """장중 슬롯만 보는 것. 장전에는 이 블록이 통째로 없다.

    `so_far_pct`와 예측 축을 가르는 것이 이 블록의 핵심이다 — `base_price`가 지금 가격이고
    답은 거기서 마감까지인데, `so_far_pct`는 전일 종가 대비 지금까지다. 프롬프트가 둘을
    가르지 않으면 모델이 이미 일어난 것을 남은 것으로 다시 센다.
    """

    open: Decimal | None = None
    high: Decimal | None = None
    low: Decimal | None = None
    # 전일 종가 대비 현재가 등락률. "지금까지 얼마나 왔나"다.
    so_far_pct: Decimal | None = None
    flows: FlowRow | None = None


class EarlierReason(_State):
    """앞 슬롯 이유 하나. **요인 코드를 문장과 함께 싣는다.**

    문장만 주면 모델이 "S&P500이 올랐다"는 글을 읽고도 그것이 `SP500` 요인이라는 것을 다시
    짐작해야 한다. 코드가 붙어 있으면 장중 슬롯이 그 요인을 `factor_history`로 다시 불러
    "아직 살아 있나"를 확인할 수 있다(2026-09-04 — 장전 이유 14건 중 상방 재료 여섯이
    장중에 말없이 사라진 것이 계기다).
    """

    direction: Direction
    statement: str
    factor: Factor | None = None
    memory_id: int | None = None


class EarlierSlot(_State):
    """오늘 앞선 슬롯이 낸 답. 장중 슬롯이 본다.

    **정답이 아니라 그때의 판단이다.** 프롬프트가 그것을 밝히고, 이유가 이것을 이어받으면
    `slot_ref`로 인용한다.
    """

    slot: RunSlot
    as_of_kst: str
    direction: Direction
    expected_change_pct: Decimal
    band_pct: Decimal
    base_price: Decimal
    reasons: tuple[EarlierReason, ...] = ()


class ObservedState(_State):
    """전망 하나가 보는 것 전부. 슬롯 셋이 같은 모델을 쓰고 칸이 몇 개 비거나 찬다."""

    run_date: date
    slot: RunSlot
    # `2026-09-02 08:35 KST`. **UTC ISO를 싣지 않는다**(모델이 날짜를 하루 어긋나게 읽는다).
    as_of_kst: str
    # 등락률의 분모와 그 값의 시각.
    base_price: Decimal
    base_at_kst: str
    # 기준가가 무엇인지 한 줄. 슬롯마다 달라 모델이 축을 헷갈리지 않게 명시한다.
    base_note: str
    bars: tuple[DailyBar, ...] = ()
    moves: MoveBaseline | None = None
    relations: tuple[RelationRow, ...] = ()
    memories: tuple[MemoryRow, ...] = ()
    intraday: IntradayState | None = None
    earlier_slots: tuple[EarlierSlot, ...] = ()


class GradedForecast(_State):
    """오늘 슬롯 하나의 전망과 그 채점. 장후 관찰이 본다."""

    slot: RunSlot
    direction: Direction
    expected_change_pct: Decimal
    band_pct: Decimal
    base_price: Decimal
    reasons: tuple[str, ...] = ()
    actual_change_pct: Decimal | None = None
    hit: bool | None = None
    within_band: bool | None = None


class ReviewState(_State):
    """장후 관찰이 보는 것 전부.

    전망과 달리 **오늘 종가를 안다.** 맞히는 것이 목적이 아니라 무엇이 움직였는지를
    적는 것이 목적이라, 결과를 아는 편이 낫다.
    """

    run_date: date
    as_of_kst: str
    close: Decimal
    previous_close: Decimal
    # 전일 종가 대비 오늘 종가 등락률.
    change_pct: Decimal
    bars: tuple[DailyBar, ...] = ()
    relations: tuple[RelationRow, ...] = ()
    memories: tuple[MemoryRow, ...] = ()
    forecasts: tuple[GradedForecast, ...] = ()
