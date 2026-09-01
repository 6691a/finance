"""Neo4j에 보내는 문장. **파이썬 코드가 아니라 데이터다.**

`airflow/sql/`이 Postgres 쿼리를 파일로 두는 것과 같은 이유로 흐름과 갈랐다 — 그래프
스키마가 바뀌는 이유와 읽기·투영·쓰기 순서가 바뀌는 이유가 다르다. Cypher는 `read_sql`이
읽는 `.sql` 트리에 두지 않는다: 그쪽은 엔진별 폴더(`postgres/`)에 테이블 단위로 나뉘어 있고,
여기는 테이블도 엔진도 다르다.
"""

# 제약은 붙을 때마다 멱등하게 보장한다. Neo4j는 Alembic 대상이 아니라 마이그레이션 파일로
# 관리하지 않는다. `NODE KEY`를 쓰지 않는 이유는 Enterprise 전용이라 community 이미지에서
# `CREATE CONSTRAINT`가 거절되기 때문이다 — 복합 속성 유일성으로 같은 것을 얻는다.
CONSTRAINTS = (
    ("CREATE CONSTRAINT event_key IF NOT EXISTS FOR (e:Event) REQUIRE (e.title, e.occurred_on) IS UNIQUE"),
    "CREATE CONSTRAINT channel_key IF NOT EXISTS FOR (c:Channel) REQUIRE c.name IS UNIQUE",
    ("CREATE CONSTRAINT target_key IF NOT EXISTS FOR (t:Target) REQUIRE (t.kind, t.code) IS UNIQUE"),
)

# MERGE 키에 `path_id`와 `position`이 들어간다. 재적재가 엣지를 누적하지 않게 하는 장치다.
WRITES: tuple[tuple[str, str], ...] = (
    (
        "events",
        "UNWIND $rows AS r MERGE (:Event {title: r.title, occurred_on: r.occurred_on})",
    ),
    ("channels", "UNWIND $rows AS r MERGE (:Channel {name: r.name})"),
    ("targets", "UNWIND $rows AS r MERGE (:Target {kind: r.kind, code: r.code})"),
    (
        "from_event",
        (
            "UNWIND $rows AS r"
            " MATCH (e:Event {title: r.title, occurred_on: r.occurred_on})"
            " MATCH (c:Channel {name: r.channel})"
            " MERGE (e)-[l:LEADS_TO {path_id: r.path_id, position: r.position}]->(c)"
            " SET l.week_start = r.week_start, l.created_at = r.created_at"
            " RETURN count(l) AS merged"
        ),
    ),
    (
        "from_target",
        (
            "UNWIND $rows AS r"
            " MATCH (t:Target {kind: r.src_kind, code: r.src_code})"
            " MATCH (c:Channel {name: r.channel})"
            " MERGE (t)-[l:LEADS_TO {path_id: r.path_id, position: r.position}]->(c)"
            " SET l.week_start = r.week_start, l.created_at = r.created_at, l.sign = r.sign"
            " RETURN count(l) AS merged"
        ),
    ),
    (
        "chain",
        (
            "UNWIND $rows AS r"
            " MATCH (a:Channel {name: r.src})"
            " MATCH (b:Channel {name: r.dst})"
            " MERGE (a)-[l:LEADS_TO {path_id: r.path_id, position: r.position}]->(b)"
            " SET l.week_start = r.week_start, l.created_at = r.created_at"
            " RETURN count(l) AS merged"
        ),
    ),
    (
        "hits",
        (
            "UNWIND $rows AS r"
            " MATCH (c:Channel {name: r.channel})"
            " MATCH (t:Target {kind: r.kind, code: r.code})"
            " MERGE (c)-[h:HITS {path_id: r.path_id}]->(t)"
            " SET h.week_start = r.week_start, h.created_at = r.created_at, h.sign = r.sign,"
            " h.confidence = r.confidence,"
            " h.reasoning = r.reasoning, h.return_unit = r.return_unit,"
            " h.return_week_change = r.return_week_change,"
            " h.return_t1_change = r.return_t1_change,"
            " h.return_t5_change = r.return_t5_change"
            " RETURN count(h) AS merged"
        ),
    ),
)

# MATCH를 지나는 문장. Cypher의 MATCH가 한 행에서 아무 것도 못 찾으면 그 행은 **오류 없이
# 통째로 빠진다** — 엣지 0개, 예외 0개다. 그래서 문장 끝의 `count(...)`로 MERGE에 닿은 행 수를
# 받아 보낸 행 수와 대조한다(2026-08-31 조사 G-59). Neo4j 카운터(`relationships_created`)는
# 재적재에서 0이라 이 대조에 못 쓴다 — MERGE는 이미 있는 엣지를 만들지 않는다.
EDGE_WRITES = frozenset({"from_event", "from_target", "chain", "hits"})
