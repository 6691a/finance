"""모아 둔 값을 LLM이 부를 수 있는 툴로 노출한다.

`docs/economic-document-archive-design.md` 4단계가 쓴다. 모델에게 데이터를 통째로 넣는 대신
**무엇이 있는지 목록을 주고 필요한 것만 부르게 한다.** 분봉 73만 행과 금리 20만 행을
프롬프트에 넣을 방법은 없다.

## LLM에게 SQL을 주지 않는다

툴은 여기 등록된 것만 있다. 이름과 인자가 스키마로 고정돼 있고 인자는 Pydantic이 검증한다.
자유 SQL을 주면 틀린 조인 하나가 그럴듯한 숫자로 리포트에 들어가고, 그걸 사후에 가려낼
방법이 없다. `ecos.py`가 항목코드를 Enum으로 좁히는 것과 같은 판단이다.

모르는 툴 이름이나 인자는 호출 자체가 거절된다. 그 사실이 모델에게 오류로 돌아가고,
아무 값도 만들어지지 않는다.

## 시계열은 좌표 하나로 부른다

금리는 `indicator_observation`, 환율은 `exchange_rate`, 지수·원자재는 `quote_daily`, 종목
종가는 `stock_investor_trade_daily`에 있다. 툴을 넷으로 나누면 모델이 어느 것을 부를지부터
틀린다. `daily_series` 뷰가 넷을 한 이름 공간으로 모으고, 좌표는 `provider:series_id`
문자열 하나다(`yahoo:USDKRW`, `fred:DGS10`). 문서 태깅 프롬프트가 쓰는 표기와 같다.

## 결과 크기는 우리가 정한다

모든 툴에 행 상한이 있다. 상한이 없으면 한 번의 호출이 컨텍스트 창을 통째로 먹는다.
"""

import json
import logging
from collections.abc import Callable, Sequence
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Protocol, Self

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from modules.llm import ChatClient, ToolCall
from modules.sql import read_sql

logger = logging.getLogger(__name__)

# 한 번의 호출이 돌려줄 수 있는 최대 행 수. 프롬프트에 그대로 들어가므로 상한이 필요하다.
MAX_ROWS = 500
DEFAULT_ROWS = 120

# 한 번의 `get_series` 호출이 받을 수 있는 계열 수. 하나씩 부르면 조사 예산이 나열로 다 나간다.
MAX_SERIES_PER_CALL = 12

# 상관을 낼 때 쓸 최대 관측 수. 넘겨도 계산은 되지만 최근 구간을 보는 것이 목적이다.
MAX_WINDOW = 2500
DEFAULT_WINDOW = 120

# 이보다 표본이 적으면 상관을 숫자로 돌려주되 경고를 함께 싣는다. 24일로 낸 0.9는 이야기가
# 아니라 잡음이다.
MIN_MEANINGFUL_OBSERVATIONS = 60


class Cursor(Protocol):
    def __enter__(self) -> Self: ...

    def __exit__(self, *args: object) -> bool | None: ...

    def execute(self, statement: str, parameters: Sequence[Any] = ()) -> object: ...

    def fetchall(self) -> Any: ...


class Connection(Protocol):
    def cursor(self) -> Cursor: ...


class ToolError(ValueError):
    """모델이 부를 수 없는 툴이거나 인자가 스키마에 맞지 않는다."""


def _series_ref(value: str) -> tuple[str, str]:
    """`provider:series_id`를 좌표로 쪼갠다."""
    provider, separator, series_id = value.partition(":")
    if not separator or not provider or not series_id:
        raise ToolError(f"series must look like 'provider:series_id', got {value!r}")
    return provider, series_id


class ListSeriesInput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: str | None = Field(
        default=None,
        description="price(지수·원자재·종목), rate(금리), fx(환율) 중 하나로 좁힌다. 비우면 전부.",
    )
    query: str | None = Field(default=None, description="이름이나 라벨에 포함된 문자열로 좁힌다.")


class GetSeriesInput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    series: tuple[str, ...] = Field(
        min_length=1,
        max_length=MAX_SERIES_PER_CALL,
        description=(
            "시계열 좌표 목록. 'provider:series_id' 형식이다. "
            f"**한 번에 최대 {MAX_SERIES_PER_CALL}개를 받을 수 있다. 하나씩 나눠 부르지 마라.** "
            "예: ['fred:DGS10', 'ecos:KTB10Y', 'mof:JGB10Y']"
        ),
    )
    start: date | None = Field(default=None, description="시작일(YYYY-MM-DD). 비우면 끝에서부터 limit만큼.")
    end: date | None = Field(default=None, description="종료일(YYYY-MM-DD).")
    limit: int = Field(default=DEFAULT_ROWS, ge=1, le=MAX_ROWS, description="계열마다 돌려받을 최대 행 수.")


