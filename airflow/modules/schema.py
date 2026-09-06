"""Pydantic 모델을 모델이 **반드시 따라야 하는** JSON Schema로 바꾼다.

지금까지는 응답을 받아서 검증했다. 형식이 깨지면 한 번 교정을 요청하고, 두 번째도 깨지면
그 문서를 넘겼다. 그건 사후 처리다. `response_format`에 스키마를 실으면 제공처가 **디코딩
단계에서** 형식을 강제해서 깨진 응답이 아예 오지 않는다.

## strict 모드가 요구하는 것

OpenAI 호환 `json_schema` strict 모드는 Pydantic이 그냥 뱉는 스키마를 받지 않는다.

- 모든 객체에 `additionalProperties: false`가 있어야 한다.
- **모든 속성이 `required`에 있어야 한다.** 기본값이 있는 필드도 예외가 아니다. 그래서
  선택 필드라도 모델이 키를 반드시 내놓는다.
- `default`, `title`, `format` 같은 키워드는 무시되거나 거절된다. 떼어 낸다.

## 지원하지 않는 제공처가 있다

제3자 OpenAI 호환 제공자가 `json_schema`를 모를 수 있다. 그때는 요청이 400으로 거절되므로
`modules/llm.py`의 `invoke`가 **같은 스키마를 문장으로 바꿔 붙이고 스키마 없이 한 번 더
부른다**(`format_instruction`). 강제가 되면 좋고, 안 되면 검증이 받는다.

## 출력 형식의 원본은 Pydantic 모델이다

프롬프트 YAML은 목적·규칙·주의만 갖고 **모양은 적지 않는다.** 필드가 무엇인지는
`Field(description=...)`이 말하고 그것이 스키마에 실려 모델에게 간다. 같은 모양을 예시 JSON으로
프롬프트에 한 번 더 적으면 필드를 더할 때 둘이 어긋난다. `tests/modules/test_prompt_versions.py`가
YAML과 스키마를 함께 해시로 잠그므로 description을 고치는 것도 판을 올리는 일이다.

## `with_structured_output`을 쓰지 않는다

LangChain에는 같은 일을 하는 `with_structured_output()`이 있지만 그건 파싱까지 가져간다.
우리가 막아야 하는 것은 제공처가 스키마를 **거절할 때**가 아니라 **무시할 때**이고, 그건
이 모듈의 `json_object`와 부르는 쪽의 검증이 받는다. 그래서 여기서 만든 값을
`.bind(response_format=...)`으로 건다. 그것도 LangChain 경로다.

## 열린 dict는 못 쓴다

`dict[str, float]` 같은 필드는 strict 모드에서 표현할 수 없다. 값 이름이 스키마에 없기
때문이다. 그런 필드는 `{"name": ..., "value": ...}` 배열로 바꾼다. 이 제약이 오히려 낫다.
이름이 자유 문자열이면 나중에 그 값을 찾아 쓰는 쪽이 매번 추측해야 한다.
"""

import json
from typing import Any

from pydantic import BaseModel

# strict 모드가 무시하거나 거절하는 키워드. 붙여 보내면 400이 온다.
UNSUPPORTED_KEYWORDS = ("default", "title", "format", "examples", "$comment")


def strict_json_schema(model: type[BaseModel]) -> dict[str, Any]:
    """Pydantic 모델의 스키마를 strict 모드가 받는 모양으로 바꾼다."""
    return _tighten(model.model_json_schema())


def _tighten(node: Any) -> Any:
    if isinstance(node, list):
        return [_tighten(item) for item in node]
    if not isinstance(node, dict):
        return node

    tightened = {key: _tighten(value) for key, value in node.items() if key not in UNSUPPORTED_KEYWORDS}

    if tightened.get("type") == "object":
        properties = tightened.get("properties", {})
        tightened["additionalProperties"] = False
        # 기본값이 있는 필드도 required에 넣는다. strict 모드는 부분 객체를 허용하지 않는다.
        tightened["required"] = list(properties)
    return tightened


def response_format(model: type[BaseModel], name: str) -> dict[str, Any]:
    """`response_format` 인자에 그대로 넣는 값."""
    return {
        "type": "json_schema",
        "json_schema": {"name": name, "strict": True, "schema": strict_json_schema(model)},
    }


class SchemaError(ValueError):
    """응답에서 JSON 객체를 찾지 못했다."""


def json_object(raw: str) -> str:
    """코드 펜스나 앞뒤 설명이 붙어 와도 JSON 객체만 뽑는다.

    스키마를 강제하지 못한 제공처는 JSON만 내라는 지시를 지키지 않는 경우가 있다.
    첫 `{`부터 마지막 `}`까지를 잘라 낸다.
    """
    start = raw.find("{")
    end = raw.rfind("}")
    if start == -1 or end <= start:
        raise SchemaError("Model did not return a JSON object")
    return raw[start : end + 1]


def format_instruction(schema_format: dict[str, Any]) -> str:
    """스키마를 강제하지 못한 제공처에 붙이는 문장. `response_format`이 준 값을 그대로 받는다."""
    schema = json.dumps(schema_format["json_schema"]["schema"], ensure_ascii=False, separators=(",", ":"))
    return f"아래 JSON Schema를 따르는 JSON 객체 하나만 출력한다. 설명이나 코드 펜스를 붙이지 않는다.\n{schema}"
