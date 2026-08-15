import json
import re
from decimal import Decimal
from typing import Any, Self

import pytest

from modules.analysts import CATEGORIES, run_analyst
from modules.llm import AssistantMessage, ToolCall
from modules.tools import (
    MAX_SERIES_PER_CALL,
    MAX_TOOL_RESULT_CHARS,
    MIN_MEANINGFUL_OBSERVATIONS,
    TOOLS,
    ToolError,
    call_tool,
    investigate,
    tool_specs,
)


class RecordingCursor:
    """실행한 문장과 파라미터를 기록한다. DB를 붙이지 않고 계약만 본다."""

    def __init__(self, rows: list[tuple]) -> None:
        self.rows = rows
        self.calls: list[tuple[str, tuple]] = []

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *args: object) -> bool:
        return False

    def execute(self, statement: str, parameters: tuple = ()) -> None:
        self.calls.append((statement, parameters))

    def fetchall(self) -> list[tuple]:
        return self.rows


class RecordingConnection:
    def __init__(self, rows: list[tuple] | None = None) -> None:
        self.recorded_cursor = RecordingCursor(rows or [])

    def cursor(self) -> RecordingCursor:
        return self.recorded_cursor


class ForbiddenConnection:
    """호출되면 안 되는 연결. 인자 검증이 DB보다 먼저 걸리는지 본다."""

    def cursor(self) -> Any:
        raise AssertionError("the tool reached the database with invalid arguments")


def placeholder_count(statement: str) -> int:
    # `%%`는 LIKE 와일드카드라 자리표시자가 아니다. 먼저 지우고 센다.
    return statement.replace("%%", "").count("%s")


ARGUMENTS: dict[str, dict[str, Any]] = {
    "list_series": {"kind": "rate", "query": "10년"},
    "get_series": {"series": ["fred:DGS10", "ecos:KTB10Y"], "limit": 5},
    "series_change": {"series": ["fred:DGS10", "ecos:KTB10Y"], "start": "2026-08-05"},
    "series_spread": {"pairs": [{"left": "fred:DGS10", "right": "fred:DGS2"}], "limit": 10},
    "compare_series": {"left": "yahoo:USDKRW", "right": "kis:005930", "window_days": 60},
    "search_documents": {"ticker": "005930", "limit": 5},
    "get_investor_flow": {"stock_code": "005930", "limit": 5},
}


def test_every_tool_has_arguments_in_this_test():
    # 툴을 늘리면 여기도 늘어야 한다. 안 그러면 새 툴이 검증 없이 지나간다.
    assert set(ARGUMENTS) == set(TOOLS)


def test_tool_specs_are_openai_function_definitions():
    for spec in tool_specs():
        assert spec["type"] == "function"
        function = spec["function"]
        assert function["name"] in TOOLS
        # 설명이 스키마의 일부다. 모델이 읽는 유일한 사용 설명서다.
        assert len(function["description"]) > 20
        assert function["parameters"]["type"] == "object"


def test_tool_specs_forbid_unknown_arguments():
    # 모델이 없는 인자를 지어내면 호출 전에 걸려야 한다.
    for spec in tool_specs():
        assert spec["function"]["parameters"]["additionalProperties"] is False


def test_tool_specs_can_be_narrowed_to_one_category():
    names = ("search_documents",)

    specs = tool_specs(names)

    assert [spec["function"]["name"] for spec in specs] == list(names)


def test_tool_specs_reject_an_unknown_name():
    with pytest.raises(ToolError, match="Unknown tools"):
        tool_specs(("list_series", "drop_table"))


def test_call_tool_rejects_an_unknown_name():
    with pytest.raises(ToolError, match="Unknown tool"):
        call_tool(ForbiddenConnection(), "run_sql", {"sql": "select 1"})


def test_call_tool_rejects_arguments_that_do_not_match_the_schema():
    # 인자 검증이 DB보다 먼저다. 뚫리면 ForbiddenConnection이 터진다.
    with pytest.raises(ToolError, match="invalid arguments"):
        call_tool(ForbiddenConnection(), "get_series", {"series": ["fred:DGS10"], "limit": 100000})

    with pytest.raises(ToolError, match="invalid arguments"):
        call_tool(ForbiddenConnection(), "get_series", {"series": ["fred:DGS10"], "sql": "drop table"})


