"""인과 그래프 Neo4j 투영. 실제 Neo4j는 안 띄운다.

가짜 드라이버/세션으로 **무엇이 실렸는지**를 본다. 그래프 DB를 붙이는 것 자체는 테스트할
값어치가 없고, 투영에서 틀릴 수 있는 것은 셋이다 — 경로를 엣지로 펴는 규칙, MERGE 키에
`path_id`가 빠지는 것, 그리고 예외를 재시도 가능/불가능으로 가르는 것.

SQL 컬럼 순서 대조가 하나 더 있다. `read_week`가 인덱스로 읽으므로 SQL의 SELECT 목록이
바뀌면 조용히 값이 밀린다.
"""

import pathlib
import re
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any, Self

import pytest
from neo4j.exceptions import ClientError, ServiceUnavailable, TransientError

from modules import graph

SQL_ROOT = pathlib.Path(__file__).resolve().parents[2] / "airflow" / "sql" / "postgres"

WEEK = date(2026, 8, 10)
# 경로가 Postgres에 생긴 시각. `W+2` 월요일 07:00 KST에 돈 것처럼 둔다.
CREATED = datetime(2026, 8, 23, 22, 0, tzinfo=UTC)


def path_row(**overrides: Any) -> graph.CausalPathRow:
    """사건에서 출발한 경로 하나. 기본값은 단계 하나짜리다."""
    values: dict[str, Any] = {
        "path_id": 1,
        "week_start": WEEK,
        "created_at": CREATED,
        "event_title": "미국 물가 둔화",
        "event_occurred_on": date(2026, 8, 12),
        "source_target_kind": None,
        "source_target_code": None,
        "source_sign": None,
        "target_kind": "instrument",
        "target_code": "005930",
        "sign": "up",
        "confidence": "plausible",
        "reasoning": "할인율이 내려 밸류에이션을 밀어 올렸다",
        "return_week_change": Decimal("1.25"),
        "return_t1_change": Decimal("0.40"),
        "return_t5_change": Decimal("-0.10"),
        "return_unit": "percent",
    }
    values.update(overrides)
    return graph.CausalPathRow(**values)


def linked_row(**overrides: Any) -> graph.CausalPathRow:
    """대상에서 출발한 경로. 링커가 내는 모양이다."""
    return path_row(
        path_id=2,
        event_title=None,
        event_occurred_on=None,
        source_target_kind="quote",
        source_target_code="US10Y",
        source_sign="down",
        target_code="000660",
        **overrides,
    )


def steps(*names: str, path_id: int = 1) -> list[graph.CausalStepRow]:
    return [
        graph.CausalStepRow(path_id=path_id, position=index, channel=name) for index, name in enumerate(names, start=1)
    ]


class FakeResult:
    """`transaction.run`이 주는 것 중 우리가 읽는 것 — `RETURN count(...) AS merged` 한 행."""

    def __init__(self, merged: int) -> None:
        self._merged = merged

    def single(self) -> dict[str, int]:
        return {"merged": self._merged}


class FakeTransaction:
    def __init__(self) -> None:
        self.calls: list[tuple[str, list[dict]]] = []
        # 문장 조각 → 그 문장이 실제로 MERGE한 행 수. 없으면 보낸 만큼 전부 됐다고 답한다.
        self.merged: dict[str, int] = {}

    def run(self, statement: str, **parameters: Any) -> FakeResult:
        rows = parameters.get("rows", [])
        self.calls.append((statement, rows))
        merged = next((count for fragment, count in self.merged.items() if fragment in statement), len(rows))
        return FakeResult(merged)


class FakeSession:
    def __init__(self, driver: "FakeDriver") -> None:
        self._driver = driver

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def run(self, statement: str, **parameters: Any) -> None:
        if self._driver.session_error is not None:
            raise self._driver.session_error
        self._driver.constraints.append(statement)

    def execute_write(self, unit_of_work: Any, *args: Any) -> None:
        if self._driver.write_error is not None:
            raise self._driver.write_error
        unit_of_work(self._driver.transaction, *args)


