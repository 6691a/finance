"""추론의 어휘와 셈 — enum, 근거, 임계값, 채점 수식.

**이 모듈은 LangChain·LangGraph를 import하지 않는다.** 슬롯 모듈과 `thesis_common.py`가
`ThesisSubjectKind` 하나 때문에 LangChain 전체를 끌고 오던 것을 여기서 끊는다. 무거운 것은
`thesis_toolbox`·`thesis_generation`·`thesis_outcomes`에 있고 그쪽은 늦게 import한다.

값은 `apps/models/analysis/thesis.py`의 같은 이름 enum과 같아야 한다. Airflow는 `apps/`를 보지
못해 import하지 못하므로 값을 한 벌 더 둔다(중복 허용 + 테스트 대조 규칙).
`tests/models/test_analysis_models.py`가 대조한다.
"""

"""시장 추론(thesis)을 만들고, 저장하고, 채점한다.

**맞고 틀림이 목적이 아니다.** "어떤 정보를 근거로 어떤 결론을 냈다"가 기록으로 남는 것이
목적이다. 채점은 그 기록 위에 나중에 얹히고, 틀린 판단도 고치지 않는다.

## 근거는 고정 풀이 아니라 모델이 조회한다

프롬프트에는 **관측 상태만** 준다("코스피 +1.61%", "SK하이닉스 전일 -2.1%"). 관측 상태는
전부 SQL이 계산한다. 왜인지 알아내는 데 필요한 정보는 모델이 `ThesisToolbox`의 읽기 전용 툴을
호출해 스스로 가져온다 — 어떤 것을 얼마나 볼지는 모델이 정한다.

**모델이 실제로 인용한 근거만 저장한다.** 툴이 돌려준 항목에는 전부 `ref`가 붙어 있고,
답변의 `evidence_refs`는 그 레지스트리로 검증한다. 목록 밖 ref는 버린다. 이것이 모델이 근거를
지어내지 못하게 막는 유일한 장치다.

## 조사와 답변을 나눈다

`modules/llm.py`의 원칙 그대로다. 조사 단계는 툴만 바인딩하고, 답변 단계는 툴을 빼고
`response_format`을 강제한다. 한 요청에 둘을 섞지 않는다 — `llm.invoke`가 그것을 막는다.

## 기준 시각은 벽시계가 아니다

**모든 조회의 끝은 슬롯이 정한 `as_of_at`이다.** 오후에 장전 슬롯을 다시 돌려도 장중 정보로
아침 예측을 덮지 않는다. 이것은 event-time cutoff다 — 현재 DB에서 확인 가능한 범위에서
`as_of_at` 이후 감지·평가·갱신된 행을 뺀다. 과거 시점을 완전히 복원하지는 못한다
(`document`는 본문·평가를 같은 행에 덮어쓰고 버전 이력을 두지 않는다).

## 첫 성공본은 불변이다

같은 (날짜, 슬롯)에 추론 행이 이미 있으면 LLM을 다시 부르지 않는다. LLM은 재호출마다 답이
달라서 덮어쓰면 최초 판단이 사라진다. `existing_theses`가 먼저 보고, 없을 때만 Builder를 돈다.

## 채점에 LLM이 없다

수식이 SQL이 아니라 파이썬에 있는 이유는 경계값을 DB 없이 테스트하기 위해서다(테스트에서
실 DB를 쓰지 않는 프로젝트 규칙). `select_session_return.sql`이 등락률을 주고
`update_outcome.sql`은 여기서 나온 값 넷을 쓰기만 한다.

설계는 `docs/market-thesis/1-storage.md`와 `docs/market-thesis/2-agent.md`에 있다.
"""

import logging
from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from modules import technical
from modules.thesis_state import (
    RunSlot,
)
from modules.thesis_tools import (
    EvidenceDetail,
)
from modules.utility import KST_TIMEZONE

logger = logging.getLogger(__name__)

logger = logging.getLogger(__name__)

