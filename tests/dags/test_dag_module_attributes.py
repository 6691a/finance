"""DAG이 **태스크 안에서** 부르는 모듈 속성이 실제로 있나.

DAG 파일의 import는 전부 태스크 함수 안이다(DagBag 30초 타임아웃, 프로젝트 규칙). 그래서
`from modules.causal import store` 뒤에 오는 `store.store_directions(...)`는 **그 태스크가
운영에서 실제로 돌기 전까지 아무도 확인하지 않는다.** DagBag 테스트도 import error 검사도
모듈 객체만 보지 그 안의 이름은 안 본다.

2026-08-31에 그 자리에서 났다 — `store_directions`를 `causal/direction.py`에 두고 DAG은
`store.store_directions`로 불렀다. 단위 테스트는 정의된 자리를 직접 import해 통과했고,
운영에서 `AttributeError: module 'modules.causal.store' has no attribute 'store_directions'`가
났다. 그 태스크는 LLM을 부르고 나서 저장 단계에서 죽어 비용만 쓴 셈이다.

**여기 적는 것은 `<모듈>.<이름>()` 꼴로 부르는 자리뿐이다.** `from x import y`는 import가
바로 죽으므로 DagBag이 이미 잡는다.
"""

import importlib

import pytest

# (모듈 경로, 그 모듈에서 부르는 이름들). DAG 파일에서 `모듈.이름(` 꼴로 쓰는 것.
MODULE_ATTRIBUTES: dict[str, tuple[str, ...]] = {
    "modules.causal.store": ("store_paths", "store_directions", "start_llm_run", "finish_llm_run", "week_has_paths"),
    "modules.causal.run": ("build_weekly_graph", "connection"),
    "modules.graph.projection": ("stored_weeks", "read_week", "project", "write_graph"),
    "modules.graph.query": ("driver", "read_direction_input"),
    "modules.llm": ("direction_model",),
}


@pytest.mark.parametrize(("module_path", "names"), sorted(MODULE_ATTRIBUTES.items()))
def test_the_names_a_dag_calls_exist_on_the_module(module_path: str, names: tuple[str, ...]):
    module = importlib.import_module(module_path)

    missing = [name for name in names if not hasattr(module, name)]

    assert not missing, f"{module_path}에 {', '.join(missing)}이(가) 없다. DAG이 그 이름으로 부른다."
