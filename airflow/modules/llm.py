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

## 대화 하나는 서버 하나로 보낸다

xAI의 프롬프트 캐시는 **서버마다 따로** 저장된다. 같은 대화의 다음 요청이 다른 서버로 가면
앞의 접두를 못 만나 전부 새 입력으로 청구된다. `x-grok-conv-id` 헤더가 같은 값을 가진
요청을 같은 서버로 보내는 sticky 라우팅이고, 그래서 **툴 왕복이 있는 흐름은 이 헤더가
없으면 왕복마다 대화 전체를 새로 낸다.**

2026-08-27 `market_thesis_intraday` 실측이 그 값이다. 모델 호출 네 번 중 캐시를 만난 것은
셋째 하나였고(51,968 토큰), 나머지 셋은 512였다 — 입력 246,395 토큰 중 캐시 적중이 21.5%다.

`conv_id`는 **결정적 문자열**이다. 실행마다 난수를 만들면 재시도가 새 대화가 되어 캐시를
버린다. 날짜와 슬롯처럼 그 실행을 가리키는 값을 쓴다.

## 토큰은 콜백이 센다

`AIMessage.usage_metadata`는 응답마다 붙지만 **실패한 대화에는 최종 상태가 없다.** 그래서
왕복을 세는 자리(`ThesisToolbox.round_count`)와 같은 모양으로, 그래프 밖에 사는 객체가
누적한다 — LangChain의 `UsageMetadataCallbackHandler`다. 예외가 나도 그때까지 부른 만큼이
남고, 부르는 쪽이 원장을 닫으면서 `token_usage()`로 읽는다.

## 오류는 여기서만 분류한다

`ChatXAI`는 `BaseChatOpenAI` 서브클래스라 제공처 오류가 `openai` 예외로 그대로 올라온다.
그걸 재시도할 값어치가 있는 것(`RetryableLlmError`)과 없는 것(`LlmError`)으로 가르는 곳이
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
from langchain_core.callbacks import UsageMetadataCallbackHandler
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.tools import BaseTool
from langchain_openai import ChatOpenAI
from langchain_xai import ChatXAI
from pydantic import BaseModel, ConfigDict

from modules.prompt import read_fragments

logger = logging.getLogger(__name__)

# 한 번의 호출을 기다리는 시간. 스트리밍을 쓰지 않으므로 모델이 추론을 끝내야 응답 헤더가
# 온다. 긴 문서 하나가 이 시간을 넘기면 팬아웃 배치 전체가 되돌아가므로 넉넉히 잡는다.
REQUEST_TIMEOUT_SECONDS = 300.0

# 시장 추론만 더 오래 기다린다. 한 요청이 툴 결과 여러 건을 읽고 대상 전부에 확률·이유를
# 쓰는 일이라 문서 한 건을 태깅하는 것보다 길다. 2026-08-21 운영 첫 실행이 300초에서
# `APITimeoutError`로 죽었다. 노트북(`narrator_ab.ipynb`)은 이미 900을 쓰고 있었다.
#
# **1800은 관측이 아니라 예방이다**(2026-08-22). 900에서 죽은 실행은 아직 없고, 툴이 11개로
# 늘면서 왕복이 길어질 것을 보고 미리 올렸다. 되돌릴 후보라는 사실을 손잡이 장부에 남겼다
# (`docs/analysis/market-thesis/TUNING.md` 6절 이력). 실제 소요 분포가 쌓이면 다시 정한다.
#
# **문서 태깅의 300초를 같이 올리지 않는다.** 그쪽은 지금 값으로 잘 돌고 있고, 한 번에
# 손잡이 하나만 당긴다(`docs/analysis/market-thesis/TUNING.md` 1절).
THESIS_TIMEOUT_SECONDS = 1800.0

# 산문에 숫자를 쓰는 규칙. 문장은 `modules/prompts/fragments/shared.yaml`이 갖는다 —
# 산문을 내는 프롬프트 넷이 `$number_style`로 받아 쓰는 조각이라 흐름 하나에 속하지 않는다.
# 이름은 그대로 남긴다. 네 소비자와 테스트가 이 상수로 쓰고 있다.
NUMBER_STYLE = read_fragments("shared")["number_style"]


class LlmError(RuntimeError):
    """모델 호출이 실패했다."""


class RetryableLlmError(LlmError):
    """네트워크·429·5xx처럼 잠시 뒤 다시 부르면 성공할 수 있는 모델 호출 실패다."""


