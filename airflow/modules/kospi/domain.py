"""코스피 일일 전망의 어휘와 셈 — 요인, 슬롯, 상한, 채점 순수 함수.

**이 모듈은 LangChain·LangGraph·Airflow를 import하지 않는다.** 경계값을 DB 없이 테스트할 수
있어야 하고, 요인 목록 하나를 보려고 무거운 의존성이 딸려 오면 안 된다.

설계는 `docs/analysis/kospi-forecast.md`에 있다.

## 요인은 고정 어휘다

관계 그래프의 노드가 자유 생성이 아닌 이유는 **요인마다 조회 툴이 있어야 하기 때문**이다.
모델이 `반도체 업황`이라는 요인을 새로 만들면 다음 날 전망이 그 값을 가져올 방법이 없다.
어휘를 열어 두는 것과 값을 볼 수 있는 것 중 뒤를 골랐다.

요인을 더할 때 고치는 자리는 셋이다 — `Factor`에 한 값, `FACTORS`에 한 줄, 설계 문서의
표에 한 줄. 조회 SQL은 `FactorSource`가 이미 넷을 갖고 있어 대부분 늘지 않는다.

## 판정은 코드가 한다

모델이 내는 것은 방향·크기·폭·이유와 관찰(부호·세기)뿐이다. 관계 가중치, 채점, 메모 만료는
전부 여기 순수 함수가 정한다. 그래야 틀렸을 때 "모델이 틀렸나 집계가 틀렸나"를 가를 수 있다.
"""

from datetime import date, datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict

# ---------------------------------------------------------------------------
# 판(版)
# ---------------------------------------------------------------------------

# 전망 프롬프트의 판. **문장을 고치면 올리고 `tests/modules/test_prompt_versions.py`의
# 해시를 같은 커밋에서 갱신한다.** 판이 섞이면 채점이 서로 다른 프롬프트의 결과를 한 판으로
# 센다 — 옛 추론이 8일에 판 여덟으로 아무 것도 못 잰 자리가 그것이다.
#
# 판 1: 첫 판(2026-09-02).
# 판 2: 크기 절을 실측 기준선 위에 다시 썼다(2026-09-03). 판 1은 "최근 진폭에서 출발하라"고만
#       말해 모델이 봉 열다섯을 눈대중했고, 중앙값 이동 2.27퍼센트인 시장에 폭 1.00퍼센트
#       포인트를 불렀다 — 구조적으로 못 맞히는 값이라 폭 채점이 뜻을 잃었다. 이제
#       `ObservedState.moves`가 250봉의 분위수와 방향별 중앙값을 직접 준다.
# 판 3: "폭이 중심값보다 크면 뜻이 없다"를 지웠다(2026-09-03). 그 문장이 틀렸다 — 방향을
#       완벽히 맞혀도 필요한 폭이 2.5~2.9%p인데 기대 크기가 약 2.0이라, 이 시장에서는 폭이
#       중심을 넘어야 정상이다. 백테스트 닷새에서 폭이 1.4~1.8로 눌려 방향이 틀린 날마다
#       폭도 틀렸다(폭 2/5 = 방향 2/5). 튜닝이 아니라 잘못 쓴 문장을 바로잡은 것이다.
PROMPT_VERSION = "4"

# 장후 관찰 프롬프트의 판. 전망과 축이 다르다 — 저쪽은 "잘 맞혔나", 이쪽은 "관계를 잘
# 읽었나"다. 따로 올린다.
REVIEW_PROMPT_VERSION = "1"


class KospiError(RuntimeError):
    """전망·관찰이 쓸 수 있는 답을 못 얻었다. 다시 불러도 같은 결과다."""


class KospiNotReady(RuntimeError):
    """선행 DAG의 데이터가 아직 없다. 재시도하면 풀릴 수 있다."""


class ToolLimitExceeded(RuntimeError):
    """툴 상한을 넘었거나 모르는 툴이다. 모델이 고쳐 부를 수 있는 오류다."""