class FakeDriver:
    def __init__(self) -> None:
        self.constraints: list[str] = []
        self.transaction = FakeTransaction()
        self.session_error: Exception | None = None
        self.write_error: Exception | None = None
        self.kwargs: dict[str, Any] = {}

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def session(self) -> FakeSession:
        return FakeSession(self)


@pytest.fixture
def driver(monkeypatch: pytest.MonkeyPatch) -> FakeDriver:
    fake = FakeDriver()

    def factory(uri: str, **kwargs: Any) -> FakeDriver:
        fake.kwargs = {"uri": uri, **kwargs}
        return fake

    monkeypatch.setattr(graph.GraphDatabase, "driver", factory)
    return fake


class FakeCursor:
    def __init__(self, connection: "FakeConnection") -> None:
        self._connection = connection
        self._rows: list[tuple] = []

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def execute(self, statement: str, parameters: Any = ()) -> None:
        self._connection.calls.append((statement, parameters))
        if "market_causal_step" in statement and "position" in statement:
            self._rows = list(self._connection.steps)
        elif "DISTINCT week_start" in statement:
            self._rows = list(self._connection.weeks)
        else:
            self._rows = list(self._connection.paths)

    def fetchall(self) -> list[tuple]:
        return self._rows


class FakeConnection:
    def __init__(
        self,
        paths: list[tuple] | None = None,
        steps: list[tuple] | None = None,
        weeks: list[tuple] | None = None,
    ) -> None:
        self.paths = paths or []
        self.steps = steps or []
        self.weeks = weeks or []
        self.calls: list[tuple[str, Any]] = []

    def cursor(self) -> FakeCursor:
        return FakeCursor(self)


# --- 투영 규칙 -------------------------------------------------------------


def test_event_path_becomes_one_edge_per_step_plus_one():
    payload = graph.project([path_row()], steps("할인율"))

    assert payload.node_count == 3  # Event + Channel + Target
    assert payload.edge_count == 2  # Event→Channel, Channel→Target
    assert [edge.channel for edge in payload.from_event] == ["할인율"]
    assert payload.chain == ()
    assert [(edge.channel, edge.code) for edge in payload.hits] == [("할인율", "005930")]


def test_two_step_chain_adds_a_channel_to_channel_edge():
    payload = graph.project([path_row()], steps("금리 기대", "할인율"))

    assert payload.edge_count == 3
    assert [(edge.src, edge.dst) for edge in payload.chain] == [("금리 기대", "할인율")]
    # 마지막 단계가 대상을 문다. 첫 단계가 아니다.
    assert [edge.channel for edge in payload.hits] == ["할인율"]


def test_target_sourced_path_starts_at_a_target_node():
    payload = graph.project([linked_row()], steps("할인율", path_id=2))

    assert payload.from_event == ()
    assert [(edge.src_kind, edge.src_code, edge.sign) for edge in payload.from_target] == [("quote", "US10Y", "down")]
    # 원인 대상과 결과 대상이 둘 다 노드다. 이것이 주를 잇는 장치다.
    assert {(node.kind, node.code) for node in payload.targets} == {
        ("quote", "US10Y"),
        ("instrument", "000660"),
    }


def test_shared_channel_is_one_node_across_paths():
    payload = graph.project(
        [path_row(), linked_row()],
        steps("할인율") + steps("할인율", path_id=2),
    )

    assert [node.name for node in payload.channels] == ["할인율"]
    # 노드는 하나여도 엣지는 경로마다 따로다. `path_id`가 그것을 가른다.
    assert {edge.path_id for edge in payload.hits} == {1, 2}


