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
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.tools import BaseTool
from langchain_openai import ChatOpenAI
from langchain_xai import ChatXAI

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

# 산문에 숫자를 쓰는 규칙. 모델은 툴 JSON의 raw 숫자를 그대로 문장에 베끼기 때문에 "상승 1174",
# "외국인이 140762백만원" 같은 문장이 나온다(2026-08-25 실측). Slack·브리핑은 프론트엔드가 없는
# 출력이라 그 표기가 유일한 단서다.
#
# **산문을 내는 프롬프트 넷이 이 한 벌을 함께 쓴다.** 같은 문장을 네 곳에 적으면 반드시 어긋난다.
# `expectation_extraction`은 뺀다 — 그쪽 숫자는 산문이 아니라 JSON 숫자 칸으로 가고 쉼표가 파싱을 깬다.
#
# **중괄호를 넣지 않는다.** `assessment.SYSTEM_PROMPT_TEMPLATE`이 `.format()` 템플릿이라
# 중괄호가 섞이면 `KeyError`로 죽는다. `tests/modules/test_llm.py`가 그 자리를 지킨다.
NUMBER_STYLE = """## 숫자 표기

**문장 안의 숫자에만 적용한다.** JSON의 숫자 칸에는 쉼표를 넣지 마라.

- 네 자리 이상이면 천 단위 쉼표를 찍는다 — `1174`가 아니라 `1,174`다.
  연도·종목코드·`ref` 같은 식별자는 예외다(`2026`, `005930`).
- 수에는 단위를 붙인다. 등락 종목 수는 `1,174종목`, 등락률은 `%`,
  금리 변화는 `bp`, 금액은 툴이 준 `amount_unit` 그대로 `140,762백만원`이다.
- **단위를 바꾸지 마라.** 백만원을 억원으로 고쳐 쓰지 마라.
  단위를 모르는 값에는 단위를 지어 붙이지 마라."""


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
    """이벤트 기대치 추출(`modules/expectation_extraction.py`)이 쓰는 모델.

    문서 하나를 읽고 구조화 JSON을 내는 일이라 문서 태깅과 같은 모델로 시작한다. 함수를
    나눠 두는 이유도 같다 — 추출만 다른 모델로 옮기고 싶어질 때 이 함수만 고친다.
    """
    return ChatOpenAI(
        model="gpt-5.6-luna",
        timeout=REQUEST_TIMEOUT_SECONDS,
        # 재시도는 Airflow가 한다. 위 모듈 docstring 참고.
        max_retries=0,
    )


def briefing_model() -> BaseChatModel:
    """Slack 브리핑 선별(`modules/briefing/picks.py`)이 쓰는 모델.

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


def thesis_model() -> BaseChatModel:
    """시장 추론(`modules/thesis_generation.py`·`thesis_outcomes.py`)이 쓰는 모델.

    툴 왕복이 많은 작업이라 툴 호출 품질로 고른다. 브리핑 선별과 같은 `grok-4.6`이지만 함수를
    나눠 둔다 — 선별은 목록을 읽고 고르는 일이고 추론은 툴을 여러 번 돌며 가설을 세우는
    일이라, 한쪽만 다른 모델로 옮기고 싶어질 때 그 함수만 고치면 된다.

    키는 이 클래스가 `XAI_API_KEY`에서 스스로 읽는다. 우리가 넘기지 않는다.
    **운영 키를 먼저 확인한다** — 2026-08-20 실측에서 `compose/prod/airflow/.env`의
    `XAI_API_KEY`가 `Incorrect API key provided`였다. 키가 무효면 이 DAG는 매 슬롯 실패한다.
    """
    return ChatXAI(
        model="grok-4.6",
        timeout=THESIS_TIMEOUT_SECONDS,
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