def test_call_tool_rejects_a_series_coordinate_without_a_provider():
    with pytest.raises(ToolError, match="provider:series_id"):
        call_tool(ForbiddenConnection(), "get_series", {"series": ["DGS10"]})


@pytest.mark.parametrize("name", sorted(ARGUMENTS))
def test_each_statement_gets_exactly_as_many_parameters_as_it_has_placeholders(name):
    """자리표시자와 파라미터 수가 어긋나면 런타임에야 터진다.

    실제로 `compare.sql` 주석에 있던 퍼센트 기호 하나가 자리표시자로 잡혀
    `IndexError: tuple index out of range`가 났다. 주석이 파서를 건드리면 안 된다.
    """
    # 행은 비워 둔다. 여기서 보는 것은 결과가 아니라 문장과 파라미터의 개수다.
    connection = RecordingConnection()

    call_tool(connection, name, ARGUMENTS[name])

    assert connection.recorded_cursor.calls, name
    for statement, parameters in connection.recorded_cursor.calls:
        assert placeholder_count(statement) == len(parameters), name


def test_statements_do_not_contain_a_bare_percent_sign():
    from modules import tools

    statements = {
        "list": tools.LIST_SERIES,
        "get": tools.GET_SERIES,
        "change": tools.SERIES_CHANGE,
        "spread": tools.SERIES_SPREAD,
        "compare": tools.COMPARE_SERIES,
        "search": tools.SEARCH_DOCUMENTS,
        "flow": tools.INVESTOR_FLOW,
    }
    for name, statement in statements.items():
        # `%s`와 `%%` 밖의 퍼센트 기호는 psycopg2가 자리표시자로 읽는다.
        assert re.search(r"%(?![s%])", statement.replace("%%", "")) is None, name


def test_compare_warns_when_the_sample_is_too_short():
    connection = RecordingConnection(rows=[(32, "2026-06-30", "2026-08-14", 0.43)])

    result = call_tool(connection, "compare_series", ARGUMENTS["compare_series"])

    # 숫자를 감추지 않는다. 표본이 짧다는 사실이 결론의 일부다.
    assert result["correlation"] == 0.43
    assert str(MIN_MEANINGFUL_OBSERVATIONS) in result["warning"]


def test_compare_does_not_warn_when_the_sample_is_long_enough():
    connection = RecordingConnection(rows=[(250, "2025-08-18", "2026-08-14", 0.06)])

    result = call_tool(connection, "compare_series", ARGUMENTS["compare_series"])

    assert "warning" not in result


def test_results_are_json_safe():
    connection = RecordingConnection(rows=[("fred", "DGS10", "2026-08-14", Decimal("4.63"))])

    result = call_tool(connection, "get_series", {"series": ["fred:DGS10"]})

    # Decimal과 date는 그대로 JSON에 실리지 않는다. 툴 응답은 프롬프트에 문자열로 들어간다.
    assert result["series"] == [
        {"series": "fred:DGS10", "count": 1, "values": [{"business_date": "2026-08-14", "value": 4.63}]}
    ]


def test_get_series_takes_many_series_in_one_call():
    """계열마다 따로 부르면 조사 예산이 나열로 다 나간다.

    실측(grok-4)에서 금리 분석가가 6개국을 한 계열씩 받다 호출 상한 12회를 소진했고,
    `compare_series`는 한 번도 부르지 못했다.
    """
    rows = [
        ("fred", "DGS10", "2026-08-13", Decimal("4.63")),
        ("ecos", "KTB10Y", "2026-08-14", Decimal("4.313")),
    ]
    connection = RecordingConnection(rows=rows)

    result = call_tool(connection, "get_series", {"series": ["fred:DGS10", "ecos:KTB10Y"]})

    assert [entry["series"] for entry in result["series"]] == ["fred:DGS10", "ecos:KTB10Y"]
    assert len(connection.recorded_cursor.calls) == 1


def test_get_series_rejects_more_series_than_one_call_may_carry():
    too_many = [f"fred:S{index}" for index in range(MAX_SERIES_PER_CALL + 1)]

    with pytest.raises(ToolError, match="invalid arguments"):
        call_tool(ForbiddenConnection(), "get_series", {"series": too_many})


def test_a_series_with_no_rows_still_appears_in_the_answer():
    # 빈 계열이 응답에서 사라지면 모델이 물어본 것과 받은 것을 짝지을 수 없다.
    connection = RecordingConnection(rows=[])

    result = call_tool(connection, "get_series", {"series": ["fred:DGS10", "ecos:KTB10Y"]})

    assert [entry["count"] for entry in result["series"]] == [0, 0]