# ---------------------------------------------------------------------------
# 슬롯
# ---------------------------------------------------------------------------


class RunSlot(StrEnum):
    """전망을 만든 슬롯. **슬롯이 곧 기준가의 뜻을 정한다.**

    `pre_open`은 전일 종가 대비 오늘 종가를 묻고, 나머지 둘은 **그 시각 현재가 대비**
    오늘 종가를 묻는다. 정답(오늘 KRX 종가)은 셋이 같고 분모만 다르다.

    이름을 시각이 아니라 뜻으로 짓는다. 슬롯 시각은 운영 손잡이여서 30분을 옮기는 순간
    `slot_1135` 같은 이름은 거짓이 된다.
    """

    PRE_OPEN = "pre_open"
    MIDDAY = "midday"
    PRE_CLOSE = "pre_close"


# 슬롯이 도는 시각(KST). **원본은 이 표 하나다** — `dags/kospi_intraday_daily.py`의 cron이
# 이것과 어긋나면 `resolve_slot`이 실행을 죽인다(조용히 다른 슬롯을 도는 것보다 낫다).
# 테스트가 둘을 대조한다.
SLOT_TIMES: dict[RunSlot, time] = {
    RunSlot.PRE_OPEN: time(8, 35),
    RunSlot.MIDDAY: time(11, 35),
    RunSlot.PRE_CLOSE: time(14, 35),
}

# 장중 DAG가 도는 슬롯. 장전은 DAG가 따로다(앞단이 매크로 수집이라 기다리는 것이 다르다).
INTRADAY_SLOTS: tuple[RunSlot, ...] = (RunSlot.MIDDAY, RunSlot.PRE_CLOSE)

SLOT_LABELS: dict[RunSlot, str] = {
    RunSlot.PRE_OPEN: "장전",
    RunSlot.MIDDAY: "장중",
    RunSlot.PRE_CLOSE: "마감전",
}

# KRX 정규장의 양 끝(KST).
OPEN_TIME = time(9, 0)
CLOSE_TIME = time(15, 30)

# 장후 관찰이 도는 시각(KST). `kis_index_daily`(18:20)와 투자자별 매매 확정(18:10) 뒤다.
REVIEW_TIME = time(19, 0)

# 대상은 하나다. 성과가 보이면 늘린다 — 그때 이 상수가 목록이 된다.
INDEX_CODE = "KOSPI"
INDEX_LABEL = "코스피"
INDEX_PROVIDER = "kis"


# ---------------------------------------------------------------------------
# 요인
# ---------------------------------------------------------------------------


class Factor(StrEnum):
    """관계 그래프의 출발 노드. **자유 생성이 아니다**(모듈 docstring 참고)."""

    FOREIGN_NET_BUY = "FOREIGN_NET_BUY"
    INSTITUTION_NET_BUY = "INSTITUTION_NET_BUY"
    INDIVIDUAL_NET_BUY = "INDIVIDUAL_NET_BUY"
    US10Y = "US10Y"
    KTB10Y = "KTB10Y"
    KRBASE = "KRBASE"
    USDKRW = "USDKRW"
    DXY = "DXY"
    SP500 = "SP500"
    NASDAQ = "NASDAQ"
    SOX = "SOX"
    VIX = "VIX"
    WTI = "WTI"
    SAMSUNG = "SAMSUNG"
    SK_HYNIX = "SK_HYNIX"
    NEWS = "NEWS"
    DISCLOSURE = "DISCLOSURE"
    # 코스피 자기 값(봉·장중 기준가·크기 기준선)을 근거로 든 이유가 쓴다. **관계 그래프에는
    # 안 들어간다** — 지수가 자기 자신과 같은 방향인 것은 언제나 참이다. 이 값이 없으면 그런
    # 이유가 `factor=null`로 저장돼 무엇을 근거로 들었는지 뒤에서 가릴 수 없다.
    KOSPI = "KOSPI"


