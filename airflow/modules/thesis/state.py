"""프롬프트에 들어가고 `thesis.input_state`에 저장되는 상태의 **모양**.

`dict[str, Any]`로 주고받지 않는다. 이 상태는 두 경계를 넘는다 — LLM 프롬프트와 JSONB
컬럼이다. 맨 dict로 흘리면 키 오타가 런타임까지 살아 있고, 프롬프트에 빈 칸이 실려도 아무도
못 잡는다. 나중에 `input_state`를 읽는 SQL(기술지표 문서 14.4절)이 무슨 키를 기대해도
되는지도 코드에 안 남는다.

**이 모듈은 LangChain·Airflow·DB를 import하지 않는다.** `thesis.py`(LangChain)와
`thesis/common.py`(Airflow) 둘이 모듈 수준에서 이것을 import하는데, 저 둘은 서로를 모듈
수준에서 import할 수 없기 때문이다(DagBag 30초 타임아웃, `thesis.common` docstring).

값을 만드는 것은 `thesis.common.observed_state`·`technical_state`와 `thesis.past_theses`이고,
JSON으로 바꾸는 것은 프롬프트 조립과 저장이다(`model_dump(mode="json")`).
"""

from datetime import date, time
from enum import StrEnum

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field


class RunSlot(StrEnum):
    """추론을 만든 슬롯. 슬롯이 곧 추론의 종류다.

    `thesis.py`가 아니라 여기 있는 것은 슬롯을 아는 코드가 셋으로 갈려 있기 때문이다 —
    LangChain을 끄는 `thesis.py`, Airflow를 끄는 `thesis/common.py`, 그리고 슬롯 모듈 셋이다.
    감쌀 의존성이 없는 값이라 방화벽 쪽에 두면 셋 다 그대로 본다. `thesis.py`가 재수출하므로
    부르는 쪽은 전과 같다.

    **값은 시각이 아니라 뜻으로 짓는다.** 장중 슬롯의 시각은 `TUNING.md` 3절이 당기라고
    적어 둔 손잡이라, `intraday_1035` 같은 이름은 시각을 30분 옮기는 순간 거짓이 된다.

    값은 `apps/models/analysis/thesis.py`의 같은 이름 enum과 같아야 한다.
    `tests/models/test_analysis_models.py`가 대조한다.
    """

    PRE_OPEN = "pre_open"
    INTRADAY_MORNING = "intraday_morning"
    INTRADAY_MIDDAY = "intraday_midday"
    INTRADAY_AFTERNOON = "intraday_afternoon"
    PRE_CLOSE = "pre_close"
    POST_CLOSE = "post_close"
    POST_NXT_CLOSE = "post_nxt_close"


# 예측 슬롯. 채점(`thesis_outcome`의 Brier)이 붙는 슬롯이 이것뿐이다 — 나머지 둘은 이미
# 일어난 일의 해석이라 맞고 틀림을 물을 대상이 없다. 목록의 원본이 여기 하나이고
# `select_pending_grades.sql`이 파라미터로 받는다.
FORECAST_SLOTS: tuple[RunSlot, ...] = (
    RunSlot.PRE_OPEN,
    RunSlot.INTRADAY_MORNING,
    RunSlot.INTRADAY_MIDDAY,
    RunSlot.INTRADAY_AFTERNOON,
    RunSlot.PRE_CLOSE,
)

# 장중 슬롯. 기준가가 전일 종가가 아니라 `as_of` 직전 봉이라 채점 조회가 갈린다.
INTRADAY_SLOTS: tuple[RunSlot, ...] = (
    RunSlot.INTRADAY_MORNING,
    RunSlot.INTRADAY_MIDDAY,
    RunSlot.INTRADAY_AFTERNOON,
    RunSlot.PRE_CLOSE,
)

# 사후 해설을 받는 슬롯. 애프터마켓은 아직 빠져 있다(`7-nxt-review.md` 3절).
NARRATED_SLOTS: tuple[RunSlot, ...] = (*FORECAST_SLOTS, RunSlot.POST_CLOSE)