def test_rate_changes_are_reported_in_basis_points():
    """4.0에서 4.1은 2.5퍼센트 상승이 아니라 10bp 상승이다.

    비율로 주면 그 오독이 리포트에 그대로 실린다.
    """
    connection = RecordingConnection(
        rows=[("fred", "DGS10", "rate", "2026-08-05", "2026-08-13", 7, Decimal("4.63"), Decimal("4.72"))]
    )

    (entry,) = call_tool(connection, "series_change", ARGUMENTS["series_change"])["changes"]

    assert entry["change_bp"] == 9.0
    assert "change_percent" not in entry


def test_price_changes_are_reported_in_percent():
    connection = RecordingConnection(
        rows=[("yahoo", "SOX", "price", "2026-08-05", "2026-08-13", 7, Decimal(100), Decimal(110))]
    )

    (entry,) = call_tool(connection, "series_change", ARGUMENTS["series_change"])["changes"]

    assert entry["change_percent"] == 10.0
    assert "change_bp" not in entry


def test_spread_reports_how_much_the_gap_moved():
    # 폭이 벌어졌는지 좁아졌는지가 곡선·나라 비교의 알맹이다. 모델이 빼게 두지 않는다.
    rows = [
        ("fred", "DGS10", "fred", "DGS2", "2026-08-13", Decimal("4.63"), Decimal("4.15"), Decimal("0.48")),
        ("fred", "DGS10", "fred", "DGS2", "2026-08-05", Decimal("4.63"), Decimal("4.18"), Decimal("0.45")),
    ]
    connection = RecordingConnection(rows=rows)

    (entry,) = call_tool(connection, "series_spread", ARGUMENTS["series_spread"])["spreads"]

    assert entry["latest_spread"] == 0.48
    assert entry["spread_change"] == 0.03


def test_spread_takes_many_pairs_in_one_call():
    """쌍마다 따로 부르면 조사 예산이 또 나열로 나간다.

    실측(grok-4)에서 곡선·나라 스프레드를 하나씩 여덟 번 불러 호출 상한을 다시 소진했다.
    """
    rows = [
        ("ecos", "KTB10Y", "fred", "DGS10", "2026-08-13", Decimal("4.30"), Decimal("4.63"), Decimal("-0.33")),
        ("fred", "DGS10", "fred", "DGS2", "2026-08-13", Decimal("4.63"), Decimal("4.15"), Decimal("0.48")),
    ]
    connection = RecordingConnection(rows=rows)
    pairs = [
        {"left": "fred:DGS10", "right": "fred:DGS2"},
        {"left": "ecos:KTB10Y", "right": "fred:DGS10"},
    ]

    result = call_tool(connection, "series_spread", {"pairs": pairs})

    assert result["count"] == 2
    assert len(connection.recorded_cursor.calls) == 1


def test_a_spread_with_no_overlapping_days_says_so_instead_of_guessing():
    connection = RecordingConnection(rows=[])

    (entry,) = call_tool(connection, "series_spread", ARGUMENTS["series_spread"])["spreads"]

    assert entry["count"] == 0
    assert "spread_change" not in entry


def test_spread_rejects_more_pairs_than_one_call_may_carry():
    too_many = [{"left": f"fred:S{index}", "right": "fred:DGS2"} for index in range(MAX_SERIES_PER_CALL + 1)]

    with pytest.raises(ToolError, match="invalid arguments"):
        call_tool(ForbiddenConnection(), "series_spread", {"pairs": too_many})


# ---------------------------------------------------------------------------
# 툴 루프. `modules/tools.py`의 `investigate`가 모델과 툴 사이를 오간다.
# ---------------------------------------------------------------------------


def test_investigate_runs_the_tool_and_feeds_the_result_back():
    client = ScriptedClient(tool_call("list_series", {"kind": "rate"}), AssistantMessage(content="done"))

    conversation, used = investigate(
        client, FakeSeriesConnection(), "m", [{"role": "user", "content": "go"}], ("list_series",), 5
    )

    assert used == 1
    tool_message = conversation[-1]
    assert tool_message["role"] == "tool"
    assert tool_message["tool_call_id"] == "call_1"
    assert "미국 10년물" in tool_message["content"]


