"""Neo4j에 선 인과 그래프를 대상 하나 기준으로 읽는다.

설계는 [17-graph-query.md](../../docs/analysis/market-thesis/17-graph-query.md)다.
`graph.py`가 쓰기, 여기가 읽기다. **둘은 서로를 import하지 않는다** — 투영은
`market_causal_weekly`가 태스크 안에서 부르고, 여기는 그 뒤 방향성 요약이 부른다.

## Cypher를 LLM이 쓰지 않는다

초안(2026-08-30)은 LLM이 자유 Cypher를 쓰고 코드가 가드로 검사하는 형태였다. 프로토타입이
그것을 접었다(설계 §6.8) — 조건 조각을 프롬프트에 실으면 모델이 그대로 붙여 거절이 0/6이었고,
그 대신 호출 하나가 45~104초였다. 임의 질문이 필요하지 않다면 우리가 쿼리를 갖는 편이 짧다.

여기 있는 것은 상수 둘이고 파라미터만 바인딩한다.

## 다중 홉의 규칙 셋

가변 길이 매치는 이 셋을 안 걸면 **조용히 틀린 답**을 준다. 문법도 맞고 에러도 안 난다.

- **`created_at <= $as_of_at`** — 경로는 그 주가 끝나고 한 주 뒤(`W+2` 월요일)에 생기고
  재실행이면 아무 때나 생긴다. 추론의 event-time cutoff가 이 값으로만 정확하다.
- **`week_start` 단조 증가** — `Target` 노드가 주를 넘어 하나라 안 걸면 미래가 과거의
  원인이 된다.
- **`path_id`는 `Target` 접점에서만 바뀐다** — 채널 노드는 모든 경로가 공유하므로 거기서
  바뀌면 아무도 하지 않은 인과가 만들어진다. 반대로 대상 노드에서 바뀌는 것은 이 그래프의
  목적이다(앞 주장이 착지한 곳에서 다음 주장이 출발한다).

셋째가 `path_id`를 전부 같게 거는 것과 다르다. 그렇게 걸면 6홉에 닿는 노드가 5.1에서 멈추고
(접점 규칙은 6.7, 경계 없음은 8.1) 주장 사이의 홉이 통째로 사라진다 — 이 단계의 존재 이유다.
"""

import logging
from collections.abc import Sequence
from datetime import date, datetime

from neo4j import Driver, GraphDatabase, Query
from neo4j.exceptions import ClientError, Neo4jError, ServiceUnavailable, SessionExpired, TransientError
from pydantic import BaseModel, ConfigDict

logger = logging.getLogger(__name__)

# 탐색 깊이 상한. 채널 그래프에 사이클이 있어 상한이 없으면 응답이 폭발한다.
# 6인 이유는 포화점(4홉)보다 여유를 둔 값이기 때문이다 — 사건 하나에서 닿는 노드가
# 3홉 5.3, 4홉 6.3, 6홉 6.7로 늘다 멈춘다(설계 §6.6). 올릴 때는 그 표를 다시 잰다.
MAX_QUERY_DEPTH = 6

# 대상 하나가 받는 행 상한. 다중 홉은 조합마다 한 행이라 실제로 는다 — 2026-08-31 실측에서
# "000660에 무엇이 닿았나"가 31행이었고 같은 경로가 여러 번 나왔다(설계 §6.7 발견 ③).
# **넘으면 조용히 자르지 않고 잘렸다고 남긴다**(`DirectionInput.truncated`).
MAX_ROWS_PER_TARGET = 60

# 서버가 끊는 시각. 넘으면 거절이 아니라 실행 실패라 그대로 올린다.
QUERY_TIMEOUT_SECONDS = 15.0

# 다중 홉에 붙는 조건 셋. **문자열 하나로 둔다** — 두 쿼리가 같은 규칙을 써야 하고,
# 조각이 갈리면 한쪽만 고친 날 답이 조용히 달라진다.
WALK_GUARD = """all(x IN r WHERE x.created_at <= $as_of_at)
      AND all(i IN range(0, size(r) - 2)
              WHERE r[i].week_start <= r[i + 1].week_start
                AND (r[i].path_id = r[i + 1].path_id OR nodes(p)[i + 1]:Target))"""

