"""모델이 부르는 툴 — 인자 스키마, 조회 SQL, `ThesisToolbox`.

툴은 `StructuredTool`로 정의하고 `ToolNode`가 돌린다. 인자는 Pydantic 모델이고 JSON Schema는
`args_schema`에서 뽑는다 — wire format dict를 손으로 쓰지 않는다(프로젝트 규칙).

**상한은 코드 상수로 강제하고 그 값을 `Field(description=...)`에 f-string으로 싣는다.**
상수를 고치면 프롬프트가 따라간다. 상수는 `thesis.domain`에 있다.
"""

"""시장 추론(thesis)을 만들고, 저장하고, 채점한다.

**목적은 정확도다 — 다만 개별 추론이 아니라 판(版)의 정확도다.** 한 건의 적중은 운과
구분되지 않으므로 "어떤 정보를 근거로 어떤 결론을 냈다"를 먼저 기록으로 남기고, 채점이
쌓이면 model·prompt 판별로 비교해 다음 변경을 유지하거나 되돌린다. **이미 쓴 추론은
고치지 않는다** — 고칠 수 있으면 나쁜 판이 사후 수정으로 좋아 보인다.

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

설계는 `docs/analysis/market-thesis/1-storage.md`와 `docs/analysis/market-thesis/2-agent.md`에 있다.
"""

import json
import logging
import time
from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from langchain_core.messages import BaseMessage, ToolMessage
from langchain_core.tools import BaseTool, StructuredTool
from langgraph.prebuilt import ToolNode

# **공개 API가 아니다.** `langgraph.prebuilt`가 export하지 않아 여기서만 import된다
# (1.2.2·1.2.11 실측). 마이너 판올림에서 움직일 수 있다는 것을 알고 쓴다 — 대안은 이 예외를
# 안 넣고 인자 검증 실패도 태스크 실패로 두거나(모델이 고쳐 부를 기회를 잃는다) 상위
# `ToolException`으로 잡는 것인데(툴이 던지는 **모든** 것을 삼킨다) 둘 다 이쪽보다 나쁘다.
from langgraph.prebuilt.tool_node import ToolInvocationError
from pydantic import BaseModel

from modules.db import TransactionalConnection as Connection
from modules.sql import read_sql
from modules.technical import base_rate
from modules.thesis.domain import (
    BASIS_POINT_INDICATOR_KINDS,
    CLOSE_REF_SUFFIX,
    DART_VIEWER_URL,
    DOMESTIC_COUNTRY,
    DOMESTIC_SESSION_KINDS,
    INDICATOR_KINDS,
    MACRO_KINDS,
    MAX_HISTORY_DAYS,
    MAX_INDICATOR_RESULTS,
    MAX_PAST_THESES,
    MAX_TOOL_CALLS,
    MAX_TOOL_RESULT_CHARS,
    MAX_TOOL_RESULTS,
    MAX_VALUE_SCORE,
    MAX_WINDOW_HOURS,
    MIN_HISTORY_DAYS,
    MIN_PAST_THESES,
    MIN_VALUE_SCORE,
    MIN_WINDOW_HOURS,
    SIGNAL_HISTORY_DAYS,
    SIGNAL_LABELS,
    SNAPSHOT_LOOKBACK,
    TECHNICAL_LOOKBACK_BARS,
    Evidence,
    ThesisEvidenceKind,
    ToolCallErrorKind,
    ToolCallRecord,
    evidence_ref,
)
from modules.thesis.state import (
    PastThesis,
    SignalBaseRate,
    SignalObservation,
)
from modules.thesis.tool_args import (
    TOOL_DESCRIPTIONS,
    AnalystOpinionsArgs,
    DailyHistoryArgs,
    EventSurprisesArgs,
    MacroChangesArgs,
    MacroIndicatorsArgs,
    MarketFundsArgs,
    NoArgs,
    PastThesesArgs,
    RecentDisclosuresArgs,
    RecentDocumentsArgs,
    StockFlowsArgs,
    TypicalMoveArgs,
)
from modules.thesis.tool_rows import (
    as_float,
    change_label,
    clamp_int,
    document_detail,
    indicator_row,
    macro_detail,
    macro_title,
    opinion_detail,
    pending_expectation_detail,
    surprise_detail,
    technical_snapshot,
    tool_row,
    us_close_detail,
)
from modules.thesis.tools import (
    AnalystOpinionsPayload,
    AvailableSymbolRow,
    DailyBarRow,
    DailyHistoryEmptyPayload,
    DailyHistoryPayload,
    DisclosureDetail,
    EventSurprisesPayload,
    IndicatorPayload,
    MarketBreadthRow,
    MarketFlowRow,
    MarketFundsRow,
    MoveWindow,
    ShortCreditRow,
    SignalDetail,
    StockFlowEstimateRow,
    StockFlowPayload,
    StockFlowSettledRow,
    TypicalMovePayload,
)
from modules.utility import KST_TIMEZONE

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Toolbox — 읽기 전용 툴
# ---------------------------------------------------------------------------

