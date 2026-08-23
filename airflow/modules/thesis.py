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

import json
import logging
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum
from typing import Annotated, Any, Literal, Protocol, Self, TypedDict

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_core.tools import BaseTool, StructuredTool
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from modules import llm
from modules.llm import UnsupportedResponseFormat
from modules.schema import SchemaError, json_object, response_format
from modules.sql import read_sql
from modules.utility import KST_TIMEZONE, atomic

logger = logging.getLogger(__name__)

# 프롬프트를 고치면 올린다. `thesis.prompt_version`에 저장돼 채점 결과를 가르는 기준이 된다.
# 2: 과거 추론과 결과를 프롬프트에 미리 싣는 절이 생겼고, 인용이 `evidence_refs`에서 근거별
#    방향·경로를 담는 `claims`로 바뀌었다(2026-08-21, 둘 다 운영에 나가기 전이라 한 판이다).
PROMPT_VERSION = "2"

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

# 조사 왕복 상한. 넘으면 조사를 끝내고 답변 단계로 넘어간다. 왕복 하나가 모델 호출 하나라
# 이 값이 빌드 한 번의 길이를 정한다(`thesis_common.BUILD_TIMEOUT`이 그 바깥 울타리다).
MAX_TOOL_ROUNDS = 3

# 실행당 tool call 총 상한(왕복 3 × 회당 4). 모델이 같은 툴을 반복해 부르는 것을 막는다.
# 왕복을 줄여도 이 값은 두어, 한 왕복에 여러 툴을 묶어 부르면 전처럼 많이 볼 수 있다.
MAX_TOOL_CALLS = 12

# 실행당 툴 결과 누적 문자 상한. 넘으면 그 뒤 호출을 거절한다 — 컨텍스트가 근거로 가득 차면
# 답변 단계에 쓸 자리가 없다.
MAX_TOOL_RESULT_CHARS = 24_000

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

# `past_theses`가 한 번에 돌려줄 과거 추론 수의 허용 범위. 문맥을 과거로 다 채우지 않는다.
MIN_PAST_THESES = 1
MAX_PAST_THESES = 10

# 장전 추론의 프롬프트에 **미리 실어 주는** 같은 대상의 과거 추론 수. 툴로 두면 모델이 부를지
# 말지를 정하고 불렀는지도 DB에 안 남는다. 미리 실으면 본 것이 확정되고 `thesis_precedent`에
# 엣지로 남는다. 0이면 끄는 것이다 — 과거 추론을 안 싣고 엣지도 안 남긴다.
# T+5 지평이 한 주라 한 주치를 준다.
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


class Cursor(Protocol):
    def __enter__(self) -> Self: ...

    def __exit__(self, *args: object) -> bool | None: ...

    def execute(self, statement: str, parameters: Sequence[Any] = ()) -> object: ...

    def fetchall(self) -> Any: ...

    def fetchone(self) -> Any: ...


class Connection(Protocol):
    def cursor(self) -> Cursor: ...

    def commit(self) -> None: ...

    def rollback(self) -> None: ...


class ThesisError(RuntimeError):
    """모델이 쓸 수 있는 추론을 내지 않았다. 다시 불러도 같은 결과다."""


# ---------------------------------------------------------------------------
# 값 종류. `apps/models/analysis.py`의 같은 이름 enum과 값이 같아야 한다.
# Airflow는 `apps/`를 보지 못해 import하지 못하므로 값을 한 벌 더 둔다
# (프로젝트의 중복 허용 + 테스트 대조 규칙). `tests/models/test_analysis_models.py`가 대조한다.
# ---------------------------------------------------------------------------


class RunSlot(StrEnum):
    """추론을 만든 슬롯. 슬롯이 곧 추론의 종류다."""

    PRE_OPEN = "pre_open"
    POST_CLOSE = "post_close"
    POST_NXT_CLOSE = "post_nxt_close"


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
    detail: dict[str, Any] = Field(default_factory=dict)


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
# Toolbox — 읽기 전용 툴
# ---------------------------------------------------------------------------

RECENT_DOCUMENTS = read_sql("postgres", "document", "select_recent_top.sql")
RECENT_DISCLOSURES = read_sql("postgres", "disclosure_event", "select_recent.sql")
WINDOW_CHANGES = read_sql("postgres", "quote_bar", "select_window_changes.sql")
US_MARKET_CLOSE = read_sql("postgres", "quote_bar", "select_thesis_us_close.sql")
PAST_THESES = read_sql("postgres", "thesis", "select_past_with_outcomes.sql")
PRECEDENT_INSERT = read_sql("postgres", "thesis_precedent", "insert.sql")


def past_theses(connection: Connection, *, as_of_at: datetime, subject_code: str, n: int) -> list[dict[str, Any]]:
    """이 대상의 지난 장전 추론과 지평별 결과. 최근 것부터 `n`건이다. 피드백 루프는 이 조회 하나다.

    **창의 끝은 `as_of_at`이다.** 없으면 장전 슬롯을 오후에 재실행할 때 그날 저녁의 채점이
    아침 예측에 섞인다. SQL이 술어 셋을 건다.

    `n <= 0`이면 조회하지 않고 빈 목록이다 — `PREFETCHED_PAST_THESES = 0`이 끄는 스위치다.
    """
    if n <= 0:
        return []
    with connection.cursor() as cursor:
        cursor.execute(PAST_THESES, (as_of_at, subject_code, n))
        rows = cursor.fetchall()
    return [
        {
            "id": row[0],
            "run_date": row[1].isoformat(),
            "prob_up": float(row[2]),
            "prob_down": float(row[3]),
            "prob_flat": float(row[4]),
            "up_reasoning": row[5],
            "down_reasoning": row[6],
            "flat_reasoning": row[7],
            "outcomes": row[8],
        }
        for row in rows
    ]


# 아래 일곱은 2026-08-21에 열었다. 그전까지 모델이 볼 수 있는 것은 문서·공시·분봉 창
# 변화뿐이어서, 수집 중인 것의 대부분(국채 금리·물가·수급·시장폭·증시자금·일봉 이력)이
# 보이지 않았다. **국채 금리를 못 보면서 "왜 움직였나"를 묻고 있었다.**
#
# 브리핑에 이미 비슷한 쿼리가 있지만 **파일을 나눴다.** 브리핑은 지금까지를 보고 추론은
# `as_of_at`까지만 본다. 브리핑 쿼리에 상한을 얹으면 브리핑이 쓰지 않는 파라미터를 매번
# 넘겨야 하고, 한쪽을 고칠 때 다른 쪽이 조용히 따라 바뀐다.
INDICATOR_LATEST = read_sql("postgres", "indicator_observation", "select_thesis_latest.sql")
MARKET_FLOWS = read_sql("postgres", "market_investor_flow_snapshot", "select_thesis_latest.sql")
MARKET_BREADTH = read_sql("postgres", "market_movement_snapshot", "select_thesis_latest.sql")
STOCK_FLOWS = read_sql("postgres", "stock_investor_trade_daily", "select_thesis_flows.sql")
STOCK_FLOW_ESTIMATES = read_sql("postgres", "stock_investor_estimate_snapshot", "select_thesis_latest.sql")
MARKET_FUNDS = read_sql("postgres", "krx_market_funds_daily", "select_thesis_recent.sql")
DAILY_HISTORY = read_sql("postgres", "quote_daily", "select_thesis_history.sql")
DAILY_HISTORY_SYMBOLS = read_sql("postgres", "quote_daily", "select_thesis_symbols.sql")
SHORT_AND_CREDIT = read_sql("postgres", "krx_stock_short_sale_daily", "select_thesis_latest.sql")
# 6단계(2026-08-22). 증권사 투자의견·목표주가. 리포트 본문은 `recent_documents`가 문서로 준다.
ANALYST_OPINIONS = read_sql("postgres", "stock_analyst_opinion", "select_thesis_recent.sql")

# 툴 인자는 **Pydantic 모델로 선언한다.** JSON Schema는 LangChain이 뽑는다 — 손으로 쓴
# `{"type": "function", ...}` dict는 제공처 wire format이라 이름·타입이 코드와 어긋나도
# 아무도 못 잡는다(2026-08-21 전환).


