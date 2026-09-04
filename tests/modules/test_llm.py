import json
from typing import Any

import httpx
import openai
import pytest
from langchain_core.messages import AIMessage
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from modules.assessment import DEFAULT_PERSPECTIVE, Assessment, system_prompt
from modules.briefing import picks
from modules.expectation.extraction import SYSTEM_PROMPT as EXTRACTION_PROMPT
from modules.llm import (
    NUMBER_STYLE,
    REQUEST_TIMEOUT_SECONDS,
    LlmError,
    RetryableLlmError,
    UnsupportedResponseFormat,
    briefing_model,
    classify,
    document_model,
    invoke,
    model_name,
    token_usage,
)
from modules.schema import response_format, strict_json_schema


class ScriptedModel:
    """LangChain 모델 자리에 끼운다. 네트워크를 쓰지 않는다.

    `bind`와 `bind_tools`가 자신을 돌려주고 무엇을 받았는지 기록한다.
    """

    def __init__(self, *replies: AIMessage | Exception) -> None:
        self.replies = list(replies)
        self.bound: dict[str, Any] = {}
        self.tools: Any = None
        self.calls: list[list[Any]] = []

    def bind(self, **kwargs: Any) -> "ScriptedModel":
        self.bound.update(kwargs)
        return self

    def bind_tools(self, tools: Any) -> "ScriptedModel":
        self.tools = tools
        return self

    def invoke(self, messages: list[Any]) -> AIMessage:
        self.calls.append(list(messages))
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


def status_error(kind: type[openai.APIStatusError], code: int, body: str) -> openai.APIStatusError:
    """제공처가 본문으로 실패를 알리는 상황을 만든다."""
    request = httpx.Request("POST", "https://api.x.ai/v1/chat/completions")
    response = httpx.Response(code, text=body, request=request)
    return kind("boom", response=response, body=None)


def test_the_document_model_is_defined_in_code_not_in_settings(monkeypatch):
    """제공처를 환경변수로 갈아 끼우지 않는다. 모델을 바꾸는 곳은 이 함수 하나다."""
    monkeypatch.setenv("OPENAI_API_KEY", "secret-key")

    model = document_model()

    assert model_name(model) == "gpt-5.6-luna"
    # 재시도는 Airflow가 한다. SDK가 먼저 재시도하면 태스크 타임아웃 안에서 몇 번 불렀는지
    # 로그와 트레이스가 어긋난다.
    assert model.max_retries == 0
    assert model.request_timeout == REQUEST_TIMEOUT_SECONDS


def test_the_briefing_model_is_its_own_function(monkeypatch):
    """지금은 문서 태깅과 같은 모델이지만 함수를 나눠 둔다. 브리핑 요약과 태깅은 요구가 달라
    한쪽만 바꾸고 싶어질 때 그 함수만 고치면 된다."""
    monkeypatch.setenv("XAI_API_KEY", "secret-key")

    model = briefing_model("test-conv")

    assert model_name(model) == "grok-4.6"
    assert model.max_retries == 0
    assert model.request_timeout == REQUEST_TIMEOUT_SECONDS
    # 캐시는 서버마다다. 이 헤더가 없으면 같은 대화의 다음 요청이 다른 서버로 가 접두를
    # 못 만난다(`modules/llm.py`의 "대화 하나는 서버 하나로 보낸다").
    assert model.default_headers == {"x-grok-conv-id": "test-conv"}


def test_invoke_binds_the_schema_and_no_tools():
    scripted = ScriptedModel(AIMessage("{}"))
    schema = response_format(Assessment, "assessment")

    invoke(scripted, [], schema=schema)

    assert scripted.bound["response_format"] == schema
    # 툴과 스키마를 같은 요청에 넣지 않는다. 제공처마다 동작이 갈린다.
    assert scripted.tools is None


def test_invoke_refuses_tools_and_a_schema_in_one_request():
    scripted = ScriptedModel(AIMessage("{}"))

    # "조사 → 답변" 두 단계가 원칙이고 이 검사가 그것을 코드 계약으로 만든다.
    with pytest.raises(ValueError, match="never both"):
        invoke(scripted, [], schema=response_format(Assessment, "assessment"), tools=[{"type": "function"}])

    # 거절은 제공처에 닿기 전이다. 요청이 나가지 않는다.
    assert scripted.calls == []


def test_a_rejected_schema_becomes_its_own_error():
    scripted = ScriptedModel(status_error(openai.BadRequestError, 400, '{"error":"response_format is not supported"}'))

    with pytest.raises(UnsupportedResponseFormat):
        invoke(scripted, [], schema={"type": "json_schema"})


def test_other_http_errors_stay_separate():
    scripted = ScriptedModel(status_error(openai.AuthenticationError, 401, '{"error":"bad key"}'))

    # 스키마 문제가 아닌 실패를 폴백으로 삼키면 원인을 못 찾는다.
    with pytest.raises(LlmError) as failure:
        invoke(scripted, [], schema={"type": "json_schema"})
    assert not isinstance(failure.value, UnsupportedResponseFormat)
    assert not isinstance(failure.value, RetryableLlmError)