RECENT_DOCUMENTS = read_sql("postgres", "document", "select_recent_top.sql")
RECENT_DISCLOSURES = read_sql("postgres", "disclosure_event", "select_recent.sql")
WINDOW_CHANGES = read_sql("postgres", "quote_bar", "select_window_changes.sql")
US_MARKET_CLOSE = read_sql("postgres", "quote_bar", "select_thesis_us_close.sql")


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
DAILY_HISTORY = read_sql("postgres", "technical", "select_history.sql")
DAILY_HISTORY_SYMBOLS = read_sql("postgres", "technical", "select_symbols.sql")
RECENT_SIGNALS = read_sql("postgres", "technical_signal", "select_thesis_recent.sql")
SHORT_AND_CREDIT = read_sql("postgres", "krx_stock_short_sale_daily", "select_thesis_latest.sql")
# 6단계(2026-08-22). 증권사 투자의견·목표주가. 리포트 본문은 `recent_documents`가 문서로 준다.
ANALYST_OPINIONS = read_sql("postgres", "stock_analyst_opinion", "select_thesis_recent.sql")
# 8단계(2026-08-24). 기대 대비 발표 판정과, 아직 발표되지 않은 이벤트의 대표 기대치.
EVENT_SURPRISES = read_sql("postgres", "stock_event_outcome", "select_thesis_recent.sql")
EVENT_EXPECTATIONS = read_sql("postgres", "stock_event_claim", "select_thesis_pending.sql")


def _message_text(message: ToolMessage) -> str:
    """`ToolMessage` 본문. 제공처에 따라 문자열이 아니라 조각 리스트로 온다."""
    content = message.content
    if isinstance(content, str):
        return content
    return "".join(part if isinstance(part, str) else str(part.get("text", "")) for part in content)


class ToolLimitExceeded(RuntimeError):
    """상한에 걸려 실행하지 않았다. 오류 `ToolMessage`가 되어 모델에게 돌아간다."""


