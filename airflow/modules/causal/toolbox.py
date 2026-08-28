"""주간 인과 그래프가 쓰는 툴 — 시세 창, 투자자별 수급, 과거 경로.

**LangChain을 import한다.** 그래서 `domain.py`가 이 모듈을 모르고, DAG 테스트와 순수 함수
테스트가 이 무게 없이 돈다.

첫 배포에는 툴이 없었다. 다섯 판을 돌린 결과가 늘릴 신호를 냈다 — `observed`가 5/25에
그치고, `외국인 수급` 채널을 쓰면서 투자자별 매매를 한 줄도 안 보며, 실현 등락이 대상
주부터라 선반영(§9)에 닿지 않았다. 운영 DB 56개 테이블 1,017,063행 중 흐름이 읽는 것이
11개 143,043행이었다.

**전부 창 인자를 받는다.** 기존 툴 14개(`thesis/toolbox.py`)는 `recent_*(hours)` 형태라 슬롯
직전 몇 시간용이고 과거 구간에 그대로 쓸 수 없다. 조회 SQL도 새 파일이다
(`airflow/sql/postgres/causal_tools/`).

계약은 `docs/analysis/market-causal-graph.md` §5.2다.
"""

import logging
from collections.abc import Sequence
from datetime import date, timedelta
from typing import Any

from langchain_core.tools import BaseTool, StructuredTool
from pydantic import BaseModel, ConfigDict, Field

from modules.causal.domain import (
    CausalTarget,
    CausalTargetKind,
    CausalWindow,
)
from modules.db import Connection
from modules.sql import read_sql

logger = logging.getLogger(__name__)

# 사건 전 며칠까지 거슬러 볼 수 있는가. **선반영을 보는 창이다**(설계 §9) — 국채가 기준금리를
# 먼저 반영하는지 물으려면 사건 앞을 봐야 한다. 60일이면 두 달치 거래일 40여 개다.
MAX_DAYS_BEFORE = 60

# 과거 경로를 몇 주까지 거슬러 보는가. 어휘가 수렴하는 데 8주가 걸렸으므로(프로토타입)
# 그보다 넉넉히 둔다.
MAX_PAST_WEEKS = 12

# 한 호출이 돌려주는 과거 경로 수. 한 대상에 주당 서너 개가 붙으므로 12주면 40 안팎이다.
MAX_PAST_PATHS = 60

PRICE_WINDOW_SQL: dict[CausalTargetKind, str] = {
    CausalTargetKind.INDEX: read_sql("postgres", "causal_tools", "price_window_index.sql"),
    CausalTargetKind.INSTRUMENT: read_sql("postgres", "causal_tools", "price_window_stock.sql"),
    CausalTargetKind.QUOTE: read_sql("postgres", "causal_tools", "price_window_quote.sql"),
    CausalTargetKind.INDICATOR: read_sql(
        "postgres", "causal_tools", "price_window_indicator.sql"
    ),
}
INVESTOR_FLOW_SQL = read_sql("postgres", "causal_tools", "investor_flow.sql")
PAST_PATHS_SQL = read_sql("postgres", "causal_tools", "past_paths.sql")


class ToolLimitExceeded(RuntimeError):
    """상한에 걸리거나 목록 밖 대상이라 실행하지 않았다.

    **`ToolNode`가 이 타입만 오류 `ToolMessage`로 바꾼다.** 모델이 고쳐 부를 수 있는 것만
    여기 담고, DB 오류는 그대로 올라가 태스크를 죽인다 — `handle_tool_errors=True`(기본값)는
    둘을 안 가려 연결 끊김이 "결과 없음"으로 위장된다.
    """


class PriceWindowArgs(BaseModel):
    """`price_window` 인자. 상한은 코드 상수가 원본이고 설명에 f-string으로 실린다."""

    target_code: str = Field(description="대상 코드. 위 대상 목록 안의 값만 받는다")
    days_before: int = Field(
        description=(
            f"대상 주 시작일로부터 며칠 전까지 거슬러 볼지. 1~{MAX_DAYS_BEFORE}. "
            "사건보다 값이 먼저 움직였는지(선반영) 보려면 넉넉히 준다"
        )
    )


class InvestorFlowArgs(BaseModel):
    """`investor_flow` 인자. 국내 종목만 받는다."""

    stock_code: str = Field(description="국내 종목 코드. 지수·환율·금리에는 이 값이 없다")