def test_the_schema_is_not_blamed_when_none_was_sent():
    """스키마를 안 걸었는데 본문에 그 단어가 있다고 폴백으로 보내면 무한히 같은 요청을 한다."""
    scripted = ScriptedModel(status_error(openai.BadRequestError, 400, '{"error":"response_format is not supported"}'))

    with pytest.raises(LlmError) as failure:
        invoke(scripted, [])
    assert not isinstance(failure.value, UnsupportedResponseFormat)


def test_network_failure_is_reported_as_retryable():
    request = httpx.Request("POST", "https://api.x.ai/v1/chat/completions")

    assert isinstance(classify(openai.APIConnectionError(request=request)), RetryableLlmError)


def test_rate_limiting_is_reported_as_retryable():
    """429를 재시도 못 할 오류로 올리면 동시 호출 수를 올린 순간부터 배치 전체가 즉시 죽는다."""
    error = status_error(openai.RateLimitError, 429, '{"error":"rate limit exceeded"}')

    assert isinstance(classify(error), RetryableLlmError)


@pytest.mark.parametrize("status", [500, 502, 503, 504, 520, 522, 524])
def test_origin_server_errors_are_reported_as_retryable(status):
    error = status_error(openai.APIStatusError, status, '{"retryable":true}')

    assert isinstance(classify(error), RetryableLlmError)


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


# --- 숫자 표기 규칙 -------------------------------------------------------------


@pytest.mark.parametrize(
    "prompt",
    [picks.SYSTEM_PROMPT, system_prompt(DEFAULT_PERSPECTIVE)],
)
def test_every_prose_prompt_carries_the_number_style(prompt):
    """산문을 내는 프롬프트가 같은 한 벌을 쓴다. 여러 곳에 따로 적으면 반드시 어긋난다."""
    assert NUMBER_STYLE in prompt


def test_the_extraction_prompt_stays_out_of_it():
    """추출 프롬프트의 숫자는 산문이 아니라 JSON 숫자 칸으로 간다. 쉼표가 파싱을 깬다."""
    assert NUMBER_STYLE not in EXTRACTION_PROMPT


# --- 토큰 계량 -----------------------------------------------------------------


def _handler(usage: dict[str, Any]) -> Any:
    from langchain_core.callbacks import UsageMetadataCallbackHandler

    handler = UsageMetadataCallbackHandler()
    handler.usage_metadata = usage
    return handler


def test_token_usage_folds_every_model_the_conversation_touched():
    """핸들러는 모델 이름마다 칸을 갖는다. 원장은 대화 하나의 합을 받는다."""
    usage = token_usage(
        _handler(
            {
                "grok-4.6": {
                    "input_tokens": 224970,
                    "output_tokens": 17593,
                    "output_token_details": {"reasoning": 13975},
                },
                "grok-4.6-mini": {
                    "input_tokens": 30,
                    "output_tokens": 7,
                    "output_token_details": {"reasoning": 2},
                },
            }
        )
    )

    assert usage.prompt == 225000
    assert usage.completion == 17600
    assert usage.reasoning == 13977


def test_token_usage_counts_the_cache_hits_inside_the_prompt():
    """캐시 몫이 없으면 최적화 효과를 못 잰다. 왕복을 줄여도 그 몫이 캐시였으면 청구는 그대로다.

    `cached`는 `prompt`에 **포함된다.** 더하면 입력을 두 번 센다.
    """
    usage = token_usage(
        _handler(
            {
                "grok-4.6": {
                    "input_tokens": 246395,
                    "input_token_details": {"cache_read": 52992},
                    "output_tokens": 17593,
                    "output_token_details": {"reasoning": 13975},
                }
            }
        )
    )

    assert usage.prompt == 246395
    assert usage.cached == 52992


def test_token_usage_survives_a_provider_that_hides_the_cache_split():
    """`input_token_details`가 없는 제공처는 캐시 몫이 0이다. 입력 합은 그대로 온다."""
    usage = token_usage(_handler({"gpt-5.6-luna": {"input_tokens": 10, "output_tokens": 3}}))

    assert (usage.prompt, usage.cached) == (10, 0)


def test_token_usage_is_zero_when_the_model_never_ran():
    """모델을 못 부르고 죽은 대화는 0이다. NULL("안 쟀다")과 갈려야 한다."""
    usage = token_usage(_handler({}))

    assert (usage.prompt, usage.cached, usage.completion, usage.reasoning) == (0, 0, 0, 0)


def test_token_usage_survives_a_provider_that_hides_reasoning():
    """`output_token_details`가 없거나 비어도 죽지 않는다. 그 제공처는 reasoning이 0이다."""
    usage = token_usage(_handler({"gpt-5.6-luna": {"input_tokens": 10, "output_tokens": 3}}))

    assert (usage.prompt, usage.completion, usage.reasoning) == (10, 3, 0)


def test_token_usage_is_frozen():
    """재시도 경로에서 값이 바뀌면 원장과 트레이스가 어긋난다."""
    usage = token_usage(_handler({}))

    with pytest.raises(ValidationError):
        usage.prompt = 1