class FactorSource(StrEnum):
    """그 요인의 값이 어느 표에 있나. `factor_history` 툴이 SQL 파일을 이것으로 고른다.

    `DOCUMENT`은 값이 아니라 글이라 `factor_history`가 받지 않는다 — 뉴스와 공시는
    `recent_news`·`recent_disclosures` 툴이 따로 준다.
    """

    QUOTE_DAILY = "quote_daily"
    INDEX_SELF = "index_self"
    INDICATOR = "indicator_observation"
    MARKET_FLOW = "market_investor_flow_snapshot"
    STOCK_DAILY = "stock_investor_trade_daily"
    DOCUMENT = "document"


class FactorUnit(StrEnum):
    """변화를 어떤 단위로 주나. **금리는 퍼센트가 아니다.**

    4.65 → 4.70을 `+1.08%`로 읽는 것이 이 칸이 있는 이유다. 그 숫자는 틀린 것이 아니라
    아무도 그렇게 읽지 않는 값이라, 칸이 있으면 모델이 그렇게 읽는다.
    """

    PERCENT = "percent"
    BASIS_POINT = "basis_point"
    # 주식 수. **시장 단위 수급은 금액이 아니라 수량으로 준다** — `market_investor_flow_snapshot`
    # 의 `*_net_buy_amount`는 모델 주석이 "단위 미확정"이라(2026-09-02 확인) 그 값을 원이라고
    # 부르면 거짓이 된다. 수량은 정의가 분명하다. 금액은 툴이 원문 그대로 함께 주고
    # 단위를 밝히지 않는다.
    SHARES = "shares"
    NONE = "none"


class FactorSpec(BaseModel):
    """요인 하나의 정의. 이름·자리·단위가 한 줄에 모인다."""

    model_config = ConfigDict(frozen=True)

    code: Factor
    label: str
    source: FactorSource
    # 그 표에서 이 요인을 가리키는 값. `quote_daily`는 symbol, `indicator_observation`은
    # series_id, 종목은 종목코드, 수급은 어느 투자자인지다. 문서 요인은 비운다.
    key: str
    unit: FactorUnit