# 장전·장중이 프롬프트에 되돌아보는 슬롯. 해설 대상보다 애프터마켓 리뷰 하나가 더 있다.
#
# **목록이 둘인 이유는 뜻이 둘이기 때문이다.** 애프터마켓 리뷰는 채점도 해설도 없어 해설
# 루프 밖이지만(`NARRATED_SLOTS`), "정규장이 닫힌 뒤 무슨 재료가 나왔고 시장이 처음에 어느
# 쪽으로 읽었나"를 담고 있어 다음날 아침이 볼 값어치가 있다. 한 상수로 두 뜻을 지면 그것을
# 보여 주려고 해설 루프까지 늘려야 한다(`18-nxt-precedent.md` 2.1절).
#
# `select_past_with_outcomes.sql`이 파라미터로 받는다.
PRECEDENT_SLOTS: tuple[RunSlot, ...] = (*NARRATED_SLOTS, RunSlot.POST_NXT_CLOSE)

# 장중 슬롯의 기준 시각(KST). **분기가 아니라 표다** — 슬롯 값이 인자로 흘러 시각 하나를
# 고르는 것이고, 슬롯으로 코드 경로가 갈리지 않는다.
#
# 여기 있는 이유는 이 값을 보는 곳이 둘이기 때문이다 — Airflow를 끄는 `thesis.intraday`가
# 기준 시각을 만들고, LangChain을 끄는 `thesis.domain`이 사람이 읽는 라벨을 만든다.
# 저 둘은 서로를 모듈 수준에서 import할 수 없다.
#
# **DAG의 cron과 이 표가 같아야 한다.** 어긋나면 `resolve_slot`이 슬롯을 못 찾아 실행이
# 죽는다 — 조용히 다른 슬롯으로 떨어지는 것보다 낫다. 테스트가 둘을 대조한다.
#
# **넷이던 장중 슬롯이 하나로 줄었다.** `intraday_afternoon`(14:35)은 2026-08-27에
# `pre_close`(15:00)와 25분 차이라 뺐고, `intraday_morning`(10:35)과 `pre_close`는
# 2026-08-28에 뺐다 — 채점 84건에서 그 둘의 Brier가 0.721·0.798로 균등 추측(0.667)보다
# 나빴다. 남은 `intraday_midday`(0.595)와 `pre_open`(0.634)만 균등보다 낫다.
#
# 값은 전부 `RunSlot`에 남는다 — 이미 저장된 행을 채점·해설하고 조회하는 쪽이 그 값을 읽는다.
INTRADAY_SLOT_TIMES: dict[RunSlot, time] = {
    RunSlot.INTRADAY_MIDDAY: time(12, 35),
}


class IndexObservation(BaseModel):
    """지수 하나의 세션 마감. 등락률이 없는 봉은 부르는 쪽이 이미 걸렀다."""

    model_config = ConfigDict(frozen=True)

    close: float
    return_pct: float


class StockObservation(BaseModel):
    """종목 하나의 확정 종가. 등락률은 담지 않는다 — 기준가가 슬롯마다 달라서다."""

    model_config = ConfigDict(frozen=True)

    close: float


class IntradayObservation(BaseModel):
    """장중 대상 하나의 **기준 시각 현재가**. 확정 종가가 아니다.

    `bar_at`을 함께 담는 이유는 그것이 값의 절반이기 때문이다 — 10:35 슬롯이 보는 봉은
    10:30 봉이고, 수집이 밀리면 10:20 봉일 수도 있다. 어느 봉을 봤는지가 프롬프트에
    KST로 실려야 모델이 "지금"을 정확히 읽는다.

    `return_pct`는 **전일 종가 대비**다(봉의 `previous_close`). 예측의 기준가인
    `price`와 축이 다르다 — 이쪽은 "오늘 여기까지 얼마나 왔나"이고 예측은 "여기서
    마감까지"다.
    """

    model_config = ConfigDict(frozen=True)

    price: float
    return_pct: float
    bar_at: AwareDatetime


class AfterHoursObservation(BaseModel):
    """NXT 애프터마켓 종목 하나. 정규장 마감 뒤 무엇이 움직였나를 말한다."""

    model_config = ConfigDict(frozen=True)

    close: float
    return_pct: float
    last_bar_at: AwareDatetime
    bars: int


