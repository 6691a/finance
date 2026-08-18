"""문서 평가 대시보드가 저장 계약과 어긋나지 않는지 확인한다.

패널 SQL은 코드가 아니라 JSON 문자열이라 리팩터링이 따라오지 않는다. 그래서 **모델이 정한
값 집합과 평가기가 정한 점수 범위**를 여기서 대조한다. `Direction`에 값을 추가하고 화면을
빠뜨리면 그 방향의 문서만 조용히 안 보인다.
"""

import json
from pathlib import Path

import pytest

from apps.models.content import Direction

DASHBOARD = Path(__file__).resolve().parents[2] / "compose/local/grafana/dashboards/document-assessment.json"


@pytest.fixture(scope="module")
def dashboard() -> dict:
    return json.loads(DASHBOARD.read_text())


def panel_sql(dashboard: dict) -> dict[int, str]:
    return {
        panel["id"]: target["rawSql"]
        for panel in dashboard["panels"]
        for target in panel.get("targets", [])
        if "rawSql" in target
    }


def test_uid_and_variables_stay_put(dashboard):
    # uid를 바꾸면 Grafana가 같은 파일로 두 번째 대시보드를 만들고 갱신이 막힌다.
    assert dashboard["uid"] == "document-assessment"
    assert [variable["name"] for variable in dashboard["templating"]["list"]] == ["direction", "min_score"]


def test_the_direction_variable_covers_every_stored_direction(dashboard):
    # 화면에 없는 방향의 문서는 어느 패널에도 나오지 않는다. Enum에 값을 더하면 여기가 먼저 깨진다.
    variable = next(v for v in dashboard["templating"]["list"] if v["name"] == "direction")
    values = {option.split(":")[-1].strip() for option in variable["query"].split(",")}

    assert values == {member.value for member in Direction}


def test_the_score_histogram_spans_the_whole_range(dashboard):
    """0~8 전체를 그린다. 네 항목이 각각 0~2점이라 합계 상한이 8이다.

    빈 점수 칸을 빼면 몰려 있는 분포가 몰려 있어 보이지 않는다. 그래서 관측값이 아니라
    `generate_series`가 축을 만든다.
    """
    from modules.assessment import SCORE_FIELDS

    maximum = len(SCORE_FIELDS) * 2

    assert f"generate_series(0, {maximum})" in panel_sql(dashboard)[6]


def test_indicator_tags_are_shown_with_their_provider(dashboard):
    # series_id는 제공처 안에서만 고유하다. provider 없이 보여 주면 제공처가 늘 때 같은 이름이 겹친다.
    for sql in (panel_sql(dashboard)[7], panel_sql(dashboard)[8]):
        assert "provider || ':' || " in sql


def test_no_panel_filters_documents_out_by_score_in_the_aggregates(dashboard):
    """집계 패널은 점수로 문서를 거르지 않는다.

    이 프로젝트는 문서를 점수로 버리지 않는다. 분포·태그·출처 패널이 `min_score`를 함께
    걸면 화면이 "낮은 점수가 없다"고 말하게 되어 점수가 눌린 것을 못 잡는다.
    """
    sql = panel_sql(dashboard)

    assert "$min_score" in sql[7]
    assert not any("$min_score" in sql[panel_id] for panel_id in (1, 2, 3, 4, 5, 6, 8, 9))