class SeriesChangeInput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    series: tuple[str, ...] = Field(
        min_length=1,
        max_length=MAX_SERIES_PER_CALL,
        description=f"시계열 좌표 목록. 한 번에 최대 {MAX_SERIES_PER_CALL}개.",
    )
    start: date | None = Field(default=None, description="구간 시작일(YYYY-MM-DD).")
    end: date | None = Field(default=None, description="구간 종료일(YYYY-MM-DD).")


class SeriesPair(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    left: str = Field(description="빼는 쪽 좌표. 예: fred:DGS10")
    right: str = Field(description="빼이는 쪽 좌표. 예: fred:DGS2")


class SeriesSpreadInput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    pairs: tuple[SeriesPair, ...] = Field(
        min_length=1,
        max_length=MAX_SERIES_PER_CALL,
        description=(
            f"차이를 볼 쌍 목록. **한 번에 최대 {MAX_SERIES_PER_CALL}쌍. 쌍마다 나눠 부르지 마라.** "
            '예: [{"left": "fred:DGS10", "right": "fred:DGS2"}, {"left": "ecos:KTB10Y", "right": "fred:DGS10"}]'
        ),
    )
    start: date | None = Field(default=None, description="구간 시작일(YYYY-MM-DD).")
    end: date | None = Field(default=None, description="구간 종료일(YYYY-MM-DD).")
    limit: int = Field(default=30, ge=1, le=MAX_ROWS, description="쌍마다 돌려받을 최대 날짜 수.")


class CompareSeriesInput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    left: str = Field(description="첫 번째 시계열 좌표. 예: yahoo:USDKRW")
    right: str = Field(description="두 번째 시계열 좌표. 예: kis:005930")
    window_days: int = Field(
        default=DEFAULT_WINDOW,
        ge=2,
        le=MAX_WINDOW,
        description="상관을 낼 최근 관측 수. 거래일 기준이며 달력 일수가 아니다.",
    )
    end: date | None = Field(default=None, description="이 날짜까지만 본다. 비우면 최신까지.")


class SearchDocumentsInput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    since: datetime | None = Field(default=None, description="이 시각 이후 발행된 문서만(ISO 8601).")
    until: datetime | None = Field(default=None, description="이 시각 이전 발행된 문서만(ISO 8601).")
    ticker: str | None = Field(default=None, description="이 종목에 태그된 문서만. 예: 005930")
    series: str | None = Field(default=None, description="이 지표에 태그된 문서만. 'provider:series_id' 형식.")
    min_score: int | None = Field(default=None, ge=0, le=8, description="value_score가 이 값 이상인 문서만.")
    limit: int = Field(default=30, ge=1, le=200, description="돌려받을 최대 문서 수.")


class GetInvestorFlowInput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    stock_code: str = Field(description="6자리 종목코드. 예: 005930")
    start: date | None = Field(default=None, description="시작일(YYYY-MM-DD).")
    end: date | None = Field(default=None, description="종료일(YYYY-MM-DD).")
    limit: int = Field(default=DEFAULT_ROWS, ge=1, le=MAX_ROWS, description="돌려받을 최대 거래일 수.")


LIST_SERIES = read_sql("postgres", "daily_series", "list.sql")
GET_SERIES = read_sql("postgres", "daily_series", "get.sql")
SERIES_CHANGE = read_sql("postgres", "daily_series", "change.sql")
SERIES_SPREAD = read_sql("postgres", "daily_series", "spread.sql")
COMPARE_SERIES = read_sql("postgres", "daily_series", "compare.sql")
SEARCH_DOCUMENTS = read_sql("postgres", "document", "search.sql")
INVESTOR_FLOW = read_sql("postgres", "stock_investor_trade_daily", "select_flow.sql")


def _rows(connection: Connection, statement: str, parameters: Sequence[Any]) -> list[tuple]:
    with connection.cursor() as cursor:
        cursor.execute(statement, tuple(parameters))
        return list(cursor.fetchall())


def _plain(value: Any) -> Any:
    """JSON으로 실을 수 있는 값으로 바꾼다. Decimal과 date가 그대로는 안 나간다."""
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime | date):
        return value.isoformat()
    return value


def _records(rows: Sequence[Sequence[Any]], columns: Sequence[str]) -> list[dict[str, Any]]:
    return [{name: _plain(value) for name, value in zip(columns, row, strict=True)} for row in rows]