# 프롬프트를 고치면 올린다. `thesis.prompt_version`에 저장돼 채점 결과를 가르는 기준이 된다.
# 2: 과거 추론과 결과를 프롬프트에 미리 싣는 절이 생겼고, 인용이 `evidence_refs`에서 근거별
#    방향·경로를 담는 `claims`로 바뀌었다(2026-08-21, 둘 다 운영에 나가기 전이라 한 판이다).
# 3: 기술적 보조지표가 들어왔다(2026-08-24). `daily_history`가 국내 지수·종목 일봉과
#    technical_snapshot·recent_signals를 함께 주고, 관측 상태에 technical 블록이 실린다.
# 4: 과거 추론 절에 장후 리뷰(`post_close`)와 그 사후 해설이 함께 실린다(2026-08-25).
#    그전에는 장전 예측만 돌아왔고 리뷰 해설은 Slack T+5와 그래프로만 나갔다.
# 5: 확률의 뜻을 정의했다(2026-08-25). `prob_flat`이 "±임계 안에 들어올 빈도"임을 밝히고
#    실측 base rate를 실었다. 그전에는 정의가 없어 모델이 `flat`을 "방향을 모르겠다"로 읽고
#    30%대를 줬다 — 실제 빈도는 5~11%다.
PROMPT_VERSION = "5"

# 채점 지평. KRX 영업일 수이고 달력일이 아니다. 0은 예측일 세션 하나다.
HORIZON_DAYS: tuple[int, ...] = (0, 1, 3, 5)

# 해설을 받는 지평. 0은 그날의 후속 보도가 아직 쌓이지 않아 쓸 재료가 없다.
NARRATED_HORIZON_DAYS: tuple[int, ...] = (1, 3, 5)

# |누적 등락률|이 이 값보다 작으면 방향이 없었다고 본다(퍼센트). **지평마다 다르다.**
#
# 하루 임계 0.3을 5영업일 누적에 그대로 쓰면 `flat`이 사실상 사라진다. 그러면 `prob_flat`이
# 항상 틀린 쪽에 붙어 Brier가 조용히 왜곡된다.
#
# **값의 근거는 `0.3 × sqrt(N)`을 반올림한 것뿐이고 실측이 아니다.** 배포 4주 뒤 지평별
# `actual_outcome` 분포를 보고 조정한다 — 한 지평에서만 `flat` 비율이 5% 아래거나 60% 위면
# 그 값이 틀린 것이다(`docs/market-thesis/5-followup.md` 2·11절).
FLAT_THRESHOLD_PCT: dict[int, Decimal] = {
    0: Decimal("0.3"),
    1: Decimal("0.3"),
    3: Decimal("0.5"),
    5: Decimal("0.7"),
}

# 프롬프트에 싣는 `flat` 실현 빈도(퍼센트). 지평 0, 임계 0.3% 기준의 **실측**이다
# (`index_daily` 132거래일, `stock_investor_trade_daily` 123거래일, 2026-08-25 조회).
#
# 이 값이 프롬프트에 있는 이유는 모델이 `prob_flat`을 "±0.3% 안"이 아니라 "방향을
# 모르겠다"로 읽어 30%대를 주고 있었기 때문이다. 그만큼이 up·down에서 빠져나가 최고 확률이
# 0.44를 넘지 못했다. 기준선을 주면 그것을 넘길 때 근거를 대게 된다.
#
# **`FLAT_THRESHOLD_PCT[0]`을 고치면 이 값도 다시 재야 한다.** 임계가 곧 이 빈도의 정의다.
FLAT_BASE_RATE_PCT: dict[str, int] = {"KOSPI": 6, "KOSDAQ": 11, "stock": 6}

# 조사 왕복 상한. 넘으면 조사를 끝내고 답변 단계로 넘어간다. 왕복 하나가 모델 호출 하나라
# 이 값이 빌드 한 번의 길이를 정한다(`thesis_common.BUILD_TIMEOUT`이 그 바깥 울타리다).
MAX_TOOL_ROUNDS = 3