class PastPathsArgs(BaseModel):
    """`past_paths` 인자."""

    target_code: str = Field(description="대상 코드. 위 대상 목록 안의 값만 받는다")
    weeks: int = Field(
        description=f"몇 주를 거슬러 볼지. 1~{MAX_PAST_WEEKS}"
    )


class PriceRow(BaseModel):
    """하루치 값. 금리는 퍼센트 포인트, 나머지는 가격이다."""

    model_config = ConfigDict(frozen=True)

    business_date: date
    close: float


class PriceWindow(BaseModel):
    """`price_window` 응답."""

    model_config = ConfigDict(frozen=True)

    code: str
    kind: CausalTargetKind
    rows: tuple[PriceRow, ...]


class FlowRow(BaseModel):
    """하루치 투자자별 순매수 **수량**. 금액이 아니다 — 금액 칸이 셋뿐이라 짝이 안 맞는다."""

    model_config = ConfigDict(frozen=True)

    business_date: date
    close: float | None
    foreign_net_buy: int | None
    institution_net_buy: int | None
    individual_net_buy: int | None
    pension_fund_net_buy: int | None
    investment_trust_net_buy: int | None


class InvestorFlow(BaseModel):
    """`investor_flow` 응답."""

    model_config = ConfigDict(frozen=True)

    code: str
    rows: tuple[FlowRow, ...]


class PastPath(BaseModel):
    """과거 주가 이 대상에 이은 경로 하나."""

    model_config = ConfigDict(frozen=True)

    week_start: date
    event_title: str
    occurred_on: date
    chain: str
    sign: str
    confidence: str
    week_change: float | None
    t1_change: float | None
    t5_change: float | None
    unit: str
    reasoning: str


class PastPaths(BaseModel):
    """`past_paths` 응답."""

    model_config = ConfigDict(frozen=True)

    code: str
    paths: tuple[PastPath, ...]


TOOL_DESCRIPTIONS: dict[str, str] = {
    "price_window": (
        "대상의 일별 종가를 사건 전 구간부터 반응 끝까지 준다. "
        "실현 등락은 세 숫자(주간·T+1·T+5)로 접혀 있어 주 안에서의 경로와 사건 전 움직임을 "
        "볼 수 없다. **값이 사건보다 먼저 움직였는지**를 확인할 때 쓴다. "
        "금리 계열은 퍼센트 포인트 수준 값이고 나머지는 가격이다."
    ),
    "investor_flow": (
        "국내 종목의 투자자별 일별 순매수 수량. 외국인·기관·개인·연기금·투신 다섯을 준다. "
        "`수급`이나 `외국인 수급` 같은 경로를 쓸 참이면 **먼저 이 툴로 확인한다** — "
        "종가만 보고 누가 샀는지 추측하지 마라. 지수·환율·금리에는 이 값이 없다."
    ),
    "past_paths": (
        "이 대상에 과거 주가 이은 경로. 사건, 사슬, 방향, 확신, 실현 등락을 준다. "
        "기존 경로 후보 목록은 이름만 주므로, **그 이름이 전에 어떻게 쓰였는지**를 보려면 "
        "이 툴을 쓴다. 같은 사슬이 반복해 맞았는지 틀렸는지가 여기 있다."
    ),
}


