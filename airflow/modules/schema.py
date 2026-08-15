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
**호출하는 쪽이 스키마 없이 한 번 더 시도한다**(`modules/llm.py`). 프롬프트에도 출력 형식을
그대로 적어 두는 이유가 이것이다. 강제가 되면 좋고, 안 되면 검증이 받는다.

## 열린 dict는 못 쓴다

`dict[str, float]` 같은 필드는 strict 모드에서 표현할 수 없다. 값 이름이 스키마에 없기
때문이다. 그런 필드는 `{"name": ..., "value": ...}` 배열로 바꾼다. 이 제약이 오히려 낫다.
이름이 자유 문자열이면 나중에 그 값을 찾아 쓰는 쪽이 매번 추측해야 한다.
"""

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