# 실행당 tool call 총 상한. 모델이 같은 툴을 반복해 부르는 것을 막는다. 왕복을 줄여도 이
# 값은 두어, 한 왕복에 여러 툴을 묶어 부르면 전처럼 많이 볼 수 있다.
#
# **툴 수보다 커야 한다.** 12이던 때 툴이 14개라 모델이 툴마다 한 번씩도 못 불렀다
# (2026-08-25에 20으로). 툴을 더 열면 이 값도 같이 본다.
MAX_TOOL_CALLS = 20

# 실행당 툴 결과 누적 문자 상한. 넘으면 그 뒤 호출을 거절한다. **폭주만 받는 안전망이다** —
# 실질 브레이크는 `MAX_TOOL_CALLS`와 호출당 상한(`MAX_TOOL_RESULTS` × `MAX_ITEM_DETAIL_CHARS`)이
# 잡는다.
#
# **한 바퀴보다 커야 한다.** 2026-08-26 장전 `as_of_at`으로 운영 DB에 붙어 툴 14개를 한 번씩
# 돌면 44,340자다(장전 실질 12개도 44,336자 — 장중 스냅샷 툴 둘은 `SNAPSHOT_LOOKBACK` 밖이라
# 빈 값이다). 40,000이던 때는 모델이 한 바퀴를 끝내지 못했고, 어느 툴이 잘리는지가 호출 순서
# 운이었다. 대상별 툴까지 부르는 현실적 조사는 26호출 64,694자였다.
#
# 이 값은 그 한 바퀴에 표적 2차 조사와 여유를 더한 크기다. 실측 내역은
# `docs/market-thesis/TUNING.md` 5절에 있다(2026-08-26에 40,000에서, 그 전은 2026-08-25에
# 24,000에서).
MAX_TOOL_RESULT_CHARS = 100_000

# 툴 호출 하나가 돌려주는 항목 수 상한.
MAX_TOOL_RESULTS = 20

# 항목 하나의 `new_facts` + `reason` 합계 문자 상한.
MAX_ITEM_DETAIL_CHARS = 600

# 투자의견 한 건에 붙는 리포트 요약의 상한. 스무 건까지 오므로 문서 한 건(600자)보다 짧게 둔다.
# 사유의 첫 문단이 결론이라 앞쪽만으로도 "왜 그 목표가인가"가 읽힌다. 전문은 같은 리포트가
# `recent_documents`로 올 때 나온다.
MAX_OPINION_REASON_CHARS = 200

# `hours` 인자의 허용 범위. 모델이 벗어난 값을 넘기면 잘라서 실행한다.
MIN_WINDOW_HOURS = 1
MAX_WINDOW_HOURS = 72

# `min_score` 인자의 허용 범위. `value_score`는 0~8이지만 상한을 넉넉히 둔다.
MIN_VALUE_SCORE = 0
MAX_VALUE_SCORE = 100

# `past_theses`가 한 번에 돌려줄 과거 추론 수의 허용 범위. **슬롯마다 세는 값이다**(2026-08-25).
# 문맥을 과거로 다 채우지 않는다.
MIN_PAST_THESES = 1
MAX_PAST_THESES = 10

# 장전 추론의 프롬프트에 **미리 실어 주는** 같은 대상의 과거 추론 수. 툴로 두면 모델이 부를지
# 말지를 정하고 불렀는지도 DB에 안 남는다. 미리 실으면 본 것이 확정되고 `thesis_precedent`에
# 엣지로 남는다. 0이면 끄는 것이다 — 과거 추론을 안 싣고 엣지도 안 남긴다.
# T+5 지평이 한 주라 한 주치를 준다. **슬롯마다이므로 실제 행 수는 최대 두 배다** — 장전
# 예측 5건과 장후 리뷰 5건이다.
PREFETCHED_PAST_THESES = 5

# 이유 문장 하나의 상한. 넘으면 그 필드만 자른다.
MAX_REASONING_CHARS = 500

# 근거 하나의 경로(mechanism) 문장 상한. 엣지 속성이라 이유 문장보다 짧게 둔다.
MAX_MECHANISM_CHARS = 200