FACTORS: tuple[FactorSpec, ...] = (
    FactorSpec(
        code=Factor.FOREIGN_NET_BUY,
        label="외국인 순매수",
        source=FactorSource.MARKET_FLOW,
        key="foreign",
        unit=FactorUnit.SHARES,
    ),
    FactorSpec(
        code=Factor.INSTITUTION_NET_BUY,
        label="기관 순매수",
        source=FactorSource.MARKET_FLOW,
        key="institution",
        unit=FactorUnit.SHARES,
    ),
    FactorSpec(
        code=Factor.INDIVIDUAL_NET_BUY,
        label="개인 순매수",
        source=FactorSource.MARKET_FLOW,
        key="individual",
        unit=FactorUnit.SHARES,
    ),
    FactorSpec(
        code=Factor.US10Y,
        label="미국 10년물",
        source=FactorSource.QUOTE_DAILY,
        key="US10Y",
        unit=FactorUnit.BASIS_POINT,
    ),
    FactorSpec(
        code=Factor.KTB10Y,
        label="국고채 10년",
        source=FactorSource.INDICATOR,
        key="KTB10Y",
        unit=FactorUnit.BASIS_POINT,
    ),
    FactorSpec(
        code=Factor.KRBASE,
        label="한국은행 기준금리",
        source=FactorSource.INDICATOR,
        key="KRBASE",
        unit=FactorUnit.BASIS_POINT,
    ),
    FactorSpec(
        code=Factor.USDKRW,
        label="원달러 환율",
        source=FactorSource.QUOTE_DAILY,
        key="USDKRW",
        unit=FactorUnit.PERCENT,
    ),
    FactorSpec(
        code=Factor.DXY,
        label="달러인덱스",
        source=FactorSource.QUOTE_DAILY,
        key="DXY",
        unit=FactorUnit.PERCENT,
    ),
    FactorSpec(
        code=Factor.SP500,
        label="S&P500",
        source=FactorSource.QUOTE_DAILY,
        key="SP500",
        unit=FactorUnit.PERCENT,
    ),
    FactorSpec(
        code=Factor.NASDAQ,
        label="나스닥",
        source=FactorSource.QUOTE_DAILY,
        key="NASDAQ",
        unit=FactorUnit.PERCENT,
    ),
    FactorSpec(
        code=Factor.SOX,
        label="필라델피아 반도체",
        source=FactorSource.QUOTE_DAILY,
        key="SOX",
        unit=FactorUnit.PERCENT,
    ),
    FactorSpec(
        code=Factor.VIX,
        label="VIX",
        source=FactorSource.QUOTE_DAILY,
        key="VIX",
        unit=FactorUnit.PERCENT,
    ),
    FactorSpec(
        code=Factor.WTI,
        label="WTI 유가",
        source=FactorSource.QUOTE_DAILY,
        key="WTI",
        unit=FactorUnit.PERCENT,
    ),
    FactorSpec(
        code=Factor.SAMSUNG,
        label="삼성전자",
        source=FactorSource.STOCK_DAILY,
        key="005930",
        unit=FactorUnit.PERCENT,
    ),
    FactorSpec(
        code=Factor.SK_HYNIX,
        label="SK하이닉스",
        source=FactorSource.STOCK_DAILY,
        key="000660",
        unit=FactorUnit.PERCENT,
    ),
    FactorSpec(
        code=Factor.NEWS,
        label="뉴스",
        source=FactorSource.DOCUMENT,
        key="",
        unit=FactorUnit.NONE,
    ),
    FactorSpec(
        code=Factor.DISCLOSURE,
        label="공시",
        source=FactorSource.DOCUMENT,
        key="",
        unit=FactorUnit.NONE,
    ),
    FactorSpec(
        code=Factor.KOSPI,
        label="코스피 자체",
        source=FactorSource.INDEX_SELF,
        key=INDEX_CODE,
        unit=FactorUnit.PERCENT,
    ),
)

FACTOR_SPECS: dict[Factor, FactorSpec] = {spec.code: spec for spec in FACTORS}

# `factor_history`가 받는 요인. 문서 요인 둘은 값이 아니라 글이라 빠지고, 코스피 자체는
# 이미 관측 상태에 실려 있어 툴로 다시 부를 것이 없다.
HISTORY_FACTORS: tuple[Factor, ...] = tuple(
    spec.code
    for spec in FACTORS
    if spec.source not in (FactorSource.DOCUMENT, FactorSource.INDEX_SELF)
)

# 관계 그래프의 출발 노드가 되는 요인. **코스피 자체는 빠진다** — 지수가 자기와 같은 방향인
# 것은 언제나 참이라 엣지를 쌓으면 가중치 표에 뜻 없는 +3이 하나 박힌다.
RELATION_FACTORS: tuple[FactorSpec, ...] = tuple(
    spec for spec in FACTORS if spec.source is not FactorSource.INDEX_SELF
)


def factor_label(code: Factor) -> str:
    return FACTOR_SPECS[code].label


class ObservationSign(StrEnum):
    """오늘 그 요인이 코스피와 같은 방향이었나."""

    SAME = "same"
    INVERSE = "inverse"


class Direction(StrEnum):
    """전망의 방향. **`flat`을 두지 않는다.**

    옛 추론의 3-클래스 확률이 캘리브레이션 실패의 자리였다(`prob_flat` 평균 0.31, 실제 13%).
    "얼마나 움직이나"는 `expected_change_pct`와 `band_pct`가 이미 말한다 — 방향까지 세
    갈래로 두면 같은 것을 두 번 묻는다.
    """

    UP = "up"
    DOWN = "down"


class MemoryVerdict(StrEnum):
    """활성 메모 하나를 이번 관찰이 어떻게 봤나."""

    KEEP = "keep"
    DROP = "drop"