def list_series(connection: Connection, request: ListSeriesInput) -> dict[str, Any]:
    rows = _rows(connection, LIST_SERIES, (request.kind, request.kind, request.query, request.query, request.query))
    columns = ("provider", "series_id", "label", "kind", "first_date", "last_date", "observations")
    series = [{**record, "series": f"{record['provider']}:{record['series_id']}"} for record in _records(rows, columns)]
    return {"count": len(series), "series": series}


def get_series(connection: Connection, request: GetSeriesInput) -> dict[str, Any]:
    refs = [_series_ref(value) for value in request.series]
    rows = _rows(
        connection,
        GET_SERIES,
        (
            [provider for provider, _ in refs],
            [series_id for _, series_id in refs],
            request.start,
            request.start,
            request.end,
            request.end,
            request.limit,
        ),
    )

    grouped: dict[str, list[dict[str, Any]]] = {f"{provider}:{series_id}": [] for provider, series_id in refs}
    for provider, series_id, business_date, value in rows:
        grouped[f"{provider}:{series_id}"].append({"business_date": _plain(business_date), "value": _plain(value)})

    return {
        "series": [
            {"series": coordinate, "count": len(values), "values": values} for coordinate, values in grouped.items()
        ]
    }


def series_change(connection: Connection, request: SeriesChangeInput) -> dict[str, Any]:
    refs = [_series_ref(value) for value in request.series]
    rows = _rows(
        connection,
        SERIES_CHANGE,
        (
            [provider for provider, _ in refs],
            [series_id for _, series_id in refs],
            request.start,
            request.start,
            request.end,
            request.end,
        ),
    )

    changes = []
    for provider, series_id, kind, first_date, last_date, observations, first_value, last_value in rows:
        change = float(last_value) - float(first_value)
        entry: dict[str, Any] = {
            "series": f"{provider}:{series_id}",
            "kind": kind,
            "first_date": _plain(first_date),
            "last_date": _plain(last_date),
            "observations": observations,
            "first_value": float(first_value),
            "last_value": float(last_value),
            "change": round(change, 8),
        }
        if kind == "rate":
            # **금리는 퍼센트 변화가 아니라 bp로 읽는다.** 4.0에서 4.1은 2.5퍼센트 상승이
            # 아니라 10bp 상승이다. 비율을 주면 그 오독이 리포트에 그대로 실린다.
            entry["change_bp"] = round(change * 100, 4)
        elif first_value:
            entry["change_percent"] = round(change / float(first_value) * 100, 6)
        changes.append(entry)

    return {"count": len(changes), "changes": changes}


def series_spread(connection: Connection, request: SeriesSpreadInput) -> dict[str, Any]:
    refs = [(_series_ref(pair.left), _series_ref(pair.right)) for pair in request.pairs]
    rows = _rows(
        connection,
        SERIES_SPREAD,
        (
            [left[0] for left, _ in refs],
            [left[1] for left, _ in refs],
            [right[0] for _, right in refs],
            [right[1] for _, right in refs],
            request.start,
            request.start,
            request.end,
            request.end,
            request.limit,
        ),
    )

    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {
        (f"{left[0]}:{left[1]}", f"{right[0]}:{right[1]}"): [] for left, right in refs
    }
    for left_provider, left_id, right_provider, right_id, business_date, left_value, right_value, spread in rows:
        key = (f"{left_provider}:{left_id}", f"{right_provider}:{right_id}")
        grouped[key].append(
            {
                "business_date": _plain(business_date),
                "left_value": _plain(left_value),
                "right_value": _plain(right_value),
                "spread": _plain(spread),
            }
        )

    spreads = []
    for (left, right), values in grouped.items():
        entry: dict[str, Any] = {"left": left, "right": right, "count": len(values), "values": values}
        if values:
            # 폭이 벌어졌는지 좁아졌는지가 곡선·나라 비교의 알맹이다. 모델이 빼게 두지 않는다.
            latest, oldest = values[0]["spread"], values[-1]["spread"]
            entry["latest_spread"] = latest
            entry["oldest_spread"] = oldest
            entry["spread_change"] = round(latest - oldest, 8)
        spreads.append(entry)

    return {"count": len(spreads), "spreads": spreads}