# 확률 합이 1에서 이만큼 안이면 비율을 유지한 채 정규화한다. 넘으면 그 subject를 버린다.
PROB_SUM_TOLERANCE = Decimal("0.02")

# `thesis.prob_*`가 numeric(5,4)다. 정규화 결과를 이 자리수로 맞춘다.
PROB_QUANTUM = Decimal("0.0001")

# DART 뷰어 주소. 접수번호만 있으면 사람이 원문을 열 수 있다.
DART_VIEWER_URL = "https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcept_no}"

# `macro_changes`가 보는 심볼 종류. 개별 종목(`equity`)은 세션 등락률 SQL이 따로 준다.
MACRO_KINDS: tuple[str, ...] = (
    "index",
    "index_future",
    "fx",
    "rate",
    "bond_future",
    "commodity",
    "crypto",
)

# 값이 퍼센트라 변화를 bp로 읽어야 하는 종류. 4.65→4.70은 `+1.08%`가 아니라 `+5bp`다.
BASIS_POINT_KINDS = frozenset({"rate"})

# `us_market_close`가 만드는 근거의 ref 접미. 같은 심볼이라도 창 변화(`macro_change:SP500_FUT`)와
# 마감 등락(`macro_change:SP500_FUT@close`)은 다른 숫자라 ref가 겹치면 레지스트리가 하나를
# 조용히 덮는다. ref는 `<kind>:<id>` 2단을 지켜야 해서(`thesis_evidence.evidence_ref` 주석)
# 콜론이 아니라 `@`로 붙인다.
CLOSE_REF_SUFFIX = "@close"

# `macro_indicators`가 고를 수 있는 `indicator_series.kind`. 단위가 달라 **반드시 걸어야 한다** —
# 안 걸면 국채 금리(Percent)와 물가지수(Index 1982-1984=100)가 한 표에 섞인다.
INDICATOR_KINDS: tuple[str, ...] = ("government_bond", "money_market", "price_index", "activity")

# 값이 연이율 퍼센트라 변화를 bp로 읽어야 하는 지표 종류. 위 `BASIS_POINT_KINDS`와 뜻은
# 같지만 대상이 다르다 — 저쪽은 `quote_symbol.kind`, 이쪽은 `indicator_series.kind`다.
BASIS_POINT_INDICATOR_KINDS = frozenset({"government_bond", "money_market"})

# `macro_indicators` 한 번이 돌려줄 계열 수 상한. 국채만 40계열이라 안 걸면 한 호출이
# 결과 예산(`MAX_TOOL_RESULT_CHARS`)을 혼자 다 쓴다.
MAX_INDICATOR_RESULTS = 40

# 장중 스냅샷 툴이 거슬러 올라가는 길이. 그 슬롯의 세션 안이면 충분하다.
SNAPSHOT_LOOKBACK = timedelta(hours=12)

# 일별 이력 툴의 `days` 허용 범위.
MIN_HISTORY_DAYS = 1
MAX_HISTORY_DAYS = 30

# 지표 계산에 받는 봉 수. 모델에게 보여 주는 봉(`days`)과 다르다.
TECHNICAL_LOOKBACK_BARS = technical.TECHNICAL_LOOKBACK_BARS

# 국내 종목의 하루 가격제한폭보다 큰 인접 종가 단절은 분할·병합이나 원천 이상을 의심한다.
# 그 구간의 이동평균을 그대로 보여 주느니 지표를 내지 않는 편이 안전하다.
DOMESTIC_MAX_DAILY_CHANGE_PCT = 35.0

# 툴이 보여 주는 신호 이력의 창. 달력일이고 브리핑(30일)보다 길다 — 브리핑은 "지금 상태"를
# 한 칸으로 말하고 툴은 "이 대상에 최근 무슨 일이 있었나"를 이력으로 말한다.
SIGNAL_HISTORY_DAYS = 90