class RetireReason(StrEnum):
    """메모를 내린 이유. **모델이 정한 것과 코드가 정한 것을 가른다.**

    `dropped`만 모델의 판단이고 나머지 둘은 코드가 상한으로 내린 것이다. 섞으면
    "모델이 메모를 잘 지우나"를 못 잰다.
    """

    DROPPED = "dropped"
    EXPIRED = "expired"
    UNREVIEWED = "unreviewed"


# ---------------------------------------------------------------------------
# 상한과 창 — 전부 코드 상수다. 프롬프트에는 자리표시자로 실린다
# ---------------------------------------------------------------------------

# 관측 상태에 싣는 일봉 수. 영업일 달력을 세지 않고 저장된 행 이만큼을 쓴다.
BARS_WINDOW = 15

# 크기 기준선을 재는 창(저장된 일봉 행 수). **15봉으로는 못 잰다** — 분위수 넷을 뽑기에
# 표본이 너무 얇고, 그 열다섯이 마침 조용한 구간이면 기준선이 통째로 낮아진다.
#
# 250은 대략 1년이다. 짧으면 지금 체제를 빨리 따라가고 표본이 얇아진다. 시장의 변동성
# 체제가 바뀌면 이 값을 줄이는 것이 손잡이다.
MOVE_BASELINE_BARS = 250

# 기준선을 믿을 최소 표본. 미만이면 **전부 `None`으로 둔다** — 0으로 채우면 모델이 그
# 숫자를 쓴다. "재지 않았다"와 "0이다"는 다르다.
MIN_MOVE_BASELINE_BARS = 40

# 가중치를 재는 관측 수. 반감기가 5일이라 15번째 관측은 무게가 1/8 아래다 — 창을 두는
# 이유는 프롬프트 길이가 아니라 계산량이다.
RELATION_WINDOW = 15

# 관계를 읽을 때 훑는 날 수(달력일). 창(`RELATION_WINDOW`)이 관측 수라면 이것은 그 관측을
# 찾을 범위다. 반감기 5일이면 90일 전 관측의 무게가 2^-18이라 값에 영향이 없고, 상한을 두는
# 이유는 그래프가 자란 뒤 한 번의 조회가 전 이력을 훑지 않게 하기 위해서다.
RELATION_LOOKBACK_DAYS = 90

# 관측의 반감기(달력일). 5일 전 관측이 오늘의 절반, 10일 전이 1/4 무게다.
#
# **옛 관측을 지우지 않는다.** 엣지는 전부 남고 무게만 준다 — "외국인이 사면 올랐다"가
# 이번 주 뒤집혔을 때 단순 평균은 그 반전을 2주 뒤에야 보여 준다.
#
# **시작값이다.** 실측 없음(설계 §9). 반전이 너무 빨리 따라가면 올리고, 늦으면 내린다.
RELATION_HALF_LIFE_DAYS = 5

# 프롬프트 표에 감쇠 없이 그대로 보이는 최근 관측 수. 가중치 하나로는 "오래 일관된 -0.5"와
# "막 뒤집히는 중인 -0.15"가 구분되지 않는다.
RECENT_SIGN_COUNT = 3

# 활성 메모 상한. 넘치면 새 메모를 쓰지 않고 원장에 센다.
# **0으로 두면 기능이 꺼진다** — 쓰기가 전부 거절되고 활성이 만료로 빈다.
MAX_ACTIVE_MEMORIES = 20

# 메모의 나이 상한(달력일). 넘으면 모델 판정과 무관하게 코드가 내린다.
#
# **이 경계가 이 기능의 울타리다.** 메모는 "요즘 볼 것"이지 규칙이 아니다. 규칙이 되려면
# 관계 엣지로 쌓여 가중치가 되어야 한다 — 그것이 "LLM 출력이 LLM 입력이 되어 스스로를
# 강화하는" 순환을 끊는 자리다.
MEMORY_MAX_AGE_DAYS = 20