def compare_series(connection: Connection, request: CompareSeriesInput) -> dict[str, Any]:
    left_provider, left_id = _series_ref(request.left)
    right_provider, right_id = _series_ref(request.right)
    rows = _rows(
        connection,
        COMPARE_SERIES,
        (
            left_provider,
            left_id,
            right_provider,
            right_id,
            request.end,
            request.end,
            left_provider,
            left_id,
            right_provider,
            right_id,
            request.window_days,
        ),
    )
    observations, first_date, last_date, correlation = rows[0] if rows else (0, None, None, None)
    result = {
        "left": request.left,
        "right": request.right,
        "observations": observations,
        "first_date": _plain(first_date),
        "last_date": _plain(last_date),
        "correlation": _plain(correlation),
    }
    if observations < MIN_MEANINGFUL_OBSERVATIONS:
        # 숫자를 감추지 않고 경고를 함께 준다. 표본이 짧다는 사실이 결론의 일부다.
        result["warning"] = (
            f"표본이 {observations}개뿐이다. {MIN_MEANINGFUL_OBSERVATIONS}개 미만의 상관은 근거로 쓰지 않는다."
        )
    return result


def search_documents(connection: Connection, request: SearchDocumentsInput) -> dict[str, Any]:
    provider, series_id = _series_ref(request.series) if request.series else (None, None)
    rows = _rows(
        connection,
        SEARCH_DOCUMENTS,
        (
            request.since,
            request.since,
            request.until,
            request.until,
            request.min_score,
            request.min_score,
            request.ticker,
            request.ticker,
            request.series,
            provider,
            series_id,
            request.limit,
        ),
    )
    columns = (
        "id",
        "source",
        "published_at",
        "title",
        "summary",
        "direction",
        "value_score",
        "url",
    )
    documents = _records(rows, columns)
    return {"count": len(documents), "documents": documents}


def get_investor_flow(connection: Connection, request: GetInvestorFlowInput) -> dict[str, Any]:
    rows = _rows(
        connection,
        INVESTOR_FLOW,
        (request.stock_code, request.start, request.start, request.end, request.end, request.limit),
    )
    columns = (
        "business_date",
        "close_price",
        "foreign_net_buy_qty",
        "institution_net_buy_qty",
        "individual_net_buy_qty",
        "pension_fund_net_buy_qty",
        "investment_trust_net_buy_qty",
    )
    days = _records(rows, columns)
    return {"stock_code": request.stock_code, "count": len(days), "days": days, "unit": "주(수량)"}


class Tool(BaseModel):
    """모델에게 보이는 툴 하나. 스키마는 입력 모델이 만든다."""

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    name: str
    description: str
    input_model: type[BaseModel]
    handler: Callable[[Any, Any], dict[str, Any]]

    def spec(self) -> dict[str, Any]:
        """OpenAI 호환 function tool 정의."""
        schema = self.input_model.model_json_schema()
        schema.pop("title", None)
        for field in schema.get("properties", {}).values():
            field.pop("title", None)
        return {
            "type": "function",
            "function": {"name": self.name, "description": self.description, "parameters": schema},
        }


TOOLS: dict[str, Tool] = {
    tool.name: tool
    for tool in (
        Tool(
            name="list_series",
            description=(
                "쓸 수 있는 일별 시계열 목록. 좌표(provider:series_id), 라벨, 종류, 관측 구간, 관측 수를 준다. "
                "**무엇을 볼지 정하기 전에 먼저 부른다.** 표본이 짧은 계열을 여기서 걸러야 한다."
            ),
            input_model=ListSeriesInput,
            handler=list_series,
        ),
        Tool(
            name="get_series",
            description=(
                "여러 시계열의 일별 값을 최근 것부터 준다. "
                f"**한 번에 최대 {MAX_SERIES_PER_CALL}개까지 함께 받는다.** 계열마다 따로 부르면 "
                "조사 예산이 나열로 다 나가고 정작 계산할 여유가 없어진다."
            ),
            input_model=GetSeriesInput,
            handler=get_series,
        ),
        Tool(
            name="series_change",
            description=(
                "여러 시계열의 구간 시작값·끝값·변화폭을 한 번에 준다. "
                "**값을 나열해 놓고 눈으로 비교하지 말고 이걸 부른다.** "
                "금리는 bp(`change_bp`), 가격·환율은 퍼센트(`change_percent`)로 함께 준다."
            ),
            input_model=SeriesChangeInput,
            handler=series_change,
        ),
        Tool(
            name="series_spread",
            description=(
                "여러 쌍의 차이를 날짜별로 주고 그 폭이 얼마나 벌어졌는지도 함께 준다. "
                f"**한 번에 최대 {MAX_SERIES_PER_CALL}쌍까지 함께 받는다.** "
                "곡선 기울기(10년-2년)와 나라 사이 벌어짐(한국-미국)이 이 모양이다. "
                "상관이 아니라 폭을 볼 때 쓴다."
            ),
            input_model=SeriesSpreadInput,
            handler=series_spread,
        ),
        Tool(
            name="compare_series",
            description=(
                "두 시계열의 일별 변화 상관과 **표본 수**를 준다. "
                "금리는 변화폭, 가격·환율은 로그 수익률로 계산한다. "
                "상관계수만 보고 판단하지 말고 관측 수와 구간을 함께 본다."
            ),
            input_model=CompareSeriesInput,
            handler=compare_series,
        ),
        Tool(
            name="search_documents",
            description=(
                "기간과 태그로 수집한 기사·보도자료를 찾는다. 제목·요약·방향·점수만 주고 본문은 주지 않는다. "
                "종목이나 지표에 태그된 문서만 좁힐 수 있다."
            ),
            input_model=SearchDocumentsInput,
            handler=search_documents,
        ),
        Tool(
            name="get_investor_flow",
            description="종목 하나의 일별 투자자 순매수(외국인·기관·개인·연기금·투신)를 수량으로 준다.",
            input_model=GetInvestorFlowInput,
            handler=get_investor_flow,
        ),
    )
}


