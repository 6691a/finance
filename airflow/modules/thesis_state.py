"""프롬프트에 들어가고 `thesis.input_state`에 저장되는 상태의 **모양**.

`dict[str, Any]`로 주고받지 않는다. 이 상태는 두 경계를 넘는다 — LLM 프롬프트와 JSONB
컬럼이다. 맨 dict로 흘리면 키 오타가 런타임까지 살아 있고, 프롬프트에 빈 칸이 실려도 아무도
못 잡는다. 나중에 `input_state`를 읽는 SQL(기술지표 문서 14.4절)이 무슨 키를 기대해도
되는지도 코드에 안 남는다.

**이 모듈은 LangChain·Airflow·DB를 import하지 않는다.** `thesis.py`(LangChain)와
`thesis_common.py`(Airflow) 둘이 모듈 수준에서 이것을 import하는데, 저 둘은 서로를 모듈
수준에서 import할 수 없기 때문이다(DagBag 30초 타임아웃, `thesis_common` docstring).

값을 만드는 것은 `thesis_common.observed_state`·`technical_state`와 `thesis.past_theses`이고,
JSON으로 바꾸는 것은 프롬프트 조립과 저장이다(`model_dump(mode="json")`).
"""

from datetime import date

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field


class IndexObservation(BaseModel):
    """지수 하나의 세션 마감. 등락률이 없는 봉은 부르는 쪽이 이미 걸렀다."""

    model_config = ConfigDict(frozen=True)

    close: float
    return_pct: float


class StockObservation(BaseModel):
    """종목 하나의 확정 종가. 등락률은 담지 않는다 — 기준가가 슬롯마다 달라서다."""

    model_config = ConfigDict(frozen=True)

    close: float


class AfterHoursObservation(BaseModel):
    """NXT 애프터마켓 종목 하나. 정규장 마감 뒤 무엇이 움직였나를 말한다."""

    model_config = ConfigDict(frozen=True)

    close: float
    return_pct: float
    last_bar_at: AwareDatetime
    bars: int


class SignalObservation(BaseModel):
    """기술적 매매 신호 하나. **사건이지 판정이 아니다.**

    `ref`가 있어 모델이 `claims`로 인용할 수 있다(기술지표 문서 14.3절).
    """

    model_config = ConfigDict(frozen=True)

    ref: str
    signal_date: date
    kind: str
    direction: str


class TechnicalObservation(BaseModel):
    """대상 하나의 기술적 관측. **절대값이 아니라 비율이다.**

    `sma20=3160.2`를 주면 모델이 종가와 비교하는 계산을 해야 하고 그 계산은 틀릴 수 있다.
    절대값이 필요하면 `daily_history` 툴이 있다(기술지표 문서 14.1절).
    """

    model_config = ConfigDict(frozen=True)

    close_vs_sma20_pct: float
    sma20_vs_sma60_pct: float
    rsi14: float
    macd_histogram: float
    volume_ratio20: float | None = None
    recent_signals: tuple[SignalObservation, ...] = ()


class TechnicalState(BaseModel):
    """기술적 관측 블록 전체.

    **`as_of_date`가 블록에 하나다.** 프롬프트가 이 칸을 가리켜 "이 날짜 마감 기준"이라고
    알린다 — 슬롯의 cutoff 때문에 브리핑 시각과 다를 수 있다.

    **지표를 못 낸 대상은 `subjects`에 `None`으로 남는다.** 키를 빼거나 0으로 채우면 모델이
    "지표가 중립"으로 읽는다.
    """

    model_config = ConfigDict(frozen=True)

    as_of_date: date | None = None
    subjects: dict[str, TechnicalObservation | None] = Field(default_factory=dict)


class ObservedState(BaseModel):
    """장전·장후 두 슬롯이 프롬프트에 싣는 관측 상태.

    `session`이 `None`이면 볼 세션이 없다(휴장·미판정). 그때는 나머지가 전부 비어 있다.
    """

    model_config = ConfigDict(frozen=True)

    session: date | None = None
    index: dict[str, IndexObservation] = Field(default_factory=dict)
    stock: dict[str, StockObservation] = Field(default_factory=dict)
    technical: TechnicalState = TechnicalState()


class NxtObservedState(BaseModel):
    """NXT 애프터마켓 리뷰의 관측 상태. **대상이 종목뿐이다** — NXT에 지수가 없다.

    지수를 `index_regular`라는 이름으로 주는 이유는 subject가 아니라 맥락이라는 사실이
    키에서 보여야 모델이 지수에 대한 추론을 쓰지 않기 때문이다.
    """

    model_config = ConfigDict(frozen=True)

    session: date
    regular: dict[str, StockObservation] = Field(default_factory=dict)
    after_hours: dict[str, AfterHoursObservation] = Field(default_factory=dict)
    index_regular: dict[str, IndexObservation] = Field(default_factory=dict)
    technical: TechnicalState = TechnicalState()


class PastOutcome(BaseModel):
    """과거 추론 하나의 지평별 채점과 해설. 아직 안 온 지평은 목록에 없다."""

    model_config = ConfigDict(frozen=True)

    horizon_days: int
    actual_return_pct: float | None = None
    actual_outcome: str | None = None
    brier_score: float | None = None
    verdict: str | None = None
    narrative: str | None = None


class ThesisRunResult(BaseModel):
    """추론 태스크 한 번의 결과. **XCom을 건너 다음 태스크가 읽는다.**

    `slot`이 `RunSlot`이 아니라 `str`인 것은 이 모듈이 `thesis.py`(LangChain)를 모듈 수준에서
    import할 수 없어서다. 읽는 쪽이 `RunSlot(result.slot)`으로 되돌린다.

    `written`을 읽는 코드는 없다. Airflow UI의 XCom 화면에서 그 실행이 몇 건을 썼는지
    보는 값이라 남긴다.
    """

    model_config = ConfigDict(frozen=True)

    run_date: date
    slot: str
    written: int


class PastThesis(BaseModel):
    """지난 장전 추론 하나와 그 결과. 피드백 루프가 프롬프트에 싣는 것이다.

    `id`는 `thesis_precedent` 엣지가 되므로 그대로 들고 간다.
    """

    model_config = ConfigDict(frozen=True)

    id: int
    run_date: date
    prob_up: float
    prob_down: float
    prob_flat: float
    up_reasoning: str
    down_reasoning: str
    flat_reasoning: str
    outcomes: tuple[PastOutcome, ...] = ()