# 검토에서 빠진 채로 이 횟수를 넘기면 코드가 내린다. 답에 없는 것을 `keep`으로 치지 않는다.
MAX_UNREVIEWED = 2

# 툴 상한 셋. 값이 `Field(description=...)`에 f-string으로 실려 프롬프트가 따라간다.
#
# **호출 상한을 8에서 15로 올렸다**(2026-09-03, 사용자 결정). 근거는 실측 하나와 셈 하나다.
#
# - 2026-09-03 장전 프로토타입이 한 왕복에 **정확히 8회**를 쓰고 답했다. 천장에 닿은 값이라
#   "쓸 만큼 썼다"로 읽을 수 없다.
# - `factor_history`로 볼 수 있는 요인이 **열다섯**인데 호출이 8이면 절반도 못 본다.
#   뉴스·공시를 부르면 요인은 여섯 남는다.
#
# **문자 상한은 같이 안 올린다.** 같은 실행 실측에서 `recent_news`가 15,851자,
# `factor_history`가 997자였다 — 뉴스 셋에 요인 열둘을 부르면 약 60,000자로 120,000의
# 절반이다. 실질 브레이크를 호출 수에 두는 것이 이 값의 목적이고, 한 커밋에 손잡이 하나다.
#
# **왕복(3)도 안 올린다.** 왕복은 곧 모델 호출 수라 타임아웃과 비용에 직접 걸린다.
MAX_TOOL_CALLS = 25
MAX_TOOL_ROUNDS = 3
MAX_TOOL_RESULT_CHARS = 120_000

# 툴 하나가 돌려주는 행 수.
MAX_TOOL_RESULTS = 30

# `factor_history`의 창(영업일 아닌 저장 행 수).
MIN_HISTORY_DAYS = 2
MAX_HISTORY_DAYS = 30
DEFAULT_HISTORY_DAYS = 10

# 문서 툴의 창(시간).
MIN_WINDOW_HOURS = 1
MAX_WINDOW_HOURS = 48
DEFAULT_WINDOW_HOURS = 24


# 답의 범위. **폭주만 막는 값이다** — 정합성은 프롬프트와 저장 전 검증이 본다.
MAX_EXPECTED_CHANGE_PCT = Decimal(10)
MIN_BAND_PCT = Decimal("0.1")
MAX_BAND_PCT = Decimal(5)

# 저장 자릿수. 모델이 소수점 넷을 내도 두 자리로 접는다.
CHANGE_QUANTUM = Decimal("0.01")

# 문장 상한.
MAX_STATEMENT_CHARS = 200
MAX_NOTE_CHARS = 200
MAX_MEMORY_CHARS = 200
MAX_REASON_CHARS = 100

# Slack에 보이는 이유 수. **저장 상한이 아니다** — 이유 개수에는 상한이 없고 전부 저장한다.
SLACK_REASON_LIMIT = 3

# 관찰이 0건이어도 되는 날의 경계. 이만큼 움직였는데 이유가 없으면 그건 답이 아니다.
OBSERVATION_REQUIRED_PCT = Decimal("0.5")

# 장중 슬롯의 준비 검사. 최신 봉이 `as_of_at`에서 이보다 오래됐으면 실행하지 않는다.
# 오래된 가격을 "지금"으로 읽고 답하는 것보다 안 도는 편이 낫다.
BAR_STALENESS = timedelta(minutes=15)

# 세기의 허용 값. 3이 "주도했다"다.
MIN_STRENGTH = 1
MAX_STRENGTH = 3


# ---------------------------------------------------------------------------
# 순수 함수 — 판정은 전부 여기 있다
# ---------------------------------------------------------------------------


def change_pct(base: Decimal, close: Decimal) -> Decimal:
    """기준가 대비 등락률(퍼센트). 두 자리로 접는다.

    슬롯 셋이 같은 식을 쓰고 분모만 다르다 — 장전은 전일 종가, 장중은 그 시각 현재가다.
    """
    if base <= 0:
        raise ValueError(f"base price must be positive, got {base}")
    return quantize_change((close - base) / base * 100)


