import io
import json
import pathlib
import urllib.error
from typing import Any, Self

import pytest
from pydantic import BaseModel, ConfigDict, Field

from modules.assessment import Assessment
from modules.llm import (
    AssistantMessage,
    LlmError,
    ToolCall,
    UnsupportedResponseFormat,
    answer,
    chat_client,
)
from modules.schema import response_format, strict_json_schema

REPORT = json.dumps(
    {
        "instruments": [],
        "indicators": [],
        "topics": [],
        "direction": "neutral",
        "scores": {"relevance": 0, "novelty": 0, "specificity": 0, "impact": 0},
        "new_facts": [],
        "reason": "",
    },
    ensure_ascii=False,
)


class FakeCursor:
    def __init__(self, rows: list[tuple]) -> None:
        self.rows = rows

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *args: object) -> bool:
        return False

    def execute(self, statement: str, parameters: tuple = ()) -> None:
        pass

    def fetchall(self) -> list[tuple]:
        return self.rows


class FakeConnection:
    def __init__(self, rows: list[tuple] | None = None) -> None:
        self.rows = rows or [("fred", "DGS10", "미국 10년물", "rate", "2005-01-03", "2026-08-13", 5408)]

    def cursor(self) -> FakeCursor:
        return FakeCursor(self.rows)


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


def test_answer_sends_the_schema_and_no_tools():
    client = ScriptedClient(AssistantMessage(content=REPORT))
    schema = response_format(Assessment, "assessment")

    answer(client, "m", [], schema, "정리하라")

    request = client.requests[0]
    assert request["response_format"] == schema
    # 툴과 스키마를 같은 요청에 넣지 않는다. 제공처마다 동작이 갈린다.
    assert request["tools"] is None


def test_answer_falls_back_when_the_provider_rejects_the_schema():
    class Picky(ScriptedClient):
        def complete(self, *, model, messages, tools=None, response_format=None):
            if response_format is not None:
                raise UnsupportedResponseFormat("json_schema is not supported")
            return super().complete(model=model, messages=messages, tools=tools)

    client = Picky(AssistantMessage(content=REPORT))

    content = answer(client, "m", [], response_format(Assessment, "assessment"), "정리하라")

    # 강제가 안 되면 프롬프트와 검증이 형식을 지킨다.
    assert content == REPORT
    assert client.requests[-1]["response_format"] is None


class Nested(BaseModel):
    model_config = ConfigDict(frozen=True)

    kept: str = Field(default="x", description="기본값이 있어도 required에 들어가야 한다")


class Outer(BaseModel):
    model_config = ConfigDict(frozen=True)

    nested: Nested
    items: tuple[Nested, ...] = ()


def test_strict_schema_marks_every_property_required():
    """strict 모드는 부분 객체를 허용하지 않는다. 기본값이 있어도 키가 와야 한다."""
    schema = strict_json_schema(Outer)

    assert schema["required"] == ["nested", "items"]
    assert schema["$defs"]["Nested"]["required"] == ["kept"]


def test_strict_schema_closes_every_object():
    schema = strict_json_schema(Outer)

    assert schema["additionalProperties"] is False
    assert schema["$defs"]["Nested"]["additionalProperties"] is False


def test_strict_schema_drops_keywords_the_provider_rejects():
    schema = json.dumps(strict_json_schema(Outer))

    for keyword in ('"default"', '"title"'):
        assert keyword not in schema


@pytest.mark.parametrize("model", [Assessment, Outer])
def test_response_format_is_shaped_for_the_api(model):
    formatted = response_format(model, "thing")

    assert formatted["type"] == "json_schema"
    assert formatted["json_schema"]["name"] == "thing"
    assert formatted["json_schema"]["strict"] is True


class FakeHTTPResponse:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *args: object) -> bool:
        return False

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


def test_chat_client_needs_no_sdk():
    """SDK를 쓰지 않는다. 주피터가 도는 가상환경에도, 배포 이미지에도 의존성이 늘지 않는다."""
    import modules.llm as module

    source = pathlib.Path(module.__file__).read_text(encoding="utf-8")
    assert "import openai" not in source
    assert "from openai" not in source


def test_chat_client_parses_tool_calls(monkeypatch):
    captured: dict[str, Any] = {}

    def fake_urlopen(request, timeout=None):
        captured["url"] = request.full_url
        captured["auth"] = request.get_header("Authorization")
        captured["body"] = json.loads(request.data.decode("utf-8"))
        return FakeHTTPResponse(
            {
                "choices": [
                    {
                        "message": {
                            "content": None,
                            "tool_calls": [
                                {"id": "c1", "function": {"name": "list_series", "arguments": '{"kind":"rate"}'}}
                            ],
                        }
                    }
                ]
            }
        )

    monkeypatch.setattr("modules.llm.urllib.request.urlopen", fake_urlopen)
    client = chat_client("https://api.x.ai/v1/", "secret-key")

    reply = client.complete(model="grok-4", messages=[{"role": "user", "content": "go"}], tools=[{"type": "function"}])

    assert captured["url"] == "https://api.x.ai/v1/chat/completions"
    # 키는 헤더에 실린다. URL에 비밀이 없어야 오류 메시지에 URL을 남길 수 있다.
    assert captured["auth"] == "Bearer secret-key"
    assert "secret-key" not in captured["url"]
    assert reply.tool_calls[0].name == "list_series"
    assert reply.content == ""


def test_chat_client_maps_a_rejected_schema_to_its_own_error(monkeypatch):
    def fake_urlopen(request, timeout=None):
        raise urllib.error.HTTPError(
            request.full_url, 400, "Bad Request", {}, io.BytesIO(b'{"error":"response_format is not supported"}')
        )

    monkeypatch.setattr("modules.llm.urllib.request.urlopen", fake_urlopen)
    client = chat_client("https://api.x.ai/v1", "k")

    with pytest.raises(UnsupportedResponseFormat):
        client.complete(model="m", messages=[], response_format={"type": "json_schema"})


def test_chat_client_keeps_other_http_errors_separate(monkeypatch):
    def fake_urlopen(request, timeout=None):
        raise urllib.error.HTTPError(request.full_url, 401, "Unauthorized", {}, io.BytesIO(b'{"error":"bad key"}'))

    monkeypatch.setattr("modules.llm.urllib.request.urlopen", fake_urlopen)
    client = chat_client("https://api.x.ai/v1", "k")

    # 스키마 문제가 아닌 실패를 폴백으로 삼키면 원인을 못 찾는다.
    with pytest.raises(LlmError) as failure:
        client.complete(model="m", messages=[], response_format={"type": "json_schema"})
    assert not isinstance(failure.value, UnsupportedResponseFormat)


def test_chat_client_reports_network_failure_as_retryable(monkeypatch):
    def fake_urlopen(request, timeout=None):
        raise urllib.error.URLError("dns is down")

    monkeypatch.setattr("modules.llm.urllib.request.urlopen", fake_urlopen)
    client = chat_client("https://api.x.ai/v1", "k")

    with pytest.raises(ConnectionError):
        client.complete(model="m", messages=[])
