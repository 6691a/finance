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
from collections.abc import Iterable, Sequence
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum
from typing import Any, Protocol, Self, TypedDict

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from modules import llm
from modules.llm import UnsupportedResponseFormat
from modules.schema import SchemaError, json_object, response_format
from modules.sql import read_sql
from modules.utility import atomic

logger = logging.getLogger(__name__)

# 프롬프트를 고치면 올린다. `thesis.prompt_version`에 저장돼 채점 결과를 가르는 기준이 된다.
PROMPT_VERSION = "1"

# |등락률|이 이 값보다 작으면 방향이 없었다고 본다(퍼센트).
FLAT_THRESHOLD_PCT = Decimal("0.3")

# 조사 왕복 상한. 넘으면 조사를 끝내고 답변 단계로 넘어간다.
MAX_TOOL_ROUNDS = 4

# 실행당 tool call 총 상한(왕복 4 × 회당 3). 모델이 같은 툴을 반복해 부르는 것을 막는다.
MAX_TOOL_CALLS = 12

# 실행당 툴 결과 누적 문자 상한. 넘으면 그 뒤 호출을 거절한다 — 컨텍스트가 근거로 가득 차면
# 답변 단계에 쓸 자리가 없다.
MAX_TOOL_RESULT_CHARS = 24_000

# 툴 호출 하나가 돌려주는 항목 수 상한.
MAX_TOOL_RESULTS = 20

# 항목 하나의 `new_facts` + `reason` 합계 문자 상한.
MAX_ITEM_DETAIL_CHARS = 600

# `hours` 인자의 허용 범위. 모델이 벗어난 값을 넘기면 잘라서 실행한다.
MIN_WINDOW_HOURS = 1
MAX_WINDOW_HOURS = 72

# `min_score` 인자의 허용 범위. `value_score`는 0~8이지만 상한을 넉넉히 둔다.
MIN_VALUE_SCORE = 0
MAX_VALUE_SCORE = 100

# 이유 문장 하나의 상한. 넘으면 그 필드만 자른다.
MAX_REASONING_CHARS = 500

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


class ThesisSubjectKind(StrEnum):
    """추론 대상의 종류."""

    INDEX = "index"
    STOCK = "stock"


class ThesisDirection(StrEnum):
    """방향. 예측 확률과 실제 결과가 같은 세 값을 쓴다."""

    UP = "up"
    DOWN = "down"
    FLAT = "flat"


class ThesisEvidenceKind(StrEnum):
    """근거의 출처 종류. `evidence_ref`의 앞자리와 글자 그대로 같다."""

    DOCUMENT = "document"
    DISCLOSURE = "disclosure"
    MACRO_CHANGE = "macro_change"


# ---------------------------------------------------------------------------
# 채점 — LLM 없음
# ---------------------------------------------------------------------------


def classify_outcome(return_pct: Decimal) -> ThesisDirection:
    """실제 세션 등락률을 방향으로 분류한다.

    예측과 비교하지 않는다. 실제 움직임만 본다 — 얼마나 잘 맞췄는지는 `brier_score`가 답한다.
    경계값은 `flat` 쪽이다: 0.30은 `up`이고 0.29는 `flat`이다.
    """
    if abs(return_pct) < FLAT_THRESHOLD_PCT:
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


# ---------------------------------------------------------------------------
# Toolbox — 읽기 전용 툴
# ---------------------------------------------------------------------------

RECENT_DOCUMENTS = read_sql("postgres", "document", "select_recent_top.sql")
RECENT_DISCLOSURES = read_sql("postgres", "disclosure_event", "select_recent.sql")
WINDOW_CHANGES = read_sql("postgres", "quote_bar", "select_window_changes.sql")