class ToolArgs(BaseModel):
    """툴 인자의 공통 규칙.

    **못 읽는 값은 거절하지 않고 기본값으로 되돌린다.** 모델이 `hours`에 `"bad"`나 null을
    넣어도 왕복 하나를 오타에 쓰지 않는다. 범위를 자르는 것은 각 툴의 `_clamp_int`다
    (`docs/market-thesis/2-agent.md` 1절 "상한은 코드 상수로 강제한다 — 모델이 인자를
    넘겨도 잘라서 실행한다").

    거절하는 것은 이 층이 아니라 위다: 모르는 툴 이름과 상한 초과는 `ToolLimitExceeded`가
    되어 오류 `ToolMessage`로 모델에게 돌아간다.
    """

    model_config = ConfigDict(extra="ignore")

    @model_validator(mode="before")
    @classmethod
    def _drop_unreadable(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        cleaned = dict(data)
        for name, field in cls.model_fields.items():
            if name not in cleaned:
                continue
            value = cleaned[name]
            caster = field.annotation
            if value is None or not callable(caster):
                cleaned.pop(name)
                continue
            try:
                cleaned[name] = caster(value)
            except (TypeError, ValueError):
                # 키를 빼면 필드 기본값이 들어간다. 그 기본값이 곧 fallback이다.
                cleaned.pop(name)
        return cleaned


class RecentDocumentsArgs(ToolArgs):
    hours: int = Field(
        default=MAX_WINDOW_HOURS,
        description=f"기준 시각에서 거슬러 올라갈 시간. {MIN_WINDOW_HOURS}~{MAX_WINDOW_HOURS}.",
    )
    min_score: int = Field(
        default=MIN_VALUE_SCORE,
        description="가치 점수 하한(0~8). 낮추면 건수가 늘고 잡음도 는다.",
    )


class RecentDisclosuresArgs(ToolArgs):
    hours: int = Field(
        default=MAX_WINDOW_HOURS,
        description=f"기준 시각에서 거슬러 올라갈 시간. {MIN_WINDOW_HOURS}~{MAX_WINDOW_HOURS}.",
    )


class MacroChangesArgs(ToolArgs):
    """인자가 없다. 창은 슬롯이 정한다."""


class PastThesesArgs(ToolArgs):
    subject_code: str = Field(description="이번 실행의 대상 목록 안에 있는 값만. 다른 값은 거절된다.")
    n: int = Field(
        default=MIN_PAST_THESES,
        description=f"최근 몇 건을 볼지. {MIN_PAST_THESES}~{MAX_PAST_THESES}.",
    )


class MacroIndicatorsArgs(ToolArgs):
    kind: str = Field(
        default="government_bond",
        description=(
            "볼 지표 종류. government_bond(각국 국채 금리), money_market(단기 자금시장 금리), "
            "price_index(물가지수), activity(소매판매 등 실물활동). "
            "**단위가 달라 한 번에 하나만 본다.** 모르는 값은 government_bond로 읽는다."
        ),
    )


class NoArgs(ToolArgs):
    """인자가 없다. 창은 슬롯이 정한다."""


class StockFlowsArgs(ToolArgs):
    days: int = Field(
        default=5,
        description=f"종목마다 최근 며칠치 확정 수급을 볼지. {MIN_HISTORY_DAYS}~{MAX_HISTORY_DAYS}.",
    )


class MarketFundsArgs(ToolArgs):
    days: int = Field(
        default=10,
        description=f"최근 며칠치 증시자금을 볼지. {MIN_HISTORY_DAYS}~{MAX_HISTORY_DAYS}.",
    )


class DailyHistoryArgs(ToolArgs):
    symbol: str = Field(
        description=(
            "일봉을 볼 심볼 하나. macro_changes가 돌려준 symbol 값을 그대로 쓴다"
            "(예: SP500_FUT, USDKRW, VIX). **국내 지수(KOSPI, KOSDAQ)는 일봉이 없다** — "
            "없는 심볼을 물으면 쓸 수 있는 목록을 돌려준다."
        )
    )
    days: int = Field(
        default=10,
        description=f"최근 며칠치를 볼지. {MIN_HISTORY_DAYS}~{MAX_HISTORY_DAYS}.",
    )


class AnalystOpinionsArgs(ToolArgs):
    ticker: str = Field(
        description="추적 종목 코드 6자리(예: 005930). 추적 목록 밖이면 거절하고 쓸 수 있는 목록을 돌려준다."
    )


TOOL_DESCRIPTIONS: dict[str, str] = {
    "recent_documents": (
        "최근 평가된 경제 문서 중 가치 점수가 높은 것들. 제목, 발행 시각, 방향, 점수, "
        "관련 종목 티커, 그리고 앞선 평가가 남긴 새 사실과 판단 근거를 준다. "
        "source_slug가 naver_research_로 시작하면 증권사 리서치 리포트다 — 제목 끝에 증권사 이름이 있고, "
        "종목분석은 요약 첫머리에 투자의견·목표가가 있다."
    ),
    "recent_disclosures": "추적 종목에 대해 최근 접수된 DART 공시. 회사명, 보고서명, 접수일, 감지 시각을 준다.",
    "macro_changes": (
        "분석 창 동안 해외 지수·선물·환율이 얼마나 움직였나. 첫 봉 대비 마지막 봉의 변화를 준다. "
        "금리 계열은 퍼센트가 아니라 bp 차이로 준다. "
        "**밤사이 미국장이 얼마나 움직였나는 us_market_close로 본다** — 이 툴의 창 변화는 창 첫 봉 "
        "대비라 마감 직전 몇 시간만 쌓이는 현물 지수는 거의 0으로 보인다."
    ),
    "us_market_close": (
        "밤사이 미국장 마감. 미국 지수·선물·원자재·환율·금리의 마감 종가와 **전일 정규장 종가 대비** "
        "등락을 준다(금리 계열은 퍼센트가 아니라 bp). 한국 장이 열리기 전 가장 먼저 볼 값이다. "
        "빈 배열은 이 창에 미국 봉이 없다는 뜻이지 움직이지 않았다는 뜻이 아니다 — 장후 슬롯의 창은 "
        "당일 09:00부터라 미국 세션이 창 밖이다."
    ),
    "past_theses": (
        "이 대상에 대해 전에 낸 장전 추론과 그 결과. 그때의 세 확률·세 이유, 지평별 실제 등락률과 "
        "Brier 점수, 사후 해설과 판정을 준다. 같은 실수를 반복하고 있는지 볼 수 있다."
    ),
    "macro_indicators": (
        "각국 국채 금리 곡선과 물가·실물 지표의 최신 관측값, 그리고 직전 값 대비 변화. "
        "미국·한국·일본·영국·독일·유로 지역 등의 만기별 금리를 만기와 나라와 함께 준다. "
        "금리 변화는 퍼센트가 아니라 bp다. 시세(macro_changes)로는 안 보이는 채권 시장을 본다."
    ),
    "market_investor_flows": (
        "코스피·코스닥의 외국인·기관·개인 장중 누적 순매수. 지수가 왜 그렇게 움직였는지를 "
        "누가 샀고 누가 팔았나로 본다. 금액 단위는 백만원이다."
    ),
    "market_breadth": (
        "코스피·코스닥의 상승·보합·하락 종목 수와 상한가·하한가 수. 지수 등락률만으로는 "
        "안 보이는 것을 본다 — 지수는 올랐는데 하락 종목이 더 많은 날이 있다."
    ),
    "stock_investor_flows": (
        "추적 종목의 최근 확정 수급(외국인·기관·개인 순매수)과 오늘의 장중 추정치. "
        "확정은 마감 뒤 값이고 추정은 장중 값이라 따로 표시해 준다."
    ),
    "market_funds": (
        "고객예탁금, 신용융자 잔고, 미수금 등 국내 증시자금의 최근 추이. 살 돈이 늘고 있는지 줄고 있는지를 본다."
    ),
    "daily_history": (
        "심볼 하나의 최근 일봉(시가·고가·저가·종가·거래량). macro_changes가 창 하나의 양 끝만 "
        "주는 것과 달리 며칠치 추세를 준다 — '어제 하루 빠진 것'과 '닷새째 빠지는 중'을 가른다."
    ),
    "short_and_credit": (
        "추적 종목의 최신 공매도 수량·비중, 대차 잔고, 신용융자 잔고. 셋이 서로의 재고라 "
        "한 표로 준다. 수집을 최근에 시작해 아직 며칠치뿐일 수 있다."
    ),
    "analyst_opinions": (
        "추적 종목 하나에 대한 증권사 애널리스트의 최근 투자의견·목표주가. 발표일, 증권사, 의견, "
        "직전 의견, 목표가, 발표 전일 종가, 목표가 괴리율을 최신 발표부터 준다. 의견이 바뀌었는지는 "
        "의견과 직전 의견을 비교해 읽는다. 같은 증권사가 같은 날 낸 리포트가 수집돼 있으면 그 요약이 "
        "reason에 함께 온다 — 왜 그 목표가인지가 거기 있다. 인용할 ref가 붙은 리포트 전문은 "
        "recent_documents가 naver_research_* 문서로 준다."
    ),
}


class ToolLimitExceeded(RuntimeError):
    """상한에 걸려 실행하지 않았다. 오류 `ToolMessage`가 되어 모델에게 돌아간다."""


def tool_node(toolbox: "ThesisToolbox") -> ToolNode:
    """툴 실행 노드. 두 그래프(`ThesisBuilder`·`FollowupNarrator`)가 같은 것을 쓴다.

    **`handle_tool_errors`에 타입을 준다.** `ToolLimitExceeded`(상한 초과·모르는 툴·대상
    목록 밖)만 오류 `ToolMessage`가 되어 모델이 고쳐 부를 기회를 얻고, psycopg 오류 같은
    나머지는 그대로 올라가 태스크를 죽인다.

    기본값(`True`)을 쓰면 **연결 끊김이 "결과 없음"으로 위장된다.** 빈 결과는 "그 창에
    문서가 없다"는 뜻이어야 한다는 규칙이 거기서 깨진다.
    """
    return ToolNode(toolbox.tools, handle_tool_errors=(ToolLimitExceeded,))


class ThesisToolbox:
    """읽기 전용 툴 셋과 근거 레지스트리.

    **모든 창의 끝은 `as_of_at`이다.** `hours`는 `as_of_at`에서 거슬러 올라가는 길이이지
    `now()`에서가 아니다. SQL 술어는 event-time 컬럼으로 건다.

    **DB 오류는 위장하지 않는다.** 연결 끊김이나 SQL 오류를 빈 결과로 바꾸지 않고 그대로
    올려 태스크를 실패시킨다. 빈 결과는 "그 창에 문서가 없다"는 뜻이어야 한다.
    """

    def __init__(
        self,
        connection: Connection,
        *,
        as_of_at: datetime,
        macro_window_start: datetime,
        watched_codes: Sequence[str],
        subject_codes: Sequence[str] = (),
    ) -> None:
        self._connection = connection
        self._as_of_at = as_of_at
        self._macro_window_start = macro_window_start
        self._watched_codes = list(watched_codes)
        # `past_theses`가 볼 수 있는 대상. 이번 실행의 목록 밖은 거절한다 — 모델이 아무
        # 종목이나 조회하며 문맥을 채우게 두지 않는다.
        self._subject_codes = frozenset(subject_codes)
        self._registry: dict[str, Evidence] = {}
        self._calls = 0
        self._chars = 0
        self._tools = self._build_tools()
        self._by_name = {tool.name: tool for tool in self._tools}

    def _build_tools(self) -> list[BaseTool]:
        """`ToolNode`와 `bind_tools`에 그대로 넘길 툴 목록.

        `StructuredTool.from_function`이 `args_schema`에서 JSON Schema를 뽑으므로 우리가
        스키마를 손으로 쓰지 않는다. 함수는 **바인드된 메서드**다 — 툴이 연결·`as_of_at`·
        레지스트리·상한 같은 이 객체의 상태를 봐야 해서 모듈 수준 `@tool`을 쓸 수 없다.
        """
        return [
            StructuredTool.from_function(
                func=self._tool_recent_documents,
                name="recent_documents",
                description=TOOL_DESCRIPTIONS["recent_documents"],
                args_schema=RecentDocumentsArgs,
            ),
            StructuredTool.from_function(
                func=self._tool_recent_disclosures,
                name="recent_disclosures",
                description=TOOL_DESCRIPTIONS["recent_disclosures"],
                args_schema=RecentDisclosuresArgs,
            ),
            StructuredTool.from_function(
                func=self._tool_macro_changes,
                name="macro_changes",
                description=TOOL_DESCRIPTIONS["macro_changes"],
                args_schema=MacroChangesArgs,
            ),
            StructuredTool.from_function(
                func=self._tool_us_market_close,
                name="us_market_close",
                description=TOOL_DESCRIPTIONS["us_market_close"],
                args_schema=NoArgs,
            ),
            StructuredTool.from_function(
                func=self._tool_past_theses,
                name="past_theses",
                description=TOOL_DESCRIPTIONS["past_theses"],
                args_schema=PastThesesArgs,
            ),
            StructuredTool.from_function(
                func=self._tool_macro_indicators,
                name="macro_indicators",
                description=TOOL_DESCRIPTIONS["macro_indicators"],
                args_schema=MacroIndicatorsArgs,
            ),
            StructuredTool.from_function(
                func=self._tool_market_investor_flows,
                name="market_investor_flows",
                description=TOOL_DESCRIPTIONS["market_investor_flows"],
                args_schema=NoArgs,
            ),
            StructuredTool.from_function(
                func=self._tool_market_breadth,
                name="market_breadth",
                description=TOOL_DESCRIPTIONS["market_breadth"],
                args_schema=NoArgs,
            ),
            StructuredTool.from_function(
                func=self._tool_stock_investor_flows,
                name="stock_investor_flows",
                description=TOOL_DESCRIPTIONS["stock_investor_flows"],
                args_schema=StockFlowsArgs,
            ),
            StructuredTool.from_function(
                func=self._tool_market_funds,
                name="market_funds",
                description=TOOL_DESCRIPTIONS["market_funds"],
                args_schema=MarketFundsArgs,
            ),
            StructuredTool.from_function(
                func=self._tool_daily_history,
                name="daily_history",
                description=TOOL_DESCRIPTIONS["daily_history"],
                args_schema=DailyHistoryArgs,
            ),
            StructuredTool.from_function(
                func=self._tool_short_and_credit,
                name="short_and_credit",
                description=TOOL_DESCRIPTIONS["short_and_credit"],
                args_schema=NoArgs,
            ),
            StructuredTool.from_function(
                func=self._tool_analyst_opinions,
                name="analyst_opinions",
                description=TOOL_DESCRIPTIONS["analyst_opinions"],
                args_schema=AnalystOpinionsArgs,
            ),
        ]

    @property
    def tools(self) -> list[BaseTool]:
        """`ToolNode(toolbox.tools)`와 `llm.invoke(..., tools=toolbox.tools)`가 쓴다."""
        return self._tools

    @property
    def registry(self) -> dict[str, Evidence]:
        """`ref → Evidence`. 답변 검증과 `thesis_evidence` 저장의 원본이다."""
        return self._registry

    @property
    def call_count(self) -> int:
        return self._calls

    # --- 툴 본체 ---------------------------------------------------------
    # 시그니처가 곧 스키마다. 반환은 `ToolMessage`에 실릴 본문 문자열이다.

    def _tool_recent_documents(self, hours: int, min_score: int) -> str:
        self._charge()
        return self._as_evidence_body(self._recent_documents({"hours": hours, "min_score": min_score}))

    def _tool_recent_disclosures(self, hours: int) -> str:
        self._charge()
        return self._as_evidence_body(self._recent_disclosures({"hours": hours}))

    def _tool_macro_changes(self) -> str:
        self._charge()
        return self._as_evidence_body(self._macro_changes({}))

    def _tool_us_market_close(self) -> str:
        self._charge()
        return self._as_evidence_body(self._us_market_close())

    def _tool_past_theses(self, subject_code: str, n: int) -> str:
        # **레지스트리에 넣지 않는다.** 자기 과거 추론은 근거가 아니다 — 근거 종류는
        # document·disclosure·macro_change 셋 그대로 두고, 이 툴은 문맥으로만 쓴다.
        self._charge()
        body = json.dumps(
            self._past_theses({"subject_code": subject_code, "n": n}),
            ensure_ascii=False,
            default=str,
        )
        self._chars += len(body)
        return body

    # 아래 일곱은 근거(`Evidence`)를 만들지 않는다. `thesis_evidence`의 근거 종류는
    # document·disclosure·macro_change 셋 그대로 두고, 이들은 **문맥으로만** 쓴다.
    # `past_theses`와 같은 취급이다 — 시장 상태는 인용할 "출처"가 아니라 관측이다.

    def _tool_macro_indicators(self, kind: str) -> str:
        self._charge()
        chosen = kind if kind in INDICATOR_KINDS else INDICATOR_KINDS[0]
        rows = self._fetch(
            INDICATOR_LATEST,
            {"kinds": [chosen], "as_of_at": self._as_of_at, "limit": MAX_INDICATOR_RESULTS},
        )
        as_basis_points = chosen in BASIS_POINT_INDICATOR_KINDS
        return self._body(
            {
                "kind": chosen,
                "unit_note": "변화는 bp다" if as_basis_points else "변화는 값 그대로다",
                "series": [_indicator_row(row, as_basis_points=as_basis_points) for row in rows],
            }
        )

    def _tool_market_investor_flows(self) -> str:
        self._charge()
        rows = self._fetch(MARKET_FLOWS, self._snapshot_window())
        return self._body(
            [
                {
                    "market_code": row[0],
                    "observed_at": row[1],
                    "foreign_net_buy_amount": _number(row[2]),
                    "institution_net_buy_amount": _number(row[3]),
                    "individual_net_buy_amount": _number(row[4]),
                    "pension_fund_net_buy_qty": _number(row[5]),
                    "investment_trust_net_buy_qty": _number(row[6]),
                    "amount_unit": "백만원",
                }
                for row in rows
            ]
        )

    def _tool_market_breadth(self) -> str:
        self._charge()
        rows = self._fetch(MARKET_BREADTH, self._snapshot_window())
        return self._body(
            [
                {
                    "symbol": row[0],
                    "observed_at": row[1],
                    "rising": row[2],
                    "unchanged": row[3],
                    "falling": row[4],
                    "upper_limit": row[5],
                    "lower_limit": row[6],
                }
                for row in rows
            ]
        )

    def _tool_stock_investor_flows(self, days: int) -> str:
        self._charge()
        span = _clamp_int(days, MIN_HISTORY_DAYS, MAX_HISTORY_DAYS, 5)
        settled = self._fetch(
            STOCK_FLOWS,
            {"stock_codes": self._watched_codes, "as_of_at": self._as_of_at, "days": span},
        )
        estimates = self._fetch(
            STOCK_FLOW_ESTIMATES,
            {"stock_codes": self._watched_codes, "as_of_at": self._as_of_at},
        )
        return self._body(
            {
                "settled": [
                    {
                        "stock_code": row[0],
                        "business_date": row[1],
                        "close_price": _number(row[2]),
                        "volume": _number(row[3]),
                        "foreign_net_buy_qty": _number(row[4]),
                        "institution_net_buy_qty": _number(row[5]),
                        "individual_net_buy_qty": _number(row[6]),
                        "foreign_net_buy_amount": _number(row[7]),
                        "institution_net_buy_amount": _number(row[8]),
                        "individual_net_buy_amount": _number(row[9]),
                    }
                    for row in settled
                ],
                "intraday_estimate": [
                    {
                        "stock_code": row[0],
                        "business_date": row[1],
                        "source_time_code": row[2],
                        "collected_at": row[3],
                        "foreign_net_buy_qty": _number(row[4]),
                        "institution_net_buy_qty": _number(row[5]),
                        "total_net_buy_qty": _number(row[6]),
                    }
                    for row in estimates
                ],
                "note": "settled는 마감 뒤 확정값, intraday_estimate는 장중 추정값이다. 둘은 어긋날 수 있다",
            }
        )

    def _tool_market_funds(self, days: int) -> str:
        self._charge()
        span = _clamp_int(days, MIN_HISTORY_DAYS, MAX_HISTORY_DAYS, 10)
        rows = self._fetch(MARKET_FUNDS, {"as_of_at": self._as_of_at, "days": span})
        return self._body(
            [
                {
                    "business_date": row[0],
                    "index_close": _number(row[1]),
                    "index_change": _number(row[2]),
                    "customer_deposit": _number(row[3]),
                    "customer_deposit_change": _number(row[4]),
                    "credit_loan_balance": _number(row[5]),
                    "unsettled_amount": _number(row[6]),
                    "turnover_ratio": _number(row[7]),
                }
                for row in rows
            ]
        )

    def _tool_daily_history(self, symbol: str, days: int) -> str:
        """심볼 하나의 일봉. **없는 심볼이면 쓸 수 있는 목록을 함께 돌려준다.**

        2026-08-21 실측: `quote_daily`에 KOSPI·KOSDAQ 일봉이 없다. 국내 지수는 분봉만
        수집하고 일봉 테이블에는 해외 지수만 들어 있다. 그냥 빈 배열을 주면 모델이
        "이력이 없다"가 아니라 "움직임이 없었다"로 읽을 수 있다.
        """
        self._charge()
        span = _clamp_int(days, MIN_HISTORY_DAYS, MAX_HISTORY_DAYS, 10)
        wanted = str(symbol).strip()
        rows = self._fetch(
            DAILY_HISTORY,
            {"symbol": wanted, "as_of_at": self._as_of_at, "days": span},
        )
        if not rows:
            available = self._fetch(DAILY_HISTORY_SYMBOLS, {"as_of_at": self._as_of_at})
            return self._body(
                {
                    "symbol": wanted,
                    "bars": [],
                    "note": f"{wanted}의 일봉이 없다. 아래 심볼만 일봉을 갖는다",
                    "available_symbols": [{"symbol": row[0], "label": row[1], "kind": row[2]} for row in available],
                }
            )
        return self._body(
            {
                "symbol": wanted,
                "bars": [
                    {
                        "label": row[1],
                        "kind": row[2],
                        "country": row[3],
                        "business_date": row[4],
                        "open": _number(row[5]),
                        "high": _number(row[6]),
                        "low": _number(row[7]),
                        "close": _number(row[8]),
                        "volume": _number(row[9]),
                    }
                    for row in rows
                ],
            }
        )

    def _tool_short_and_credit(self) -> str:
        self._charge()
        rows = self._fetch(
            SHORT_AND_CREDIT,
            {"stock_codes": self._watched_codes, "as_of_at": self._as_of_at},
        )
        return self._body(
            [
                {
                    "stock_code": row[0],
                    "label": row[1],
                    "business_date": row[2],
                    "short_sale_quantity": _number(row[3]),
                    "short_sale_volume_ratio": _number(row[4]),
                    "short_sale_amount": _number(row[5]),
                    "lending_balance_quantity": _number(row[6]),
                    "lending_balance_change_quantity": _number(row[7]),
                    "credit_loan_balance_quantity": _number(row[8]),
                    "credit_loan_balance_amount": _number(row[9]),
                    "credit_loan_balance_rate": _number(row[10]),
                }
                for row in rows
            ]
        )

    def _tool_analyst_opinions(self, ticker: str) -> str:
        """종목 하나의 최근 투자의견. 문맥 툴이라 레지스트리에 넣지 않는다 — 인용할 출처는
        리포트 문서(`recent_documents`)이고 이것은 시장 참여자의 관측이다.

        추적 목록 밖 종목은 거절한다. `past_theses`의 `subject_code`와 같은 이유다 — 모델이
        아무 종목이나 조회하며 문맥을 채우게 두지 않는다.
        """
        self._charge()
        code = str(ticker or "").strip()
        if code not in self._watched_codes:
            raise ToolLimitExceeded(f"추적 종목 밖이다: {code!r}. 쓸 수 있는 것은 {sorted(self._watched_codes)}")
        rows = self._fetch(
            ANALYST_OPINIONS,
            {"stock_code": code, "as_of_at": self._as_of_at, "limit": MAX_TOOL_RESULTS},
        )
        return self._body(
            {
                "stock_code": code,
                "opinions": [_opinion_detail(row) for row in rows],
            }
        )

    def _snapshot_window(self) -> dict[str, Any]:
        """장중 스냅샷 툴의 창. 끝은 `as_of_at`, 시작은 거기서 `SNAPSHOT_LOOKBACK`만큼 앞."""
        return {"window_start": self._as_of_at - SNAPSHOT_LOOKBACK, "as_of_at": self._as_of_at}

    def _fetch(self, statement: str, parameters: dict[str, Any]) -> list[Sequence[Any]]:
        with self._connection.cursor() as cursor:
            cursor.execute(statement, parameters)
            return list(cursor.fetchall())

    def _body(self, payload: Any) -> str:
        """근거를 만들지 않는 툴의 반환. 문자 예산만 단다."""
        body = json.dumps(payload, ensure_ascii=False, default=str)
        self._chars += len(body)
        return body

    def _as_evidence_body(self, items: list[Evidence]) -> str:
        for item in items:
            self._registry[item.ref] = item
        body = json.dumps([_tool_row(item) for item in items], ensure_ascii=False)
        self._chars += len(body)
        return body

    def _charge(self) -> None:
        """호출 한 번을 상한에 단다. 넘으면 실행하지 않고 `ToolLimitExceeded`다."""
        self._calls += 1
        if self._calls > MAX_TOOL_CALLS:
            raise ToolLimitExceeded(f"상한 초과: 이 실행의 tool call이 {MAX_TOOL_CALLS}회를 넘었다. 조사를 끝내라")
        if self._chars >= MAX_TOOL_RESULT_CHARS:
            raise ToolLimitExceeded(
                f"상한 초과: 툴 결과가 누적 {MAX_TOOL_RESULT_CHARS}자에 이르렀다. 이미 받은 것으로 답하라"
            )

    def run(self, name: str, arguments: dict[str, Any]) -> str:
        """이름으로 툴 하나를 부른다. `ToolNode`를 거치지 않는 유일한 경로다.

        운영 흐름은 `ToolNode`가 돌리고 이 메서드는 **툴 하나를 따로 확인할 때** 쓴다
        (테스트, 노트북). 같은 `StructuredTool`을 지나가므로 인자 검증과 상한 계산이
        운영 경로와 어긋나지 않는다.

        모르는 툴은 `ToolLimitExceeded`다. 부르는 쪽이 그것을 오류 `ToolMessage`에 담아
        모델이 고쳐 부를 기회를 준다.
        """
        tool = self._by_name.get(name)
        if tool is None:
            raise ToolLimitExceeded(f"모르는 툴 이름이다: {name!r}. 쓸 수 있는 것은 {sorted(self._by_name)}")
        return tool.invoke(arguments)

    def _past_theses(self, arguments: dict[str, Any]) -> list[dict[str, Any]]:
        """툴 판 `past_theses`. 대상 목록 밖을 거절하고 건수를 자른 뒤 모듈 함수에 맡긴다.

        장전은 같은 조회를 프롬프트에 미리 싣는다(`PREFETCHED_PAST_THESES`). 툴은 모델이
        더 보고 싶을 때의 길이고, 툴로 본 것은 `thesis_precedent`에 남지 않는다.
        """
        code = str(arguments.get("subject_code") or "").strip()
        if not self._subject_codes:
            raise ToolLimitExceeded("이번 실행에는 대상 목록이 없어 past_theses를 쓸 수 없다")
        if code not in self._subject_codes:
            raise ToolLimitExceeded(f"대상 목록 밖이다: {code!r}. 쓸 수 있는 것은 {sorted(self._subject_codes)}")
        count = _clamp_int(arguments.get("n"), MIN_PAST_THESES, MAX_PAST_THESES, MIN_PAST_THESES)
        return past_theses(self._connection, as_of_at=self._as_of_at, subject_code=code, n=count)

    def _recent_documents(self, arguments: dict[str, Any]) -> list[Evidence]:
        hours = _clamp_int(arguments.get("hours"), MIN_WINDOW_HOURS, MAX_WINDOW_HOURS, MAX_WINDOW_HOURS)
        min_score = _clamp_int(arguments.get("min_score"), MIN_VALUE_SCORE, MAX_VALUE_SCORE, MIN_VALUE_SCORE)
        window_start = self._as_of_at - timedelta(hours=hours)
        with self._connection.cursor() as cursor:
            cursor.execute(RECENT_DOCUMENTS, (window_start, self._as_of_at, min_score, MAX_TOOL_RESULTS))
            rows = cursor.fetchall()
        return [
            Evidence(
                kind=ThesisEvidenceKind.DOCUMENT,
                ref=evidence_ref(ThesisEvidenceKind.DOCUMENT, str(row[0])),
                title=row[1],
                url=row[2],
                detail=_document_detail(row),
            )
            for row in rows
        ]

    def _recent_disclosures(self, arguments: dict[str, Any]) -> list[Evidence]:
        hours = _clamp_int(arguments.get("hours"), MIN_WINDOW_HOURS, MAX_WINDOW_HOURS, MAX_WINDOW_HOURS)
        window_start = self._as_of_at - timedelta(hours=hours)
        with self._connection.cursor() as cursor:
            cursor.execute(
                RECENT_DISCLOSURES,
                (window_start, self._as_of_at, self._watched_codes, MAX_TOOL_RESULTS),
            )
            rows = cursor.fetchall()
        return [
            Evidence(
                kind=ThesisEvidenceKind.DISCLOSURE,
                ref=evidence_ref(ThesisEvidenceKind.DISCLOSURE, row[0]),
                title=f"{row[2]} {row[3]}",
                url=DART_VIEWER_URL.format(rcept_no=row[0]),
                detail={
                    "stock_code": row[1],
                    "company_name": row[2],
                    "report_name": row[3],
                    "receipt_date": row[4].isoformat(),
                    "detected_at": row[5].isoformat(),
                },
            )
            for row in rows
        ]

    def _macro_changes(self, _arguments: dict[str, Any]) -> list[Evidence]:
        with self._connection.cursor() as cursor:
            cursor.execute(WINDOW_CHANGES, (self._macro_window_start, self._as_of_at, list(MACRO_KINDS)))
            rows = cursor.fetchall()
        return [
            Evidence(
                kind=ThesisEvidenceKind.MACRO_CHANGE,
                ref=evidence_ref(ThesisEvidenceKind.MACRO_CHANGE, row[1]),
                title=f"{row[2]} {_change_label(row[3], row[5], row[6])}",
                url=None,
                detail=_macro_detail(row),
            )
            for row in rows[:MAX_TOOL_RESULTS]
        ]

    def _us_market_close(self) -> list[Evidence]:
        """미국 심볼의 마감 값과 전일 종가 대비 등락.

        **`macro_changes`와 ref가 겹치지 않는다.** 같은 심볼이라도 창 변화와 마감 등락은
        다른 숫자여서, 겹치면 나중에 부른 툴이 앞의 근거를 조용히 덮는다.
        """
        rows = self._fetch(
            US_MARKET_CLOSE,
            {
                "window_start": self._macro_window_start,
                "as_of_at": self._as_of_at,
                "kinds": list(MACRO_KINDS),
            },
        )
        return [
            Evidence(
                kind=ThesisEvidenceKind.MACRO_CHANGE,
                ref=evidence_ref(ThesisEvidenceKind.MACRO_CHANGE, f"{row[1]}{CLOSE_REF_SUFFIX}"),
                title=f"{row[2]} 마감 {_change_label(row[3], row[5], row[4])}",
                url=None,
                detail=_us_close_detail(row),
            )
            for row in rows[:MAX_TOOL_RESULTS]
        ]


def _tool_row(item: Evidence) -> dict[str, Any]:
    """모델에게 보이는 모양. `ref`가 인용 키라 항상 첫 칸이다."""
    row: dict[str, Any] = {"ref": item.ref, "title": item.title, **item.detail}
    if item.url:
        row["url"] = item.url
    return row


def _document_detail(row: Sequence[Any]) -> dict[str, Any]:
    """문서 한 건이 모델에게 보여 줄 값.

    `new_facts`와 `reason`을 함께 준다. 제목·점수만 주면 이유 문장을 쓸 재료가 없어 모델이
    근거를 지어낸다. 둘 합계가 길면 자른다 — 한 건이 컨텍스트를 다 먹으면 안 된다.
    """
    new_facts = list(row[7] or ())
    reason = row[8] or ""
    budget = MAX_ITEM_DETAIL_CHARS - len(reason)
    kept: list[str] = []
    for fact in new_facts:
        if budget - len(fact) < 0:
            break
        kept.append(fact)
        budget -= len(fact)
    return {
        "source": row[3],
        "published_at": row[4].isoformat() if row[4] else None,
        "value_score": row[5],
        "direction": row[6],
        "new_facts": kept,
        "reason": reason[:MAX_ITEM_DETAIL_CHARS],
        "tickers": list(row[9] or ()),
    }


def _opinion_detail(row: Sequence[Any]) -> dict[str, Any]:
    """투자의견 한 건. 같은 증권사·같은 날 리포트 요약이 있으면 사유로 함께 준다.

    KIS는 숫자만 주고 왜 그 의견인지는 안 준다. 사유가 없으면 모델이 목표가 숫자만 보고
    이유를 지어낸다. 요약은 길어서 자른다 — 스무 건이 컨텍스트를 다 먹으면 안 된다.
    """
    detail: dict[str, Any] = {
        "business_date": row[0],
        "broker_name": row[1],
        "opinion": row[2],
        "previous_opinion": row[3],
        "target_price": _number(row[4]),
        "previous_close": _number(row[5]),
        "gap_rate": _number(row[6]),
    }
    reason = row[7] if len(row) > 7 else None
    if reason:
        detail["reason"] = str(reason)[:MAX_OPINION_REASON_CHARS]
    return detail


def _macro_detail(row: Sequence[Any]) -> dict[str, Any]:
    """심볼 하나의 창 변화.

    **금리는 퍼센트가 아니라 bp로 준다.** 4.65→4.70을 `+1.08%`로 주면 모델이 급등으로 읽는다
    (`briefing/market.py`의 `QUOTED_KINDS`와 같은 이유).
    """
    kind, first_close, last_close = row[3], row[5], row[6]
    detail: dict[str, Any] = {
        "kind": kind,
        "country": row[4],
        "first_close": float(first_close),
        "last_close": float(last_close),
        "window_start": row[7].isoformat(),
        "window_end": row[8].isoformat(),
        "bar_count": row[9],
    }
    if kind in BASIS_POINT_KINDS:
        detail["change_bp"] = round(float(last_close - first_close) * 100, 1)
    elif first_close:
        detail["change_pct"] = round(float((last_close - first_close) / first_close) * 100, 2)
    return detail


def _us_close_detail(row: Sequence[Any]) -> dict[str, Any]:
    """심볼 하나의 마감 값. 비교 대상은 창의 첫 봉이 아니라 **전일 정규장 종가**다.

    시각은 `closed_at_kst` 한 칸이고 이름이 시간대를 밝힌다. 다른 툴의 시각 칸은 UTC라
    프롬프트가 "9시간을 더한다"고 알리는데, 마감 시각은 모델이 "어느 날 장이었나"를
    정하는 데 쓰므로 표시 시간대로 준다(`kst_label`).
    """
    kind, close, previous_close = row[3], row[4], row[5]
    detail: dict[str, Any] = {
        "kind": kind,
        "close": float(close),
        "previous_close": float(previous_close),
        "closed_at_kst": kst_label(row[6]),
    }
    if kind in BASIS_POINT_KINDS:
        detail["change_bp"] = round(float(close - previous_close) * 100, 1)
    elif previous_close:
        detail["change_pct"] = round(float((close - previous_close) / previous_close) * 100, 2)
    return detail


def _change_label(kind: str, first_close: Decimal, last_close: Decimal) -> str:
    """제목 뒤에 붙는 변화 표기. Slack 근거 줄에도 그대로 쓰인다."""
    if kind in BASIS_POINT_KINDS:
        return f"{float(last_close - first_close) * 100:+.1f}bp"
    if not first_close:
        return "변화 없음"
    return f"{float((last_close - first_close) / first_close) * 100:+.2f}%"


def _number(value: Any) -> Any:
    """`Decimal`을 JSON이 읽는 수로 바꾼다. `None`은 그대로 둔다.

    **0으로 채우지 않는다.** 결측(아직 안 들어온 값)과 실제 0은 다른 뜻이고, 모델이
    "순매수 0"을 관측으로 읽으면 없는 사실을 근거로 쓴다.
    """
    if value is None:
        return None
    if isinstance(value, Decimal):
        return float(value)
    return value


def _indicator_row(row: Sequence[Any], *, as_basis_points: bool) -> dict[str, Any]:
    """지표 계열 하나의 최신값과 직전값 대비 변화.

    **금리는 bp로 준다.** 4.65에서 4.70으로 가는 것은 `+1.08%`가 아니라 `+5bp`다
    (`_change_label`과 같은 이유). 물가지수처럼 퍼센트가 아닌 계열은 변화를 값 그대로 준다.

    직전값이 없으면 `change`를 만들지 않는다. 첫 관측을 0 변화로 꾸미지 않기 위해서다.
    """
    value, previous = row[9], row[10]
    detail: dict[str, Any] = {
        "provider": row[0],
        "series_id": row[1],
        "country": row[2],
        "country_name": row[3],
        "label": row[4],
        "maturity_months": row[6],
        "unit": row[7],
        "observation_date": row[8],
        "value": _number(value),
        "previous_date": row[11],
        "previous_value": _number(previous),
    }
    if value is not None and previous is not None:
        difference = Decimal(value) - Decimal(previous)
        detail["change_bp" if as_basis_points else "change"] = (
            round(float(difference) * 100, 1) if as_basis_points else round(float(difference), 4)
        )
    return detail


def _clamp_int(value: Any, low: int, high: int, fallback: int) -> int:
    """모델이 넘긴 인자를 허용 범위로 자른다.

    범위를 벗어난 값에 오류로 답하지 않고 잘라서 실행한다. 상한은 우리가 지키면 되는 것이고,
    한 번 더 왕복하는 값어치가 없다. 숫자가 아니면 기본값을 쓴다.
    """
    try:
        number = int(value)
    except (TypeError, ValueError):
        return fallback
    return max(low, min(high, number))


# ---------------------------------------------------------------------------
# 답변 스키마
# ---------------------------------------------------------------------------


class Subject(BaseModel):
    """추론을 요청할 대상 하나."""

    model_config = ConfigDict(frozen=True)

    kind: ThesisSubjectKind
    code: str
    label: str


class ClaimAnswer(BaseModel):
    """모델이 근거 하나를 어떻게 썼는지. 검증 전 원본이다.

    이유 문장은 산문이라 그래프 엣지에 실을 수 없다. 근거마다 **방향과 경로**를 따로 받아야
    `(:Thesis)-[:CITES {direction, mechanism}]->(:Evidence)`가 된다.
    """

    model_config = ConfigDict(frozen=True)

    ref: str
    direction: Literal["up", "down", "flat"]
    mechanism: str = ""


class ThesisAnswer(BaseModel):
    """모델이 subject 하나에 대해 낸 답. 검증 전 원본이다."""

    model_config = ConfigDict(frozen=True)

    subject_code: str
    prob_up: float = Field(ge=0, le=1)
    prob_down: float = Field(ge=0, le=1)
    prob_flat: float = Field(ge=0, le=1)
    up_reasoning: str = ""
    down_reasoning: str = ""
    flat_reasoning: str = ""
    claims: tuple[ClaimAnswer, ...] = ()


class Answers(BaseModel):
    """모델 응답 전체. 스키마를 강제하되 강제가 안 되는 제공처를 위해 검증도 남긴다."""

    model_config = ConfigDict(frozen=True)

    theses: tuple[ThesisAnswer, ...] = ()


class Claim(BaseModel):
    """레지스트리로 검증을 마친 인용 하나. `thesis_evidence` 행의 direction·mechanism이 된다."""

    model_config = ConfigDict(frozen=True)

    ref: str
    direction: ThesisDirection
    mechanism: str


class ThesisDraft(BaseModel):
    """검증·정규화를 마친 추론 하나. 그대로 `thesis` 행이 된다."""

    model_config = ConfigDict(frozen=True)

    subject: Subject
    prob_up: Decimal
    prob_down: Decimal
    prob_flat: Decimal
    up_reasoning: str
    down_reasoning: str
    flat_reasoning: str
    # 레지스트리로 검증하고 ref 첫 등장 순서로 중복을 없앤 인용. rank는 이 순서다.
    claims: tuple[Claim, ...] = ()

    @property
    def evidence_refs(self) -> tuple[str, ...]:
        return tuple(claim.ref for claim in self.claims)


def normalize_probabilities(
    prob_up: float,
    prob_down: float,
    prob_flat: float,
) -> tuple[Decimal, Decimal, Decimal] | None:
    """세 확률의 합을 정확히 1로 맞춘다. 허용 오차를 넘으면 `None`이다.

    모델에게 직접 1로 맞춰 달라고 프롬프트에 적어 두고, 여기서는 반올림·형식 오차만 흡수한다.
    합이 `PROB_SUM_TOLERANCE`를 넘게 어긋났다는 것은 모델이 규칙을 안 지켰다는 뜻이라
    그 subject를 버린다 — 억지로 정규화하면 모델이 부르지 않은 확률을 우리가 지어내게 된다.
    """
    values = [Decimal(str(prob_up)), Decimal(str(prob_down)), Decimal(str(prob_flat))]
    total = sum(values)
    if total <= 0 or abs(total - 1) > PROB_SUM_TOLERANCE:
        return None

    scaled = [(value / total).quantize(PROB_QUANTUM, rounding=ROUND_HALF_UP) for value in values]
    # 자리수를 맞추면서 생긴 잔차를 가장 큰 칸에 몰아 준다. DB CHECK가 합 오차 0.001 미만을
    # 요구하므로 여기서 정확히 1이 되어야 한다.
    residual = Decimal(1) - sum(scaled)
    largest = max(range(3), key=lambda index: scaled[index])
    scaled[largest] += residual
    return scaled[0], scaled[1], scaled[2]


# ---------------------------------------------------------------------------
# 프롬프트
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = f"""너는 시장 추론 기록기다. 주어진 관측 상태를 읽고, 왜 그렇게 움직였는지 또는
움직일 것 같은지를 **가설로** 적는다.

**너는 예측 정확도로 평가받지 않는다.** 맞고 틀림은 시간이 지나야 알고, 채점은 시스템이
자동으로 한다. 네가 할 일은 "어떤 정보를 근거로 어떤 결론을 냈다"를 남기는 것이다.
확신이 없으면 확률을 고르게 두면 된다. 억지로 한쪽을 고르지 마라.

## 조사

근거가 필요하면 툴을 불러 직접 가져와라. 무엇을 얼마나 볼지는 네가 정한다.
조사가 끝나면 답을 낸다.

## 규칙

- **툴 결과와 관측 상태에 없는 사실·숫자를 쓰지 마라.** 지어낸 근거는 기록을 망친다.
- `claims`에는 인용하는 근거마다 툴이 준 `ref`, 그 근거가 대상을 미는 방향 `direction`
  (`up`/`down`/`flat`), 그 방향으로 작용하는 경로 `mechanism` 한 문장({MAX_MECHANISM_CHARS}자
  이내)을 쓴다. 목록 밖의 ref는 버려진다. 같은 ref는 한 번만 쓴다.
  인용할 것이 없으면 빈 배열로 둔다. **억지 인용이 근거 없음보다 나쁘다.**
- 세 확률 `prob_up`, `prob_down`, `prob_flat`은 각각 0~1이고 **합이 정확히 1이어야 한다.**
- 세 방향의 이유를 **모두** 쓴다. 오를 이유, 내릴 이유, 횡보할 이유가 각각 있다.
  한 방향만 쓰고 나머지를 비우지 마라 — 왜 그 반대를 배제했는지가 기록의 절반이다.
- 각 이유는 {MAX_REASONING_CHARS}자 이내의 한국어다. 넘으면 잘린다.
- 투자 조언, 매수·매도 권유, 목표가를 쓰지 마라.
- 요청 목록에 있는 subject마다 **정확히 하나씩** 답한다. 같은 subject를 두 번 쓰지 마라.

출력 형식:
{{"theses": [{{"subject_code": "", "prob_up": 0.0, "prob_down": 0.0, "prob_flat": 0.0,
 "up_reasoning": "", "down_reasoning": "", "flat_reasoning": "",
 "claims": [{{"ref": "", "direction": "up", "mechanism": ""}}]}}]}}"""

SLOT_INSTRUCTION = {
    RunSlot.PRE_OPEN: (
        "오늘 한국 장이 열리기 전이다. 밤사이 해외 시장과 전일 국내 세션을 근거로 "
        "**오늘 각 대상이 어느 방향으로 움직일지**를 가설로 적어라."
    ),
    RunSlot.POST_CLOSE: ("오늘 한국 장이 닫혔다. 오늘의 세션 등락을 근거로 **왜 그렇게 움직였는지**를 가설로 적어라."),
    RunSlot.POST_NXT_CLOSE: (
        "한국 정규장(KRX)이 15:30에 닫히고 NXT 애프터마켓이 20:00에 닫혔다. 관측 상태에 "
        "정규장 등락(`regular`)과 애프터마켓 등락(`after_hours`)이 따로 있다. "
        "**정규장이 닫힌 뒤 무엇이 애프터마켓을 움직였는지**를 가설로 적어라. "
        "지수(`index_regular`)는 정규장 마감값이라 애프터마켓 움직임을 담지 않는다 — 맥락으로만 읽어라."
    ),
}

INSTRUCTION = """{slot_instruction}

기준 시각(이 시각 이후의 정보는 너에게 주어지지 않는다): {as_of_at}

**툴이 돌려주는 시각(`published_at`, `detected_at`, `window_start`, `window_end`)은 UTC다.**
한국 시장 시각으로 읽으려면 9시간을 더한다. 날짜 필드(`run_date`, `receipt_date`, `session`)는
이미 한국 기준 영업일이라 더하지 않는다.

## 추론 대상
{subjects}

## 관측 상태
```json
{observed_state}
```

## 과거 추론과 결과
같은 대상에 대해 전에 낸 장전 추론과 그 채점·해설이다. 채점은 실제 등락이고, 해설은 사실이
아니라 **그때의 해석**이다. 같은 이유로 같은 방향을 고르고 있다면 그 이유가 이번에도 맞는지
따로 확인하라. 과거 문장을 베끼지 마라.
{past_theses}
"""

# 과거 추론이 없을 때 그 절에 넣는 말. 절 자체를 빼면 프롬프트 모양이 날마다 달라진다.
NO_PAST_THESES = "(없음)"

REPAIR_INSTRUCTION = (
    "이전 응답을 쓸 수 없다. 요청 목록의 subject_code만 쓰고, 세 확률의 합을 정확히 1로 맞추고, "
    "툴이 준 ref만 인용해 JSON 객체 하나를 다시 출력하라."
)


# ---------------------------------------------------------------------------
# Builder — LangGraph
# ---------------------------------------------------------------------------


class ThesisState(TypedDict):
    """추론 한 번의 상태.

    연결·설정 객체는 넣지 않는다. 상태는 트레이스 입력으로 나간다. 레지스트리도 넣지 않는다 —
    조사 중에 자라는 값이라 Toolbox가 들고 있고 노드가 그것을 읽는다.
    """

    # `add_messages` 리듀서를 단다. 노드는 **새로 생긴 메시지만** 돌려주고 병합은
    # 리듀서가 한다 — `ToolNode`가 그 형태로 반환하므로 이게 맞춰야 할 쪽이다.
    messages: Annotated[list[BaseMessage], add_messages]
    # 요청한 대상. 답변을 거를 때 노드가 읽으므로 상태에 있어야 한다.
    subjects: tuple[Subject, ...]
    tool_rounds: int
    drafts: tuple[ThesisDraft, ...] | None
    error: str | None
    attempts: int


class ThesisBuilder:
    """관측 상태를 받아 subject마다 추론 하나를 만든다.

    흐름은 `investigate → (tool_calls 있으면) tools → investigate → … → answer →
    (형식 실패) repair → answer`다. 교정은 한 번뿐이다.

    **실행당 대화 하나에 모든 subject를 한 번에** 준다. subject마다 부르면 모델이 대상들을
    비교하지 못하고 비용도 배로 든다.
    """

    def __init__(self, model: BaseChatModel, toolbox: ThesisToolbox) -> None:
        self._model = model
        self._toolbox = toolbox
        self._schema = response_format(Answers, "market_theses")
        self._tool_node = tool_node(toolbox)
        self._graph = self._build_graph()

    @staticmethod
    def build_messages(
        *,
        run_slot: RunSlot,
        as_of_at: datetime,
        subjects: Sequence[Subject],
        observed_state: dict[str, Any],
        past_theses: Mapping[str, Sequence[Mapping[str, Any]]],
    ) -> list[BaseMessage]:
        """`past_theses`는 subject 코드별 과거 추론 목록(`thesis.past_theses`의 행)이다.

        빈 매핑이면 그 절에 `NO_PAST_THESES`가 들어간다. 장후 리뷰가 그 경우다.
        """
        subject_lines = "\n".join(f"- {subject.code} ({subject.label}, {subject.kind.value})" for subject in subjects)
        shown = {code: rows for code, rows in past_theses.items() if rows}
        past_section = (
            f"```json\n{json.dumps(shown, ensure_ascii=False, indent=2, default=str)}\n```" if shown else NO_PAST_THESES
        )
        return [
            SystemMessage(SYSTEM_PROMPT),
            HumanMessage(
                INSTRUCTION.format(
                    slot_instruction=SLOT_INSTRUCTION[run_slot],
                    as_of_at=kst_label(as_of_at),
                    subjects=subject_lines or "(없음)",
                    observed_state=json.dumps(observed_state, ensure_ascii=False, indent=2, default=str),
                    past_theses=past_section,
                )
            ),
        ]

    def run(
        self,
        *,
        run_slot: RunSlot,
        as_of_at: datetime,
        subjects: Sequence[Subject],
        observed_state: dict[str, Any],
        past_theses: Mapping[str, Sequence[Mapping[str, Any]]],
    ) -> tuple[tuple[ThesisDraft, ...], int]:
        """추론들과 툴 왕복 수. 두 번째도 실패하면 `ThesisError`를 올린다."""
        if not subjects:
            return (), 0
        state: ThesisState = {
            "messages": self.build_messages(
                run_slot=run_slot,
                as_of_at=as_of_at,
                subjects=subjects,
                observed_state=observed_state,
                past_theses=past_theses,
            ),
            "subjects": tuple(subjects),
            "tool_rounds": 0,
            "drafts": None,
            "error": None,
            "attempts": 0,
        }
        final = self._graph.invoke(
            state,
            config={
                "run_name": "build_theses",
                "metadata": {"run_slot": run_slot.value, "subjects": len(subjects)},
            },
        )
        drafts = final.get("drafts")
        if drafts is None:
            raise ThesisError(final.get("error") or "Model did not return any thesis")
        return drafts, final["tool_rounds"]

    def parse(self, raw: str, subjects: Sequence[Subject]) -> tuple[ThesisDraft, ...]:
        """응답을 검증하고 쓸 수 없는 항목을 버린다.

        전부 버려지면 `ThesisError`다. 그건 모델이 요청을 안 보고 답했다는 뜻이라 교정을
        요청할 값어치가 있다. 반대로 **일부만 남는 것은 정상이다** — 요청 목록에 있는데 답에
        없는 subject는 그 슬롯에 없던 것으로 남기고 재요청하지 않는다.
        """
        try:
            parsed = Answers.model_validate_json(json_object(raw))
        except SchemaError as error:
            raise ThesisError(str(error)) from error
        except ValidationError as error:
            raise ThesisError(f"Model returned an unusable object: {error}") from error

        by_code = {subject.code: subject for subject in subjects}
        seen: set[str] = set()
        duplicated: set[str] = set()
        drafts: list[ThesisDraft] = []
        dropped: list[str] = []

        for answer in parsed.theses:
            subject = by_code.get(answer.subject_code)
            if subject is None:
                dropped.append(f"{answer.subject_code}(목록 밖)")
                continue
            if answer.subject_code in seen:
                # 어느 쪽이 진짜인지 알 수 없다. 먼저 넣은 것도 함께 뺀다.
                duplicated.add(answer.subject_code)
                dropped.append(f"{answer.subject_code}(중복)")
                continue
            seen.add(answer.subject_code)
            probabilities = normalize_probabilities(answer.prob_up, answer.prob_down, answer.prob_flat)
            if probabilities is None:
                dropped.append(f"{answer.subject_code}(확률 합 {answer.prob_up + answer.prob_down + answer.prob_flat})")
                continue
            drafts.append(
                ThesisDraft(
                    subject=subject,
                    prob_up=probabilities[0],
                    prob_down=probabilities[1],
                    prob_flat=probabilities[2],
                    up_reasoning=_shorten(answer.up_reasoning),
                    down_reasoning=_shorten(answer.down_reasoning),
                    flat_reasoning=_shorten(answer.flat_reasoning),
                    claims=self._known_claims(answer),
                )
            )

        kept = tuple(draft for draft in drafts if draft.subject.code not in duplicated)
        if dropped:
            logger.warning("dropped %s theses: %s", len(dropped), dropped)
        if parsed.theses and not kept:
            raise ThesisError(f"Model returned {len(parsed.theses)} theses, none of them usable")
        return kept

    def _known_claims(self, answer: ThesisAnswer) -> tuple[Claim, ...]:
        """레지스트리에 있는 ref의 인용만, ref 첫 등장 순서로 중복 없이.

        순서가 곧 `thesis_evidence.rank`다. 같은 ref를 두 번 인용하면 **첫 것이 남는다** — 행이
        ref당 하나라 방향 둘을 담을 수 없다. 목록 밖 ref는 버리고 건수를 로그로 남긴다 —
        조용히 버리면 모델이 무엇을 지어내는지 알 수 없다.
        """
        registry = self._toolbox.registry
        kept: dict[str, Claim] = {}
        unknown: list[str] = []
        for claim in answer.claims:
            if claim.ref not in registry:
                unknown.append(claim.ref)
            elif claim.ref not in kept:
                kept[claim.ref] = Claim(
                    ref=claim.ref,
                    direction=ThesisDirection(claim.direction),
                    mechanism=_shorten_to(claim.mechanism, MAX_MECHANISM_CHARS),
                )
        if unknown:
            logger.warning("%s cited %s refs that no tool returned: %s", answer.subject_code, len(unknown), unknown)
        return tuple(kept.values())

    def _build_graph(self):
        graph = StateGraph(ThesisState)
        graph.add_node("investigate", self._investigate)
        graph.add_node("tools", self._tools)
        graph.add_node("answer", self._answer)
        graph.add_node("repair", self._repair)
        graph.add_edge(START, "investigate")
        graph.add_conditional_edges("investigate", self._after_investigate, {"tools": "tools", "answer": "answer"})
        graph.add_edge("tools", "investigate")
        graph.add_conditional_edges("answer", self._after_answer, {"repair": "repair", END: END})
        graph.add_edge("repair", "answer")
        return graph.compile()

    def _investigate(self, state: ThesisState) -> dict[str, Any]:
        """툴만 바인딩해 부른다. 스키마는 넣지 않는다(`llm.invoke`가 막는다)."""
        reply = llm.invoke(self._model, state["messages"], tools=self._toolbox.tools)
        return {"messages": [reply]}

    def _tools(self, state: ThesisState) -> dict[str, Any]:
        """`ToolNode`가 tool_call을 돌리고 `ToolMessage`를 만든다. 우리는 왕복만 센다.

        **`tool_call_id`마다 `ToolMessage`가 정확히 하나**여야 하는 것도 `ToolNode`가
        보장한다. 손으로 짜던 때는 그것이 우리 책임이었다.

        `handle_tool_errors`에 타입을 준 것이 이 노드의 핵심이다 — `ToolLimitExceeded`만
        오류 `ToolMessage`가 되어 모델이 고쳐 부를 기회를 얻고, **DB 오류는 그대로 올라가
        태스크를 죽인다.** 기본값(`True`)은 둘을 가르지 않아 연결 끊김이 "결과 없음"으로
        위장된다.
        """
        update = self._tool_node.invoke(state)
        return {"messages": update["messages"], "tool_rounds": state["tool_rounds"] + 1}

    def _answer(self, state: ThesisState) -> dict[str, Any]:
        """툴을 빼고 스키마를 강제한다. 제공처가 스키마를 안 받으면 그때만 한 번 더."""
        messages = state["messages"]
        try:
            reply = llm.invoke(self._model, messages, schema=self._schema)
        except UnsupportedResponseFormat as error:
            logger.warning("provider does not accept a response schema; falling back to validation: %s", error)
            reply = llm.invoke(self._model, messages)

        try:
            drafts = self.parse(_text(reply), state["subjects"])
        except ThesisError as error:
            return {"messages": [reply], "drafts": None, "error": str(error)}
        return {"messages": [reply], "drafts": drafts, "error": None}

    def _repair(self, state: ThesisState) -> dict[str, Any]:
        logger.warning("retrying the theses once after %s", state["error"])
        return {
            "messages": [HumanMessage(REPAIR_INSTRUCTION)],
            "attempts": state["attempts"] + 1,
        }

    @staticmethod
    def _after_investigate(state: ThesisState) -> str:
        """툴을 부르자고 했고 왕복 상한이 남았으면 조사를 잇는다."""
        reply = state["messages"][-1]
        if getattr(reply, "tool_calls", None) and state["tool_rounds"] < MAX_TOOL_ROUNDS:
            return "tools"
        return "answer"

    @staticmethod
    def _after_answer(state: ThesisState) -> str:
        if state["drafts"] is not None:
            return END
        return "repair" if state["attempts"] == 0 else END


def _shorten(text: str) -> str:
    """이유가 길면 그 필드만 자른다. 한 문장 때문에 subject 전체를 버리지 않는다."""
    return _shorten_to(text, MAX_REASONING_CHARS)


def _text(reply: AIMessage) -> str:
    """응답 본문. 제공처에 따라 문자열이 아니라 조각 리스트로 온다."""
    content = reply.content
    if isinstance(content, str):
        return content
    return "".join(part if isinstance(part, str) else part.get("text", "") for part in content)


# ---------------------------------------------------------------------------
# 저장 — 첫 성공본 불변
# ---------------------------------------------------------------------------

THESIS_INSERT = read_sql("postgres", "thesis", "insert.sql")
THESIS_SELECT_BY_RUN = read_sql("postgres", "thesis", "select_by_run.sql")
EVIDENCE_INSERT = read_sql("postgres", "thesis_evidence", "insert.sql")


class StoredThesis(BaseModel):
    """저장된 추론 한 행. `select_by_run.sql`의 행 계약이다."""

    model_config = ConfigDict(frozen=True)

    id: int
    run_slot: RunSlot
    run_date: date
    as_of_at: datetime
    dag_run_id: str
    subject_kind: ThesisSubjectKind
    subject_code: str
    label: str
    prob_up: Decimal
    prob_down: Decimal
    prob_flat: Decimal
    up_reasoning: str
    down_reasoning: str
    flat_reasoning: str
    tool_rounds: int
    llm_model: str
    prompt_version: str


WATCHED_INSTRUMENTS = read_sql("postgres", "instrument", "select_watched.sql")

# 추론 대상 지수. `quote_symbol`이 아니라 여기 두는 이유는 이것이 "무엇을 추론할지"의
# 목록이지 "어떤 심볼을 수집할지"가 아니기 때문이다. KOSPI200은 코스피와 거의 같이 움직여
# 대상에서 뺀다 — 같은 판단을 두 번 적는 것이 된다.
INDEX_SUBJECTS: tuple[tuple[str, str], ...] = (("KOSPI", "코스피"), ("KOSDAQ", "코스닥"))


def subjects(connection: Connection) -> tuple[Subject, ...]:
    """이번 실행의 추론 대상. 지수는 코드가, 종목은 `instrument.is_watched`가 정한다.

    종목을 마스터에서 읽는 이유는 추적 종목이 늘 때 이 모듈을 고치지 않기 위해서다.
    지수는 마스터에 없어(그쪽은 `quote_symbol`이다) 코드에 둔다.
    """
    with connection.cursor() as cursor:
        cursor.execute(WATCHED_INSTRUMENTS)
        watched = cursor.fetchall()
    return (
        *(Subject(kind=ThesisSubjectKind.INDEX, code=code, label=label) for code, label in INDEX_SUBJECTS),
        *(Subject(kind=ThesisSubjectKind.STOCK, code=row[0], label=row[1]) for row in watched),
    )


def existing_theses(connection: Connection, *, run_date: date, run_slot: RunSlot) -> tuple[StoredThesis, ...]:
    """이 (날짜, 슬롯)에 이미 저장된 추론.

    **부르는 쪽은 LLM을 부르기 전에 이것을 먼저 본다.** 비어 있지 않으면 모델을 부르지 않는다
    (첫 성공본 불변). 재실행은 기존 행을 읽어 다음 태스크로 넘길 뿐이다.
    """
    with connection.cursor() as cursor:
        cursor.execute(THESIS_SELECT_BY_RUN, (run_date, run_slot.value))
        rows = cursor.fetchall()
    return tuple(_stored(row) for row in rows)


def store_theses(
    connection: Connection,
    *,
    run_date: date,
    run_slot: RunSlot,
    as_of_at: datetime,
    dag_run_id: str,
    drafts: Sequence[ThesisDraft],
    registry: dict[str, Evidence],
    observed_state: dict[str, Any],
    llm_model: str,
    tool_rounds: int,
    precedents: Mapping[str, Sequence[int]],
) -> tuple[StoredThesis, ...]:
    """추론과 근거, 그리고 본 과거 추론을 한 트랜잭션에 쓴다.

    **추론은 `INSERT ... ON CONFLICT DO NOTHING`이다.** 같은 (날짜, 슬롯, subject)에 행이 이미
    있으면 아무 것도 바꾸지 않는다. `RETURNING`이 0행이면 삽입 직전에 다른 실행이 먼저 넣은
    것이므로, 그 경우에도 실패로 보지 않고 저장된 행을 읽어 돌려준다.

    thesis와 evidence를 한 트랜잭션에 쓴다 — 추론만 들어가고 근거가 빠진 상태를 남기지 않는다.
    `precedents`는 subject 코드별로 프롬프트에 실린 과거 thesis ID 목록이고 `thesis_precedent`
    엣지가 된다. 같은 트랜잭션이다 — "무엇을 보고 냈나"도 추론과 함께 들어가거나 함께 빠진다.
    """
    with atomic(connection) as transaction, transaction.cursor() as cursor:
        for draft in drafts:
            cursor.execute(
                THESIS_INSERT,
                (
                    run_slot.value,
                    run_date,
                    as_of_at,
                    dag_run_id,
                    draft.subject.kind.value,
                    draft.subject.code,
                    draft.subject.label,
                    draft.prob_up,
                    draft.prob_down,
                    draft.prob_flat,
                    draft.up_reasoning,
                    draft.down_reasoning,
                    draft.flat_reasoning,
                    json.dumps(observed_state, ensure_ascii=False, default=str),
                    tool_rounds,
                    llm_model,
                    PROMPT_VERSION,
                ),
            )
            returned = cursor.fetchone()
            if returned is None:
                logger.info("thesis for %s %s %s already existed", run_date, run_slot.value, draft.subject.code)
                continue
            _store_evidence(cursor, returned[0], draft.evidence_refs, registry, claims=draft.claims)
            for precedent_id in precedents.get(draft.subject.code, ()):
                cursor.execute(PRECEDENT_INSERT, (returned[0], precedent_id))

    return existing_theses(connection, run_date=run_date, run_slot=run_slot)


def _store_evidence(
    cursor: Cursor,
    thesis_id: int,
    refs: Iterable[str],
    registry: dict[str, Evidence],
    outcome_horizon_days: int | None = None,
    claims: Sequence[Claim] = (),
) -> None:
    """인용 순서를 `rank`로 굳혀 근거를 넣는다. 1부터 센다.

    `outcome_horizon_days`가 `None`이면 원 추론이 인용한 근거이고, 1·3·5면 그 지평의 사후
    해설이 인용한 근거다. 같은 테이블에 들어가고 그 칸이 둘을 가른다.

    `claims`는 원 추론의 인용에만 온다 — ref마다 방향과 경로다. 해설의 인용은 둘 다 NULL이다.
    """
    by_ref = {claim.ref: claim for claim in claims}
    for rank, ref in enumerate(refs, start=1):
        item = registry[ref]
        claim = by_ref.get(ref)
        cursor.execute(
            EVIDENCE_INSERT,
            (
                thesis_id,
                outcome_horizon_days,
                item.kind.value,
                item.ref,
                item.title,
                item.url,
                json.dumps(item.detail, ensure_ascii=False, default=str),
                rank,
                claim.direction.value if claim else None,
                claim.mechanism if claim else None,
            ),
        )


def _stored(row: Sequence[Any]) -> StoredThesis:
    return StoredThesis(
        id=row[0],
        run_slot=row[1],
        run_date=row[2],
        as_of_at=row[3],
        dag_run_id=row[4],
        subject_kind=row[5],
        subject_code=row[6],
        label=row[7],
        prob_up=row[8],
        prob_down=row[9],
        prob_flat=row[10],
        up_reasoning=row[11],
        down_reasoning=row[12],
        flat_reasoning=row[13],
        tool_rounds=row[14],
        llm_model=row[15],
        prompt_version=row[16],
    )


# ---------------------------------------------------------------------------
# 다지평 채점 — LLM 없음
# ---------------------------------------------------------------------------

PENDING_GRADES = read_sql("postgres", "thesis_outcome", "select_pending_grades.sql")
INSERT_GRADE = read_sql("postgres", "thesis_outcome", "insert_grade.sql")
NTH_OPEN_DAY = read_sql("postgres", "market_session", "select_nth_open_day.sql")
STOCK_HORIZON_RETURN = read_sql("postgres", "stock_investor_trade_daily", "select_horizon_return.sql")
INDEX_HORIZON_RETURN = read_sql("postgres", "index_bar", "select_horizon_return.sql")


class PendingGrade(BaseModel):
    """채점을 기다리는 (추론, 지평) 하나. `select_pending_grades.sql`의 행 계약이다."""

    model_config = ConfigDict(frozen=True)

    thesis_id: int
    run_date: date
    as_of_at: datetime
    subject_kind: ThesisSubjectKind
    subject_code: str
    prob_up: Decimal
    prob_down: Decimal
    prob_flat: Decimal
    horizon_days: int


def pending_grades(connection: Connection, horizons: Sequence[int] = HORIZON_DAYS) -> tuple[PendingGrade, ...]:
    """아직 채점하지 않은 (추론, 지평) 전부. `pre_open`만이다."""
    with connection.cursor() as cursor:
        cursor.execute(PENDING_GRADES, (list(horizons),))
        rows = cursor.fetchall()
    return tuple(
        PendingGrade(
            thesis_id=row[0],
            run_date=row[1],
            as_of_at=row[2],
            subject_kind=row[3],
            subject_code=row[4],
            prob_up=row[5],
            prob_down=row[6],
            prob_flat=row[7],
            horizon_days=row[8],
        )
        for row in rows
    )


def nth_open_day(connection: Connection, base_date: date, horizon_days: int) -> date | None:
    """`base_date`부터 세어 `horizon_days`번째 KRX 개장일. 달력이 안 채워졌으면 `None`.

    0이면 `base_date` 자신(개장일일 때)이다. **날짜를 우리가 세지 않는다** — 휴장일에서
    어긋난다. `None`이면 부르는 쪽은 그 조합을 미채점으로 남기고 다음 실행이 다시 집는다.
    """
    with connection.cursor() as cursor:
        cursor.execute(NTH_OPEN_DAY, (base_date, horizon_days))
        row = cursor.fetchone()
    return row[0] if row else None


def horizon_returns(
    connection: Connection,
    *,
    subject_kind: ThesisSubjectKind,
    run_date: date,
    target_date: date,
    codes: Sequence[str],
    base_bar_at: datetime | None = None,
    target_bar_at: datetime | None = None,
) -> dict[str, Decimal]:
    """대상별 누적 등락률. 종가·봉이 없으면 그 대상은 결과에 없다.

    기준가는 지평이 달라도 같다 — 예측일 전 영업일 종가다. 지수는 봉 시각 둘을 받는다
    (KST 경계 계산은 파이썬이 한다).
    """
    if not codes:
        return {}
    if subject_kind is ThesisSubjectKind.STOCK:
        statement, parameters = STOCK_HORIZON_RETURN, (run_date, target_date, list(codes))
    else:
        if base_bar_at is None or target_bar_at is None:
            raise ThesisError("index horizon returns need both bar timestamps")
        statement, parameters = INDEX_HORIZON_RETURN, (base_bar_at, target_bar_at, list(codes))

    with connection.cursor() as cursor:
        cursor.execute(statement, parameters)
        rows = cursor.fetchall()
    return {row[0]: row[4] for row in rows if row[4] is not None}


def store_grade(
    connection: Connection,
    *,
    pending: PendingGrade,
    as_of_at: datetime,
    dag_run_id: str,
    return_pct: Decimal,
    evaluated_at: datetime,
) -> None:
    """한 (추론, 지평)의 채점을 쓴다. 이미 매긴 점수는 덮지 않는다(SQL의 WHERE가 막는다)."""
    outcome = classify_outcome(return_pct, pending.horizon_days)
    score = brier_score(
        prob_up=pending.prob_up,
        prob_down=pending.prob_down,
        prob_flat=pending.prob_flat,
        outcome=outcome,
    )
    with connection.cursor() as cursor:
        cursor.execute(
            INSERT_GRADE,
            (
                pending.thesis_id,
                pending.horizon_days,
                as_of_at,
                dag_run_id,
                evaluated_at,
                return_pct,
                outcome.value,
                score.quantize(Decimal("0.00001")),
            ),
        )


# ---------------------------------------------------------------------------
# 사후 해설 — 원 추론의 이유가 이후 보도로 지지됐는가
# ---------------------------------------------------------------------------

# 해설 프롬프트를 고치면 올린다. `thesis_outcome.prompt_version`에 변형과 함께 저장된다.
NARRATIVE_PROMPT_VERSION = "1"

# 해설 한 편의 상한. 넘으면 그 항목만 자른다.
MAX_NARRATIVE_CHARS = 1000


class NarrativeVariant(StrEnum):
    """해설 프롬프트가 실제 결과를 보느냐 마느냐.

    어느 쪽이 나은지 추측하지 않고 실측으로 갈랐다(`docs/market-thesis/5-followup.md` 12절).

    **`INFORMED`가 기본이다**(2026-08-21 2회차). `BLIND`가 사는 것이 없었다 — 툴 호출·
    레지스트리·서술의 질이 같고, 가격은 어차피 후속 기사로 새어 들어오며, 판정만 체계적으로
    약해졌다(같은 사실을 찾고도 `contradicted` 대신 `unresolved`를 골랐다).

    남겨 두는 이유는 되돌릴 수 있게 하기 위해서다. 독립 사건 둘로 정한 값이라 분기 단위로
    다시 본다.
    """

    INFORMED = "informed"
    BLIND = "blind"


class NarrativeTarget(BaseModel):
    """해설을 붙일 (추론, 지평) 하나. 프롬프트 입력이다."""

    model_config = ConfigDict(frozen=True)

    thesis_id: int
    # 원 추론의 슬롯. 한 날짜에 같은 대상의 장전·장후 추론이 둘 다 있어 `subject_code`만으로는
    # 어느 추론인지 모른다. 해설 호출은 슬롯마다 따로 한다(`FollowupNarrator.run`).
    run_slot: RunSlot
    subject: Subject
    prob_up: Decimal
    prob_down: Decimal
    prob_flat: Decimal
    up_reasoning: str
    down_reasoning: str
    flat_reasoning: str
    # 원 추론이 인용했던 근거 제목. 무엇을 보고 그 이유를 썼는지 모델이 알아야 판정할 수 있다.
    cited_titles: tuple[str, ...] = ()
    # 채점 결과. `informed` 변형만 프롬프트에 싣는다. `post_close` 추론은 채점이 없어 None이다.
    actual_return_pct: Decimal | None = None
    actual_outcome: ThesisDirection | None = None
    brier_score: Decimal | None = None


class NarrativeAnswer(BaseModel):
    """모델이 대상 하나에 대해 낸 해설. 검증 전 원본이다."""

    model_config = ConfigDict(frozen=True)

    subject_code: str
    narrative: str = ""
    # 검증기가 아니라 타입으로 막는다. Literal은 스키마에 enum으로 실려 모델이 애초에
    # 다른 값을 내지 못한다(`assessment.py`의 `direction`과 같은 방식).
    verdict: Literal["supported", "contradicted", "unresolved"] = "unresolved"
    evidence_refs: tuple[str, ...] = ()


class Narratives(BaseModel):
    """모델 응답 전체."""

    model_config = ConfigDict(frozen=True)

    narratives: tuple[NarrativeAnswer, ...] = ()


class NarrativeDraft(BaseModel):
    """검증을 마친 해설 하나. 그대로 `thesis_outcome`의 해설 칸이 된다."""

    model_config = ConfigDict(frozen=True)

    thesis_id: int
    subject_code: str
    narrative: str
    verdict: ThesisVerdict
    evidence_refs: tuple[str, ...] = ()


NARRATIVE_SYSTEM_PROMPT = f"""너는 지나간 시장 추론을 되돌아보는 기록자다.

며칠 전에 쓴 추론과 그 뒤 쌓인 보도를 놓고 두 가지를 남긴다.

1. **해설**(`narrative`) — 그 기간 보도가 무엇을 말하는지. {MAX_NARRATIVE_CHARS}자 이내 한국어.
2. **판정**(`verdict`) — 그 추론이 **든 이유**가 이후 보도로 지지됐는가.

## 판정 규칙 — 여기가 이 작업의 핵심이다

- `supported` — 후속 보도가 원 추론의 이유를 **직접** 뒷받침했다.
- `contradicted` — 후속 보도가 그 이유를 반박했거나 다른 원인을 지목했다.
- `unresolved` — 후속 보도가 그 이유를 다루지 않았다. **이것이 기본값이고 가장 흔한 답이다.**

**`supported`나 `contradicted`를 고르면 그 판단의 근거가 된 ref를 반드시 인용하라.**
인용할 문서가 없으면 `unresolved`다. 근거 없는 판정은 저장 전에 `unresolved`로 내려간다.

**가격이 어느 쪽으로 움직였는지는 판정의 근거가 아니다.** 방향이 맞았는지는 시스템이
Brier 점수로 따로 잰다. 네가 답할 것은 **이유가 맞았는가**이고 그 답은 문서에서만 나온다.
움직임을 보고 이유를 거꾸로 맞추지 마라 — 그건 사후확신이지 검증이 아니다.

## 그 밖의 규칙

- 툴 결과에 없는 사실·숫자를 쓰지 마라.
- **확률을 다시 내지 마라.** 원 추론의 확률은 불변이고, 결과를 아는 상태의 확률은 채점할 수 없다.
- 투자 조언, 매수·매도 권유, 앞으로의 방향 예측을 쓰지 마라.
- 대상마다 **정확히 하나씩** 답한다. 같은 대상을 두 번 쓰지 마라.
- 해설은 단정하지 말고 "이 기사들은 …라고 본다" 형태로 쓴다. 너는 결과를 아는 자리에서
  쓰고 있고 그 자리는 편향돼 있다.

출력 형식:
{{"narratives": [{{"subject_code": "", "narrative": "", "verdict": "unresolved", "evidence_refs": []}}]}}"""

NARRATIVE_INSTRUCTION = """{run_date} {slot_label}에 쓴 추론을 {horizon_days}영업일 뒤에 되돌아본다.

기준 시각(이 시각 이후의 정보는 너에게 주어지지 않는다): {as_of_at}

**툴이 돌려주는 시각(`published_at`, `detected_at`, `window_start`, `window_end`)은 UTC다.**
한국 시장 시각으로 읽으려면 9시간을 더한다. 날짜 필드는 이미 한국 기준 영업일이다.

필요하면 툴로 그동안 쌓인 문서·공시·시세 변화를 직접 가져와라.

## 되돌아볼 추론
{targets}
"""

NARRATIVE_REPAIR_INSTRUCTION = (
    "이전 응답을 쓸 수 없다. 주어진 subject_code만 쓰고, verdict는 "
    "supported·contradicted·unresolved 중 하나로, JSON 객체 하나를 다시 출력하라."
)


class NarrativeState(TypedDict):
    """해설 한 번의 상태. 연결·설정 객체는 넣지 않는다."""

    # `add_messages` 리듀서를 단다. 노드는 **새로 생긴 메시지만** 돌려주고 병합은
    # 리듀서가 한다 — `ToolNode`가 그 형태로 반환하므로 이게 맞춰야 할 쪽이다.
    messages: Annotated[list[BaseMessage], add_messages]
    targets: tuple[NarrativeTarget, ...]
    drafts: tuple[NarrativeDraft, ...] | None
    error: str | None
    attempts: int


class FollowupNarrator:
    """지나간 추론에 사후 해설과 판정을 붙인다. `ThesisBuilder`와 같은 LangGraph 계보다.

    **지평마다 별도 호출이다.** 툴 조회의 기준 시각이 지평마다 달라 한 대화에 섞을 수 없다.
    한 호출 안에서는 그 지평의 모든 대상을 한 번에 준다(건별 호출 금지 규칙 그대로).

    `include_outcome`이 프롬프트 변형을 가른다. 어느 쪽이 나은지는 실측으로 정한다
    (`docs/market-thesis/5-followup.md` 12절).
    """

    def __init__(self, model: BaseChatModel, toolbox: ThesisToolbox, *, include_outcome: bool = True) -> None:
        self._model = model
        self._toolbox = toolbox
        self._include_outcome = include_outcome
        self._schema = response_format(Narratives, "thesis_narratives")
        self._tool_node = tool_node(toolbox)
        self._graph = self._build_graph()

    @property
    def variant(self) -> NarrativeVariant:
        return NarrativeVariant.INFORMED if self._include_outcome else NarrativeVariant.BLIND

    @property
    def prompt_revision(self) -> str:
        """`thesis_outcome.prompt_version`에 저장할 값. 변형을 판에 싣는다.

        `assessment.py`의 `LlmSettings.prompt_revision`이 관점을 판에 싣는 것과 같은 방식이다.
        새 컬럼을 만들지 않고도 어느 변형이 그 행을 썼는지 DB가 증명한다.
        """
        return f"{NARRATIVE_PROMPT_VERSION}/{self.variant.value}"

    def build_messages(
        self,
        *,
        run_date: date,
        run_slot: RunSlot,
        horizon_days: int,
        as_of_at: datetime,
        targets: Sequence[NarrativeTarget],
    ) -> list[BaseMessage]:
        return [
            SystemMessage(NARRATIVE_SYSTEM_PROMPT),
            HumanMessage(
                NARRATIVE_INSTRUCTION.format(
                    run_date=run_date.isoformat(),
                    slot_label=SLOT_LABELS[run_slot],
                    horizon_days=horizon_days,
                    as_of_at=kst_label(as_of_at),
                    targets="\n\n".join(self._render_target(target) for target in targets),
                )
            ),
        ]

    def _render_target(self, target: NarrativeTarget) -> str:
        lines = [
            f"### {target.subject.code} ({target.subject.label})",
            f"- 상승 {target.prob_up:.0%} / 하락 {target.prob_down:.0%} / 횡보 {target.prob_flat:.0%}",
            f"- 상승 이유: {target.up_reasoning}",
            f"- 하락 이유: {target.down_reasoning}",
            f"- 횡보 이유: {target.flat_reasoning}",
        ]
        if target.cited_titles:
            lines.append("- 그때 인용한 근거: " + " · ".join(target.cited_titles))
        if self._include_outcome and target.actual_outcome is not None:
            lines.append(
                f"- **실제 결과**: {target.actual_return_pct:+.2f}% ({target.actual_outcome.value}), "
                f"Brier {target.brier_score}"
            )
        return "\n".join(lines)

    def run(
        self,
        *,
        run_date: date,
        horizon_days: int,
        as_of_at: datetime,
        targets: Sequence[NarrativeTarget],
    ) -> tuple[NarrativeDraft, ...]:
        """해설들. 두 번째도 실패하면 `ThesisError`를 올린다.

        **한 호출은 슬롯 하나다.** 프롬프트 첫 줄이 슬롯을 전제하고, 응답은 `subject_code`로
        대상을 찾는데 같은 날 장전·장후 추론이 같은 대상을 갖는다. 슬롯이 섞이면 한쪽이
        다른 쪽의 해설을 받고 나머지는 영영 미해설로 남는다. 슬롯은 대상에서 읽는다 —
        부르는 쪽이 따로 넘기면 어긋날 수 있다(2026-08-23까지 `PRE_OPEN` 고정이었다).
        """
        if not targets:
            return ()
        slots = {target.run_slot for target in targets}
        if len(slots) != 1:
            raise ThesisError(f"narration targets span {len(slots)} slots; call once per slot: {sorted(slots)}")
        (run_slot,) = slots
        state: NarrativeState = {
            "messages": self.build_messages(
                run_date=run_date,
                run_slot=run_slot,
                horizon_days=horizon_days,
                as_of_at=as_of_at,
                targets=targets,
            ),
            "targets": tuple(targets),
            "drafts": None,
            "error": None,
            "attempts": 0,
        }
        final = self._graph.invoke(
            state,
            config={
                "run_name": "narrate_followups",
                "metadata": {"horizon_days": horizon_days, "run_slot": run_slot.value, "variant": self.variant.value},
            },
        )
        drafts = final.get("drafts")
        if drafts is None:
            raise ThesisError(final.get("error") or "Model did not return any narrative")
        return drafts

    def parse(self, raw: str, targets: Sequence[NarrativeTarget]) -> tuple[NarrativeDraft, ...]:
        """응답을 검증하고 쓸 수 없는 항목을 버린다."""
        try:
            parsed = Narratives.model_validate_json(json_object(raw))
        except SchemaError as error:
            raise ThesisError(str(error)) from error
        except ValidationError as error:
            raise ThesisError(f"Model returned an unusable object: {error}") from error

        by_code = {target.subject.code: target for target in targets}
        seen: set[str] = set()
        drafts: list[NarrativeDraft] = []
        dropped: list[str] = []

        for answer in parsed.narratives:
            target = by_code.get(answer.subject_code)
            if target is None or answer.subject_code in seen:
                dropped.append(answer.subject_code)
                continue
            seen.add(answer.subject_code)
            refs = self._known_refs(answer.subject_code, answer.evidence_refs)
            drafts.append(
                NarrativeDraft(
                    thesis_id=target.thesis_id,
                    subject_code=answer.subject_code,
                    narrative=_shorten_to(answer.narrative, MAX_NARRATIVE_CHARS),
                    verdict=_grounded_verdict(answer.subject_code, answer.verdict, refs),
                    evidence_refs=refs,
                )
            )

        if dropped:
            logger.warning("dropped %s narratives: %s", len(dropped), dropped)
        if parsed.narratives and not drafts:
            raise ThesisError(f"Model returned {len(parsed.narratives)} narratives, none of them usable")
        return tuple(drafts)

    def _known_refs(self, subject_code: str, refs: Sequence[str]) -> tuple[str, ...]:
        """레지스트리에 있는 ref만, 첫 등장 순서로 중복 없이. 순서가 곧 `rank`다."""
        registry = self._toolbox.registry
        kept: list[str] = []
        unknown: list[str] = []
        for ref in refs:
            if ref in registry:
                if ref not in kept:
                    kept.append(ref)
            else:
                unknown.append(ref)
        if unknown:
            logger.warning("%s cited %s refs that no tool returned: %s", subject_code, len(unknown), unknown)
        return tuple(kept)

    def _build_graph(self):
        graph = StateGraph(NarrativeState)
        graph.add_node("investigate", self._investigate)
        graph.add_node("tools", self._tools)
        graph.add_node("answer", self._answer)
        graph.add_node("repair", self._repair)
        graph.add_edge(START, "investigate")
        graph.add_conditional_edges("investigate", self._after_investigate, {"tools": "tools", "answer": "answer"})
        graph.add_edge("tools", "investigate")
        graph.add_conditional_edges("answer", self._after_answer, {"repair": "repair", END: END})
        graph.add_edge("repair", "answer")
        return graph.compile()

    def _investigate(self, state: NarrativeState) -> dict[str, Any]:
        reply = llm.invoke(self._model, state["messages"], tools=self._toolbox.tools)
        return {"messages": [reply]}

    def _tools(self, state: NarrativeState) -> dict[str, Any]:
        """`ThesisBuilder._tools`와 같은 노드다. 여기는 왕복을 세지 않고 상한은
        `ThesisToolbox.call_count`가 본다(`_after_investigate`)."""
        return {"messages": self._tool_node.invoke(state)["messages"]}

    def _answer(self, state: NarrativeState) -> dict[str, Any]:
        messages = state["messages"]
        try:
            reply = llm.invoke(self._model, messages, schema=self._schema)
        except UnsupportedResponseFormat as error:
            logger.warning("provider does not accept a response schema; falling back to validation: %s", error)
            reply = llm.invoke(self._model, messages)

        try:
            drafts = self.parse(_text(reply), state["targets"])
        except ThesisError as error:
            return {"messages": [reply], "drafts": None, "error": str(error)}
        return {"messages": [reply], "drafts": drafts, "error": None}

    def _repair(self, state: NarrativeState) -> dict[str, Any]:
        logger.warning("retrying the narratives once after %s", state["error"])
        return {
            "messages": [HumanMessage(NARRATIVE_REPAIR_INSTRUCTION)],
            "attempts": state["attempts"] + 1,
        }

    def _after_investigate(self, state: NarrativeState) -> str:
        reply = state["messages"][-1]
        if getattr(reply, "tool_calls", None) and self._toolbox.call_count < MAX_TOOL_CALLS:
            return "tools"
        return "answer"

    @staticmethod
    def _after_answer(state: NarrativeState) -> str:
        if state["drafts"] is not None:
            return END
        return "repair" if state["attempts"] == 0 else END


def _grounded_verdict(subject_code: str, verdict: str, refs: Sequence[str]) -> ThesisVerdict:
    """근거 없는 판정을 `unresolved`로 내린다.

    프롬프트에 규칙을 적어 두지만 그것만으로는 역산을 못 막는다. 이 검사는 막는다 —
    문서를 인용하지 못한 `supported`·`contradicted`는 가격을 보고 지어낸 것이다.
    오염을 없애는 장치가 아니라 **되짚을 수 있게 만드는 장치**다.
    """
    chosen = ThesisVerdict(verdict)
    if chosen is not ThesisVerdict.UNRESOLVED and not refs:
        logger.warning("%s answered %s with no evidence; downgrading to unresolved", subject_code, chosen.value)
        return ThesisVerdict.UNRESOLVED
    return chosen


def _shorten_to(text: str, limit: int) -> str:
    """길면 그 항목만 자른다."""
    stripped = text.strip()
    if len(stripped) > limit:
        return stripped[: limit - 1].rstrip() + "…"
    return stripped


# ---------------------------------------------------------------------------
# 해설 저장
# ---------------------------------------------------------------------------

PENDING_NARRATIVES = read_sql("postgres", "thesis_outcome", "select_pending_narratives.sql")
INSERT_NARRATIVE = read_sql("postgres", "thesis_outcome", "insert_narrative.sql")


def pending_narratives(
    connection: Connection,
    *,
    run_date: date,
    horizon_days: int,
) -> tuple[NarrativeTarget, ...]:
    """그 지평에서 아직 해설이 없는 대상. **두 슬롯 모두 온다.**

    채점 값이 있으면 함께 담는다. 프롬프트에 실을지는 `FollowupNarrator`의
    `include_outcome`이 정한다 — 이 함수는 있는 대로 준다.

    `cited_titles`는 여기서 채우지 않는다. 원 추론이 인용한 근거 제목은
    `thesis_evidence`에 있고, 부르는 쪽이 필요하면 붙인다.
    """
    if horizon_days not in NARRATED_HORIZON_DAYS:
        raise ThesisError(f"horizon {horizon_days} does not take a narrative; known: {NARRATED_HORIZON_DAYS}")
    with connection.cursor() as cursor:
        cursor.execute(PENDING_NARRATIVES, (horizon_days, run_date))
        rows = cursor.fetchall()
    return tuple(
        NarrativeTarget(
            thesis_id=row[0],
            run_slot=RunSlot(row[2]),
            subject=Subject(kind=row[3], code=row[4], label=row[5]),
            prob_up=row[6],
            prob_down=row[7],
            prob_flat=row[8],
            up_reasoning=row[9],
            down_reasoning=row[10],
            flat_reasoning=row[11],
            actual_return_pct=row[12],
            actual_outcome=row[13],
            brier_score=row[14],
        )
        for row in rows
    )


def store_narratives(
    connection: Connection,
    *,
    horizon_days: int,
    as_of_at: datetime,
    dag_run_id: str,
    drafts: Sequence[NarrativeDraft],
    registry: dict[str, Evidence],
    llm_model: str,
    prompt_revision: str,
) -> int:
    """해설과 그 근거를 한 트랜잭션에 쓴다. 쓴 건수를 돌려준다.

    **해설 갱신과 근거 INSERT가 한 트랜잭션이다.** 해설만 들어가고 근거가 빠진 상태를
    남기지 않는다 — 근거 없는 판정은 되짚을 수 없다.

    이미 해설이 있는 행은 SQL의 `WHERE narrative IS NULL`이 막는다. 그때 근거를 다시
    넣지 않도록 `RETURNING`으로 실제 갱신 여부를 확인한다.
    """
    if horizon_days not in NARRATED_HORIZON_DAYS:
        raise ThesisError(f"horizon {horizon_days} does not take a narrative; known: {NARRATED_HORIZON_DAYS}")

    stored = 0
    with atomic(connection) as transaction, transaction.cursor() as cursor:
        for draft in drafts:
            cursor.execute(
                INSERT_NARRATIVE,
                (
                    draft.thesis_id,
                    horizon_days,
                    as_of_at,
                    dag_run_id,
                    draft.narrative,
                    draft.verdict.value,
                    datetime.now(UTC),
                    llm_model,
                    prompt_revision,
                ),
            )
            if cursor.rowcount == 0:
                # 다른 실행이 먼저 썼다. 근거를 덧붙이면 그 해설과 어긋난 인용이 남는다.
                logger.info("thesis %s already had a T+%s narrative", draft.thesis_id, horizon_days)
                continue
            _store_evidence(cursor, draft.thesis_id, draft.evidence_refs, registry, horizon_days)
            stored += 1
    return stored


# ---------------------------------------------------------------------------
# Slack 렌더링
#
# `briefing/market.py`가 자기 도메인 렌더링을 갖는 것과 같다. thesis는 정기 리포트
# 3부작과 다른 도메인이라 `briefing/` 아래 두지 않는다.
# ---------------------------------------------------------------------------

EVIDENCE_SELECT_TOP = read_sql("postgres", "thesis_evidence", "select_top_by_thesis_ids.sql")
OUTCOME_SELECT_BY_IDS = read_sql("postgres", "thesis_outcome", "select_by_thesis_ids.sql")

# 근거 줄에 그릴 개수. 세 개를 넘으면 한 줄이 길어져 읽히지 않는다.
SLACK_EVIDENCE_LIMIT = 3

# 되돌아보기 섹션을 그릴 지평. T+1·T+3까지 매일 보내면 하루 세 덩이가 더 붙어 원래 알림이
# 묻힌다. T+5가 해설이 가장 굳은 시점이기도 하다.
SLACK_REVIEW_HORIZON = 5

SLOT_HEADERS = {
    RunSlot.PRE_OPEN: "🔮 장전 전망",
    RunSlot.POST_CLOSE: "🔎 장후 리뷰",
    RunSlot.POST_NXT_CLOSE: "🌙 애프터마켓 리뷰",
}

# 되돌아보기 제목에 쓰는 짧은 이름. 헤더의 이모지까지 반복하면 줄이 길어진다.
SLOT_LABELS = {
    RunSlot.PRE_OPEN: "장전 전망",
    RunSlot.POST_CLOSE: "장후 리뷰",
    RunSlot.POST_NXT_CLOSE: "애프터마켓 리뷰",
}

DIRECTION_MARKS = {"up": "▲", "down": "▼", "flat": "–"}
DIRECTION_NAMES = {"up": "상승", "down": "하락", "flat": "횡보"}

# 판정을 영문 enum 값 그대로 보이면 읽는 사람이 매번 해석해야 한다. 이모지가 앞에서 갈라 준다.
VERDICT_LABELS = {
    ThesisVerdict.SUPPORTED: "✅ 이유 지지됨",
    ThesisVerdict.CONTRADICTED: "❌ 이유 반박됨",
    ThesisVerdict.UNRESOLVED: "❔ 판단 보류",
}


class StoredOutcome(BaseModel):
    """저장된 지평 결과 한 행. `thesis_outcome/select_by_thesis_ids.sql`의 행 계약이다."""

    model_config = ConfigDict(frozen=True)

    thesis_id: int
    horizon_days: int
    actual_return_pct: Decimal | None = None
    actual_outcome: ThesisDirection | None = None
    brier_score: Decimal | None = None
    narrative: str | None = None
    verdict: ThesisVerdict | None = None


class StoredEvidence(BaseModel):
    """저장된 근거 한 행. Slack 근거 줄이 쓴다."""

    model_config = ConfigDict(frozen=True)

    thesis_id: int
    evidence_title: str
    evidence_url: str | None = None
    rank: int


def top_evidence(
    connection: Connection,
    thesis_ids: Sequence[int],
    *,
    outcome_horizon_days: int | None = None,
    limit: int = SLACK_EVIDENCE_LIMIT,
) -> dict[int, tuple[StoredEvidence, ...]]:
    """추론별 상위 근거. `outcome_horizon_days`가 `None`이면 원 추론이 인용한 것이다."""
    if not thesis_ids:
        return {}
    with connection.cursor() as cursor:
        cursor.execute(EVIDENCE_SELECT_TOP, (list(thesis_ids), outcome_horizon_days, limit))
        rows = cursor.fetchall()
    grouped: dict[int, list[StoredEvidence]] = {}
    for row in rows:
        grouped.setdefault(row[0], []).append(
            StoredEvidence(thesis_id=row[0], evidence_title=row[4], evidence_url=row[5], rank=row[6])
        )
    return {thesis_id: tuple(items) for thesis_id, items in grouped.items()}


def stored_outcomes(connection: Connection, thesis_ids: Sequence[int]) -> dict[int, tuple[StoredOutcome, ...]]:
    """추론별 지평 결과 전부."""
    if not thesis_ids:
        return {}
    with connection.cursor() as cursor:
        cursor.execute(OUTCOME_SELECT_BY_IDS, (list(thesis_ids),))
        rows = cursor.fetchall()
    grouped: dict[int, list[StoredOutcome]] = {}
    for row in rows:
        grouped.setdefault(row[0], []).append(
            StoredOutcome(
                thesis_id=row[0],
                horizon_days=row[1],
                actual_return_pct=row[5],
                actual_outcome=row[6],
                brier_score=row[7],
                narrative=row[8],
                verdict=row[9],
            )
        )
    return {thesis_id: tuple(items) for thesis_id, items in grouped.items()}


def _evidence_context(items: Sequence[StoredEvidence]) -> str:
    """근거 줄. `context` 블록에 들어가 본문보다 작게 그려진다.

    URL이 있는 것만 링크로 만든다 — 매크로 변화는 링크할 곳이 없다.
    """
    if not items:
        # 억지 인용보다 낫다는 판단의 결과라 그렇게 적는다.
        return "📎 근거 없음 — 관측 상태만으로 추론"
    parts = [
        f"<{item.evidence_url}|{item.evidence_title}>" if item.evidence_url else item.evidence_title for item in items
    ]
    return "📎 " + " · ".join(parts)


def _dominant(thesis: StoredThesis) -> tuple[str, Decimal]:
    """모델이 가장 높은 확률을 준 방향과 그 확률."""
    return max(
        (("up", thesis.prob_up), ("down", thesis.prob_down), ("flat", thesis.prob_flat)),
        key=lambda pair: pair[1],
    )


def _probability_line(thesis: StoredThesis) -> str:
    """세 확률을 한 줄에. **가장 높은 것만 굵게 한다.**

    순서는 ▲▼– 로 고정한다. 대상마다 순서가 바뀌면 눈이 매번 다시 읽어야 한다.
    """
    dominant, _ = _dominant(thesis)
    cells = []
    for key, mark, name, value in (
        ("up", "▲", "상승", thesis.prob_up),
        ("down", "▼", "하락", thesis.prob_down),
        ("flat", "–", "횡보", thesis.prob_flat),
    ):
        text = f"{mark} {name} {value:.0%}"
        cells.append(f"*{text}*" if key == dominant else text)
    return "   ".join(cells)


def _thesis_section(thesis: StoredThesis) -> str:
    """추론 하나. 이유 셋은 인용줄로 내려 확률 줄과 무게를 가른다."""
    return "\n".join(
        [
            f"*{thesis.label}*",
            _probability_line(thesis),
            f"> *▲* {thesis.up_reasoning}",
            f"> *▼* {thesis.down_reasoning}",
            f"> *–* {thesis.flat_reasoning}",
        ]
    )


def render_blocks(
    run_slot: RunSlot,
    run_date: date,
    theses: Sequence[StoredThesis],
    evidence: dict[int, tuple[StoredEvidence, ...]],
) -> list[dict[str, Any]]:
    """Slack 블록. 추론이 0건이면 그 사실을 한 줄로 알린다.

    대상마다 `section`(확률·이유)과 `context`(근거) 둘을 낸다. 근거를 본문에 두면 이유
    문장과 같은 무게로 읽혀 어느 것이 판단이고 어느 것이 출처인지 흐려진다.

    **채점과 사후 해설은 여기 싣지 않는다**(2026-08-21 결정). 읽는 사람이 다르다 — 이 메시지는
    오늘 시장을 보는 사람이 읽고, "우리 추론이 잘 맞고 있나"는 운영자가 본다. 지표는
    `slack_ops_briefing`이 낸다. 한 메시지에 섞으면 매일 아침 자기 감사 보고가 딸려 온다.
    """
    from modules.briefing import blocks as block

    weekday = block.WEEKDAY_NAMES[run_date.weekday()]
    built: list[dict[str, Any]] = [block.header(f"{SLOT_HEADERS[run_slot]} · {run_date:%m/%d}({weekday})")]
    if not theses:
        built.append(block.section("_이번 슬롯에 남은 추론이 없다._"))
        return built
    for thesis in theses:
        built.append(block.section(_thesis_section(thesis)))
        built.append(block.context([_evidence_context(evidence.get(thesis.id, ()))]))
    return built


def render_text(run_slot: RunSlot, run_date: date, theses: Sequence[StoredThesis]) -> str:
    """블록을 못 그리는 자리(알림, 검색)에 뜨는 대체 문구. 항상 채운다."""
    if not theses:
        return f"{SLOT_HEADERS[run_slot]} {run_date:%m/%d} — 추론 결과 없음"
    names = " · ".join(thesis.label for thesis in theses)
    return f"{SLOT_HEADERS[run_slot]} {run_date:%m/%d} — {names}"