def tool_specs(names: Sequence[str] | None = None) -> list[dict[str, Any]]:
    """모델에게 보낼 툴 정의. 이름을 주면 그것만 노출한다.

    **분석가마다 보이는 툴이 다르다.** 카테고리별 분석가에게 전부 보여 주면 각자 자기 영역
    밖을 뒤지게 되고, 그러면 카테고리를 나눈 뜻이 없어진다(`modules/analysts.py`).
    """
    selected = list(TOOLS) if names is None else list(names)
    unknown = [name for name in selected if name not in TOOLS]
    if unknown:
        raise ToolError(f"Unknown tools: {', '.join(sorted(unknown))}")
    return [TOOLS[name].spec() for name in selected]


def call_tool(connection: Connection, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """툴 하나를 부른다. 이름과 인자가 맞지 않으면 부르기 전에 거절한다."""
    tool = TOOLS.get(name)
    if tool is None:
        raise ToolError(f"Unknown tool: {name!r}")
    try:
        request = tool.input_model.model_validate(arguments)
    except ValidationError as error:
        raise ToolError(f"{name}: invalid arguments: {error}") from None
    return tool.handler(connection, request)


# 툴 응답 하나가 대화에 실릴 수 있는 최대 글자 수. 넘으면 잘라서 넣는다. 상한이 없으면
# 목록 한 번이 창을 통째로 먹는다.
MAX_TOOL_RESULT_CHARS = 8000


def _tool_result(connection: Connection, call: ToolCall) -> str:
    """툴 하나를 부르고 대화에 실을 문자열을 만든다."""
    try:
        arguments = json.loads(call.arguments or "{}")
    except json.JSONDecodeError as error:
        return json.dumps({"error": f"arguments are not valid JSON: {error}"}, ensure_ascii=False)

    try:
        result = call_tool(connection, call.name, arguments)
    except ToolError as error:
        # 실패로 끝내지 않는다. 모델이 고쳐서 다시 부를 수 있게 오류를 돌려준다.
        logger.warning("tool %s rejected: %s", call.name, error)
        return json.dumps({"error": str(error)}, ensure_ascii=False)

    payload = json.dumps(result, ensure_ascii=False)
    if len(payload) > MAX_TOOL_RESULT_CHARS:
        payload = payload[:MAX_TOOL_RESULT_CHARS] + '..."truncated"'
    return payload


def investigate(
    client: ChatClient,
    connection: Connection,
    model: str,
    messages: Sequence[dict[str, Any]],
    tool_names: Sequence[str],
    max_calls: int,
) -> tuple[list[dict[str, Any]], int]:
    """모델이 툴을 부르게 두고 (늘어난 대화, 호출 수)를 돌려준다.

    모델이 툴을 그만 부르거나 상한에 닿으면 멈춘다. **여기서 답변을 받아 쓰지 않는다.**
    답변은 스키마를 강제한 다음 호출이 만든다(`modules.llm.answer`).
    """
    specs = tool_specs(tool_names)
    conversation = list(messages)
    used = 0

    while used < max_calls:
        reply = client.complete(model=model, messages=conversation, tools=specs)
        if not reply.tool_calls:
            break

        conversation.append(reply.raw or {"role": "assistant", "content": reply.content})
        for call in reply.tool_calls:
            if used >= max_calls:
                break
            used += 1
            conversation.append(
                {
                    "role": "tool",
                    "tool_call_id": call.id,
                    "content": _tool_result(connection, call),
                }
            )

    return conversation, used
