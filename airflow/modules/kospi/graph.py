"""관계와 메모의 원본 — Neo4j 읽기·쓰기.

**Postgres에 관계 테이블을 두지 않는다**(사용자 결정, 2026-09-02). 전망과 원장만 Postgres이고
관계·메모는 여기가 원본이다. 그래서 이 모듈이 죽으면 그 값이 없어진다 — 투영이 아니다.

## 그래프 모양

```
(:Factor {code, label})-[:OBSERVED {date, sign, strength, note, llm_run_id}]->(:Index {code:'KOSPI'})
(:Memory {id, created_on, text, verify_count, unreviewed_count, last_verified_on,
          retired_on, retire_reason, llm_run_id})-[:ABOUT]->(:Factor)
```

- **하루에 요인당 엣지 하나.** `MERGE ... {date}` + `ON CREATE SET`이라 같은 날 재실행은
  덮지 않는다. 쌓이는 것이지 갱신되는 것이 아니다.
- **옛 관측을 지우지 않는다.** 무게만 준다(`domain.decay_weight`).
- **내린 메모의 노드를 지우지 않는다.** `retired_on`과 이유가 남아 무엇을 왜 지웠는지 본다.
- 라벨이 옛 인과 그래프(`Event`·`Channel`·`Target`)와 겹치지 않는다. 옛것을 지울 때 새것이
  같이 지워지지 않는다.

## 가중치는 파이썬이 계산한다

Cypher가 관측을 주고 `domain.relation_weight`가 접는다. 집계를 Cypher에 넣으면 감쇠 식을
DB 없이 테스트할 수 없다 — 이 저장소는 테스트에서 실 DB를 쓰지 않는다.

## 쓰기는 트랜잭션 하나다

관찰·새 메모·메모 판정이 반씩 들어가면 다음 날 관계 표와 메모가 서로 다른 실행을 가리킨다.
"""

import logging
from collections.abc import Sequence
from datetime import date, datetime

from neo4j import Driver, GraphDatabase
from neo4j.exceptions import ClientError, Neo4jError, ServiceUnavailable, SessionExpired, TransientError
from pydantic import BaseModel, ConfigDict

from modules.kospi.domain import (
    INDEX_CODE,
    INDEX_LABEL,
    MAX_STRENGTH,
    MIN_STRENGTH,
    RELATION_FACTORS,
    RELATION_LOOKBACK_DAYS,
    Factor,
    Observation,
    ObservationSign,
    RelationWeight,
    RetireReason,
    factor_label,
    relation_weight,
)

logger = logging.getLogger(__name__)


class GraphError(RuntimeError):
    """Neo4j가 거절했고 다시 불러도 같은 결과다(인증·쿼리 오류)."""


# 제약은 붙을 때마다 멱등하게 보장한다. Neo4j는 Alembic 대상이 아니라 마이그레이션 파일로
# 관리하지 않는다. `NODE KEY`는 Enterprise 전용이라 community 이미지가 거절한다 —
# 복합 속성 유일성으로 같은 것을 얻는다.
CONSTRAINTS = (
    "CREATE CONSTRAINT kospi_factor_key IF NOT EXISTS FOR (f:Factor) REQUIRE f.code IS UNIQUE",
    "CREATE CONSTRAINT kospi_index_key IF NOT EXISTS FOR (i:Index) REQUIRE i.code IS UNIQUE",
    "CREATE CONSTRAINT kospi_memory_key IF NOT EXISTS FOR (m:Memory) REQUIRE m.id IS UNIQUE",
    "CREATE CONSTRAINT kospi_counter_key IF NOT EXISTS FOR (c:Counter) REQUIRE c.name IS UNIQUE",
)

# 요인 노드는 코드 상수에서 온다. 그래프에 먼저 세워 두면 관측이 0인 요인도 조회에 잡혀
# "관측 없음"으로 표에 실린다 — 빈 칸이 "관계가 없다"로 읽히지 않게 하는 자리다.
SEED_FACTORS = (
    "UNWIND $rows AS r MERGE (f:Factor {code: r.code}) SET f.label = r.label",
)

SEED_INDEX = "MERGE (i:Index {code: $code}) SET i.label = $label"

