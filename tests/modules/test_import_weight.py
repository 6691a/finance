"""DAG이 모듈 수준에서 끌고 오는 무게.

**DagBag은 파일 하나를 30초 안에 import해야 한다.** LangChain·LangGraph는 그것만으로
몇 초를 쓰므로, 슬롯 모듈과 `thesis.common`이 모듈 수준에서 그것을 끌고 오면 스케줄러가
DAG을 못 읽는다. 무거운 것은 `thesis.toolbox`·`thesis.generation`·`thesis.outcomes`에 있고
부르는 쪽이 함수 안에서 늦게 import한다.

`thesis.py`가 한 파일이던 때는 `ThesisSubjectKind` 하나를 쓰려 해도 전체가 딸려 와서
`observed_state`가 모듈 객체를 인자로 받는 우회를 하고 있었다(2026-08-25 분리로 없어졌다).

`expectation`도 같은 이유로 갈랐다(2026-08-25). 판정은 LLM이 없는데 추출과 한 파일이라
판정만 쓰는 쪽도 LangChain을 끌고 왔다.

**별도 인터프리터에서 잰다.** 같은 프로세스에서 재면 다른 테스트가 이미 import해 둔 것이
섞여 언제나 통과한다.
"""

import json
import subprocess
import sys

# 추론 DAG 태스크가 모듈 수준에서 import하는 것들.
THESIS_DAG_MODULES = (
    "modules.thesis.common",
    "modules.thesis.domain",
    "modules.thesis.forecast",
    "modules.thesis.review",
    "modules.thesis.nxt_review",
)

# 기대 대비 발표에서 LLM이 없는 쪽. 판정 태스크만 쓰는 코드다.
EXPECTATION_LIGHT_MODULES = (
    "modules.expectation.domain",
    "modules.expectation.judgment",
)

# 주간 인과 그래프 DAG이 모듈 수준에서 import하는 것. 후보 조립과 저장은 연결과 SQL만
# 알고, LLM은 `causal.generation`에만 있다.
CAUSAL_LIGHT_MODULES = (
    "modules.causal.domain",
    "modules.causal.candidates",
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


def test_the_thesis_dag_modules_do_not_import_langchain():
    """이것이 `thesis.py`를 여섯으로 가른 이유다. 되돌아가면 여기서 죽는다."""
    assert _heavy_modules_after_importing(THESIS_DAG_MODULES) == []


def test_the_expectation_judgment_side_does_not_import_langchain():
    """판정은 LLM이 없다. 추출과 한 파일이던 때는 그 사실이 코드에 드러나지 않았다."""
    assert _heavy_modules_after_importing(EXPECTATION_LIGHT_MODULES) == []


def test_the_disclosure_briefing_query_side_does_not_import_langchain():
    """조회·렌더는 DAG 파일이 최상단에서 끌고 온다. 무거운 것이 섞이면 DagBag이 죽는다."""
    assert _heavy_modules_after_importing(DISCLOSURE_LIGHT_MODULES) == []


def test_the_causal_graph_query_side_does_not_import_langchain():
    """`domain`은 순수 함수만, `candidates`는 연결과 SQL만 안다. 그것이 이 배치의 핵심이다."""
    assert _heavy_modules_after_importing(CAUSAL_LIGHT_MODULES) == []


def test_the_llm_modules_are_where_the_weight_lives():
    """반대 방향도 잰다. 무거운 것이 아예 없으면 위 테스트들은 아무 것도 지키지 않는다."""
    assert _heavy_modules_after_importing(("modules.thesis.generation",))
    assert _heavy_modules_after_importing(("modules.expectation.extraction",))
    assert _heavy_modules_after_importing(("modules.briefing.disclosure_picks",))
    assert _heavy_modules_after_importing(("modules.causal.generation",))
