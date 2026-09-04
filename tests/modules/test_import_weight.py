"""DagBag이 매번 무는 무게.

Airflow 스케줄러는 모든 DAG 파일을 주기적으로 다시 파싱한다. LangChain·LangGraph는 첫
import에 몇 초를 쓰므로, 슬롯 모듈이 모듈 수준에서 그것을 끌고 오면 스케줄러가 DAG을 못
읽는다. 무거운 것은 흐름 모듈(`kospi.generation` 등) 하나에만 있어야 한다.

**하위 패키지의 `__init__.py`를 비워 두는 이유도 이것이다.** 거기서 재수출하면 가벼운 모듈
하나를 import해도 전부가 딸려 온다.

별도 프로세스로 재는 이유는 pytest가 이미 무거운 것을 올려 뒀기 때문이다.
"""

import json
import subprocess
import sys

# 코스피 DAG이 모듈 수준에서 import하는 것 전부. 어휘·순수 함수부터 슬롯 진입점까지
# 하나도 LangChain을 끌고 오면 안 된다.
#
# **2026-09-03에 이 목록이 202개를 물고 있었다.** 사슬이 둘이었다 — `store.py`가 타입 하나
# (`TokenUsage`)를 `modules.llm`에서 가져왔고, `run.py`·`review.py`가 흐름 클래스를 모듈
# 수준에서 올렸다. 앞의 것은 모델을 가벼운 잎(`modules/usage.py`)으로 빼서, 뒤의 것은
# import를 함수 안으로 내려서 끊었다.
KOSPI_DAG_MODULES = (
    "modules.kospi.domain",
    "modules.kospi.state",
    "modules.kospi.common",
    "modules.kospi.forecast",
    "modules.kospi.intraday",
    "modules.kospi.review",
)

# 기대 대비 발표에서 LLM이 없는 쪽. 판정 태스크만 쓰는 코드다.
EXPECTATION_LIGHT_MODULES = (
    "modules.expectation.domain",
    "modules.expectation.judgment",
)

# 공시 알림 DAG이 모듈 수준에서 import하는 것. 강조를 고르는 층은 따로 있고 태스크가
# 늦게 읽는다.
DISCLOSURE_LIGHT_MODULES = ("modules.briefing.disclosures",)

HEAVY_ROOTS = ("langchain", "langchain_core", "langchain_xai", "langgraph")

PROBE = """
import importlib, json, sys
for name in {modules!r}:
    importlib.import_module(name)
roots = {roots!r}
print(json.dumps(sorted({{m for m in sys.modules if m.split('.')[0] in roots}})))
"""


def _heavy_modules_after_importing(modules: tuple[str, ...]) -> list[str]:
    result = subprocess.run(
        [sys.executable, "-c", PROBE.format(modules=list(modules), roots=list(HEAVY_ROOTS))],
        capture_output=True,
        text=True,
        cwd="airflow",
        check=True,
    )
    return json.loads(result.stdout.strip().splitlines()[-1])


def test_the_kospi_dag_modules_do_not_import_langchain():
    """DagBag은 모든 DAG 파일을 주기적으로 다시 파싱하면서 태스크는 돌리지 않는다.

    모듈 수준에 무거운 것이 있으면 전망을 만들지도 않는 파싱이 매번 그 무게를 문다.
    무거운 것은 흐름 모듈(`kospi/generation.py`·`toolbox.py`) 하나에만 있고, 부르는 쪽이
    함수 안에서 늦게 올린다.
    """
    assert _heavy_modules_after_importing(KOSPI_DAG_MODULES) == []


def test_the_token_usage_model_is_reachable_without_langchain():
    """**이 사슬이 되살아나는 자리가 여기다.**

    원장에 쓰는 값이라 저장 층이 봐야 하는데, 그것을 만드는 `modules/llm.py`는 LangChain을
    올린다. `from X import Y`가 `X`를 통째로 실행하므로 이름 하나가 202개를 끌고 왔다.
    """
    assert _heavy_modules_after_importing(("modules.usage",)) == []
    assert _heavy_modules_after_importing(("modules.kospi.store",)) == []


def test_the_expectation_judgment_side_does_not_import_langchain():
    """판정은 LLM이 없다. 추출과 한 파일이던 때는 그 사실이 코드에 드러나지 않았다."""
    assert _heavy_modules_after_importing(EXPECTATION_LIGHT_MODULES) == []


def test_the_disclosure_briefing_query_side_does_not_import_langchain():
    """조회·렌더는 DAG 파일이 최상단에서 끌고 온다. 무거운 것이 섞이면 DagBag이 죽는다."""
    assert _heavy_modules_after_importing(DISCLOSURE_LIGHT_MODULES) == []


def test_the_llm_modules_are_where_the_weight_lives():
    """반대 방향도 잰다. 무거운 것이 아예 없으면 위 테스트들은 아무 것도 지키지 않는다."""
    assert _heavy_modules_after_importing(("modules.kospi.generation",))
    assert _heavy_modules_after_importing(("modules.expectation.extraction",))
    assert _heavy_modules_after_importing(("modules.briefing.disclosure_picks",))