# **`o.date`가 아니라 `o.created_at`으로 자른다.** `date`는 관찰한 거래일이고 엣지는 그날
# 19:00에 만들어진다 — 날짜로 자르면 같은 날 08:35 장전 전망이 그날 저녁의 관찰을 본다.
# 운영에서는 장후가 아직 안 돌아 우연히 안 물리지만 과거를 돌리는 순간 물린다.
READ_OBSERVATIONS = """
MATCH (f:Factor)-[o:OBSERVED]->(:Index {code: $index})
WHERE o.created_at <= $as_of_at AND o.date >= $window_start
RETURN f.code AS code, o.date AS date, o.sign AS sign, o.strength AS strength,
       coalesce(o.note, '') AS note
ORDER BY code, date DESC
"""

# 시각 컷오프가 관측 엣지와 같은 이유로 필요하다. **다만 둘이 다른 자리가 하나 있다** —
# 그 시점에 살아 있던 메모는 나중에 내려갔어도 그때는 보였다. `retired_on`이 기준일 이후면
# 아직 활성으로 친다.
#
# `created_at`이 없는 옛 행은 **날짜가 엄격히 앞선 것만** 인정한다. 보수적으로 자르는 쪽이
# 미래를 흘리는 것보다 낫다.
READ_MEMORIES = """
MATCH (m:Memory)
WHERE (m.retired_on IS NULL OR m.retired_on >= $as_of_date)
  AND CASE WHEN m.created_at IS NULL THEN m.created_on < $as_of_date
           ELSE m.created_at <= $as_of_at END
OPTIONAL MATCH (m)-[:ABOUT]->(f:Factor)
RETURN m.id AS id, m.created_on AS created_on, m.text AS text,
       coalesce(m.verify_count, 0) AS verify_count,
       coalesce(m.unreviewed_count, 0) AS unreviewed_count,
       f.code AS factor
ORDER BY m.created_on, m.id
"""

WRITE_OBSERVATIONS = """
UNWIND $rows AS r
MATCH (i:Index {code: $index})
MERGE (f:Factor {code: r.code})
  ON CREATE SET f.label = r.label
MERGE (f)-[o:OBSERVED {date: $observed_on}]->(i)
  ON CREATE SET o.sign = r.sign, o.strength = r.strength, o.note = r.note,
                o.llm_run_id = $llm_run_id, o.created_at = $created_at
RETURN count(o) AS touched
"""

# 메모 id는 카운터 노드가 준다. Neo4j에 시퀀스가 없고, `elementId`는 재적재에서 바뀌며
# 모델이 답에 다시 적기에도 길다. 짧은 정수가 프롬프트와 검증 양쪽에 낫다.
NEXT_MEMORY_ID = """
MERGE (c:Counter {name: 'kospi_memory'})
  ON CREATE SET c.value = 0
SET c.value = c.value + $count
RETURN c.value AS value
"""

WRITE_MEMORIES = """
UNWIND $rows AS r
CREATE (m:Memory {id: r.id, created_on: $created_on, created_at: $created_at, text: r.text,
                  reason: r.reason, verify_count: 0, unreviewed_count: 0,
                  retired_on: NULL, retire_reason: NULL, llm_run_id: $llm_run_id})
WITH m, r
CALL {
  WITH m, r
  MATCH (f:Factor {code: r.factor})
  MERGE (m)-[:ABOUT]->(f)
  RETURN 1 AS linked
}
RETURN count(m) AS created
"""

# 요인이 없는 메모는 위 문장의 `CALL`이 행을 버리므로 따로 만든다. 서브쿼리가 0행을
# 돌려주면 그 행이 통째로 사라지는 것이 Cypher의 규칙이라, 링크를 선택으로 두려면
# 문장을 나누는 것이 유일한 방법이다.
WRITE_MEMORIES_UNLINKED = """
UNWIND $rows AS r
CREATE (m:Memory {id: r.id, created_on: $created_on, created_at: $created_at, text: r.text,
                  reason: r.reason, verify_count: 0, unreviewed_count: 0,
                  retired_on: NULL, retire_reason: NULL, llm_run_id: $llm_run_id})
RETURN count(m) AS created
"""

KEEP_MEMORIES = """
UNWIND $ids AS id
MATCH (m:Memory {id: id})
WHERE m.retired_on IS NULL
SET m.verify_count = coalesce(m.verify_count, 0) + 1,
    m.last_verified_on = $reviewed_on,
    m.unreviewed_count = 0
RETURN count(m) AS updated
"""