def test_every_edge_carries_path_id_week_start_and_created_at():
    """`path_id`가 빠지면 서로 다른 주장이 채널 노드에서 섞인다(설계 §7.8 발견 ①).

    `week_start`가 빠지면 조회가 시각 역행을 막을 수 없다(발견 ②). `created_at`이 빠지면
    추론 툴이 슬롯 시각 뒤에 생긴 경로를 본다(17-graph-query.md §5.3).
    """
    payload = graph.project([path_row()], steps("금리 기대", "할인율"))

    edges = [*payload.from_event, *payload.from_target, *payload.chain, *payload.hits]
    assert edges
    for edge in edges:
        assert edge.path_id == 1
        assert edge.week_start == WEEK
        assert edge.created_at == CREATED


def test_path_without_steps_is_an_error():
    with pytest.raises(graph.GraphError, match="no steps"):
        graph.project([path_row()], [])


def test_path_without_a_source_is_an_error():
    orphan = path_row(event_title=None, event_occurred_on=None)
    with pytest.raises(graph.GraphError, match="neither an event nor a source target"):
        graph.project([orphan], steps("할인율"))


# --- 쓰기 ------------------------------------------------------------------


def test_constraints_run_before_the_merges(driver: FakeDriver):
    graph.write_graph("bolt://x:7687", ("neo4j", "pw"), graph.project([path_row()], steps("할인율")))

    assert len(driver.constraints) == len(graph.CONSTRAINTS)
    assert all("IS UNIQUE" in statement for statement in driver.constraints)
    # NODE KEY는 Enterprise 전용이라 community 이미지가 거절한다.
    assert not any("NODE KEY" in statement for statement in driver.constraints)
    assert driver.transaction.calls


def test_driver_retry_is_disabled(driver: FakeDriver):
    graph.write_graph("bolt://x:7687", ("neo4j", "pw"), graph.project([path_row()], steps("할인율")))

    assert driver.kwargs["max_transaction_retry_time"] == 0
    assert driver.kwargs["auth"] == ("neo4j", "pw")


def test_decimals_become_floats_and_dates_stay_dates(driver: FakeDriver):
    """드라이버 매핑에 `Decimal`이 없다. `date`와 aware `datetime`은 그대로 간다."""
    graph.write_graph("bolt://x:7687", ("neo4j", "pw"), graph.project([path_row()], steps("할인율")))

    rows = next(rows for statement, rows in driver.transaction.calls if "HITS" in statement)
    assert isinstance(rows[0]["return_week_change"], float)
    assert rows[0]["return_week_change"] == pytest.approx(1.25)
    assert rows[0]["week_start"] == WEEK
    assert isinstance(rows[0]["week_start"], date)
    assert rows[0]["created_at"] == CREATED
    assert rows[0]["created_at"].tzinfo is not None


def test_empty_row_sets_are_not_sent(driver: FakeDriver):
    graph.write_graph("bolt://x:7687", ("neo4j", "pw"), graph.project([path_row()], steps("할인율")))

    sent = [statement for statement, rows in driver.transaction.calls]
    # 단계가 하나뿐이라 채널→채널 엣지가 없다. 빈 UNWIND를 보내지 않는다.
    assert not any("MATCH (a:Channel" in statement for statement in sent)


def test_merge_keys_include_path_id():
    keys = dict(graph.WRITES)
    assert "path_id: r.path_id, position: r.position" in keys["from_event"]
    assert "path_id: r.path_id, position: r.position" in keys["from_target"]
    assert "path_id: r.path_id, position: r.position" in keys["chain"]
    assert "MERGE (c)-[h:HITS {path_id: r.path_id}]->(t)" in keys["hits"]


# --- 예외 분류 -------------------------------------------------------------


@pytest.mark.parametrize(
    "error",
    [ServiceUnavailable("down"), TransientError("busy")],
)
def test_transient_failures_become_connection_errors(driver: FakeDriver, error: Exception):
    driver.session_error = error
    with pytest.raises(ConnectionError):
        graph.write_graph("bolt://x:7687", ("neo4j", "pw"), graph.project([path_row()], steps("할인율")))