class HorizonBaseRate(BaseModel):
    """한 지평의 실현 분포. 비율은 0~1이고 셋의 합이 1이다.

    `sample_size`가 `base_rate.MIN_BASE_RATE_SAMPLE` 아래면 비율 넷이 전부 `None`이다.
    표본이 모자라 **재지 않았다**는 뜻이고 0이라는 뜻이 아니다 — 0으로 채우면 모델이
    "그런 적이 없다"로 읽는다.
    """

    model_config = ConfigDict(frozen=True)

    horizon_days: int
    sample_size: int
    up: float | None = None
    flat: float | None = None
    down: float | None = None
    median_return_pct: float | None = None


class SignalBaseRate(BaseModel):
    """한 (심볼, 종류, 방향)의 지평별 기저율과 같은 심볼의 무조건 기저.

    **둘을 같은 객체에 담는다.** 조건부만 보면 그 심볼의 평소 분포를 모른 채 읽게 된다 —
    신호 뒤 상승 60퍼센트라도 평소가 55퍼센트면 그 신호가 더하는 것은 5퍼센트포인트다.

    값을 만드는 것은 `modules/technical/base_rate.py`다. 모델이 여기 있는 것은 그 모듈이 DB와 SQL
    파일을 import하기 때문이다 — 이 모듈의 방화벽(모듈 docstring)을 지켜야 한다.
    """

    model_config = ConfigDict(frozen=True)

    conditional: tuple[HorizonBaseRate, ...] = ()
    unconditional: tuple[HorizonBaseRate, ...] = ()