# ① 그 대상에 착지한 주장 (1홉 — 방향성의 뼈대).
#
# 출발점(`Event` 또는 `Target`)을 함께 준다. **라벨로 건다** — "들어오는 `LEADS_TO`가 없는
# 노드"로 찾으면 2단 체인의 첫 채널이 함께 잡힌다(그 채널로 들어오는 것이 `Event`라 조건이
# 참이 된다). 그러면 `title`도 `code`도 없는 행이 나온다. 출발점은 정의상 둘 중 하나다
# (`ck_market_causal_path_source_exclusive`가 DB에서 그것을 보장한다).
LANDING_QUERY = """
MATCH (c:Channel)-[h:HITS]->(t:Target {kind: $kind, code: $code})
WHERE h.week_start = $week_start AND h.created_at <= $as_of_at
MATCH (s)-[l:LEADS_TO {path_id: h.path_id}]->(:Channel)
WHERE s:Event OR s:Target
RETURN h.path_id AS path_id,
       h.sign AS sign,
       h.confidence AS confidence,
       h.reasoning AS reasoning,
       c.name AS channel,
       coalesce(s.title, s.code) AS source,
       labels(s)[0] AS source_kind
ORDER BY path_id
LIMIT $limit
"""

# ② 앞 주장에서 이어져 닿은 것 (다중 홉 — 대상 접점).
#
# `path_ids`가 목록이다. 다중 홉은 주장 여럿을 이은 것이라 근거도 여럿이고, 마지막 하나만
# 돌려주면 그 이음매를 인용에서 잃는다(설계 §6.7 발견 ②).
#
# **`path_id`가 실제로 바뀌는 경로만 준다.** 안 걸면 주장 하나 안의 2단 체인
# (`사건 → 채널 → 채널 → 대상`)이 함께 잡히는데, 그것은 ①이 이미 준 주장과 같은 것이라
# 새 정보가 아니면서 행만 늘린다(2026-08-31 실측: 005930 08-17주가 21행에서 대부분).
# 이 조건이 "주장 사이의 홉"이라는 이 쿼리의 목적 그 자체다.
CHAIN_QUERY = f"""
MATCH p = (s)-[r:LEADS_TO|HITS*2..{MAX_QUERY_DEPTH}]->(t:Target {{kind: $kind, code: $code}})
WHERE (s:Event OR s:Target)
  AND last(r).week_start = $week_start
  AND any(i IN range(0, size(r) - 2) WHERE r[i].path_id <> r[i + 1].path_id)
  AND {WALK_GUARD}
RETURN [x IN r | x.path_id] AS path_ids,
       last(r).sign AS sign,
       [n IN nodes(p) | coalesce(n.name, n.code, n.title)] AS chain
ORDER BY size(chain), path_ids
LIMIT $limit
"""


class GraphQueryError(RuntimeError):
    """Neo4j가 거절했고 다시 불러도 같은 결과다(인증·쿼리 오류·timeout)."""


class _Row(BaseModel):
    """오가는 값은 전부 불변이다. 재시도 경로에서 바뀌면 원본과 어긋난다."""

    model_config = ConfigDict(frozen=True)


class Landing(_Row):
    """주장 하나가 대상에 착지한 것. 경로 하나에 행 하나다."""

    path_id: int
    sign: str
    confidence: str
    reasoning: str
    channel: str
    source: str
    source_kind: str


class Chain(_Row):
    """앞 주장에서 이어져 닿은 경로 하나. **`path_ids`가 목록이다.**"""

    path_ids: tuple[int, ...]
    sign: str
    chain: tuple[str, ...]


class DirectionInput(_Row):
    """대상 하나가 그 주에 받은 것 전부. LLM이 이것을 읽고 방향을 종합한다.

    **세기는 여기서 센다.** 모델이 숫자를 만들지 않는다(저장소 규칙).
    """

    kind: str
    code: str
    week_start: date
    landings: tuple[Landing, ...]
    chains: tuple[Chain, ...]
    truncated: bool = False

    @property
    def up_count(self) -> int:
        return sum(1 for landing in self.landings if landing.sign == "up")

    @property
    def down_count(self) -> int:
        return sum(1 for landing in self.landings if landing.sign == "down")

    @property
    def flat_count(self) -> int:
        """`market_causal_path.sign`이 up/down뿐이라 지금은 언제나 0이다.

        칸을 두는 이유는 그 CHECK가 넓어질 때 여기가 조용히 틀리지 않게 하기 위해서다.
        """
        return sum(1 for landing in self.landings if landing.sign not in ("up", "down"))

    @property
    def path_ids(self) -> tuple[int, ...]:
        """인용할 경로 전부. 착지 경로와 다중 홉의 이음매를 합쳐 순서를 지킨다."""
        seen: dict[int, None] = {}
        for landing in self.landings:
            seen.setdefault(landing.path_id, None)
        for chain in self.chains:
            for path_id in chain.path_ids:
                seen.setdefault(path_id, None)
        return tuple(seen)

    @property
    def channel_counts(self) -> tuple[dict[str, object], ...]:
        """채널별 방향 집계. 추론이 종합을 못 믿을 때 보는 재료다."""
        tally: dict[str, dict[str, int]] = {}
        for landing in self.landings:
            row = tally.setdefault(landing.channel, {"up": 0, "down": 0})
            if landing.sign in row:
                row[landing.sign] += 1
        return tuple(
            {"name": name, "up": counts["up"], "down": counts["down"]}
            for name, counts in sorted(tally.items(), key=lambda item: (-sum(item[1].values()), item[0]))
        )


