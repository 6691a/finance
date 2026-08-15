"""모델 호출과 툴 루프.

`modules/tools.py`가 무엇을 부를 수 있는지 정하고, 여기가 실제로 부른다. 프롬프트는
`modules/assessment.py`와 `modules/analysts.py`가 갖는다.

## 조사와 답변을 나눈다

한 번의 요청에 툴과 `response_format`을 함께 넣지 않는다. 제공처마다 둘을 같이 줬을 때
동작이 다르고(스키마를 주면 툴 호출을 안 하거나, 그 반대), 그 차이를 우리가 흡수할 방법이
없다. 그래서 두 단계로 나눈다.

1. **조사**: 툴만 주고 모델이 필요한 만큼 부르게 한다(`modules/tools.py`의 `investigate`).
2. **답변**: 툴을 빼고 `response_format`으로 스키마를 강제해 한 번 더 부른다.

이 구조라 마지막 응답은 항상 우리가 정한 모양이고, 툴 결과는 그 전 대화에 이미 들어가 있다.

## 스키마를 못 받는 제공처

제3자 OpenAI 호환 제공자가 `json_schema`를 모르면 요청이 거절된다. 어댑터가 그걸
`UnsupportedResponseFormat`으로 올리고, 여기서 스키마 없이 한 번 더 부른다. 프롬프트에
출력 형식을 그대로 적어 둔 이유가 이것이다. **강제가 되면 좋고, 안 되면 검증이 받는다.**

## 툴을 모른다

툴 루프는 `modules/tools.py`가 갖는다. 여기는 "메시지를 보내고 답을 받는다"까지만 안다.
그래야 문서 태깅(`modules/assessment.py`)이 리포트용 툴 계층에 묶이지 않는다. 태깅은 툴을
쓰지 않는다.
"""

import json
import logging
import urllib.error
import urllib.request
from collections.abc import Sequence
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict

logger = logging.getLogger(__name__)


class LlmError(RuntimeError):
    """모델 호출이 실패했다."""


class UnsupportedResponseFormat(LlmError):
    """제공처가 `response_format` 스키마를 받지 않는다. 스키마 없이 다시 부른다."""


class ToolCall(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    name: str
    arguments: str


class AssistantMessage(BaseModel):
    """모델이 돌려준 한 턴.

    `raw`는 제공처가 보낸 메시지 그대로다. 대화에 다시 실을 때는 우리가 재구성한 것이 아니라
    이걸 그대로 넣는다. 툴 호출 메시지의 모양이 제공처마다 조금씩 다르기 때문이다.
    """

    model_config = ConfigDict(frozen=True)

    content: str = ""
    tool_calls: tuple[ToolCall, ...] = ()
    raw: dict[str, Any] = {}


class ChatClient(Protocol):
    """우리가 쓰는 부분만. 테스트가 가짜를 끼울 수 있게 좁혀 둔다."""

    def complete(
        self,
        *,
        model: str,
        messages: Sequence[dict[str, Any]],
        tools: Sequence[dict[str, Any]] | None = None,
        response_format: dict[str, Any] | None = None,
    ) -> AssistantMessage: ...


def answer(
    client: ChatClient,
    model: str,
    messages: Sequence[dict[str, Any]],
    schema: dict[str, Any] | None,
    instruction: str | None = None,
) -> str:
    """스키마를 강제해 마지막 답변을 받는다.

    제공처가 스키마를 받지 않으면 한 번 더, 이번에는 스키마 없이 부른다. 그 경우 형식은
    프롬프트와 사후 검증이 지킨다.
    """
    conversation = list(messages)
    if instruction is not None:
        conversation.append({"role": "user", "content": instruction})
    if schema is not None:
        try:
            return client.complete(model=model, messages=conversation, response_format=schema).content
        except UnsupportedResponseFormat as error:
            logger.warning("provider does not accept a response schema; falling back to validation: %s", error)
    return client.complete(model=model, messages=conversation).content


# 제공처가 스키마를 못 받을 때 400 본문에 나오는 조각. 오류 문자열로 가르는 것이 마뜩잖지만
# OpenAI 호환 API에 "이 기능을 지원하는가"를 묻는 표준 방법이 없다.
UNSUPPORTED_MARKERS = ("response_format", "json_schema", "structured output")


def chat_client(base_url: str, api_key: str, timeout: float = 120.0) -> ChatClient:
    """OpenAI 호환 chat completions 어댑터.

    **SDK를 쓰지 않는다.** 우리가 하는 일은 JSON을 POST하고 JSON을 받는 것 하나뿐이고,
    SDK가 주는 재시도·스트리밍·타입 모델은 하나도 쓰지 않는다. 재시도는 Airflow가 한다.
    그래서 `urllib.request`로 충분하고, 그러면 배포 이미지와 백엔드 가상환경 어느 쪽에도
    의존성이 늘지 않는다. `fred.py`·`ecos.py`가 쓰는 것과 같은 도구다.

    "OpenAI 호환"은 회사가 아니라 **요청·응답 모양**을 가리킨다. xAI(Grok)를 포함해 여러
    제공처가 같은 모양을 받으므로 `base_url`만 바꾸면 된다.

    키는 헤더에 실린다. URL에는 비밀이 없어 오류 메시지에 상태와 본문을 그대로 남겨도 된다.

    `modules/assessment.py`와 `modules/analysts.py`는 `ChatClient` 프로토콜만 안다. HTTP를
    아는 코드는 이 함수 하나다.
    """

    class _Adapter:
        def complete(self, *, model, messages, tools=None, response_format=None) -> AssistantMessage:
            payload: dict[str, Any] = {"model": model, "messages": list(messages)}
            if tools:
                payload["tools"] = list(tools)
            if response_format:
                payload["response_format"] = response_format

            request = urllib.request.Request(
                f"{base_url.rstrip('/')}/chat/completions",
                data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            )
            try:
                with urllib.request.urlopen(request, timeout=timeout) as response:
                    body = json.load(response)
            except urllib.error.HTTPError as error:
                detail = error.read().decode("utf-8", "replace")[:500]
                if response_format and any(marker in detail.lower() for marker in UNSUPPORTED_MARKERS):
                    raise UnsupportedResponseFormat(f"HTTP {error.code}: {detail}") from error
                raise LlmError(f"HTTP {error.code}: {detail}") from error
            except (urllib.error.URLError, TimeoutError) as error:
                # 네트워크 실패는 재시도할 값어치가 있다. DAG이 판단한다.
                raise ConnectionError(f"chat request failed: {error}") from error

            message = body["choices"][0]["message"]
            calls = tuple(
                ToolCall(
                    id=call["id"],
                    name=call["function"]["name"],
                    arguments=call["function"].get("arguments") or "{}",
                )
                for call in (message.get("tool_calls") or ())
            )
            return AssistantMessage(content=message.get("content") or "", tool_calls=calls, raw=message)

    return _Adapter()