def quantize_change(value: Decimal) -> Decimal:
    return Decimal(value).quantize(CHANGE_QUANTUM, rounding=ROUND_HALF_UP)


class Grade(BaseModel):
    """전망 하나의 채점. **LLM이 없다.**"""

    model_config = ConfigDict(frozen=True)

    actual_change_pct: Decimal
    hit: bool
    within_band: bool


def grade_forecast(
    *,
    direction: Direction,
    expected_change_pct: Decimal,
    band_pct: Decimal,
    base_price: Decimal,
    close_price: Decimal,
) -> Grade:
    """실제 종가로 전망 하나를 채점한다.

    - `hit` — 실현 방향이 전망 방향과 같은가. **0은 틀린 것으로 센다.** 정확히 0.00이면
      어느 방향도 아닌데, 그 경우 "올랐다"고 부른 전망을 맞았다고 할 수 없다.
    - `within_band` — 크기 오차가 폭 안인가. 방향이 틀려도 잰다. 둘은 다른 축이다.
    """
    actual = change_pct(base_price, close_price)
    realized = Direction.UP if actual > 0 else Direction.DOWN if actual < 0 else None
    return Grade(
        actual_change_pct=actual,
        hit=realized is not None and realized is direction,
        within_band=abs(actual - expected_change_pct) <= band_pct,
    )


def decay_weight(observed_on: date, as_of_date: date, *, half_life_days: int = RELATION_HALF_LIFE_DAYS) -> float:
    """관측 하나의 무게. 나이가 반감기의 배수만큼 지날 때마다 절반이 된다.

    미래 관측은 무게 1이다 — 그런 행이 오면 조회가 잘못된 것이고, 여기서 조용히 키우거나
    줄이면 그 결함이 숨는다.
    """
    age = max((as_of_date - observed_on).days, 0)
    return 0.5 ** (age / half_life_days)


class Observation(BaseModel):
    """관계 엣지 하나를 읽은 것. Neo4j 행이 이 모양으로 온다."""

    model_config = ConfigDict(frozen=True)

    observed_on: date
    sign: ObservationSign
    strength: int
    note: str = ""


class RelationWeight(BaseModel):
    """요인 하나의 가중치와 그것을 만든 관측. 프롬프트 표의 한 줄이다."""

    model_config = ConfigDict(frozen=True)

    factor: Factor
    label: str
    # −1.0 ~ +1.0. 최신 관측에 기운 값이다.
    weight: float
    n_obs: int
    last_date: date | None = None
    last_note: str = ""
    # 감쇠 없이 그대로 보이는 최근 부호들. 가중치와 어긋나면 관계가 바뀌는 중이다.
    recent_signs: tuple[ObservationSign, ...] = ()


def relation_weight(
    factor: Factor,
    observations: list[Observation],
    *,
    as_of_date: date,
    half_life_days: int = RELATION_HALF_LIFE_DAYS,
    window: int = RELATION_WINDOW,
) -> RelationWeight:
    """관측들을 요인 하나의 가중치로 접는다. **최신이 무겁다.**

    `observations`는 최신순으로 온다고 가정하지 않는다 — 여기서 정렬한다. 조회가 순서를
    바꿔도 값이 흔들리지 않아야 한다.

    관측이 없으면 `weight`가 0이고 `n_obs`가 0이다. **그 둘을 가르는 것은 부르는 쪽의
    일이다** — 0은 "관계가 없다"가 아니라 "아직 모른다"이고, 프롬프트가 그 뜻을 밝힌다.
    """
    recent = sorted(observations, key=lambda item: item.observed_on, reverse=True)[:window]
    label = factor_label(factor)
    if not recent:
        return RelationWeight(factor=factor, label=label, weight=0.0, n_obs=0)

    numerator = 0.0
    denominator = 0.0
    for item in recent:
        weight = decay_weight(item.observed_on, as_of_date, half_life_days=half_life_days)
        signed = item.strength if item.sign is ObservationSign.SAME else -item.strength
        numerator += weight * signed
        denominator += weight * MAX_STRENGTH
    return RelationWeight(
        factor=factor,
        label=label,
        weight=round(numerator / denominator, 3) if denominator else 0.0,
        n_obs=len(recent),
        last_date=recent[0].observed_on,
        last_note=recent[0].note,
        recent_signs=tuple(item.sign for item in recent[:RECENT_SIGN_COUNT]),
    )


