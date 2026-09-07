"""읽는 Cypher와 쓰는 Cypher가 같은 그래프를 말하는가.

**두 트리는 서로를 import하지 않는다** — `apps/`는 `/opt/airflow`를 보지 못한다. 그래서
Cypher가 한 벌씩 있고, 라벨·속성 이름이 어긋나면 화면이 조용히 0건이 된다. 파일을 글자로
읽어 대조하는 이유가 그것이다.
"""

import pathlib
from datetime import date

import pytest

from apps.api.repository import UnsafeCypher, ensure_read_only
from apps.api.repository.kospi_graph import MEMORIES, OBSERVATIONS, RELATION_LOOKBACK_DAYS

WRITER = pathlib.Path("airflow/modules/kospi/graph.py")
DOMAIN = pathlib.Path("airflow/modules/kospi/domain.py")


def writer_source() -> str:
    return WRITER.read_text(encoding="utf-8")


def test_the_read_queries_pass_the_guard():
    """가드가 코드에 있는 이유는 community 판에 읽기 전용 계정을 만들 수 없어서다."""
    assert ensure_read_only(OBSERVATIONS)
    assert ensure_read_only(MEMORIES)


def test_a_write_disguised_as_a_read_is_refused():
    with pytest.raises(UnsafeCypher):
        ensure_read_only("MATCH (m:Memory) SET m.text = 'x' RETURN m")


def test_the_labels_and_edge_type_match_the_writer():
    """쓰는 쪽이 `Factor -[:OBSERVED]-> Index`를 만든다. 하나라도 다르면 0건이다."""
    source = writer_source()

    for fragment in ("(f:Factor {code:", "[o:OBSERVED {date:", "(i:Index {code:", "(m:Memory"):
        assert fragment in source
    assert "(f:Factor)-[o:OBSERVED]->(:Index {code: $index})" in OBSERVATIONS
    assert "(m:Memory)" in MEMORIES
    assert "(m)-[:ABOUT]->(f:Factor)" in MEMORIES


def test_the_edge_properties_we_read_are_the_ones_the_writer_sets():
    source = writer_source()

    for name in ("sign", "strength", "note", "llm_run_id"):
        assert f"o.{name}" in source, name
        assert f"o.{name}" in OBSERVATIONS, name


def test_the_memory_properties_we_read_are_the_ones_the_writer_sets():
    """쓰는 쪽은 `CREATE (m:Memory {...})`로 한 번에 세워서 `m.<이름>` 꼴이 아닌 것이 있다.
    그래서 이름만 대조한다."""
    source = writer_source()

    for name in (
        "created_on",
        "text",
        "verify_count",
        "unreviewed_count",
        "retired_on",
        "retire_reason",
        "llm_run_id",
    ):
        assert name in source, name
        assert f"m.{name}" in MEMORIES, name


def test_the_guard_does_not_trip_on_a_property_that_contains_a_keyword():
    """`m.created_on`이 `CREATE`로 읽혀 메모 조회가 막힌 적이 있다(2026-09-03)."""
    assert ensure_read_only("MATCH (m:Memory) RETURN m.created_on, m.retired_on")


def test_the_lookback_window_matches_the_airflow_constant():
    """창이 다르면 화면의 가중치가 프롬프트의 것과 다른 관측을 본다."""
    source = DOMAIN.read_text(encoding="utf-8")

    assert f"RELATION_LOOKBACK_DAYS = {RELATION_LOOKBACK_DAYS}" in source


def test_the_screen_labels_cover_the_stored_values():
    """**화면 라벨이 저장 값과 어긋나면 조용히 영문 코드가 보인다.**

    `labelOf`가 모르는 값을 그대로 보여 화면이 죽지는 않는다 — 그래서 테스트가 아니면 안
    드러난다. 2026-09-03에 `retire_reason`을 `drop`으로 적어 두고 실제 값이 `dropped`인
    것을 운영 응답에서야 봤다.
    """
    labels = pathlib.Path("frontend/src/labels.ts").read_text(encoding="utf-8")
    source = DOMAIN.read_text(encoding="utf-8")

    for enum, block in (
        ("RunSlot", "FORECAST_SLOTS"),
        ("ObservationSign", "OBSERVATION_SIGNS"),
        ("RetireReason", "RETIRE_REASONS"),
    ):
        body = source[source.index(f"class {enum}(StrEnum):") :].split("\n\n\n")[0]
        stored = {line.split('"')[1] for line in body.splitlines() if " = \"" in line}
        drawn = {
            line.split(":")[0].strip().strip('"')
            for line in labels[labels.index(f"export const {block}: Labels = {{") :]
            .split("};")[0]
            .splitlines()[1:]
            if ":" in line
        }
        assert stored, enum
        assert stored <= drawn, (enum, stored - drawn)


def test_the_factor_vocabulary_matches_the_airflow_one():
    """**어휘가 갈리면 화면이 영문 코드를 보인다.**

    `labelOf`처럼 서비스도 모르는 코드를 그대로 흘려서 죽지 않는다 — 그래서 테스트가
    아니면 안 드러난다. 2026-09-04에 `KOSPI`(코스피 자체)가 늘었다.
    """
    from apps.api.service.relation import FACTOR_LABELS

    source = DOMAIN.read_text(encoding="utf-8")
    body = source[source.index("class Factor(StrEnum):") :].split("\n\n\n")[0]
    stored = {line.split('"')[1] for line in body.splitlines() if " = \"" in line}

    assert stored == set(FACTOR_LABELS)


def test_the_index_itself_is_not_a_relation_node():
    """지수가 자기와 같은 방향인 것은 언제나 참이라 엣지를 쌓으면 뜻 없는 값이 하나 박힌다."""
    from apps.api.service.relation import INDEX_SELF, build_relations

    source = DOMAIN.read_text(encoding="utf-8")

    # 원본도 같은 판단을 한다 — `RELATION_FACTORS`가 `INDEX_SELF` 요인을 뺀다.
    assert "RELATION_FACTORS" in source
    assert "FactorSource.INDEX_SELF" in source
    at = date(2026, 9, 4)

    assert INDEX_SELF not in {item.factor for item in build_relations((), as_of_date=at)}
