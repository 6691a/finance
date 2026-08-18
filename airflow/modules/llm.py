"""공용 모델 정의와 호출.

**어떤 모델을 쓸지는 여기서 정한다.** 환경변수로 제공처를 갈아 끼우지 않는다. LangChain은
제공처마다 클래스가 다르고(`ChatXAI`, `ChatAnthropic`, …) 받는 인자도 다르다. 그걸 문자열
설정 세 개로 흉내 내면 어느 쪽도 제대로 못 쓴다. 그래서 이 파일이 LangChain 문법 그대로
모델을 만들고, 바꿀 때는 여기 한 줄을 고친다.

키만 환경에서 온다. `ChatXAI`는 `XAI_API_KEY`를 스스로 읽으므로 우리가 넘기지 않는다.
키를 코드나 인자로 옮기면 로그와 예외에 실릴 자리가 늘어난다.

## 조사와 답변을 나눈다

한 번의 요청에 툴과 `response_format`을 함께 넣지 않는다. 제공처마다 둘을 같이 줬을 때
동작이 다르고(스키마를 주면 툴 호출을 안 하거나, 그 반대), 그 차이를 우리가 흡수할 방법이
없다. 그래서 두 단계로 나눈다.

1. **조사**: 툴만 주고 모델이 필요한 만큼 부르게 한다.
2. **답변**: 툴을 빼고 `response_format`으로 스키마를 강제해 한 번 더 부른다.

이 구조라 마지막 응답은 항상 우리가 정한 모양이고, 툴 결과는 그 전 대화에 이미 들어가 있다.

## 스키마를 못 받는 제공처

제3자 OpenAI 호환 제공자가 `json_schema`를 모르면 요청이 거절된다. `classify`가 그걸
`UnsupportedResponseFormat`으로 바꾸고, 부르는 쪽이 스키마 없이 한 번 더 부른다. 프롬프트에
출력 형식을 그대로 적어 둔 이유가 이것이다. **강제가 되면 좋고, 안 되면 검증이 받는다.**

## 오류는 여기서만 분류한다

`ChatXAI`는 `BaseChatOpenAI` 서브클래스라 제공처 오류가 `openai` 예외로 그대로 올라온다.
그걸 재시도할 값어치가 있는 것(`ConnectionError`)과 없는 것(`LlmError`)으로 가르는 곳이
`classify` 하나다. 실제 재시도 여부는 DAG가 정한다.

**재시도는 Airflow가 한다.** 모델은 `max_retries=0`으로 만든다. SDK가 먼저 재시도하면 태스크
타임아웃 안에서 몇 번을 부른 것인지 로그와 트레이스가 어긋난다.

## 추적을 켜는 법

`LANGSMITH_TRACING`, `LANGSMITH_API_KEY`, `LANGSMITH_PROJECT`를 환경에 준다. 비우면 아무 것도
보내지 않고 호출 경로도 그대로다. **켜면 프롬프트 전문과 문서 본문이 LangSmith로 나간다.**
저장 위치가 문제가 되면 `LANGSMITH_ENDPOINT`로 다른 인스턴스를 가리킨다.
"""

import logging
from collections.abc import Sequence
from typing import Any

import openai
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_xai import ChatXAI

logger = logging.getLogger(__name__)

# 한 번의 호출을 기다리는 시간. 스트리밍을 쓰지 않으므로 모델이 추론을 끝내야 응답 헤더가
# 온다. 긴 문서 하나가 이 시간을 넘기면 팬아웃 배치 전체가 되돌아가므로 넉넉히 잡는다.
REQUEST_TIMEOUT_SECONDS = 300.0


class LlmError(RuntimeError):
    """모델 호출이 실패했다."""


class UnsupportedResponseFormat(LlmError):
    """제공처가 `response_format` 스키마를 받지 않는다. 스키마 없이 다시 부른다."""


# 제공처가 스키마를 못 받을 때 400 본문에 나오는 조각. 오류 문자열로 가르는 것이 마뜩잖지만
# OpenAI 호환 API에 "이 기능을 지원하는가"를 묻는 표준 방법이 없다.
UNSUPPORTED_MARKERS = ("response_format", "json_schema", "structured output")


def document_model() -> BaseChatModel:
    """문서 태깅(`modules/assessment.py`)이 쓰는 모델.

    키는 `XAI_API_KEY`에서 온다. 모델을 바꾸려면 이 함수를 고친다. 제공처를 바꾸려면 여기서
    다른 LangChain 클래스를 만들어 돌려주면 되고, 부르는 쪽은 `BaseChatModel`만 안다.
    """
    return ChatXAI(
        model="grok-4.6",
        timeout=REQUEST_TIMEOUT_SECONDS,
        max_retries=0,
    )


def briefing_model() -> BaseChatModel:
    """Slack 브리핑 요약(`modules/briefing/comment.py`)이 쓰는 모델.

    지금은 `document_model`과 같은 모델이지만 함수를 나눠 둔다. 태깅은 문서 한 건을 읽고
    JSON을 내는 일이고 브리핑은 집계 표를 읽고 글을 쓰는 일이라, 한쪽만 다른 모델로 옮기고
    싶어질 때 그 함수만 고치면 된다.
    """
    return ChatXAI(
        model="grok-4.6",
        timeout=REQUEST_TIMEOUT_SECONDS,
        # 재시도는 Airflow가 한다. 위 모듈 docstring 참고.
        max_retries=0,
    )


def model_name(model: BaseChatModel) -> str:
    """`document.llm_model`에 남길 이름. 어느 모델이 그 점수를 냈는지 나중에 읽어야 한다."""
    return getattr(model, "model_name", None) or type(model).__name__


def invoke(
    model: BaseChatModel,
    messages: Sequence[BaseMessage],
    *,
    schema: dict[str, Any] | None = None,
    tools: Sequence[dict[str, Any]] | None = None,
) -> AIMessage:
    """한 번 부른다. 재시도도 툴 루프도 여기서 돌지 않는다."""
    bound = model
    if tools:
        bound = bound.bind_tools(tools)
    if schema:
        bound = bound.bind(response_format=schema)
    try:
        return bound.invoke(list(messages))
    except openai.OpenAIError as error:
        raise classify(error, had_schema=schema is not None) from error


def classify(error: openai.OpenAIError, *, had_schema: bool = False) -> Exception:
    """제공처 예외를 DAG가 아는 종류로 바꾼다. 재시도 여부 판단이 여기 달려 있다."""
    if isinstance(error, openai.APIConnectionError | openai.APITimeoutError):
        # 네트워크 실패는 재시도할 값어치가 있다. DAG이 판단한다.
        return ConnectionError(f"chat request failed: {error}")
    if isinstance(error, openai.RateLimitError):
        # 429는 설정 문제가 아니라 지금 너무 빨리 부른 것이다. 잠시 뒤면 풀리므로 재시도할
        # 값어치가 있는 쪽에 넣는다. `LlmError`로 올리면 동시 호출 수를 올린 순간부터
        # 배치 전체가 즉시 실패로 끝난다. `RateLimitError`는 `APIStatusError`의 하위
        # 타입이라 아래 분기보다 먼저 봐야 한다.
        return ConnectionError(f"rate limited: HTTP {error.status_code}")
    if isinstance(error, openai.APIStatusError):
        detail = str(error.response.text)[:500] if error.response is not None else str(error)
        if had_schema and any(marker in detail.lower() for marker in UNSUPPORTED_MARKERS):
            return UnsupportedResponseFormat(f"HTTP {error.status_code}: {detail}")
        return LlmError(f"HTTP {error.status_code}: {detail}")
    return LlmError(str(error))