def memory_expired(created_on: date, as_of_date: date, *, max_age_days: int = MEMORY_MAX_AGE_DAYS) -> bool:
    """나이 상한을 넘겼나. 모델 판정보다 이것이 앞선다."""
    return (as_of_date - created_on).days > max_age_days


def normalize_text(value: str, limit: int) -> str:
    """공백을 접고 상한으로 자른다. 모델 문장이 DB와 Slack에 실리기 전 마지막 자리다."""
    text = " ".join(str(value or "").split())
    return text[:limit]


def memory_key(text: str) -> str:
    """같은 메모인지 판정하는 값. 공백·문장부호를 지운 소문자다.

    같은 사실을 조사만 바꿔 다시 쓰는 것을 막는다. 완벽하지 않고 그래도 된다 — 놓친 중복은
    상한(`MAX_ACTIVE_MEMORIES`)이 받는다.
    """
    stripped = "".join(char for char in str(text or "") if char.isalnum())
    return stripped.lower()


def kst_label(moment: datetime, timezone: object) -> str:
    """모델에게 주는 시각 표기. **UTC ISO를 그대로 싣지 않는다.**

    장전 기준 KST 08:35는 UTC로 전날 23:35다. 그대로 주면 모델이 "오늘"을 하루 어긋나게
    읽는다. 시간대 객체는 부르는 쪽이 준다 — 이 모듈은 pendulum을 import하지 않는다.
    """
    return moment.astimezone(timezone).strftime("%Y-%m-%d %H:%M KST")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# 툴 호출 원장의 행
# ---------------------------------------------------------------------------


class ToolCallErrorKind(StrEnum):
    """툴 호출이 실패한 종류. **어디서 막혔는지가 다르다.**

    `unknown_tool`과 `validation`은 함수에 도달하지 못한 실패라 래퍼가 못 본다.
    `limit`은 우리 상한, `execution`은 실제 실행 중 예외, `cancelled`는 sibling 실패로
    시작조차 못 한 것이다.
    """

    UNKNOWN_TOOL = "unknown_tool"
    VALIDATION = "validation"
    LIMIT = "limit"
    EXECUTION = "execution"
    CANCELLED = "cancelled"


class ToolCallRecord(BaseModel):
    """툴 호출 하나의 기록. **가변이다** — 요청 시점에 열고 결과가 오면 채운다.

    이 저장소의 다른 모델과 달리 `frozen`이 아닌 이유가 그것이다. 대화 하나가 끝나면
    `store`가 이 행들을 `kospi_tool_call`로 쓴다.
    """

    model_config = ConfigDict(frozen=False)

    seq: int
    round_no: int
    tool_call_id: str
    tool_name: str
    # 모델이 보낸 그대로. 검증 전이라 스키마와 안 맞는 값이 들어 있을 수 있다.
    arguments: dict[str, object]
    requested_at: datetime
    # 검증을 통과해 함수에 실제로 들어간 인자. 함수에 못 닿았으면 `None`이다.
    validated_arguments: dict[str, object] | None = None
    duration_ms: int | None = None
    result: str | None = None
    result_chars: int = 0
    error_kind: ToolCallErrorKind | None = None
    error: str | None = None
    # 결과가 모델 대화에 실제로 돌아갔나. sibling 실패로 버려질 수 있다.
    delivered: bool = False