RETIRE_MEMORIES = """
UNWIND $rows AS r
MATCH (m:Memory {id: r.id})
WHERE m.retired_on IS NULL
SET m.retired_on = $retired_on, m.retire_reason = r.reason, m.retire_note = r.note
RETURN count(m) AS updated
"""

MARK_UNREVIEWED = """
UNWIND $ids AS id
MATCH (m:Memory {id: id})
WHERE m.retired_on IS NULL
SET m.unreviewed_count = coalesce(m.unreviewed_count, 0) + 1
RETURN count(m) AS updated
"""


class StoredMemory(BaseModel):
    """그래프에 있는 활성 메모 하나."""

    model_config = ConfigDict(frozen=True)

    id: int
    created_on: date
    text: str
    verify_count: int = 0
    unreviewed_count: int = 0
    factor: Factor | None = None


class NewMemory(BaseModel):
    """이번 관찰이 쓰는 메모 하나. 검증을 통과한 것만 여기 온다."""

    model_config = ConfigDict(frozen=True)

    text: str
    reason: str
    factor: Factor | None = None


class RetiredMemory(BaseModel):
    """내리는 메모 하나. **이유를 남긴다** — 모델이 정한 것과 코드가 정한 것을 가른다."""

    model_config = ConfigDict(frozen=True)

    id: int
    reason: RetireReason
    note: str = ""


class ObservationWrite(BaseModel):
    """오늘의 관찰 하나. 그대로 `OBSERVED` 엣지가 된다."""

    model_config = ConfigDict(frozen=True)

    factor: Factor
    sign: ObservationSign
    strength: int
    note: str = ""


class GraphWriteResult(BaseModel):
    """쓰기 한 번이 실제로 무엇을 바꿨나. 원장과 Slack이 이 숫자를 쓴다."""

    model_config = ConfigDict(frozen=True)

    observations: int = 0
    memories_written: int = 0
    memories_kept: int = 0
    memories_dropped: int = 0
    memories_expired: int = 0
    memories_unreviewed: int = 0


def driver(uri: str, auth: tuple[str, str]) -> Driver:
    """드라이버 하나. **자체 재시도를 끈다** — 재시도는 Airflow가 한다."""
    try:
        return GraphDatabase.driver(uri, auth=auth, max_transaction_retry_time=0)
    except (ServiceUnavailable, SessionExpired) as error:
        raise ConnectionError(f"neo4j at {uri} is unavailable: {error}") from error


def ensure_schema(graph: Driver) -> None:
    """제약과 요인·지수 노드를 세운다. 멱등하고 쓰기 전마다 부른다.

    **요인 노드를 미리 세우는 것이 목적의 절반이다.** 관측이 한 번도 없는 요인도 조회에
    잡혀야 "관측 없음"으로 표에 실린다 — 빈 칸이 "관계가 없다"로 읽히면 모델이 그 요인을
    영영 안 본다.
    """
    rows = [{"code": spec.code.value, "label": spec.label} for spec in RELATION_FACTORS]
    with _session(graph) as session:
        for statement in CONSTRAINTS:
            _run(session, statement, {})
        _run(session, SEED_INDEX, {"code": INDEX_CODE, "label": INDEX_LABEL})
        for statement in SEED_FACTORS:
            _run(session, statement, {"rows": rows})


def read_relations(graph: Driver, *, as_of_date: date, as_of_at: datetime) -> tuple[RelationWeight, ...]:
    """요인별 가중치. **집계는 파이썬이 한다**(모듈 docstring 참고).

    **`as_of_at`이 컷오프이고 `as_of_date`는 감쇠의 기준일이다.** 둘을 하나로 합칠 수 없다 —
    자르는 것은 "언제 쓰였나"(시각)이고 무게를 정하는 것은 "언제 관찰됐나"(거래일)다.

    관측이 0인 요인도 행으로 나온다(`n_obs=0`). 그것이 "관계가 없다"가 아니라 "아직 모른다"
    라는 것은 프롬프트가 밝힌다.
    """
    as_of_date = _plain_date(as_of_date)
    window_start = date.fromordinal(max(as_of_date.toordinal() - RELATION_LOOKBACK_DAYS, 1))
    with _session(graph) as session:
        rows = _run(
            session,
            READ_OBSERVATIONS,
            {"index": INDEX_CODE, "as_of_at": _plain_datetime(as_of_at), "window_start": window_start},
        )

    grouped: dict[Factor, list[Observation]] = {}
    for row in rows:
        try:
            code = Factor(row["code"])
            sign = ObservationSign(row["sign"])
        except ValueError:
            # 코드에서 사라진 요인이 그래프에 남아 있을 수 있다. 조용히 건너뛰되 남긴다 —
            # 요인을 지우는 것은 사람이 하는 일이고, 그 흔적이 로그에는 보여야 한다.
            logger.warning("graph has an observation for an unknown factor or sign: %s", row)
            continue
        grouped.setdefault(code, []).append(
            Observation(
                observed_on=_as_date(row["date"]),
                sign=sign,
                strength=int(row["strength"]),
                note=str(row["note"] or ""),
            )
        )
    return tuple(
        relation_weight(spec.code, grouped.get(spec.code, []), as_of_date=as_of_date) for spec in RELATION_FACTORS
    )