def test_client_errors_become_graph_errors(driver: FakeDriver):
    driver.write_error = ClientError("constraint violated")
    with pytest.raises(graph.GraphError):
        graph.write_graph("bolt://x:7687", ("neo4j", "pw"), graph.project([path_row()], steps("할인율")))


# --- SQL 대조 --------------------------------------------------------------


def _select_columns(path: pathlib.Path) -> list[str]:
    """SELECT 목록의 바깥 이름만 뽑는다. `AS` 별칭이 있으면 그것이 이름이다."""
    text = re.sub(r"--[^\n]*", "", path.read_text())
    body = text.split("SELECT", 1)[1].split("FROM", 1)[0]
    names = []
    for item in body.split(","):
        item = item.strip()
        if not item:
            continue
        names.append(item.split()[-1] if " AS " in f" {item} " else item.split(".")[-1])
    return names


def test_path_sql_columns_match_the_positional_read():
    columns = _select_columns(SQL_ROOT / "market_causal_path" / "select_graph_by_week.sql")
    assert columns == list(graph.CausalPathRow.model_fields)


def test_step_sql_columns_match_the_positional_read():
    columns = _select_columns(SQL_ROOT / "market_causal_step" / "select_by_week.sql")
    assert columns == list(graph.CausalStepRow.model_fields)


def test_read_week_maps_rows_by_position():
    connection = FakeConnection(
        paths=[
            (
                1,
                WEEK,
                CREATED,
                "미국 물가 둔화",
                date(2026, 8, 12),
                None,
                None,
                None,
                "instrument",
                "005930",
                "up",
                "plausible",
                "이유",
                Decimal("1.25"),
                Decimal("0.40"),
                Decimal("-0.10"),
                "percent",
            )
        ],
        steps=[(1, 1, "할인율")],
    )

    paths, step_rows = graph.read_week(connection, WEEK)

    assert paths[0].target_code == "005930"
    assert paths[0].created_at == CREATED
    assert paths[0].return_unit == "percent"
    assert step_rows[0].channel == "할인율"


def test_stored_weeks_returns_dates():
    connection = FakeConnection(weeks=[(WEEK,), (date(2026, 8, 17),)])
    assert graph.stored_weeks(connection) == [WEEK, date(2026, 8, 17)]


# --- 투영 대조 (G-59) --------------------------------------------------------


def test_an_edge_whose_match_found_no_node_is_an_error(driver: FakeDriver):
    """Cypher의 MATCH가 못 찾은 행은 오류 없이 통째로 빠진다. 전에는 로그가 보내려던 수를
    찍어 "N개 투영"인데 그래프는 비어 있을 수 있었다."""
    driver.transaction.merged["HITS"] = 0

    with pytest.raises(graph.GraphError, match="hits.*sent 1.*merged 0"):
        graph.write_graph("bolt://x:7687", ("neo4j", "pw"), graph.project([path_row()], steps("할인율")))


def test_edge_statements_return_their_merged_count():
    """대조의 재료는 Neo4j 카운터가 아니라 문장 자체가 세어 주는 행 수다 — MERGE는 재적재에서
    0을 만들지만 `count(l)`은 MATCH를 통과한 행마다 하나씩 센다."""
    statements = dict(graph.WRITES)
    for key in graph.EDGE_WRITES:
        assert statements[key].endswith(" AS merged"), key
    for key in ("events", "channels", "targets"):
        assert "RETURN" not in statements[key], key


def test_a_full_projection_passes_the_check(driver: FakeDriver):
    graph.write_graph("bolt://x:7687", ("neo4j", "pw"), graph.project([path_row()], steps("할인율", "밸류에이션")))

    assert len(driver.transaction.calls) == 6  # 노드 셋 + from_event·chain·hits
