"""`modules/prompts/` 아래의 프롬프트 파일을 읽는다.

**프롬프트는 코드가 아니라 데이터다.** 문장을 고치는 일이 파이썬 파일을 여는 일이 되면
diff에 코드와 문장이 섞이고, 문장만 바꾸는 변경도 모듈을 다시 읽게 만든다. `sql/`을
파이썬 문자열로 두지 않는 것과 같은 이유다.

## 왜 `modules/` 안인가

컨테이너는 `airflow/`의 `dags`·`modules`·`utility`·`sql`·`plugins`·`config`만 마운트한다
(`compose/prod/airflow/docker-compose.yaml`). 새 최상위 폴더를 만들면 컨테이너에서 안 보이고,
compose는 건드리지 않는 것이 저장소 규칙이다. `config/`는 `.gitignore` 대상이라 커밋되지
않는다(`airflow.cfg`가 생기는 자리다). 남는 것이 `modules/`이고, 프롬프트를 쓰는 코드가
거기 있으므로 자리도 맞다.

경로는 `AIRFLOW_HOME`을 보지 않는다. 파일이 패키지와 함께 다니므로 `__file__` 기준이
컨테이너와 로컬에서 똑같이 맞는다. `sql.py`가 `AIRFLOW_HOME`을 보는 것은 `sql/`이
`modules/` 밖에 있기 때문이다.

## 치환은 `string.Template`이다

`str.format`을 쓰지 않는다. 프롬프트에는 출력 예시로 `{"highlights": [...]}` 같은 JSON이
들어가는데 `format`은 그 중괄호를 자리표시자로 읽고 죽는다. 전에는 `assessment.py`가 그
문제를 "`NUMBER_STYLE`에 중괄호를 넣지 마라"라는 규칙으로 막고 있었다 — 프롬프트를 쓰는
사람이 지켜야 하는 제약이라 언젠가 깨진다. `$name`은 JSON과 부딪히지 않는다.

**`substitute`는 빠진 값에 `KeyError`를 낸다.** `safe_substitute`를 쓰지 않는다 —
자리표시자가 그대로 모델에게 나가는 것보다 태스크가 죽는 편이 낫다.
"""

from pathlib import Path
from string import Template
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict

PROMPT_ROOT = Path(__file__).resolve().parent / "prompts"

# 흐름 하나에 속하지 않는 조각. `PROMPT_ROOT` 바로 아래가 아니라 한 단 아래에 두어
# 흐름 파일을 훑는 테스트와 섞이지 않게 한다.
FRAGMENT_ROOT = PROMPT_ROOT / "fragments"


class PromptError(RuntimeError):
    """프롬프트 파일이 없거나 모양이 다르다. 다시 불러도 같은 결과다."""


class PromptSet(BaseModel):
    """프롬프트 파일 하나. 한 흐름이 쓰는 문장 전부를 담는다.

    `variants`는 **한 흐름 안에서 갈리는 문장 조각**이다. 평가의 관점 셋(`global`·`korea`·
    `us`)처럼 값이지 코드가 아닌 것들이라, 하나를 늘리는 일이 파이썬을 여는 일일 이유가
    없다. 조각은 `render(...)`에 값으로 넘어가거나 그대로 쓰인다.

    **허용 값 검증은 코드에 남는다.** `assessment.LlmSettings`가 관점 키로 환경변수를
    막는데 그 판정은 YAML이 할 수 없다.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    system: str
    instruction: str
    repair: str
    variants: dict[str, str] = {}

    def render(self, field: str, /, **values: object) -> str:
        """`$name` 자리표시자를 채운 문장. 빠진 값이 있으면 죽는다."""
        template = Template(getattr(self, field))
        try:
            return template.substitute(**values)
        except KeyError as error:
            raise PromptError(f"prompt '{field}' needs a value for {error}") from error


def read_prompt(name: str) -> PromptSet:
    """`modules/prompts/<name>.yaml` 하나를 읽어 검증한다.

    import 시점에 부르는 것을 전제로 한다. 파일이 없거나 칸이 어긋나면 그 모듈을 쓰는
    DAG이 DagBag 단계에서 죽고, 그것이 실행 중에 프롬프트가 비는 것보다 낫다.
    """
    return PromptSet.model_validate(_load(PROMPT_ROOT / f"{name}.yaml"))


def read_fragments(name: str) -> dict[str, str]:
    """`modules/prompts/fragments/<name>.yaml`의 문장 조각들.

    **흐름 하나에 속하지 않는 문장이다.** 산문 숫자 표기 규칙처럼 여러 흐름이 같은 답을
    내야 하는 조각을 담는다. `prompts/` 바로 아래가 흐름 하나이므로 자리를 나눈다 —
    파일을 훑는 테스트가 이 폴더를 보지 않는 것도 그래서다.
    """
    raw = _load(FRAGMENT_ROOT / f"{name}.yaml")
    if not all(isinstance(value, str) for value in raw.values()):
        raise PromptError(f"every fragment must be a string: {name}")
    return raw


def _load(path: Path) -> dict[str, Any]:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as error:
        raise PromptError(f"prompt file is missing: {path}") from error
    except yaml.YAMLError as error:
        raise PromptError(f"prompt file is not valid YAML: {path}") from error

    if not isinstance(raw, dict):
        raise PromptError(f"prompt file must be a mapping: {path}")
    return raw
