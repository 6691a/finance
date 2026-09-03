"""모델이 부르는 툴 셋 — `KospiToolbox`.

툴은 **셋뿐이다.** 옛 추론은 열다섯이었고 그중 무엇이 값을 냈는지 끝내 못 가렸다. 셋이면
"어느 툴이 부족한가"를 원장으로 읽을 수 있다.

| 툴 | 무엇 |
| --- | --- |
| `factor_history` | 요인 하나의 일별 값과 변화 |
| `recent_news` | 기준 시각까지의 평가된 기사 |
| `recent_disclosures` | 기준 시각까지의 본문 있는 공시 |

**모든 창의 끝은 `as_of_at`이다.** `hours`·`days`는 거기서 거슬러 올라가는 길이이지
`now()`에서가 아니다. SQL 술어는 event-time 컬럼에 건다.

**DB 오류를 위장하지 않는다.** 연결 끊김이나 SQL 오류를 빈 결과로 바꾸지 않고 그대로 올려
태스크를 죽인다. 빈 결과는 "그 창에 그것이 없다"는 뜻이어야 한다.

**어느 요인을 조회했는지가 검증의 재료다.** `queried_factors`를 답변 검증이 읽는다 —
툴로 본 적 없는 요인을 이유나 관찰에 쓰면 버린다. 그것이 모델이 숫자를 지어내지 못하게
막는 장치다.
"""

import json
import logging
from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Any

from langchain_core.messages import BaseMessage, ToolMessage
from langchain_core.tools import BaseTool, StructuredTool
from langgraph.prebuilt import ToolNode

# **공개 API가 아니다.** `langgraph.prebuilt`가 export하지 않아 여기서만 import된다.
# 마이너 판올림에서 움직일 수 있다는 것을 알고 쓴다 — 대안은 인자 검증 실패도 태스크
# 실패로 두거나(모델이 고쳐 부를 기회를 잃는다) 상위 `ToolException`으로 잡는 것인데
# (툴이 던지는 **모든** 것을 삼킨다) 둘 다 이쪽보다 나쁘다.
from langgraph.prebuilt.tool_node import ToolInvocationError
from pydantic import BaseModel

from modules.db import Connection
from modules.kospi.domain import (
    DEFAULT_HISTORY_DAYS,
    DEFAULT_WINDOW_HOURS,
    FACTOR_SPECS,
    HISTORY_FACTORS,
    INDEX_CODE,
    INDEX_PROVIDER,
    MAX_HISTORY_DAYS,
    MAX_TOOL_CALLS,
    MAX_TOOL_RESULT_CHARS,
    MAX_TOOL_RESULTS,
    MAX_VALUE_SCORE,
    MAX_WINDOW_HOURS,
    MIN_HISTORY_DAYS,
    MIN_VALUE_SCORE,
    MIN_WINDOW_HOURS,
    Factor,
    FactorSource,
    FactorUnit,
    ToolCallRecord,
    ToolLimitExceeded,
)
from modules.kospi.tool_args import (
    TOOL_DESCRIPTIONS,
    FactorHistoryArgs,
    RecentDisclosuresArgs,
    RecentNewsArgs,
)
from modules.kospi.tool_ledger import ToolCallLedger, message_text
from modules.kospi.tools import (
    DisclosureRow,
    FactorHistoryPayload,
    FactorPoint,
    FlowPoint,
    NewsRow,
    StockPoint,
)
from modules.sql import read_sql

logger = logging.getLogger(__name__)

FACTOR_QUOTE = read_sql("postgres", "kospi_tools", "select_factor_quote.sql")
FACTOR_INDICATOR = read_sql("postgres", "kospi_tools", "select_factor_indicator.sql")
FACTOR_FLOW = read_sql("postgres", "kospi_tools", "select_factor_flow.sql")
FACTOR_STOCK = read_sql("postgres", "kospi_tools", "select_factor_stock.sql")
RECENT_NEWS = read_sql("postgres", "kospi_tools", "select_news.sql")
RECENT_DISCLOSURES = read_sql("postgres", "kospi_tools", "select_disclosures.sql")

# 지표 요인의 제공처. 둘 다 ECOS다 — `series_id`만으로 걸면 제공처가 늘 때 조용히 틀린다.
INDICATOR_PROVIDER = "ecos"

# 공시 본문을 몇 자까지 싣나. 전문을 실으면 툴 하나가 문자 예산을 다 먹는다.
DISCLOSURE_BODY_CHARS = 1_500

UNIT_NOTES: dict[FactorUnit, str] = {
    FactorUnit.PERCENT: "change는 값 차이, change_pct는 퍼센트다",
    FactorUnit.BASIS_POINT: "change는 bp 차이다. 퍼센트로 읽지 마라",
    FactorUnit.SHARES: "net_buy_qty는 주식 수다. net_buy_amount_raw는 제공처 원문이고 단위가 확정되지 않았다",
    FactorUnit.NONE: "값이 아니라 글이다",
}