class UnsupportedResponseFormat(LlmError):
    """제공처가 `response_format` 스키마를 받지 않는다. 스키마 없이 다시 부른다."""


# 제공처가 스키마를 못 받을 때 400 본문에 나오는 조각. 오류 문자열로 가르는 것이 마뜩잖지만
# OpenAI 호환 API에 "이 기능을 지원하는가"를 묻는 표준 방법이 없다.
UNSUPPORTED_MARKERS = ("response_format", "json_schema", "structured output")


def document_model() -> BaseChatModel:
    """문서 태깅(`modules/assessment.py`)이 쓰는 모델.

    키는 `OPENAI_API_KEY`에서 온다. 모델을 바꾸려면 이 함수를 고친다. 제공처를 바꾸려면 여기서
    다른 LangChain 클래스를 만들어 돌려주면 되고, 부르는 쪽은 `BaseChatModel`만 안다.
    """
    return ChatOpenAI(
        model="gpt-5.6-luna",
        timeout=REQUEST_TIMEOUT_SECONDS,
        max_retries=0,
    )


def expectation_model() -> BaseChatModel:
    """이벤트 기대치 추출(`modules/expectation/extraction.py`)이 쓰는 모델.

    문서 하나를 읽고 구조화 JSON을 내는 일이라 문서 태깅과 같은 모델로 시작한다. 함수를
    나눠 두는 이유도 같다 — 추출만 다른 모델로 옮기고 싶어질 때 이 함수만 고친다.
    """
    return ChatOpenAI(
        model="gpt-5.6-luna",
        timeout=REQUEST_TIMEOUT_SECONDS,
        # 재시도는 Airflow가 한다. 위 모듈 docstring 참고.
        max_retries=0,
    )


def _conversation_headers(conv_id: str) -> dict[str, str]:
    """같은 대화를 같은 서버로 보내는 헤더. 모듈 docstring의 "대화 하나는 서버 하나로" 참고."""
    if not conv_id:
        raise ValueError("conv_id must not be empty — the cache is keyed per server by this header")
    return {"x-grok-conv-id": conv_id}


def briefing_model(conv_id: str) -> BaseChatModel:
    """Slack 브리핑 선별(`modules/briefing/picks.py`)이 쓰는 모델.

    지금은 `document_model`과 같은 모델이지만 함수를 나눠 둔다. 태깅은 문서 한 건을 읽고
    JSON을 내는 일이고 브리핑은 집계 표를 읽고 글을 쓰는 일이라, 한쪽만 다른 모델로 옮기고
    싶어질 때 그 함수만 고치면 된다.

    `conv_id`는 이 발송 하나를 가리키는 결정적 문자열이다(모듈 docstring 참고).
    """
    return ChatXAI(
        model="grok-4.6",
        timeout=REQUEST_TIMEOUT_SECONDS,
        default_headers=_conversation_headers(conv_id),
        # 재시도는 Airflow가 한다. 위 모듈 docstring 참고.
        max_retries=0,
    )


def thesis_model(conv_id: str) -> BaseChatModel:
    """시장 추론(`modules/thesis/generation.py`·`thesis/outcomes.py`)이 쓰는 모델.

    툴 왕복이 많은 작업이라 툴 호출 품질로 고른다. 브리핑 선별과 같은 `grok-4.6`이지만 함수를
    나눠 둔다 — 선별은 목록을 읽고 고르는 일이고 추론은 툴을 여러 번 돌며 가설을 세우는
    일이라, 한쪽만 다른 모델로 옮기고 싶어질 때 그 함수만 고치면 된다.

    키는 이 클래스가 `XAI_API_KEY`에서 스스로 읽는다. 우리가 넘기지 않는다.
    **운영 키를 먼저 확인한다** — 2026-08-20 실측에서 `compose/prod/airflow/.env`의
    `XAI_API_KEY`가 `Incorrect API key provided`였다. 키가 무효면 이 DAG는 매 슬롯 실패한다.

    `conv_id`는 이 실행 하나를 가리키는 결정적 문자열이다. **이 흐름이 헤더가 제일 급한
    자리다** — 툴 왕복마다 대화 전체가 재전송되기 때문이다(모듈 docstring 참고).
    """
    return ChatXAI(
        model="grok-4.6",
        timeout=THESIS_TIMEOUT_SECONDS,
        default_headers=_conversation_headers(conv_id),
        # 재시도는 Airflow가 한다. 위 모듈 docstring 참고.
        max_retries=0,
    )