# 프롬프트가 지표를 읽는 기준. 검출 규칙과 **같은 상수**를 쓴다 — 두 곳에 숫자를 적으면
# 반드시 어긋난다. 거래량 기준만 여기에 있다(검출에 쓰지 않아서다).
RSI_OVERBOUGHT = technical.RSI_OVERBOUGHT
RSI_OVERSOLD = technical.RSI_OVERSOLD
VOLUME_HEAVY_RATIO = 1.5
VOLUME_LIGHT_RATIO = 0.7
class ThesisError(RuntimeError):
    """모델이 쓸 수 있는 추론을 내지 않았다. 다시 불러도 같은 결과다."""


# ---------------------------------------------------------------------------
# 값 종류. `apps/models/analysis/thesis.py`의 같은 이름 enum과 값이 같아야 한다.
# Airflow는 `apps/`를 보지 못해 import하지 못하므로 값을 한 벌 더 둔다
# (프로젝트의 중복 허용 + 테스트 대조 규칙). `tests/models/test_analysis_models.py`가 대조한다.
# ---------------------------------------------------------------------------


class ThesisSubjectKind(StrEnum):
    """추론 대상의 종류."""

    INDEX = "index"
    STOCK = "stock"


class ThesisDirection(StrEnum):
    """방향. 예측 확률과 실제 결과가 같은 세 값을 쓴다."""

    UP = "up"
    DOWN = "down"
    FLAT = "flat"


class ThesisVerdict(StrEnum):
    """사후 해설이 내린 판정. 원 추론의 **이유**가 이후 보도로 지지됐는가.

    `brier_score`와 다른 것을 잰다 — 저쪽은 방향이고 이쪽은 이유다. 둘을 합치지 않는다.
    `UNRESOLVED`가 기본이자 가장 흔한 답이어야 한다.
    """

    SUPPORTED = "supported"
    CONTRADICTED = "contradicted"
    UNRESOLVED = "unresolved"


class ThesisEvidenceKind(StrEnum):
    """근거의 출처 종류. `evidence_ref`의 앞자리와 글자 그대로 같다."""

    DOCUMENT = "document"
    DISCLOSURE = "disclosure"
    MACRO_CHANGE = "macro_change"
    # 기술적 매매 신호. 지표값은 문맥이라 인용 대상이 아니지만 신호는 행 ID를 가진 사건이다.
    # 인용하게 만드는 이유는 평가다 — "신호를 근거로 쓴 추론이 안 쓴 추론보다 나았나"를
    # 재려면 어느 추론이 어느 신호를 인용했는지가 엣지로 남아야 한다(문서 14.3절).
    TECHNICAL_SIGNAL = "technical_signal"


# 사건 이름. Slack 표(`briefing/market_data.SIGNAL_LABELS`)와 같은 말을 쓴다. `매수`·`매도`
# 낱말을 쓰지 않는 이유도 같다 — 사건이지 판정이 아니다.
SIGNAL_LABELS: dict[tuple[str, str], str] = {
    ("sma_cross", "up"): "골든크로스",
    ("sma_cross", "down"): "데드크로스",
    ("macd_cross", "up"): "MACD 상향 교차",
    ("macd_cross", "down"): "MACD 하향 교차",
    ("rsi_reversal", "up"): "RSI 과매도 탈출",
    ("rsi_reversal", "down"): "RSI 과매수 이탈",
}


# ---------------------------------------------------------------------------
# 채점 — LLM 없음
# ---------------------------------------------------------------------------


def classify_outcome(return_pct: Decimal, horizon_days: int) -> ThesisDirection:
    """누적 등락률을 방향으로 분류한다. **임계는 지평마다 다르다.**

    예측과 비교하지 않는다. 실제 움직임만 본다 — 얼마나 잘 맞췄는지는 `brier_score`가 답한다.
    경계값은 방향 쪽이다: 지평 1에서 0.30은 `up`이고 0.29는 `flat`이다.

    모르는 지평은 실패시킨다. 임계를 정하지 않은 지평에 기본값을 주면 그 지평만 조용히
    다른 기준으로 채점된다.
    """
    threshold = FLAT_THRESHOLD_PCT.get(horizon_days)
    if threshold is None:
        raise ThesisError(f"No flat threshold for horizon {horizon_days}; known: {sorted(FLAT_THRESHOLD_PCT)}")
    if abs(return_pct) < threshold:
        return ThesisDirection.FLAT
    return ThesisDirection.UP if return_pct > 0 else ThesisDirection.DOWN