def tool_node(toolbox: "KospiToolbox") -> ToolNode:
    """툴 실행 노드. 두 그래프(전망·관찰)가 같은 것을 쓴다.

    **`handle_tool_errors`에 타입을 준다.** `ToolLimitExceeded`(상한 초과·모르는 툴·목록 밖
    요인)와 `ToolInvocationError`(인자가 스키마와 안 맞음)만 오류 `ToolMessage`가 되어 모델이
    고쳐 부를 기회를 얻고, psycopg 오류 같은 나머지는 그대로 올라가 태스크를 죽인다.

    기본값(`True`)을 쓰면 **연결 끊김이 "결과 없음"으로 위장된다.**
    """
    return ToolNode(toolbox.tools, handle_tool_errors=(ToolLimitExceeded, ToolInvocationError))


class KospiToolbox:
    """읽기 전용 툴 셋과 조회 기록.

    상태는 셋이다 — 연결, 기준 시각, 그리고 이 대화가 쓴 예산. 셋 다 실행 동안 안 변해서
    생성자가 받는다. 요인·창처럼 호출마다 바뀌는 것은 메서드 인자다.
    """

    def __init__(self, connection: Connection, *, as_of_at: datetime) -> None:
        self._connection = connection
        self._as_of_at = as_of_at
        self._calls = 0
        self._chars = 0
        # **이 대화가 실제로 값을 본 요인.** 답변 검증이 이것을 읽는다.
        self._queried: set[Factor] = set()
        # 문서 툴을 부른 적이 있나. `NEWS`·`DISCLOSURE` 요인의 검증이 이것을 본다.
        self._saw_news = False
        self._saw_disclosures = False
        # 원장은 따로 산다. 기록만 쌓고 **DB에는 쓰지 않는다** — 읽기 전용 툴이라는 성격을
        # 유지하고 저장 시점은 부르는 쪽(`store.py`)이 정한다.
        self._ledger = ToolCallLedger()
        self._tools = self._build_tools()
        self._by_name = {tool.name: tool for tool in self._tools}

    def _build_tools(self) -> list[BaseTool]:
        """`ToolNode`와 `bind_tools`에 그대로 넘길 툴 목록.

        **함수는 전부 `record`로 감싼다.** 툴 하나하나에 계측 코드를 넣지 않는다 — 래퍼가
        실제 인자·소요·결과를 채우고, 숨은 `tool_call_id`가 어느 요청이었는지를 잇는다.

        함수는 **바인드된 메서드**다. 툴이 연결·기준 시각·예산 같은 이 객체의 상태를 봐야
        해서 모듈 수준 `@tool`을 쓸 수 없다.
        """
        return [
            StructuredTool.from_function(
                func=self._ledger.record("factor_history", self._tool_factor_history),
                name="factor_history",
                description=TOOL_DESCRIPTIONS["factor_history"],
                args_schema=FactorHistoryArgs,
            ),
            StructuredTool.from_function(
                func=self._ledger.record("recent_news", self._tool_recent_news),
                name="recent_news",
                description=TOOL_DESCRIPTIONS["recent_news"],
                args_schema=RecentNewsArgs,
            ),
            StructuredTool.from_function(
                func=self._ledger.record("recent_disclosures", self._tool_recent_disclosures),
                name="recent_disclosures",
                description=TOOL_DESCRIPTIONS["recent_disclosures"],
                args_schema=RecentDisclosuresArgs,
            ),
        ]

    # --- 속성 -------------------------------------------------------------

    @property
    def tools(self) -> list[BaseTool]:
        return self._tools

    @property
    def call_count(self) -> int:
        return self._calls

    @property
    def queried_factors(self) -> frozenset[Factor]:
        """이 대화가 값을 본 요인 전부. **답변 검증의 원본이다.**

        문서 툴을 부르면 `NEWS`·`DISCLOSURE`가 여기 들어온다 — 그 둘은 `factor_history`가
        받지 않는 요인이라 다른 길로 들어와야 한다.
        """
        seen = set(self._queried)
        if self._saw_news:
            seen.add(Factor.NEWS)
        if self._saw_disclosures:
            seen.add(Factor.DISCLOSURE)
        return frozenset(seen)

    @property
    def tool_calls(self) -> tuple[ToolCallRecord, ...]:
        return self._ledger.calls

    @property
    def round_count(self) -> int:
        return self._ledger.round_count

    def begin_round(self, tool_calls: Sequence[dict[str, Any]]) -> None:
        self._ledger.begin_round(tool_calls)

    def finish_round(self, messages: Sequence[BaseMessage]) -> None:
        """**유효한 툴 이름은 여기서만 안다.** 원장이 그것으로 "모르는 툴"과 "인자 검증 실패"를
        가른다 — 둘 다 함수에 도달하지 못해 래퍼가 못 보는 실패다.
        """
        self._ledger.finish_round(messages, known_tools=self._by_name)

    def close_open_records(self) -> None:
        self._ledger.close_open_records()

    # --- 툴 본체 ----------------------------------------------------------

    def _tool_factor_history(self, factor: str, days: int = DEFAULT_HISTORY_DAYS) -> str:
        self._charge()
        code = self._resolve_factor(factor)
        spec = FACTOR_SPECS[code]
        span = _clamp(days, MIN_HISTORY_DAYS, MAX_HISTORY_DAYS, DEFAULT_HISTORY_DAYS)
        payload = FactorHistoryPayload(
            factor=code,
            label=spec.label,
            unit=spec.unit,
            unit_note=UNIT_NOTES[spec.unit],
            **self._factor_rows(spec.source, spec.key, span),
        )
        # **조회했다는 사실을 남긴다.** 답변 검증이 이것으로 "본 것"과 "지어낸 것"을 가른다.
        self._queried.add(code)
        return self._body(payload)

    def _tool_recent_news(self, hours: int = DEFAULT_WINDOW_HOURS, min_score: int = MIN_VALUE_SCORE) -> str:
        self._charge()
        span = _clamp(hours, MIN_WINDOW_HOURS, MAX_WINDOW_HOURS, DEFAULT_WINDOW_HOURS)
        score = _clamp(min_score, MIN_VALUE_SCORE, MAX_VALUE_SCORE, MIN_VALUE_SCORE)
        rows = self._fetch(
            RECENT_NEWS,
            {
                "window_start": self._as_of_at - timedelta(hours=span),
                "as_of_at": self._as_of_at,
                "min_score": score,
                "limit": MAX_TOOL_RESULTS,
            },
        )
        # **0건이면 본 것이 아니다.** 빈 결과에 플래그를 세우면 행 하나 못 봤는데
        # `factor: NEWS` 이유가 검증을 통과한다.
        self._saw_news = bool(rows)
        return self._body(
            [
                NewsRow(
                    document_id=row[0],
                    title=row[1],
                    source=row[2],
                    published_at=row[3],
                    value_score=row[4],
                    direction=row[5],
                    reason=row[6],
                    new_facts=list(row[7] or []),
                    tickers=tuple(row[8] or ()),
                )
                for row in rows
            ]
        )

    def _tool_recent_disclosures(self, hours: int = DEFAULT_WINDOW_HOURS) -> str:
        self._charge()
        span = _clamp(hours, MIN_WINDOW_HOURS, MAX_WINDOW_HOURS, DEFAULT_WINDOW_HOURS)
        rows = self._fetch(
            RECENT_DISCLOSURES,
            {
                "window_start": self._as_of_at - timedelta(hours=span),
                "as_of_at": self._as_of_at,
                "body_chars": DISCLOSURE_BODY_CHARS,
                "limit": MAX_TOOL_RESULTS,
            },
        )
        # 두 회사 범위라 하루 창은 대개 빈다(09-03 실측 12회 전부 `[]`). 그때 `DISCLOSURE`가
        # 인용 가능해지면 안 본 공시를 근거로 쓸 수 있다.
        self._saw_disclosures = bool(rows)
        return self._body(
            [
                DisclosureRow(
                    rcept_no=row[0],
                    stock_code=row[1],
                    company_name=row[2],
                    report_name=row[3],
                    receipt_date=row[4],
                    detected_at=row[5],
                    body=row[6] or "",
                )
                for row in rows
            ]
        )

    # --- 조회 -------------------------------------------------------------

    def _factor_rows(self, source: FactorSource, key: str, span: int) -> dict[str, Any]:
        """요인의 자리에 맞는 SQL 하나를 골라 payload 칸을 만든다.

        **요인이 늘어도 이 분기는 안 는다.** `FactorSource`가 넷이고 새 요인은 그중 하나에
        속한다 — 다섯째 자리가 생길 때만 여기가 는다.
        """
        if source is FactorSource.QUOTE_DAILY:
            rows = self._fetch(FACTOR_QUOTE, {"symbol": key, "as_of_at": self._as_of_at, "limit": span})
            return {"points": tuple(FactorPoint(date=row[0], value=_num(row[1]), change=_num(row[2]), change_pct=_num(row[3])) for row in rows)}
        if source is FactorSource.INDICATOR:
            rows = self._fetch(
                FACTOR_INDICATOR,
                {"provider": INDICATOR_PROVIDER, "series_id": key, "as_of_at": self._as_of_at, "limit": span},
            )
            return {"points": tuple(FactorPoint(date=row[0], value=_num(row[1]), change=_num(row[2]), change_pct=None) for row in rows)}
        if source is FactorSource.MARKET_FLOW:
            rows = self._fetch(
                FACTOR_FLOW,
                {"market_code": INDEX_CODE, "as_of_at": self._as_of_at, "limit": span},
            )
            index = {"foreign": (2, 5), "institution": (3, 6), "individual": (4, 7)}[key]
            return {
                "flows": tuple(
                    FlowPoint(
                        date=row[0],
                        observed_at=row[1],
                        net_buy_qty=_num(row[index[0]]),
                        net_buy_amount_raw=_num(row[index[1]]),
                    )
                    for row in rows
                )
            }
        if source is FactorSource.STOCK_DAILY:
            rows = self._fetch(
                FACTOR_STOCK,
                {"provider": INDEX_PROVIDER, "stock_code": key, "as_of_at": self._as_of_at, "limit": span},
            )
            return {
                "stocks": tuple(
                    StockPoint(
                        date=row[0],
                        close=_num(row[1]),
                        change_pct=_num(row[3]),
                        foreign_net_buy_qty=_num(row[4]),
                        institution_net_buy_qty=_num(row[5]),
                        individual_net_buy_qty=_num(row[6]),
                    )
                    for row in rows
                )
            }
        # 문서 요인은 `HISTORY_FACTORS`에서 빠져 `_resolve_factor`가 먼저 거절한다.
        raise ToolLimitExceeded(f"{source.value} 요인은 factor_history로 볼 수 없다. recent_news·recent_disclosures를 써라")

    def _fetch(self, statement: str, parameters: dict[str, Any]) -> list[Sequence[Any]]:
        with self._connection.cursor() as cursor:
            cursor.execute(statement, parameters)
            return list(cursor.fetchall())

    # --- 상한과 직접 호출 --------------------------------------------------

    def _resolve_factor(self, value: str) -> Factor:
        """목록 밖 요인은 모델이 고쳐 부를 수 있는 오류다.

        **조용히 첫 값으로 떨어뜨리지 않는다.** 그러면 모델이 `반도체 업황`을 물었는데
        외국인 수급을 받고, 그것을 그 요인의 값이라고 믿는다.
        """
        try:
            code = Factor(str(value).strip().upper())
        except ValueError as error:
            raise ToolLimitExceeded(
                f"모르는 요인이다: {value!r}. 쓸 수 있는 것은 {[item.value for item in HISTORY_FACTORS]}"
            ) from error
        if code not in HISTORY_FACTORS:
            raise ToolLimitExceeded(
                f"{code.value}는 값이 아니라 글이다. recent_news·recent_disclosures를 써라"
            )
        return code

    def _body(self, payload: BaseModel | Sequence[BaseModel]) -> str:
        """모델에게 갈 본문. 문자 예산을 단다.

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

    def _charge(self) -> None:
        """호출 한 번을 상한에 단다. 넘으면 실행하지 않고 `ToolLimitExceeded`다.

        **걸린 사실을 로그로 남긴다.** 이 예외는 `ToolNode`가 오류 `ToolMessage`로 바꿔
        모델에게 돌려주므로 태스크는 성공으로 끝난다. 상한에 걸려 덜 보고 답한 실행과 다
        보고 답한 실행이 원장 밖에서는 구분되지 않는다.
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
        """
        tool = self._by_name.get(name)
        if tool is None:
            raise ToolLimitExceeded(f"모르는 툴 이름이다: {name!r}. 쓸 수 있는 것은 {sorted(self._by_name)}")
        # **full ToolCall dict로 부른다.** 인자 dict만 넘기면 `InjectedToolCallId`를 채울
        # id가 없어 LangChain이 거절한다. 운영 경로가 넘기는 모양과 같게 둬야 이 경로만
        # 다르게 도는 일이 없다.
        call_id = f"manual_{len(self._ledger.calls) + 1}"
        self.begin_round([{"name": name, "args": dict(arguments), "id": call_id}])
        reply = tool.invoke({"name": name, "args": dict(arguments), "id": call_id, "type": "tool_call"})
        if isinstance(reply, ToolMessage):
            self.finish_round([reply])
            return message_text(reply)
        return str(reply)


def _clamp(value: Any, low: int, high: int, fallback: int) -> int:
    """모델이 범위 밖 정수를 보내면 조인다. 스키마가 먼저 막지만 제공처가 안 지킬 수 있다."""
    try:
        number = int(value)
    except (TypeError, ValueError):
        return fallback
    return max(low, min(high, number))


def _num(value: Any) -> float | None:
    """`Decimal`을 JSON number로. **`None`은 `None`으로 둔다** — 0으로 채우면 "재지 않았다"가
    "0이다"가 된다."""
    return None if value is None else float(value)