def read_memories(graph: Driver, *, as_of_date: date, as_of_at: datetime) -> tuple[StoredMemory, ...]:
    """그 시점에 활성이던 메모 전부.

    **나중에 내려간 메모도 그때는 보였다.** `retired_on`이 기준일 이후면 활성으로 친다 —
    과거를 다시 돌릴 때 그 시점의 프롬프트를 그대로 재현하기 위해서다.
    """
    with _session(graph) as session:
        rows = _run(
            session,
            READ_MEMORIES,
            {"as_of_date": _plain_date(as_of_date), "as_of_at": _plain_datetime(as_of_at)},
        )
    memories: list[StoredMemory] = []
    for row in rows:
        factor = None
        if row["factor"]:
            try:
                factor = Factor(row["factor"])
            except ValueError:
                logger.warning("memory %s points at an unknown factor: %s", row["id"], row["factor"])
        memories.append(
            StoredMemory(
                id=int(row["id"]),
                created_on=_as_date(row["created_on"]),
                text=str(row["text"] or ""),
                verify_count=int(row["verify_count"] or 0),
                unreviewed_count=int(row["unreviewed_count"] or 0),
                factor=factor,
            )
        )
    return tuple(memories)


def write_review(
    graph: Driver,
    *,
    run_date: date,
    observations: Sequence[ObservationWrite],
    new_memories: Sequence[NewMemory],
    kept_ids: Sequence[int],
    retired: Sequence[RetiredMemory],
    unreviewed_ids: Sequence[int],
    llm_run_id: int | None,
    created_at: object,
) -> GraphWriteResult:
    """오늘의 관찰·메모를 **트랜잭션 하나로** 쓴다.

    반씩 들어가면 다음 날 관계 표와 메모가 서로 다른 실행을 가리킨다.

    `retired`에는 모델이 `drop`한 것과 코드가 만료·미검토로 내린 것이 함께 온다. 어느 쪽인지는
    `RetireReason`이 들고 있어 나중에 "모델이 메모를 잘 지우나"를 따로 잴 수 있다.
    """
    # `run_date`는 아래 다섯 쿼리가 전부 쓴다. 입구에서 한 번 벗긴다.
    run_date = _plain_date(run_date)
    if isinstance(created_at, datetime):
        created_at = _plain_datetime(created_at)

    for item in observations:
        if not MIN_STRENGTH <= item.strength <= MAX_STRENGTH:
            raise ValueError(f"strength must be {MIN_STRENGTH}~{MAX_STRENGTH}, got {item.strength}")


    try:
        with graph.session() as session, session.begin_transaction() as transaction:
            first_id = 0
            if new_memories:
                result = transaction.run(NEXT_MEMORY_ID, count=len(new_memories))
                # 카운터는 마지막 id를 준다. 여기서 앞으로 되돌려 첫 id를 얻는다.
                first_id = int(result.single()["value"]) - len(new_memories) + 1

            touched = 0
            if observations:
                rows = [
                    {
                        "code": item.factor.value,
                        "label": factor_label(item.factor),
                        "sign": item.sign.value,
                        "strength": item.strength,
                        "note": item.note,
                    }
                    for item in observations
                ]
                record = transaction.run(
                    WRITE_OBSERVATIONS,
                    rows=rows,
                    index=INDEX_CODE,
                    observed_on=run_date,
                    llm_run_id=llm_run_id,
                    created_at=created_at,
                ).single()
                touched = int(record["touched"]) if record else 0
                if touched != len(rows):
                    # MATCH가 한 행에서 아무 것도 못 찾으면 그 행은 **오류 없이 통째로
                    # 빠진다.** 지수 노드가 없으면 여기가 0이 되고 관찰이 조용히 사라진다.
                    raise GraphError(
                        f"observation write touched {touched} of {len(rows)} rows — is the Index node seeded?"
                    )

            # **id는 위치로 준다.** 같은 문장의 메모 둘이 값으로 같을 수 있어
            # `list.index`로 찾으면 둘이 한 id를 쓴다.
            numbered = [(first_id + offset, item) for offset, item in enumerate(new_memories)]
            linked = [
                {"id": memory_id, "text": item.text, "reason": item.reason, "factor": item.factor.value}
                for memory_id, item in numbered
                if item.factor is not None
            ]
            unlinked = [
                {"id": memory_id, "text": item.text, "reason": item.reason}
                for memory_id, item in numbered
                if item.factor is None
            ]

            created = 0
            for statement, rows in ((WRITE_MEMORIES, linked), (WRITE_MEMORIES_UNLINKED, unlinked)):
                if not rows:
                    continue
                record = transaction.run(
                    statement,
                    rows=rows,
                    created_on=run_date,
                    created_at=created_at,
                    llm_run_id=llm_run_id,
                ).single()
                created += int(record["created"]) if record else 0

            if kept_ids:
                transaction.run(KEEP_MEMORIES, ids=list(kept_ids), reviewed_on=run_date)
            if retired:
                transaction.run(
                    RETIRE_MEMORIES,
                    rows=[{"id": item.id, "reason": item.reason.value, "note": item.note} for item in retired],
                    retired_on=run_date,
                )
            if unreviewed_ids:
                transaction.run(MARK_UNREVIEWED, ids=list(unreviewed_ids))
            transaction.commit()
    except (ServiceUnavailable, SessionExpired, TransientError) as error:
        raise ConnectionError(f"neo4j is unavailable: {error}") from error
    except (ClientError, Neo4jError) as error:
        raise GraphError(f"neo4j rejected the write: {error}") from error

    return GraphWriteResult(
        observations=touched,
        memories_written=created,
        memories_kept=len(kept_ids),
        memories_dropped=sum(1 for item in retired if item.reason is RetireReason.DROPPED),
        memories_expired=sum(1 for item in retired if item.reason is RetireReason.EXPIRED),
        memories_unreviewed=len(unreviewed_ids),
    )