class CausalToolbox:
    """한 실행이 쓰는 툴 묶음. 연결·창·대상 목록을 쥔다.

    **바인드된 메서드를 `StructuredTool.from_function`으로 감싼다.** 툴이 이 객체의 상태를
    봐야 해서 모듈 수준 `@tool`을 쓸 수 없다. JSON Schema는 `args_schema`에서 뽑으므로 우리가
    손으로 쓰지 않는다.
    """

    def __init__(
        self,
        *,
        connection: Connection,
        window: CausalWindow,
        targets: Sequence[CausalTarget],
    ) -> None:
        self._connection = connection
        self._window = window
        self._targets = {target.code: target for target in targets}
        self.tools: list[BaseTool] = self._build_tools()

    def _build_tools(self) -> list[BaseTool]:
        return [
            StructuredTool.from_function(
                func=self.price_window,
                name="price_window",
                description=TOOL_DESCRIPTIONS["price_window"],
                args_schema=PriceWindowArgs,
            ),
            StructuredTool.from_function(
                func=self.investor_flow,
                name="investor_flow",
                description=TOOL_DESCRIPTIONS["investor_flow"],
                args_schema=InvestorFlowArgs,
            ),
            StructuredTool.from_function(
                func=self.past_paths,
                name="past_paths",
                description=TOOL_DESCRIPTIONS["past_paths"],
                args_schema=PastPathsArgs,
            ),
        ]

    def price_window(self, target_code: str, days_before: int) -> PriceWindow:
        """대상의 일별 값. 사건 전 `days_before`일부터 반응 끝까지."""
        target = self._target(target_code)
        if not 1 <= days_before <= MAX_DAYS_BEFORE:
            raise ToolLimitExceeded(
                f"days_before must be between 1 and {MAX_DAYS_BEFORE}, got {days_before}"
            )
        parameters: dict[str, Any] = {
            "code": target.code,
            "start": self._window.week_start - timedelta(days=days_before),
            "end": self._window.reaction_end,
        }
        if target.kind is CausalTargetKind.INDICATOR:
            # `series_id`는 제공처 안에서만 고유하다. 하나로 걸면 조용히 틀린다.
            parameters["provider"] = target.provider
        with self._connection.cursor() as cursor:
            cursor.execute(PRICE_WINDOW_SQL[target.kind], parameters)
            rows = tuple(
                PriceRow(business_date=row[0], close=float(row[1])) for row in cursor.fetchall()
            )
        return PriceWindow(code=target.code, kind=target.kind, rows=rows)

    def investor_flow(self, stock_code: str) -> InvestorFlow:
        """국내 종목의 투자자별 일별 순매수. 대상 주와 반응 주를 함께 본다."""
        target = self._target(stock_code)
        if target.kind is not CausalTargetKind.INSTRUMENT:
            raise ToolLimitExceeded(
                f"investor_flow takes a domestic stock, got {stock_code} ({target.kind})"
            )
        with self._connection.cursor() as cursor:
            cursor.execute(
                INVESTOR_FLOW_SQL,
                {
                    "code": target.code,
                    "start": self._window.week_start,
                    "end": self._window.reaction_end,
                },
            )
            rows = tuple(
                FlowRow(
                    business_date=row[0],
                    close=_number(row[1]),
                    foreign_net_buy=row[2],
                    institution_net_buy=row[3],
                    individual_net_buy=row[4],
                    pension_fund_net_buy=row[5],
                    investment_trust_net_buy=row[6],
                )
                for row in cursor.fetchall()
            )
        return InvestorFlow(code=target.code, rows=rows)

    def past_paths(self, target_code: str, weeks: int) -> PastPaths:
        """이 대상에 과거 주가 이은 경로. **대상 주 이전만 본다.**"""
        target = self._target(target_code)
        if not 1 <= weeks <= MAX_PAST_WEEKS:
            raise ToolLimitExceeded(
                f"weeks must be between 1 and {MAX_PAST_WEEKS}, got {weeks}"
            )
        with self._connection.cursor() as cursor:
            cursor.execute(
                PAST_PATHS_SQL,
                {
                    "code": target.code,
                    "since": self._window.week_start - timedelta(weeks=weeks),
                    "week_start": self._window.week_start,
                    "limit": MAX_PAST_PATHS,
                },
            )
            paths = tuple(
                PastPath(
                    week_start=row[0],
                    event_title=row[1],
                    occurred_on=row[2],
                    chain=row[3] or "",
                    sign=row[4],
                    confidence=row[5],
                    week_change=_number(row[6]),
                    t1_change=_number(row[7]),
                    t5_change=_number(row[8]),
                    unit=row[9],
                    reasoning=row[10],
                )
                for row in cursor.fetchall()
            )
        return PastPaths(code=target.code, paths=paths)

    def _target(self, code: str) -> CausalTarget:
        """목록 밖 대상은 모델이 고쳐 부를 수 있는 실수다. 태스크를 죽이지 않는다."""
        target = self._targets.get(code)
        if target is None:
            raise ToolLimitExceeded(
                f"unknown target {code}; pick one of {', '.join(sorted(self._targets))}"
            )
        return target


def _number(value: Any) -> float | None:
    """`Decimal`을 JSON number로. 경계에서 한 번만 바꾼다."""
    return None if value is None else float(value)