class SignalObservation(BaseModel):
    """기술적 매매 신호 하나. **사건이지 판정이 아니다.**

    `ref`가 있어 모델이 `claims`로 인용할 수 있다(기술지표 문서 14.3절).

    `base_rate`는 **같은 종류·방향의 신호가 과거에 어떻게 끝났나**다. 사건만 주고 확률을
    요구하면 모델이 서사로 빈도를 지어낸다. 툴이 아니라 여기 붙는 이유는, 프롬프트가
    "지표만으로 확률을 기울이지 마라"라고 가르치고 있어 기술 쪽 툴을 덜 부르기 때문이다
    (`docs/analysis/market-thesis/10-base-rate.md` 6절). 사건이 없으면 `None`이다.
    """

    model_config = ConfigDict(frozen=True)

    ref: str
    signal_date: date
    kind: str
    direction: str
    base_rate: SignalBaseRate | None = None


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
    """장전·장중·장후 슬롯이 프롬프트에 싣는 관측 상태.

    `session`이 `None`이면 볼 세션이 없다(휴장·미판정). 그때는 나머지가 전부 비어 있다.

    **`index`·`stock`과 `intraday`는 배타적이다.** 앞의 둘은 확정된 세션의 마감값이고
    `intraday`는 아직 안 끝난 세션의 현재가다. 장중 슬롯은 `session`을 채우지 않는다 —
    오늘은 아직 마감이 없어 마감값으로 읽히면 안 된다.
    """

    model_config = ConfigDict(frozen=True)

    session: date | None = None
    index: dict[str, IndexObservation] = Field(default_factory=dict)
    stock: dict[str, StockObservation] = Field(default_factory=dict)
    intraday: dict[str, IntradayObservation] = Field(default_factory=dict)
    # 그 세션 정규장이 닫힌 뒤 NXT 애프터마켓(15:30~20:00) 마감가. **장전만 채운다** —
    # 장후·장중은 기준 시각이 15:30 이전이라 이 값이 미래다.
    #
    # 등락률의 분모는 `stock`과 같은 정규 종가라 두 칸을 나란히 읽을 수 있다. 애프터 방향이
    # 다음날 정규장으로 이어지는 것은 56퍼센트라(2026-08-31 실측) 가격 신호가 아니라 "마감 뒤
    # 재료에 대한 첫 반응"으로 읽어야 하고, 그 사실은 프롬프트가 말한다
    # (`18-nxt-precedent.md` 0.1절). NXT에 지수가 없어 종목만이다.
    after_hours: dict[str, AfterHoursObservation] = Field(default_factory=dict)
    technical: TechnicalState = TechnicalState()
    # 심볼별 `flat` 기준선(최근 `base_rate.FLAT_BASE_RATE_BARS`봉의 하루 등락 분포).
    #
    # **상수가 아니라 실행마다 잰 값이다.** 프롬프트가 "코스피 6퍼센트가 실제 flat 비율"이라고
    # 상수로 가르치던 것을 여기로 옮겼다 — 2026-08-26 실측에서 그 비율이 연도별로 단조
    # 감소해(2016년 45퍼센트 → 2026년 6퍼센트) 상수가 반년 만에 낡았기 때문이다.
    #
    # `input_state`에 함께 저장되므로 "그때 어떤 기준선을 줬나"가 기록에 남는다. 봉이 모자란
    # 심볼은 키가 없다.
    flat_base_rate: dict[str, HorizonBaseRate] = Field(default_factory=dict)


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
    # 심볼별 `flat` 기준선(최근 `base_rate.FLAT_BASE_RATE_BARS`봉의 하루 등락 분포).
    #
    # **상수가 아니라 실행마다 잰 값이다.** 프롬프트가 "코스피 6퍼센트가 실제 flat 비율"이라고
    # 상수로 가르치던 것을 여기로 옮겼다 — 2026-08-26 실측에서 그 비율이 연도별로 단조
    # 감소해(2016년 45퍼센트 → 2026년 6퍼센트) 상수가 반년 만에 낡았기 때문이다.
    #
    # `input_state`에 함께 저장되므로 "그때 어떤 기준선을 줬나"가 기록에 남는다. 봉이 모자란
    # 심볼은 키가 없다.
    flat_base_rate: dict[str, HorizonBaseRate] = Field(default_factory=dict)


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

    `written`을 읽는 코드는 없다. Airflow UI의 XCom 화면에서 그 실행이 몇 건을 썼는지
    보는 값이라 남긴다.
    """

    model_config = ConfigDict(frozen=True)

    run_date: date
    slot: RunSlot
    written: int


class PastThesis(BaseModel):
    """지난 추론 하나와 그 결과. 피드백 루프가 프롬프트에 싣는 것이다.

    `id`는 `thesis_precedent` 엣지가 되므로 그대로 들고 간다.

    `run_slot`은 `pre_open`(그날의 예측, 채점이 붙는다)과 `post_close`(장이 닫힌 뒤의 해석,
    채점 없이 해설·판정만 붙는다)를 가른다. **모델이 이 둘을 구분해야 하므로 값으로 싣는다** —
    채점이 없는 행을 "빗나간 예측"으로 읽으면 안 된다.
    """

    model_config = ConfigDict(frozen=True)

    id: int
    run_slot: RunSlot
    run_date: date
    prob_up: float
    prob_down: float
    prob_flat: float
    up_reasoning: str
    down_reasoning: str
    flat_reasoning: str
    outcomes: tuple[PastOutcome, ...] = ()


class SameDayThesis(BaseModel):
    """오늘 앞 슬롯의 추론 하나와 **그 뒤 실현 등락**. 장중 슬롯의 되짚기다.

    **`thesis_outcome`에 저장하지 않는다.** 정식 채점은 확정 종가가 필요해 18:10 전에는
    설 수 없고, 장중에 유일하게 가능한 목표(뒤쪽 봉)는 "KRX 영업일 수"와 단위가 다른 새
    지평 축이라 CHECK·임계값·집계 조회가 전부 갈린다. 다음 슬롯이 실제로 필요한 것은
    Brier가 아니라 "아침에 상승 62%라 했는데 지금 -0.4%" 한 줄이고, 그건 봉만 있으면
    프롬프트 조립 시점에 계산된다.

    `base_price`는 그 슬롯이 **채점될 때 쓰일 기준가와 같다.** `pre_open`이면 전일 종가,
    장중이면 그 슬롯 `as_of_at` 직전 봉의 close다. 여기서 다른 기준을 쓰면 프롬프트가
    보여 준 성적과 밤에 매겨질 점수가 어긋난다.
    """

    model_config = ConfigDict(frozen=True)

    run_slot: RunSlot
    as_of_at: AwareDatetime
    prob_up: float
    prob_down: float
    prob_flat: float
    up_reasoning: str
    down_reasoning: str
    flat_reasoning: str
    base_price: float
    current_price: float
    return_pct: float