def test_investigate_only_offers_the_tools_of_this_category():
    client = ScriptedClient(AssistantMessage(content="done"))

    investigate(client, FakeSeriesConnection(), "m", [], CATEGORIES["news"].tools, 5)

    offered = [spec["function"]["name"] for spec in client.requests[0]["tools"]]
    assert offered == ["search_documents"]


def test_investigate_stops_at_the_call_budget():
    # 상한이 없으면 모델이 목록을 끝없이 훑는다.
    client = ScriptedClient(*[tool_call("list_series", {}) for _ in range(10)])

    _, used = investigate(client, FakeSeriesConnection(), "m", [], ("list_series",), 3)

    assert used == 3


def test_a_rejected_tool_call_comes_back_as_an_error_not_a_crash():
    """모델이 고쳐서 다시 부를 수 있어야 한다. 우리 데이터는 아무것도 만들어지지 않는다."""
    client = ScriptedClient(tool_call("drop_everything", {}), AssistantMessage(content="done"))

    conversation, _ = investigate(client, FakeSeriesConnection(), "m", [], ("list_series",), 5)

    assert "Unknown tool" in json.loads(conversation[-1]["content"])["error"]


def test_bad_tool_arguments_come_back_as_an_error():
    client = ScriptedClient(tool_call("get_series", {"series": ["DGS10"]}), AssistantMessage(content="done"))

    conversation, _ = investigate(client, FakeSeriesConnection(), "m", [], ("get_series",), 5)

    assert "provider:series_id" in json.loads(conversation[-1]["content"])["error"]


def test_a_huge_tool_result_is_truncated():
    # 목록 한 번이 컨텍스트 창을 통째로 먹으면 안 된다.
    rows = [("fred", f"S{index}", "x" * 200, "rate", "2005-01-03", "2026-08-13", 1) for index in range(200)]
    client = ScriptedClient(tool_call("list_series", {}), AssistantMessage(content="done"))

    conversation, _ = investigate(client, FakeSeriesConnection(rows), "m", [], ("list_series",), 5)

    content = conversation[-1]["content"]
    assert len(content) <= MAX_TOOL_RESULT_CHARS + 20
    assert content.endswith('"truncated"')


def test_run_analyst_investigates_then_answers_with_the_schema():
    client = ScriptedClient(
        tool_call("list_series", {"kind": "rate"}),
        AssistantMessage(content="조사 끝"),
        AssistantMessage(content=REPORT),
    )

    report, used = run_analyst(client, FakeSeriesConnection(), "m", CATEGORIES["rates"], "최근 7거래일")

    assert used == 1
    assert report.observations[0].numbers[0].value == 4.63
    # 조사 turn에는 툴만, 답변 turn에는 스키마만.
    assert client.requests[0]["tools"] is not None and client.requests[0]["response_format"] is None
    assert client.requests[-1]["tools"] is None and client.requests[-1]["response_format"] is not None


class ScriptedClient:
    """정해진 순서대로 답한다. 네트워크를 쓰지 않는다."""

    def __init__(self, *replies: AssistantMessage) -> None:
        self.replies = list(replies)
        self.requests: list[dict[str, Any]] = []

    def complete(self, *, model, messages, tools=None, response_format=None) -> AssistantMessage:
        self.requests.append({"messages": list(messages), "tools": tools, "response_format": response_format})
        return self.replies.pop(0)


def tool_call(name: str, arguments: dict[str, Any], call_id: str = "call_1") -> AssistantMessage:
    return AssistantMessage(
        tool_calls=(ToolCall(id=call_id, name=name, arguments=json.dumps(arguments)),),
        raw={"role": "assistant", "tool_calls": [{"id": call_id}]},
    )


REPORT = json.dumps(
    {
        "observations": [
            {
                "statement": "미국 10년물이 4.63이다.",
                "series": ["fred:DGS10"],
                "numbers": [{"name": "last", "value": 4.63}],
            }
        ],
        "summary": "장기금리가 소폭 내렸다.",
    },
    ensure_ascii=False,
)


class FakeSeriesConnection:
    """`list_series` 한 줄을 돌려주는 연결. 루프가 결과를 대화에 싣는지 본다."""

    def __init__(self, rows: list[tuple] | None = None) -> None:
        self.rows = rows or [("fred", "DGS10", "미국 10년물", "rate", "2005-01-03", "2026-08-13", 5408)]

    def cursor(self) -> RecordingCursor:
        return RecordingCursor(self.rows)
