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

# 코스피의 어휘·순수 함수. LangChain도 Airflow도 모른다.
KOSPI_LIGHT_MODULES = ("modules.kospi.domain", "modules.kospi.state")

# **슬롯 모듈은 아직 가볍지 않다**(2026-09-03 실측). `kospi/store.py`가 `modules.llm`에서
# `TokenUsage` 하나를 가져오는데 그 모듈이 LangChain을 끌고 온다 — `modules.kospi.common`이
# 111개, `forecast`·`intraday`·`review`가 202개를 문다. 옛 추론이 파일을 여섯으로 가르며
# 피했던 형태가 여기 그대로 있다. 고치려면 `TokenUsage`를 가벼운 모듈로 빼야 하고, 그것은
# 삭제 작업과 다른 손잡이라 따로 한다. 그때 이 상수를 지우고 위의 것에 합친다.
KOSPI_SLOT_MODULES = (
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


def test_the_kospi_vocabulary_does_not_import_langchain():
    """어휘와 순수 함수는 어디서 import해도 가벼워야 한다. 채점 함수를 쓰려고 LangChain을
    올리게 되면 그 모듈은 더 이상 순수 함수 자리가 아니다."""
    assert _heavy_modules_after_importing(KOSPI_LIGHT_MODULES) == []


def test_the_kospi_slot_modules_still_carry_langchain():
    """**지금 상태를 적어 둔 것이지 지켜야 할 규칙이 아니다.**

    `kospi/store.py` -> `modules.llm`(`TokenUsage`) -> LangChain이 사슬이다. 끊으면 이
    테스트가 깨지고, 그때 이 함수를 지우고 슬롯 모듈을 위 목록에 넣는다. 그것이 이 테스트의
    쓸모다 — 고쳐졌는데 아무도 모르는 상태를 막는다.
    """
    assert _heavy_modules_after_importing(KOSPI_SLOT_MODULES) != []


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