def _session(graph: Driver):
    return graph.session()


def _run(session, statement: str, parameters: dict[str, object]) -> list[dict]:
    try:
        result = session.run(statement, **parameters)
        return [record.data() for record in result]
    except (ServiceUnavailable, SessionExpired, TransientError) as error:
        raise ConnectionError(f"neo4j is unavailable: {error}") from error
    except (ClientError, Neo4jError) as error:
        raise GraphError(f"neo4j rejected the query: {error}") from error


def _plain_datetime(value: datetime) -> datetime:
    """드라이버가 받는 표준 `datetime`으로. **pendulum 값을 그대로 넘기면 죽는다.**

    `ValueError: Values of type <class 'pendulum.datetime.DateTime'> are not supported` —
    드라이버가 정확한 타입으로 직렬화 훅을 고르므로 서브클래스가 통하지 않는다.
    `common.slot_at`이 pendulum으로 시각을 만들어서 이 경계가 필요하다.
    """
    return datetime(
        value.year,
        value.month,
        value.day,
        value.hour,
        value.minute,
        value.second,
        value.microsecond,
        tzinfo=value.tzinfo,
    )


def _plain_date(value: date) -> date:
    """드라이버가 받는 표준 `date`로. **`_plain_datetime`과 같은 이유다.**

    `ValueError: Values of type <class 'pendulum.date.Date'> are not supported` —
    `datetime`만 벗기고 `date`를 안 벗겨서 2026-09-03 첫 운영 실행이 여기서 죽었다.
    pendulum `DateTime.date()`가 pendulum `Date`를 주므로 KST 날짜를 뽑는 경로가 전부
    이것을 만든다. **공개 함수 셋이 입구에서 한 번 벗기고 아래로는 표준 타입만 흐른다.**
    """
    return date(value.year, value.month, value.day)


def _as_date(value: object) -> date:
    """드라이버가 주는 `neo4j.time.Date`를 파이썬 `date`로. 이미 `date`면 그대로."""
    if isinstance(value, date):
        return value
    to_native = getattr(value, "to_native", None)
    if to_native is not None:
        return to_native()
    return date.fromisoformat(str(value))