class TokenUsage(BaseModel):
    """대화 하나가 청구된 토큰. `thesis_llm_run`의 세 칸이 이 값을 그대로 받는다.

    셋을 나눠 두는 이유는 **서로 다른 손잡이에 붙기 때문이다.** `prompt`는 왕복마다 대화
    전체가 재전송된 결과라 프롬프트 블록 크기와 왕복 상한이 움직이고, `reasoning`은 대화에
    남지 않아 재전송되지도 캐시되지도 않는다. 한 칸으로 묶으면 어느 쪽이 늘었는지 못 가른다.

    **`completion`은 `reasoning`을 포함한다.** 제공처가 사고 토큰도 출력 단가로 청구한다.
    """

    model_config = ConfigDict(frozen=True)

    prompt: int = 0
    completion: int = 0
    reasoning: int = 0


def token_usage(handler: UsageMetadataCallbackHandler) -> TokenUsage:
    """콜백이 누적한 것을 원장이 쓰는 모양으로. 모듈 docstring의 "토큰은 콜백이 센다" 참고.

    핸들러는 모델 이름마다 칸을 갖는다. 한 대화가 모델 하나만 쓰더라도 합으로 접는다 —
    나중에 조사와 답변을 다른 모델로 나누면 그때 이 함수만 고치면 된다.

    **모델을 한 번도 못 부르고 죽은 대화는 전부 0이다.** NULL이 아니다 — 0은 "안 썼다"이고
    NULL은 "안 쟀다"라, 원장에서 그 둘이 갈려야 한다.
    """
    prompt = completion = reasoning = 0
    for usage in handler.usage_metadata.values():
        prompt += usage.get("input_tokens", 0)
        completion += usage.get("output_tokens", 0)
        reasoning += (usage.get("output_token_details") or {}).get("reasoning", 0)
    return TokenUsage(prompt=prompt, completion=completion, reasoning=reasoning)


def model_name(model: BaseChatModel) -> str:
    """`document.llm_model`에 남길 이름. 어느 모델이 그 점수를 냈는지 나중에 읽어야 한다."""
    return getattr(model, "model_name", None) or type(model).__name__


def invoke(
    model: BaseChatModel,
    messages: Sequence[BaseMessage],
    *,
    schema: dict[str, Any] | None = None,
    tools: Sequence[BaseTool | dict[str, Any]] | None = None,
) -> AIMessage:
    """한 번 부른다. 재시도도 툴 루프도 여기서 돌지 않는다.

    **툴과 스키마를 한 요청에 섞지 않는다.** 제공처마다 둘을 같이 줬을 때 동작이 다르고
    (스키마를 주면 툴 호출을 안 하거나 그 반대) 그 차이를 흡수할 방법이 없다. 모듈 docstring의
    "조사와 답변을 나눈다"가 원칙이고, 이 검사가 그것을 코드 계약으로 만든다.
    """
    if tools and schema:
        raise ValueError("invoke() takes tools or schema, never both — investigate first, then answer")
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
        return RetryableLlmError(f"chat request failed: {error}")
    if isinstance(error, openai.RateLimitError):
        # 429는 설정 문제가 아니라 지금 너무 빨리 부른 것이다. 잠시 뒤면 풀리므로 재시도할
        # 값어치가 있는 쪽에 넣는다. `LlmError`로 올리면 동시 호출 수를 올린 순간부터
        # 배치 전체가 즉시 실패로 끝난다. `RateLimitError`는 `APIStatusError`의 하위
        # 타입이라 아래 분기보다 먼저 봐야 한다.
        return RetryableLlmError(f"rate limited: HTTP {error.status_code}")
    if isinstance(error, openai.APIStatusError):
        detail = str(error.response.text)[:500] if error.response is not None else str(error)
        if (
            error.status_code in {400, 422}
            and had_schema
            and any(marker in detail.lower() for marker in UNSUPPORTED_MARKERS)
        ):
            return UnsupportedResponseFormat(f"HTTP {error.status_code}: {detail}")
        if error.status_code in {408, 429} or error.status_code >= 500:
            return RetryableLlmError(f"HTTP {error.status_code}: {detail}")
        return LlmError(f"HTTP {error.status_code}: {detail}")
    return LlmError(str(error))