TOOL_SCHEMAS: tuple[dict[str, Any], ...] = (
    {
        "type": "function",
        "function": {
            "name": "recent_documents",
            "description": (
                "최근 평가된 경제 문서 중 가치 점수가 높은 것들. 제목, 발행 시각, 방향, 점수, "
                "관련 종목 티커, 그리고 앞선 평가가 남긴 새 사실과 판단 근거를 준다."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "hours": {
                        "type": "integer",
                        "description": f"기준 시각에서 거슬러 올라갈 시간. {MIN_WINDOW_HOURS}~{MAX_WINDOW_HOURS}.",
                    },
                    "min_score": {
                        "type": "integer",
                        "description": "가치 점수 하한(0~8). 낮추면 건수가 늘고 잡음도 는다.",
                    },
                },
                "required": ["hours", "min_score"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "recent_disclosures",
            "description": "추적 종목에 대해 최근 접수된 DART 공시. 회사명, 보고서명, 접수일, 감지 시각을 준다.",
            "parameters": {
                "type": "object",
                "properties": {
                    "hours": {
                        "type": "integer",
                        "description": f"기준 시각에서 거슬러 올라갈 시간. {MIN_WINDOW_HOURS}~{MAX_WINDOW_HOURS}.",
                    },
                },
                "required": ["hours"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "macro_changes",
            "description": (
                "분석 창 동안의 지수·선물·환율·금리·원자재 변화. 창의 첫 봉과 마지막 봉을 비교한다. "
                "창은 슬롯이 정하며 인자가 없다."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
)


class ToolLimitExceeded(RuntimeError):
    """상한에 걸려 실행하지 않았다. 오류 `ToolMessage`가 되어 모델에게 돌아간다."""


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
    ) -> None:
        self._connection = connection
        self._as_of_at = as_of_at
        self._macro_window_start = macro_window_start
        self._watched_codes = list(watched_codes)
        self._registry: dict[str, Evidence] = {}
        self._calls = 0
        self._chars = 0

    @property
    def registry(self) -> dict[str, Evidence]:
        """`ref → Evidence`. 답변 검증과 `thesis_evidence` 저장의 원본이다."""
        return self._registry

    @property
    def call_count(self) -> int:
        return self._calls

    def run(self, name: str, arguments: dict[str, Any]) -> str:
        """툴 하나를 실행하고 모델에게 돌려줄 본문을 만든다.

        상한 초과와 모르는 툴은 `ToolLimitExceeded`/`ThesisError`가 아니라 문자열로 돌아간다.
        모델이 고쳐 부를 기회를 주기 위해서다 — 부르는 쪽이 그것을 `ToolMessage`에 담는다.
        """
        self._calls += 1
        if self._calls > MAX_TOOL_CALLS:
            raise ToolLimitExceeded(f"상한 초과: 이 실행의 tool call이 {MAX_TOOL_CALLS}회를 넘었다. 조사를 끝내라")
        if self._chars >= MAX_TOOL_RESULT_CHARS:
            raise ToolLimitExceeded(
                f"상한 초과: 툴 결과가 누적 {MAX_TOOL_RESULT_CHARS}자에 이르렀다. 이미 받은 것으로 답하라"
            )

        handlers = {
            "recent_documents": self._recent_documents,
            "recent_disclosures": self._recent_disclosures,
            "macro_changes": self._macro_changes,
        }
        handler = handlers.get(name)
        if handler is None:
            raise ToolLimitExceeded(f"모르는 툴 이름이다: {name!r}. 쓸 수 있는 것은 {sorted(handlers)}")

        items = handler(arguments)
        for item in items:
            self._registry[item.ref] = item
        body = json.dumps([_tool_row(item) for item in items], ensure_ascii=False)
        self._chars += len(body)
        return body

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


def _change_label(kind: str, first_close: Decimal, last_close: Decimal) -> str:
    """제목 뒤에 붙는 변화 표기. Slack 근거 줄에도 그대로 쓰인다."""
    if kind in BASIS_POINT_KINDS:
        return f"{float(last_close - first_close) * 100:+.1f}bp"
    if not first_close:
        return "변화 없음"
    return f"{float((last_close - first_close) / first_close) * 100:+.2f}%"


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
    evidence_refs: tuple[str, ...] = ()


class Answers(BaseModel):
    """모델 응답 전체. 스키마를 강제하되 강제가 안 되는 제공처를 위해 검증도 남긴다."""

    model_config = ConfigDict(frozen=True)

    theses: tuple[ThesisAnswer, ...] = ()


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
    # 레지스트리로 검증하고 첫 등장 순서로 중복을 없앤 ref. rank는 이 순서다.
    evidence_refs: tuple[str, ...] = ()


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
- `evidence_refs`에는 툴이 준 `ref` 값만 쓴다. 목록 밖의 ref는 버려진다.
  인용할 것이 없으면 빈 배열로 둔다. **억지 인용이 근거 없음보다 나쁘다.**
- 세 확률 `prob_up`, `prob_down`, `prob_flat`은 각각 0~1이고 **합이 정확히 1이어야 한다.**
- 세 방향의 이유를 **모두** 쓴다. 오를 이유, 내릴 이유, 횡보할 이유가 각각 있다.
  한 방향만 쓰고 나머지를 비우지 마라 — 왜 그 반대를 배제했는지가 기록의 절반이다.
- 각 이유는 {MAX_REASONING_CHARS}자 이내의 한국어다. 넘으면 잘린다.
- 투자 조언, 매수·매도 권유, 목표가를 쓰지 마라.
- 요청 목록에 있는 subject마다 **정확히 하나씩** 답한다. 같은 subject를 두 번 쓰지 마라.

출력 형식:
{{"theses": [{{"subject_code": "", "prob_up": 0.0, "prob_down": 0.0, "prob_flat": 0.0,
 "up_reasoning": "", "down_reasoning": "", "flat_reasoning": "", "evidence_refs": []}}]}}"""

SLOT_INSTRUCTION = {
    RunSlot.PRE_OPEN: (
        "오늘 한국 장이 열리기 전이다. 밤사이 해외 시장과 전일 국내 세션을 근거로 "
        "**오늘 각 대상이 어느 방향으로 움직일지**를 가설로 적어라."
    ),
    RunSlot.POST_CLOSE: ("오늘 한국 장이 닫혔다. 오늘의 세션 등락을 근거로 **왜 그렇게 움직였는지**를 가설로 적어라."),
}

INSTRUCTION = """{slot_instruction}

기준 시각(이 시각 이후의 정보는 너에게 주어지지 않는다): {as_of_at}

## 추론 대상
{subjects}

## 관측 상태
```json
{observed_state}
```
"""

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

    messages: list[BaseMessage]
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
        self._graph = self._build_graph()

    @staticmethod
    def build_messages(
        *,
        run_slot: RunSlot,
        as_of_at: datetime,
        subjects: Sequence[Subject],
        observed_state: dict[str, Any],
    ) -> list[BaseMessage]:
        subject_lines = "\n".join(f"- {subject.code} ({subject.label}, {subject.kind.value})" for subject in subjects)
        return [
            SystemMessage(SYSTEM_PROMPT),
            HumanMessage(
                INSTRUCTION.format(
                    slot_instruction=SLOT_INSTRUCTION[run_slot],
                    as_of_at=as_of_at.isoformat(),
                    subjects=subject_lines or "(없음)",
                    observed_state=json.dumps(observed_state, ensure_ascii=False, indent=2, default=str),
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
                    evidence_refs=self._known_refs(answer),
                )
            )

        kept = tuple(draft for draft in drafts if draft.subject.code not in duplicated)
        if dropped:
            logger.warning("dropped %s theses: %s", len(dropped), dropped)
        if parsed.theses and not kept:
            raise ThesisError(f"Model returned {len(parsed.theses)} theses, none of them usable")
        return kept

    def _known_refs(self, answer: ThesisAnswer) -> tuple[str, ...]:
        """레지스트리에 있는 ref만, 첫 등장 순서로 중복 없이.

        순서가 곧 `thesis_evidence.rank`다. 목록 밖 ref는 버리고 건수를 로그로 남긴다 —
        조용히 버리면 모델이 무엇을 지어내는지 알 수 없다.
        """
        registry = self._toolbox.registry
        kept: list[str] = []
        unknown: list[str] = []
        for ref in answer.evidence_refs:
            if ref in registry:
                if ref not in kept:
                    kept.append(ref)
            else:
                unknown.append(ref)
        if unknown:
            logger.warning("%s cited %s refs that no tool returned: %s", answer.subject_code, len(unknown), unknown)
        return tuple(kept)

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
        reply = llm.invoke(self._model, state["messages"], tools=TOOL_SCHEMAS)
        return {"messages": [*state["messages"], reply]}

    def _tools(self, state: ThesisState) -> dict[str, Any]:
        """tool_call마다 Toolbox를 돌리고 `ToolMessage`를 붙인다.

        **`tool_call_id`마다 `ToolMessage`가 정확히 하나**여야 한다. 빠지거나 둘이면 제공처가
        다음 요청을 거절한다. 그래서 상한 초과와 모르는 툴도 예외로 올리지 않고 오류
        `ToolMessage`로 답한다 — 모델이 고쳐 부를 기회를 준다.
        """
        reply = state["messages"][-1]
        results: list[BaseMessage] = []
        for call in getattr(reply, "tool_calls", ()):
            try:
                body = self._toolbox.run(call["name"], call.get("args") or {})
            except ToolLimitExceeded as error:
                body = str(error)
            results.append(ToolMessage(content=body, tool_call_id=call["id"]))
        return {"messages": [*state["messages"], *results], "tool_rounds": state["tool_rounds"] + 1}

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
            return {"messages": [*messages, reply], "drafts": None, "error": str(error)}
        return {"messages": [*messages, reply], "drafts": drafts, "error": None}

    def _repair(self, state: ThesisState) -> dict[str, Any]:
        logger.warning("retrying the theses once after %s", state["error"])
        return {
            "messages": [*state["messages"], HumanMessage(REPAIR_INSTRUCTION)],
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
    stripped = text.strip()
    if len(stripped) > MAX_REASONING_CHARS:
        return stripped[: MAX_REASONING_CHARS - 1].rstrip() + "…"
    return stripped


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
    evaluated_at: datetime | None = None
    actual_return_pct: Decimal | None = None
    actual_outcome: ThesisDirection | None = None
    brier_score: Decimal | None = None


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
) -> tuple[StoredThesis, ...]:
    """추론과 근거를 한 트랜잭션에 쓴다.

    **추론은 `INSERT ... ON CONFLICT DO NOTHING`이다.** 같은 (날짜, 슬롯, subject)에 행이 이미
    있으면 아무 것도 바꾸지 않는다. `RETURNING`이 0행이면 삽입 직전에 다른 실행이 먼저 넣은
    것이므로, 그 경우에도 실패로 보지 않고 저장된 행을 읽어 돌려준다.

    thesis와 evidence를 한 트랜잭션에 쓴다 — 추론만 들어가고 근거가 빠진 상태를 남기지 않는다.
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
            _store_evidence(cursor, returned[0], draft.evidence_refs, registry)

    return existing_theses(connection, run_date=run_date, run_slot=run_slot)


def _store_evidence(
    cursor: Cursor,
    thesis_id: int,
    refs: Iterable[str],
    registry: dict[str, Evidence],
) -> None:
    """인용 순서를 `rank`로 굳혀 근거를 넣는다. 1부터 센다."""
    for rank, ref in enumerate(refs, start=1):
        item = registry[ref]
        cursor.execute(
            EVIDENCE_INSERT,
            (
                thesis_id,
                item.kind.value,
                item.ref,
                item.title,
                item.url,
                json.dumps(item.detail, ensure_ascii=False, default=str),
                rank,
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
        evaluated_at=row[17],
        actual_return_pct=row[18],
        actual_outcome=row[19],
        brier_score=row[20],
    )