def brier_score(
    *,
    prob_up: Decimal,
    prob_down: Decimal,
    prob_flat: Decimal,
    outcome: ThesisDirection,
) -> Decimal:
    """3-class Brier 점수. 0이 완벽이고 2가 최악이다.

    실제 결과를 원-핫 벡터로 바꿔(`up`이면 `(1, 0, 0)`) 각 확률과의 차를 제곱해 더한다.
    방향만 맞고 확신이 지나치게 낮았던 경우와 틀린 방향에 확신을 준 경우를 함께 잡아낸다 —
    hit/miss 이분법이 놓치던 "얼마나 확신 있게 맞았나"가 점수에 실린다.

    참고값: 균등 확률(1/3씩)은 결과와 무관하게 약 0.667이다. 이것이 baseline이다.
    """
    actual = {
        ThesisDirection.UP: (1, 0, 0),
        ThesisDirection.DOWN: (0, 1, 0),
        ThesisDirection.FLAT: (0, 0, 1),
    }[outcome]
    predicted = (prob_up, prob_down, prob_flat)
    return sum(((probability - truth) ** 2 for probability, truth in zip(predicted, actual)), Decimal(0))


# ---------------------------------------------------------------------------
# 근거 레지스트리
# ---------------------------------------------------------------------------


class Evidence(BaseModel):
    """툴이 돌려준 항목 하나. `ref`로 인용되고 그대로 `thesis_evidence`가 된다."""

    model_config = ConfigDict(frozen=True)

    kind: ThesisEvidenceKind
    ref: str
    title: str
    url: str | None = None
    detail: EvidenceDetail


def evidence_ref(kind: ThesisEvidenceKind, identifier: str) -> str:
    """`<evidence_kind>:<id>`. 접두가 kind와 같아 파싱이 한 규칙으로 끝난다."""
    return f"{kind.value}:{identifier}"


def kst_label(moment: datetime) -> str:
    """프롬프트에 쓰는 시각 표기. `2026-08-21 08:35 KST`.

    **연도를 뺀 `briefing/blocks.timestamp`를 재사용하지 않는다.** 모델은 이 값으로
    "오늘"이 며칠인지를 정하므로 연도가 빠지면 그 판단의 근거가 사라진다.

    저장·조회는 여전히 UTC다(프로젝트 공통 규칙). 이 함수는 표시 층에서만 쓴다 —
    `briefing/documents.pick_input`의 `as_of_kst`와 같은 자리다.
    """
    return f"{moment.astimezone(KST_TIMEZONE):%Y-%m-%d %H:%M} KST"


# ---------------------------------------------------------------------------
# 답변 스키마
# ---------------------------------------------------------------------------


class Subject(BaseModel):
    """추론을 요청할 대상 하나."""

    model_config = ConfigDict(frozen=True)

    kind: ThesisSubjectKind
    code: str
    label: str


def _shorten(text: str) -> str:
    """이유가 길면 그 필드만 자른다. 한 문장 때문에 subject 전체를 버리지 않는다."""
    return _shorten_to(text, MAX_REASONING_CHARS)


def _shorten_to(text: str, limit: int) -> str:
    """길면 그 항목만 자른다."""
    stripped = text.strip()
    if len(stripped) > limit:
        return stripped[: limit - 1].rstrip() + "…"
    return stripped

# 되돌아보기 제목에 쓰는 짧은 이름. 헤더의 이모지까지 반복하면 줄이 길어진다.
SLOT_LABELS = {
    RunSlot.PRE_OPEN: "장전 전망",
    RunSlot.POST_CLOSE: "장후 리뷰",
    RunSlot.POST_NXT_CLOSE: "애프터마켓 리뷰",
}
