"""DAG 파일이 `모듈.이름(`으로 부르는 이름이 그 모듈에 실제로 있나.

DAG 파일은 태스크 함수 **안에서** 그 이름을 부르므로, 없는 이름이어도 DagBag은 통과하고
운영에서 그 태스크가 도는 순간에야 `AttributeError`가 난다. 2026-08-31에 지금은 없는 옛
기능에서 실제로 그 자리가 났다 — 함수를 다른 파일로 옮기고 DAG의 호출을 안 고쳤다.

파이썬 import는 그것을 안 잡는다. `from modules.kospi import forecast`는 모듈이 있으면
성공하고, `forecast.build`가 없는지는 부를 때 안다. 그래서 이 표가 있다.
"""

import importlib

import pytest

# (모듈 경로, 그 모듈에서 부르는 이름들). DAG 파일에서 `모듈.이름(` 꼴로 쓰는 것.
MODULE_ATTRIBUTES: dict[str, tuple[str, ...]] = {
    "modules.kospi.common": ("notify_forecast", "notify_review", "notify_param", "run_date_param"),
    "modules.kospi.forecast": ("build",),
    "modules.kospi.intraday": ("build", "run_slot_param"),
    "modules.kospi.review": ("grade", "observe"),
    "modules.llm": ("kospi_model",),
}


@pytest.mark.parametrize(("module_path", "names"), sorted(MODULE_ATTRIBUTES.items()))
def test_the_names_a_dag_calls_exist_on_the_module(module_path: str, names: tuple[str, ...]):
    module = importlib.import_module(module_path)

    missing = [name for name in names if not hasattr(module, name)]

    assert not missing, f"{module_path}에 {', '.join(missing)}이(가) 없다. DAG이 그 이름으로 부른다."