def tool_node(toolbox: "ThesisToolbox") -> ToolNode:
    """툴 실행 노드. 두 그래프(`ThesisBuilder`·`FollowupNarrator`)가 같은 것을 쓴다.

    **`handle_tool_errors`에 타입을 준다.** `ToolLimitExceeded`(상한 초과·모르는 툴·대상
    목록 밖)와 `ToolInvocationError`(모델이 보낸 인자가 스키마와 안 맞음)만 오류
    `ToolMessage`가 되어 모델이 고쳐 부를 기회를 얻고, psycopg 오류 같은 나머지는 그대로
    올라가 태스크를 죽인다.

    기본값(`True`)을 쓰면 **연결 끊김이 "결과 없음"으로 위장된다.** 빈 결과는 "그 창에
    문서가 없다"는 뜻이어야 한다는 규칙이 거기서 깨진다.
    """
    return ToolNode(toolbox.tools, handle_tool_errors=(ToolLimitExceeded, ToolInvocationError))


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
        # 심볼별 기저율 캐시. 조회가 전 이력을 훑으므로 실행당 한 번으로 묶는다.
        self._base_rate_cache: dict[str, dict[tuple[str, str, str], SignalBaseRate]] = {}
        self._calls = 0
        self._chars = 0
        # 원장(13단계). 기록만 쌓고 **DB에는 쓰지 않는다** — 읽기 전용 툴 셋이라는 성격을
        # 유지하고 저장 시점은 부르는 쪽이 정한다.
        self._records: list[ToolCallRecord] = []
        self._by_call_id: dict[str, ToolCallRecord] = {}
        self._rounds = 0
        self._tools = self._build_tools()
        self._by_name = {tool.name: tool for tool in self._tools}

    def _build_tools(self) -> list[BaseTool]:
        """`ToolNode`와 `bind_tools`에 그대로 넘길 툴 목록.

        **함수는 전부 `_record`로 감싼다.** 툴 하나하나에 계측 코드를 넣지 않는다 —
        래퍼가 실제 인자·소요·결과를 채우고, 숨은 `tool_call_id`가 어느 요청이었는지를 잇는다.

        `StructuredTool.from_function`이 `args_schema`에서 JSON Schema를 뽑으므로 우리가
        스키마를 손으로 쓰지 않는다. 함수는 **바인드된 메서드**다 — 툴이 연결·`as_of_at`·
        레지스트리·상한 같은 이 객체의 상태를 봐야 해서 모듈 수준 `@tool`을 쓸 수 없다.
        """
        return [
            StructuredTool.from_function(
                func=self._record("recent_documents", self._tool_recent_documents),
                name="recent_documents",
                description=TOOL_DESCRIPTIONS["recent_documents"],
                args_schema=RecentDocumentsArgs,
            ),
            StructuredTool.from_function(
                func=self._record("recent_disclosures", self._tool_recent_disclosures),
                name="recent_disclosures",
                description=TOOL_DESCRIPTIONS["recent_disclosures"],
                args_schema=RecentDisclosuresArgs,
            ),
            StructuredTool.from_function(
                func=self._record("macro_changes", self._tool_macro_changes),
                name="macro_changes",
                description=TOOL_DESCRIPTIONS["macro_changes"],
                args_schema=MacroChangesArgs,
            ),
            StructuredTool.from_function(
                func=self._record("us_market_close", self._tool_us_market_close),
                name="us_market_close",
                description=TOOL_DESCRIPTIONS["us_market_close"],
                args_schema=NoArgs,
            ),
            StructuredTool.from_function(
                func=self._record("past_theses", self._tool_past_theses),
                name="past_theses",
                description=TOOL_DESCRIPTIONS["past_theses"],
                args_schema=PastThesesArgs,
            ),
            StructuredTool.from_function(
                func=self._record("macro_indicators", self._tool_macro_indicators),
                name="macro_indicators",
                description=TOOL_DESCRIPTIONS["macro_indicators"],
                args_schema=MacroIndicatorsArgs,
            ),
            StructuredTool.from_function(
                func=self._record("market_investor_flows", self._tool_market_investor_flows),
                name="market_investor_flows",
                description=TOOL_DESCRIPTIONS["market_investor_flows"],
                args_schema=NoArgs,
            ),
            StructuredTool.from_function(
                func=self._record("market_breadth", self._tool_market_breadth),
                name="market_breadth",
                description=TOOL_DESCRIPTIONS["market_breadth"],
                args_schema=NoArgs,
            ),
            StructuredTool.from_function(
                func=self._record("stock_investor_flows", self._tool_stock_investor_flows),
                name="stock_investor_flows",
                description=TOOL_DESCRIPTIONS["stock_investor_flows"],
                args_schema=StockFlowsArgs,
            ),
            StructuredTool.from_function(
                func=self._record("market_funds", self._tool_market_funds),
                name="market_funds",
                description=TOOL_DESCRIPTIONS["market_funds"],
                args_schema=MarketFundsArgs,
            ),
            StructuredTool.from_function(
                func=self._record("daily_history", self._tool_daily_history),
                name="daily_history",
                description=TOOL_DESCRIPTIONS["daily_history"],
                args_schema=DailyHistoryArgs,
            ),
            StructuredTool.from_function(
                func=self._record("typical_move", self._tool_typical_move),
                name="typical_move",
                description=TOOL_DESCRIPTIONS["typical_move"],
                args_schema=TypicalMoveArgs,
            ),
            StructuredTool.from_function(
                func=self._record("short_and_credit", self._tool_short_and_credit),
                name="short_and_credit",
                description=TOOL_DESCRIPTIONS["short_and_credit"],
                args_schema=NoArgs,
            ),
            StructuredTool.from_function(
                func=self._record("analyst_opinions", self._tool_analyst_opinions),
                name="analyst_opinions",
                description=TOOL_DESCRIPTIONS["analyst_opinions"],
                args_schema=AnalystOpinionsArgs,
            ),
            StructuredTool.from_function(
                func=self._record("event_surprises", self._tool_event_surprises),
                name="event_surprises",
                description=TOOL_DESCRIPTIONS["event_surprises"],
                args_schema=EventSurprisesArgs,
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
            [past.model_dump(mode="json") for past in self._past_theses({"subject_code": subject_code, "n": n})],
            ensure_ascii=False,
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
            IndicatorPayload(
                kind=chosen,
                unit_note="변화는 bp다" if as_basis_points else "변화는 값 그대로다",
                series=tuple(indicator_row(row, as_basis_points=as_basis_points) for row in rows),
            )
        )

    def _tool_market_investor_flows(self) -> str:
        self._charge()
        rows = self._fetch(MARKET_FLOWS, self._snapshot_window())
        return self._body(
            [
                MarketFlowRow(
                    market_code=row[0],
                    observed_at=row[1],
                    foreign_net_buy_amount=as_float(row[2]),
                    institution_net_buy_amount=as_float(row[3]),
                    individual_net_buy_amount=as_float(row[4]),
                    pension_fund_net_buy_qty=as_float(row[5]),
                    investment_trust_net_buy_qty=as_float(row[6]),
                )
                for row in rows
            ]
        )

    def _tool_market_breadth(self) -> str:
        self._charge()
        rows = self._fetch(MARKET_BREADTH, self._snapshot_window())
        return self._body(
            [
                MarketBreadthRow(
                    symbol=row[0],
                    observed_at=row[1],
                    rising=row[2],
                    unchanged=row[3],
                    falling=row[4],
                    upper_limit=row[5],
                    lower_limit=row[6],
                )
                for row in rows
            ]
        )

    def _tool_stock_investor_flows(self, days: int) -> str:
        self._charge()
        span = clamp_int(days, MIN_HISTORY_DAYS, MAX_HISTORY_DAYS, 5)
        settled = self._fetch(
            STOCK_FLOWS,
            {"stock_codes": self._watched_codes, "as_of_at": self._as_of_at, "days": span},
        )
        estimates = self._fetch(
            STOCK_FLOW_ESTIMATES,
            {"stock_codes": self._watched_codes, "as_of_at": self._as_of_at},
        )
        return self._body(
            StockFlowPayload(
                settled=tuple(
                    StockFlowSettledRow(
                        stock_code=row[0],
                        business_date=row[1],
                        close_price=as_float(row[2]),
                        volume=as_float(row[3]),
                        foreign_net_buy_qty=as_float(row[4]),
                        institution_net_buy_qty=as_float(row[5]),
                        individual_net_buy_qty=as_float(row[6]),
                        foreign_net_buy_amount=as_float(row[7]),
                        institution_net_buy_amount=as_float(row[8]),
                        individual_net_buy_amount=as_float(row[9]),
                    )
                    for row in settled
                ),
                intraday_estimate=tuple(
                    StockFlowEstimateRow(
                        stock_code=row[0],
                        business_date=row[1],
                        source_time_code=row[2],
                        collected_at=row[3],
                        foreign_net_buy_qty=as_float(row[4]),
                        institution_net_buy_qty=as_float(row[5]),
                        total_net_buy_qty=as_float(row[6]),
                    )
                    for row in estimates
                ),
            )
        )

    def _tool_market_funds(self, days: int) -> str:
        self._charge()
        span = clamp_int(days, MIN_HISTORY_DAYS, MAX_HISTORY_DAYS, 10)
        rows = self._fetch(MARKET_FUNDS, {"as_of_at": self._as_of_at, "days": span})
        return self._body(
            [
                MarketFundsRow(
                    business_date=row[0],
                    index_close=as_float(row[1]),
                    index_change=as_float(row[2]),
                    customer_deposit=as_float(row[3]),
                    customer_deposit_change=as_float(row[4]),
                    credit_loan_balance=as_float(row[5]),
                    unsettled_amount=as_float(row[6]),
                    turnover_ratio=as_float(row[7]),
                )
                for row in rows
            ]
        )

    def _tool_typical_move(self, symbol: str) -> str:
        """크기의 기준선. **대상 목록 밖은 거절한다** — `past_theses`와 같은 형태다.

        아무 심볼이나 물어보게 두면 예산만 쓰고 엉뚱한 자산에 앵커링한다. 덤으로
        "없는 심볼" 분기가 통째로 사라진다.

        **장중 잔여 구간은 안 준다.** `index_bar`의 코스피 분봉이 2026-08-18부터
        9거래일뿐이라 `MIN_BASE_RATE_SAMPLE`을 못 채운다. 없는 표본으로 숫자를 지어내지
        않고 그 사실을 `note`가 말한다.
        """
        from modules.technical.base_rate import MOVE_SIZE_BARS, RECENT_MOVE_BARS, move_sizes

        self._charge()
        code = str(symbol).strip()
        if not self._subject_codes:
            raise ToolLimitExceeded("이번 실행에는 대상 목록이 없어 typical_move를 쓸 수 없다")
        if code not in self._subject_codes:
            raise ToolLimitExceeded(f"대상 목록 밖이다: {code!r}. 쓸 수 있는 것은 {sorted(self._subject_codes)}")

        as_of_date = self._as_of_at.astimezone(KST_TIMEZONE).date()
        windows = {
            bars: move_sizes(self._connection, as_of_date=as_of_date, symbols=[code], bars=bars).get(
                code, MoveWindow(bars=bars, sample_size=0)
            )
            for bars in (RECENT_MOVE_BARS, MOVE_SIZE_BARS)
        }
        return self._body(
            TypicalMovePayload(
                symbol=code,
                as_of_date=as_of_date,
                axis="직전 세션 종가 → 그 세션 종가(1거래일)",
                recent=windows[RECENT_MOVE_BARS],
                baseline=windows[MOVE_SIZE_BARS],
                note=(
                    "하루 전체 등락이다. 장중 잔여 구간(지금 가격에서 마감까지)의 실현 분포는 "
                    "분봉 이력이 짧아 아직 못 잰다 — 장중 슬롯은 남은 시간만큼 줄여 읽는다."
                ),
            )
        )

    def _tool_daily_history(self, symbol: str, days: int) -> str:
        """심볼 하나의 일봉과 기술지표. **없는 심볼이면 쓸 수 있는 목록을 함께 돌려준다.**

        빈 배열만 주면 모델이 "이력이 없다"가 아니라 "움직임이 없었다"로 읽을 수 있다.

        **모델에게 보여 주는 봉은 요청한 `days`뿐이고 계산에는 120봉을 쓴다.** SMA60과 EMA
        안정화에 그만큼이 필요한데 그 봉을 다 실으면 문맥만 먹는다.
        """
        self._charge()
        span = clamp_int(days, MIN_HISTORY_DAYS, MAX_HISTORY_DAYS, 10)
        wanted = str(symbol).strip()
        rows = self._fetch(
            DAILY_HISTORY,
            {
                "symbols": [wanted],
                "include_watched": False,
                "as_of_at": self._as_of_at,
                "limit": TECHNICAL_LOOKBACK_BARS,
            },
        )
        if not rows:
            available = self._fetch(DAILY_HISTORY_SYMBOLS, {"as_of_at": self._as_of_at})
            return self._body(
                DailyHistoryEmptyPayload(
                    symbol=wanted,
                    note=f"{wanted}의 일봉이 없다. 아래 심볼만 일봉을 갖는다",
                    available_symbols=tuple(
                        AvailableSymbolRow(symbol=row[0], label=row[1], kind=row[2]) for row in available
                    ),
                )
            )
        snapshot = technical_snapshot(wanted, rows)
        signals = self._recent_signals(wanted)
        return self._body(
            DailyHistoryPayload(
                symbol=wanted,
                bars=tuple(
                    DailyBarRow(
                        label=row[2],
                        kind=row[3],
                        country=row[4],
                        business_date=row[5],
                        open=as_float(row[6]),
                        high=as_float(row[7]),
                        low=as_float(row[8]),
                        close=as_float(row[9]),
                        volume=as_float(row[10]),
                    )
                    for row in rows[:span]
                ),
                technical_snapshot=snapshot,
                recent_signals=tuple(signals),
            )
        )

    def _recent_signals(self, symbol: str) -> list[SignalObservation]:
        """이 심볼의 최근 매매 신호. **각 항목은 인용할 수 있는 근거다.**

        지표(`technical_snapshot`)와 달리 레지스트리에 넣는다. 신호는 행 ID를 가진 사건이고,
        모델이 그것을 인용해야 "신호가 추론에 도움이 됐나"를 나중에 잴 수 있다(문서 14.3절).
        """
        rows = self._fetch(
            RECENT_SIGNALS,
            {
                "symbol": symbol,
                "since_date": (self._as_of_at - timedelta(days=SIGNAL_HISTORY_DAYS)).date(),
                "as_of_at": self._as_of_at,
                "limit": MAX_TOOL_RESULTS,
            },
        )
        signals = []
        for row in rows:
            kind, direction = str(row[3]), str(row[4])
            ref = evidence_ref(ThesisEvidenceKind.TECHNICAL_SIGNAL, str(row[0]))
            self._registry[ref] = Evidence(
                kind=ThesisEvidenceKind.TECHNICAL_SIGNAL,
                ref=ref,
                title=f"{symbol} {SIGNAL_LABELS.get((kind, direction), f'{kind} {direction}')} ({row[2]})",
                # 사건은 링크할 곳이 없다. 매크로 변화와 같다.
                url=None,
                detail=SignalDetail(
                    symbol=symbol,
                    signal_date=str(row[2]),
                    kind=kind,
                    direction=direction,
                    close=as_float(row[5]),
                    rsi14=as_float(row[6]),
                    volume_ratio20=as_float(row[7]),
                ),
            )
            signals.append(
                SignalObservation(
                    ref=ref,
                    signal_date=row[2],
                    kind=kind,
                    direction=direction,
                    base_rate=self._base_rates(symbol).get((symbol, kind, direction)),
                )
            )
        return signals

    def _base_rates(self, symbol: str) -> dict[tuple[str, str, str], SignalBaseRate]:
        """이 심볼의 기저율. **실행당 심볼마다 한 번만 센다.**

        관측 상태와 같은 값을 붙인다 — 모델이 툴로 보든 관측 상태로 보든 같은 숫자를 봐야
        한다. 캐시가 있는 이유는 조회가 그 심볼의 전 이력을 훑기 때문이다. `daily_history`가
        tool call 상한만큼 불릴 수 있어, 호출마다 다시 세면 그만큼 반복된다.
        """
        if symbol not in self._base_rate_cache:
            self._base_rate_cache[symbol] = base_rate.signal_base_rates(
                self._connection,
                as_of_date=self._as_of_at.astimezone(KST_TIMEZONE).date(),
                symbols=(symbol,),
            )
        return self._base_rate_cache[symbol]

    def _tool_short_and_credit(self) -> str:
        self._charge()
        rows = self._fetch(
            SHORT_AND_CREDIT,
            {"stock_codes": self._watched_codes, "as_of_at": self._as_of_at},
        )
        return self._body(
            [
                ShortCreditRow(
                    stock_code=row[0],
                    label=row[1],
                    business_date=row[2],
                    short_sale_quantity=as_float(row[3]),
                    short_sale_volume_ratio=as_float(row[4]),
                    short_sale_amount=as_float(row[5]),
                    lending_balance_quantity=as_float(row[6]),
                    lending_balance_change_quantity=as_float(row[7]),
                    credit_loan_balance_quantity=as_float(row[8]),
                    credit_loan_balance_amount=as_float(row[9]),
                    credit_loan_balance_rate=as_float(row[10]),
                )
                for row in rows
            ]
        )

    def _tool_analyst_opinions(self, ticker: str) -> str:
        """종목 하나의 최근 투자의견. 문맥 툴이라 레지스트리에 넣지 않는다 — 인용할 출처는
        리포트 문서(`recent_documents`)이고 이것은 시장 참여자의 관측이다.

        추적 목록 밖 종목은 거절한다. `past_theses`의 `subject_code`와 같은 이유다 — 모델이
        아무 종목이나 조회하며 문맥을 채우게 두지 않는다.
        """
        # 종목 검사가 예산 차감보다 앞이다. 뒤에 두면 조회하지도 않은 호출이 왕복 예산을
        # 깎고, 그 거절이 `handle_tool_errors`를 거쳐 ToolMessage로 성공처럼 끝난다.
        # 모델은 `recent_documents` 태그에서 추적 밖 종목 코드를 볼 수 있다 — 태그 후보가
        # 시세 목록보다 넓기 때문이다.
        code = str(ticker or "").strip()
        if code not in self._watched_codes:
            raise ToolLimitExceeded(f"추적 종목 밖이다: {code!r}. 쓸 수 있는 것은 {sorted(self._watched_codes)}")
        self._charge()
        rows = self._fetch(
            ANALYST_OPINIONS,
            {"stock_code": code, "as_of_at": self._as_of_at, "limit": MAX_TOOL_RESULTS},
        )
        return self._body(
            AnalystOpinionsPayload(
                stock_code=code,
                opinions=tuple(opinion_detail(row) for row in rows),
            )
        )

    def _tool_event_surprises(self, ticker: str) -> str:
        """종목 하나의 기대 대비 발표 판정과, 아직 발표되지 않은 이벤트의 기대치.

        문맥 툴이라 레지스트리에 넣지 않는다. `thesis_evidence.evidence_kind`가 셋으로 닫혀
        있고, 인용이 필요하면 발표 문서 자체를 `recent_documents`로 인용하면 된다.

        추적 목록 밖 종목은 거절한다(`analyst_opinions`와 같은 처리).
        """
        # 검사가 예산 차감보다 앞이다(`analyst_opinions`와 같은 이유).
        code = str(ticker or "").strip()
        if code not in self._watched_codes:
            raise ToolLimitExceeded(f"추적 종목 밖이다: {code!r}. 쓸 수 있는 것은 {sorted(self._watched_codes)}")
        self._charge()
        parameters = {"stock_code": code, "as_of_at": self._as_of_at, "limit": MAX_TOOL_RESULTS}
        outcomes = self._fetch(EVENT_SURPRISES, parameters)
        pending = self._fetch(EVENT_EXPECTATIONS, parameters)
        return self._body(
            EventSurprisesPayload(
                stock_code=code,
                outcomes=tuple(surprise_detail(row) for row in outcomes),
                pending_expectations=tuple(pending_expectation_detail(row) for row in pending),
            )
        )

    def _snapshot_window(self) -> dict[str, Any]:
        """장중 스냅샷 툴의 창. 끝은 `as_of_at`, 시작은 거기서 `SNAPSHOT_LOOKBACK`만큼 앞."""
        return {"window_start": self._as_of_at - SNAPSHOT_LOOKBACK, "as_of_at": self._as_of_at}

    def _fetch(self, statement: str, parameters: dict[str, Any]) -> list[Sequence[Any]]:
        with self._connection.cursor() as cursor:
            cursor.execute(statement, parameters)
            return list(cursor.fetchall())

    def _body(self, payload: BaseModel | Sequence[BaseModel]) -> str:
        """근거를 만들지 않는 툴의 반환. 문자 예산만 단다.

        **`default=str`을 쓰지 않는다.** 모델이 `date`·`Decimal`을 정확히 바꾸므로 어느 칸이
        언제 문자열이 됐는지가 모델 선언에 남는다.
        """
        data = (
            payload.model_dump(mode="json")
            if isinstance(payload, BaseModel)
            else [item.model_dump(mode="json") for item in payload]
        )
        body = json.dumps(data, ensure_ascii=False)
        self._chars += len(body)
        return body

    def _as_evidence_body(self, items: list[Evidence]) -> str:
        for item in items:
            self._registry[item.ref] = item
        body = json.dumps([tool_row(item) for item in items], ensure_ascii=False)
        self._chars += len(body)
        return body

    # --- 원장 --------------------------------------------------------------
    # 툴 호출을 남기는 자리는 둘이다. **함수 래퍼만으로는 부족하다** — unknown tool과
    # Pydantic 인자 오류는 원래 함수에 도달하기 전에 `ToolNode`가 오류 `ToolMessage`로
    # 바꾸기 때문이다. 그래서 요청 shell(`begin_round`)과 실제 실행(`_record`)을 따로
    # 잡고, 마지막에 `finish_round`가 `ToolMessage`로 둘을 맞춘다.

    @property
    def tool_calls(self) -> tuple[ToolCallRecord, ...]:
        """이번 대화에서 기록한 툴 호출 전부. 부르는 쪽이 `thesis_tool_call`로 저장한다."""
        return tuple(self._records)

    @property
    def round_count(self) -> int:
        """조사 왕복 수. 실패한 대화는 그래프 최종 상태를 못 받아 이 값이 유일한 출처다."""
        return self._rounds

    def begin_round(self, tool_calls: Sequence[dict[str, Any]]) -> None:
        """모델이 요청한 tool_call마다 빈 기록을 연다.

        여기서 잡는 것은 **모델이 실제로 보낸 것**이다 — 이름, 검증 전 인자, 제공처 call id,
        그리고 요청을 등록한 시각. 실행 결과는 래퍼가, 모델에게 돌아갔는지는
        `finish_round`가 채운다.
        """
        self._rounds += 1
        requested_at = datetime.now(UTC)
        for call in tool_calls:
            call_id = str(call.get("id") or "")
            record = ToolCallRecord(
                seq=len(self._records) + 1,
                round_no=self._rounds,
                tool_call_id=call_id,
                tool_name=str(call.get("name") or ""),
                arguments=dict(call.get("args") or {}),
                requested_at=requested_at,
            )
            self._records.append(record)
            self._by_call_id[call_id] = record

    def finish_round(self, messages: Sequence[BaseMessage]) -> None:
        """`ToolNode`가 돌려준 `ToolMessage`로 그 라운드의 기록을 닫는다.

        **여기서만 알 수 있는 것이 둘이다.**

        - 함수에 진입하지 못한 실패(모르는 툴, 인자 검증). 래퍼가 못 보므로 이 자리가
          아니면 그 호출은 영영 빈 기록으로 남는다.
        - `delivered` — 결과가 모델 대화에 실제로 돌아갔나. sibling 하나가 처리되지 않은
          예외를 올리면 `ToolNode`는 나머지 결과를 **버린다.** 그런데 sync 경로가
          `executor.map`이라 이미 시작된 sibling은 취소되지 않고 끝까지 돈다 — 래퍼가
          결과를 다 채운 행이 남는다. 그것은 오류가 아니라 "모델만 못 봤다"이고,
          인용 분석이 정확히 그 구분 위에 선다.
        """
        for message in messages:
            if not isinstance(message, ToolMessage):
                continue
            record = self._by_call_id.get(str(message.tool_call_id))
            if record is None:
                continue
            record.delivered = True
            if record.result is not None:
                continue
            # 여기 오는 것은 함수에 진입하지 못했거나(unknown tool·인자 검증) 래퍼가 이미
            # 예외를 남긴 경우다. 어느 쪽이든 모델이 읽은 문자열은 이 본문이라 그것을 담는다.
            if record.error_kind is None:
                record.error_kind = (
                    ToolCallErrorKind.UNKNOWN_TOOL
                    if record.tool_name not in self._by_name
                    else ToolCallErrorKind.VALIDATION
                )
            record.error = _message_text(message)

    def close_open_records(self) -> None:
        """끝나고도 결과·오류가 없는 기록을 닫는다. 실행조차 못 한 sibling이다.

        워커가 포화됐을 때(`max_concurrency` 지정, 또는 호출 수 > `min(32, cpu+4)`) sibling
        하나의 예외가 아직 시작 안 한 것들을 취소한다. 그 행은 `result`도 `error`도 없어
        DB CHECK(둘 중 하나는 있어야 한다)를 어긴다 — 여기서 닫아야 저장할 수 있다.
        """
        for record in self._records:
            if record.result is None and record.error is None:
                record.error_kind = ToolCallErrorKind.CANCELLED
                record.error = "sibling 실패로 실행되지 않았다"

    def _record(self, name: str, func: Callable[..., str]) -> Callable[..., str]:
        """툴 함수 하나를 기록으로 감싼다. 툴 14개가 이 래퍼 하나를 지난다.

        **`**kwargs`로 받는다.** 그러면 `StructuredTool`이 `args_schema`와 시그니처를
        대조하지 않아 생기는 실패 모드(스키마에만 있는 인자 → 호출 시 `TypeError` →
        `ToolInvocationError`로 감싸이지 않아 태스크 사망)가 구조적으로 사라진다.
        개별 시그니처로 되돌리지 않는다.

        **예외는 기록한 뒤 다시 올린다.** `ToolLimitExceeded`는 `ToolNode`가 오류
        `ToolMessage`로 바꿔야 하고, DB 오류는 태스크를 죽여야 한다.
        """

        def call(**kwargs: Any) -> str:
            record = self._by_call_id.get(str(kwargs.pop("tool_call_id", "") or ""))
            started = time.perf_counter()
            try:
                body = func(**kwargs)
            except ToolLimitExceeded as error:
                self._close_record(record, kwargs, started, error=error, kind=ToolCallErrorKind.LIMIT)
                raise
            # 넓게 잡되 **반드시 다시 올린다.** 여기서 잡는 이유는 기록 하나뿐이고,
            # 삼키면 DB 끊김이 "결과 없음"으로 위장된다.
            except Exception as error:
                self._close_record(record, kwargs, started, error=error, kind=ToolCallErrorKind.EXECUTION)
                raise
            self._close_record(record, kwargs, started, result=body)
            return body

        call.__name__ = name
        call.__doc__ = func.__doc__
        return call

    @staticmethod
    def _close_record(
        record: ToolCallRecord | None,
        kwargs: dict[str, Any],
        started: float,
        *,
        result: str | None = None,
        error: BaseException | None = None,
        kind: ToolCallErrorKind | None = None,
    ) -> None:
        """실행이 끝난 기록에 실제 인자·소요·결과를 채운다. `delivered`는 아직 모른다."""
        if record is None:
            return
        record.validated_arguments = dict(kwargs)
        record.duration_ms = int((time.perf_counter() - started) * 1000)
        if error is not None:
            record.error_kind = kind
            record.error = str(error)
            return
        record.result = result
        record.result_chars = len(result or "")

    def _charge(self) -> None:
        """호출 한 번을 상한에 단다. 넘으면 실행하지 않고 `ToolLimitExceeded`다.

        **걸린 사실을 로그로 남긴다.** 이 예외는 `ToolNode`가 오류 `ToolMessage`로 바꿔
        모델에게 돌려주므로 태스크는 성공으로 끝난다. 상한에 걸려 근거를 덜 보고 답한
        실행과 다 보고 답한 실행이 `thesis` 행에서 구분되지 않고, LangSmith 공개 공유는
        루트 run만 노출해 툴 메시지를 볼 수 없다. Airflow 로그가 유일한 단서다.
        """
        self._calls += 1
        if self._calls > MAX_TOOL_CALLS:
            logger.warning("tool call budget exhausted: %s calls, %s chars", self._calls, self._chars)
            raise ToolLimitExceeded(f"상한 초과: 이 실행의 tool call이 {MAX_TOOL_CALLS}회를 넘었다. 조사를 끝내라")
        if self._chars >= MAX_TOOL_RESULT_CHARS:
            logger.warning("tool result budget exhausted: %s calls, %s chars", self._calls, self._chars)
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
        # **full ToolCall dict로 부른다.** 인자 dict만 넘기면 `InjectedToolCallId`를 채울
        # id가 없어 LangChain이 거절한다. 운영 경로(`ToolNode`)가 넘기는 모양과 같게 둬야
        # 이 경로만 다르게 도는 일이 없다. `begin_round`로 요청 shell도 함께 연다 —
        # 원장 코드가 이 경로에서도 같은 길을 지나야 테스트가 그것을 검증할 수 있다.
        call_id = f"manual_{len(self._records) + 1}"
        self.begin_round([{"name": name, "args": dict(arguments), "id": call_id}])
        reply = tool.invoke({"name": name, "args": dict(arguments), "id": call_id, "type": "tool_call"})
        # full ToolCall로 부르면 LangChain이 `ToolMessage`를 돌려준다. 이 메서드의 계약은
        # 본문 문자열이라 여기서 편다. 운영 경로는 `ToolNode`가 같은 일을 한다.
        if isinstance(reply, ToolMessage):
            self.finish_round([reply])
            return _message_text(reply)
        return str(reply)

    def _past_theses(self, arguments: dict[str, Any]) -> list[PastThesis]:
        """툴 판 `past_theses`. 대상 목록 밖을 거절하고 건수를 자른 뒤 모듈 함수에 맡긴다.

        장전은 같은 조회를 프롬프트에 미리 싣는다(`PREFETCHED_PAST_THESES`). 툴은 모델이
        더 보고 싶을 때의 길이고, 툴로 본 것은 `thesis_precedent`에 남지 않는다.
        """
        code = str(arguments.get("subject_code") or "").strip()
        if not self._subject_codes:
            raise ToolLimitExceeded("이번 실행에는 대상 목록이 없어 past_theses를 쓸 수 없다")
        if code not in self._subject_codes:
            raise ToolLimitExceeded(f"대상 목록 밖이다: {code!r}. 쓸 수 있는 것은 {sorted(self._subject_codes)}")
        # 모듈 수준에서 import하지 않는다. `thesis.store`가 초안 모델 때문에
        # `thesis.generation`을 보고, 그쪽이 다시 이 모듈을 본다. 툴박스가 store를 보는 곳은
        # 여기 하나뿐이라 늦은 import로 끊는다.
        from modules.thesis.store import ThesisStore

        count = clamp_int(arguments.get("n"), MIN_PAST_THESES, MAX_PAST_THESES, MIN_PAST_THESES)
        return ThesisStore(self._connection).past_theses(as_of_at=self._as_of_at, subject_code=code, n=count)

    def _recent_documents(self, arguments: dict[str, Any]) -> list[Evidence]:
        hours = clamp_int(arguments.get("hours"), MIN_WINDOW_HOURS, MAX_WINDOW_HOURS, MAX_WINDOW_HOURS)
        min_score = clamp_int(arguments.get("min_score"), MIN_VALUE_SCORE, MAX_VALUE_SCORE, MIN_VALUE_SCORE)
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
                detail=document_detail(row),
            )
            for row in rows
        ]

    def _recent_disclosures(self, arguments: dict[str, Any]) -> list[Evidence]:
        hours = clamp_int(arguments.get("hours"), MIN_WINDOW_HOURS, MAX_WINDOW_HOURS, MAX_WINDOW_HOURS)
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
                detail=DisclosureDetail(
                    stock_code=row[1],
                    company_name=row[2],
                    report_name=row[3],
                    receipt_date=row[4].isoformat(),
                    detected_at=row[5].isoformat(),
                ),
            )
            for row in rows
        ]

    def _macro_changes(self, _arguments: dict[str, Any]) -> list[Evidence]:
        with self._connection.cursor() as cursor:
            cursor.execute(
                WINDOW_CHANGES,
                {
                    "window_start": self._macro_window_start,
                    "as_of_at": self._as_of_at,
                    "kinds": list(MACRO_KINDS),
                    "domestic_country": DOMESTIC_COUNTRY,
                    "domestic_kinds": list(DOMESTIC_SESSION_KINDS),
                },
            )
            rows = cursor.fetchall()
        return [
            Evidence(
                kind=ThesisEvidenceKind.MACRO_CHANGE,
                ref=evidence_ref(ThesisEvidenceKind.MACRO_CHANGE, row[1]),
                title=macro_title(row),
                url=None,
                detail=macro_detail(row),
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
                title=f"{row[2]} 마감 {change_label(row[3], row[5], row[4])}",
                url=None,
                detail=us_close_detail(row),
            )
            for row in rows[:MAX_TOOL_RESULTS]
        ]