def driver(uri: str, auth: tuple[str, str]) -> Driver:
    """드라이버 하나. **자체 재시도를 끈다** — 재시도는 Airflow가 한다(`graph.py`와 같다)."""
    try:
        return GraphDatabase.driver(uri, auth=auth, max_transaction_retry_time=0)
    except (ServiceUnavailable, SessionExpired) as error:
        raise ConnectionError(f"neo4j at {uri} is unavailable: {error}") from error


def read_direction_input(graph: Driver, *, kind: str, code: str, week_start: date, as_of_at: datetime) -> DirectionInput:
    """대상 하나가 그 주에 받은 것을 읽는다. **읽기 트랜잭션이다.**"""
    parameters = {
        "kind": kind,
        "code": code,
        "week_start": week_start,
        "as_of_at": as_of_at,
        "limit": MAX_ROWS_PER_TARGET + 1,
    }
    try:
        with graph.session() as session:
            landing_rows = _run(session, LANDING_QUERY, parameters)
            chain_rows = _run(session, CHAIN_QUERY, parameters)
    except (ServiceUnavailable, SessionExpired, TransientError) as error:
        # 잠시 뒤 다시 부르면 될 실패다. Airflow가 재시도하게 그대로 올린다.
        raise ConnectionError(f"neo4j is unavailable: {error}") from error
    except (ClientError, Neo4jError) as error:
        # 인증·쿼리 오류·timeout. 다시 불러도 같은 답이다.
        raise GraphQueryError(f"neo4j rejected the read: {error}") from error

    truncated = len(landing_rows) > MAX_ROWS_PER_TARGET or len(chain_rows) > MAX_ROWS_PER_TARGET
    if truncated:
        # 조용히 자르지 않는다. 프롬프트가 이 사실을 함께 싣는다.
        logger.warning(
            "graph rows for %s:%s in %s hit the cap (%d landings, %d chains)",
            kind,
            code,
            week_start,
            len(landing_rows),
            len(chain_rows),
        )
    return DirectionInput(
        kind=kind,
        code=code,
        week_start=week_start,
        landings=tuple(Landing(**row) for row in landing_rows[:MAX_ROWS_PER_TARGET]),
        chains=tuple(
            Chain(path_ids=tuple(row["path_ids"]), sign=row["sign"], chain=tuple(row["chain"]))
            for row in chain_rows[:MAX_ROWS_PER_TARGET]
        ),
        truncated=truncated,
    )


def _run(session, statement: str, parameters: dict[str, object]) -> list[dict]:
    """`Query`로 감싸 timeout을 서버에 건다. 넘으면 서버가 끊고 드라이버가 예외를 낸다.

    **`session.run`이다.** 드라이버가 `Query` 객체를 auto-commit에서만 받는다
    (`transaction.run`은 `TypeError`). 읽기뿐이라 명시적 트랜잭션으로 묶을 것이 없다.
    """
    result = session.run(Query(statement, timeout=QUERY_TIMEOUT_SECONDS), **parameters)
    return [record.data() for record in result]


def stored_weeks(graph: Driver, *, as_of_at: datetime) -> Sequence[date]:
    """그래프에 있는 주 전부(오름차순). `as_of_at` 뒤에 생긴 것은 뺀다."""
    statement = (
        "MATCH ()-[h:HITS]->() WHERE h.created_at <= $as_of_at "
        "RETURN DISTINCT h.week_start AS week_start ORDER BY week_start"
    )
    try:
        with graph.session() as session:
            rows = _run(session, statement, {"as_of_at": as_of_at})
    except (ServiceUnavailable, SessionExpired, TransientError) as error:
        raise ConnectionError(f"neo4j is unavailable: {error}") from error
    except (ClientError, Neo4jError) as error:
        raise GraphQueryError(f"neo4j rejected the read: {error}") from error
    return [row["week_start"].to_native() for row in rows]
