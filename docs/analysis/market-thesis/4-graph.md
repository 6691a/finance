# 4단계 — 그래프 projection: Postgres가 원본, Neo4j는 탐색용 투영

- 상위: [README.md](README.md)
- 의존: [1-storage.md](1-storage.md)와 [market-causal-graph.md](../market-causal-graph.md).
- 산출물: `airflow/modules/graph.py`, `market_causal_weekly`의 `sync_graph` 태스크와
  `sync_only` Param, `market_causal_path/select_graph_by_week.sql`·`select_weeks.sql`·
  `market_causal_step/select_by_week.sql`, requirements 둘과 `pyproject.toml` dev,
  `tests/modules/test_graph.py`, `tests/dags/test_market_causal_weekly.py`의 `TestProjection`
- **선행 조건: prod Neo4j 인스턴스.** 이 저장소 밖이다(§5).
- 상태: **구현 완료, 운영 인스턴스 기동 완료(2026-08-30).** 첫 적재는
  `sync_only` 트리거로 한다.
  **투영 대상이 `thesis`에서 인과 그래프로 바뀌었다**(2026-08-30) — 그 이유와 실측이 §1에 있다.
  §7이 그 판단을 만든 학습 실행이고, 거기서 나온 발견 넷이 §2·§4에 들어가 있다.

## 1. 왜 Neo4j인가, 왜 인과 그래프인가

이 저장소 관례는 스테이트풀 저장소를 안 늘리는 쪽이다(전문 검색도 외부 엔진 대신 ParadeDB
in-DB BM25를 쓴다). 여기서 그 관례를 깬다.

**처음 계획은 `thesis`를 투영하는 것이었고 그것은 접었다.** 2026-08-27 실측에서 그 그래프는
137노드·514엣지인데 근거 허브 둘이 추론의 절반씩을 물고 있었다. 관계가
`Thesis -CITES-> Evidence` 한 종류라 별 모양이고, **2홉이면 거의 전체가 돌아온다.** 그래프
DB가 이기는 장면이 없다.

**인과 그래프는 다르다.** 노드 종류가 셋(`Event → Channel → Target`)이고 채널이 주를 잇도록
설계돼 있다([market-causal-graph.md](../market-causal-graph.md) §4). 2026-08-30 학습 실행에서
사건 하나로부터 k홉에 닿는 노드 수를 쟀다(§7.8 발견 ③).

| 홉 | 1 | 2 | 3 | 4 | 6 |
| --- | --- | --- | --- | --- | --- |
| 닿는 노드(평균, 전체 28) | 1.1 | 4.5 | 8.0 | 9.5 | 10.0 |

1홉에서 3홉으로 일곱 배가 되고 4홉에서 포화하며, 6홉에서도 전체의 36%다. **다중 홉이 새
것을 데려오면서 전부를 데려오지는 않는다.** 그것이 이 단계를 되살린 근거다.

**"미러"가 아니라 "projection"이다.** Postgres 행 전부를 옮기지 않는다 — `input_hash`,
`llm_run_id`, `market_causal_evidence`는 싣지 않는다. 그래프로 답하는 질문은 "무엇이 무엇을
통해 무엇에 닿았나"까지이고, 그 이상은 Postgres를 본다.

**그래서 Neo4j는 백업이 아니다.** 통째로 날아가도 잃는 것이 없다 — `sync_only` 한 번이면
같은 그래프가 다시 선다(2026-08-30 실측: 두 주 51경로가 몇 초). 반대 방향은 성립하지 않는다.

**검토했다 뺀 것 — Apache AGE.** 2026-08-30에 다시 봤고 결론이 같다. 파생 저장소로 쓰면
Neo4j와 값이 같은데 데이터 DB(`paradedb/paradedb:0.25.2-pg17`)에 확장을 굽는 비용만 더
든다. **원본으로 쓰는 안**도 봤는데 `cypher()`가 쿼리 리터럴을 요구해 LLM이 만든 한국어 사건
제목을 인라인하게 되고, Alembic·자연키 `ON CONFLICT`·SQL 실현값 계산을 함께 잃는다.
relational이 원본, Neo4j가 유일한 projection이다.

**동기화는 별도 태스크, 별도 트랜잭션이다.** 두 스토어를 같은 태스크에서 같이 쓰지 않는다 —
Neo4j 쓰기가 실패해도 Postgres 쓰기는 이미 커밋된 채로 남는다(Slack 발송 실패가 DB 쓰기를
되돌리지 않는 것과 같은 이유. 분산 트랜잭션을 만들지 않는다).

## 2. 그래프 모양

**노드 키는 Postgres의 자연키다.** `market_event.id`가 아니라 `(title, occurred_on)`을 쓴다.
재적재가 정상 흐름이고, 자연키면 몇 번을 다시 넣어도 같은 노드다.

```cypher
(:Event   {title, occurred_on})
(:Channel {name})
(:Target  {kind, code})
```

엣지는 둘이다. 경로 헤더 한 행 + 단계 N행이 **엣지 N+1개**가 된다.

```cypher
(:Event)-[:LEADS_TO  {path_id, week_start, position}]->(:Channel)
(:Target)-[:LEADS_TO {path_id, week_start, position, sign}]->(:Channel)
(:Channel)-[:LEADS_TO {path_id, week_start, position}]->(:Channel)
(:Channel)-[:HITS {path_id, week_start, sign, confidence, reasoning,
                   return_unit, return_week_change, return_t1_change,
                   return_t5_change}]->(:Target)
```

**출발점이 `Event`일 수도 `Target`일 수도 있다.** `market_causal_path.event_id`가 nullable이고
`source_target_*` 셋이 그 대안이다([market-causal-graph.md](../market-causal-graph.md) §11.4).
앞 주의 결과가 다음 주의 원인이 되는 자리이고, **`Target` 노드가 주를 넘어 하나라서** 그
연결이 실제로 이어진다.

**경로 수준 속성은 `HITS`에 싣는다** — 주장이 착지하는 자리가 거기다. `return_unit`을 함께
싣는 이유는 원본과 같다: percent와 basis_point가 한 칸에 섞이면 크기 비교가 조용히
무의미해진다.

### 2.1 모든 엣지가 `path_id`와 `week_start`를 싣는다

학습 실행에서 나온 발견 둘이 이 두 속성을 강제한다(§7.8).

- **`path_id` — 채널 노드가 모든 경로에 공유된다.** 제약 없이 걸으면 서로 다른 주장이
  `할인율`에서 섞인다. Cypher의 가변 길이 매치는 이것을 안 쓰면 **조용히 더 많은 답**을 준다.
  조회하는 쪽이 `WHERE all(r IN rels WHERE r.path_id = rels[0].path_id)`를 건다.
- **`week_start` — `Target` 노드가 주를 넘어 하나라 시각이 역행한다.** 08-17 주에 닿은 `SOX`가
  08-10 주의 원인으로 이어지는 경로가 실제로 만들어진다. 조회하는 쪽이 단조 증가를 걸어야
  하고, **그것이 기본값이어야 한다** — 옵션으로 두면 안 건 사람이 미래→과거 인과를 읽는다.

둘 다 Neo4j만의 문제가 아니다. Postgres 재귀 CTE도 같은 답을 낸다.

### 2.2 멱등과 제약

MERGE 키에 `path_id`와 `position`이 들어간다(`HITS`는 `path_id`). **재적재가 엣지를 누적하지
않게 하는 장치다** — 2026-08-30 실측에서 같은 두 주를 두 번 밀어도 28노드·129엣지 그대로였다.

제약은 붙을 때마다 멱등하게 보장한다. Neo4j는 Alembic 대상이 아니라 마이그레이션 파일로
관리하지 않는다.

```cypher
CREATE CONSTRAINT event_key   IF NOT EXISTS FOR (e:Event)   REQUIRE (e.title, e.occurred_on) IS UNIQUE;
CREATE CONSTRAINT channel_key IF NOT EXISTS FOR (c:Channel) REQUIRE c.name IS UNIQUE;
CREATE CONSTRAINT target_key  IF NOT EXISTS FOR (t:Target)  REQUIRE (t.kind, t.code) IS UNIQUE;
```

**`NODE KEY`를 쓰지 않는다** — Enterprise 전용이라 community 이미지에서 `CREATE CONSTRAINT`가
거절된다. 복합 속성 유일성으로 같은 것을 얻는다. 유일성 제약은 존재성을 강제하지 않으므로,
빈 값은 `project()`가 MERGE 전에 걸러 `GraphError`로 죽인다.

## 3. `airflow/modules/graph.py`

`slack.py`와 같은 자리다 — Airflow를 import하지 않고, 재시도 핸들러를 붙이지 않는다.

```python
class GraphError(RuntimeError): ...

def read_week(connection, week_start) -> tuple[list[CausalPathRow], list[CausalStepRow]]: ...
def stored_weeks(connection) -> list[date]: ...
def project(paths, steps) -> GraphPayload: ...          # 순수 함수
def write_graph(uri, auth, payload) -> None: ...
```

- **`project`가 순수 함수인 것이 이 모듈의 핵심이다.** 경로를 엣지로 펴는 규칙이 여기 한
  곳에 있고 DB도 드라이버도 안 본다. 테스트 대부분이 이 함수만 본다.
- **오가는 값은 전부 Pydantic 모델이고 `frozen=True`다.** 재시도 경로에서 값이 바뀌면 원본과
  저장값이 어긋난다. `dict`가 되는 자리는 `session.run`에 넘기기 직전 한 번뿐이다.
- **`Decimal`을 `float`로 바꾼다.** 드라이버 매핑에 `Decimal`이 없다. 모델 필드를 `float`로
  선언해 Pydantic이 경계에서 한 번에 바꾼다. `date`는 그대로 `neo4j.time.Date`가 된다.
- **드라이버 자체 재시도를 끈다**(`max_transaction_retry_time=0`). 켜 두면 `execute_write`가
  transient 오류를 기본 30초 동안 스스로 다시 부르고, 태스크 로그의 시도 횟수와 실제 호출
  횟수가 어긋난다. 재시도는 Airflow가 한다.
- 한 주 몫(제약 + MERGE 전부)이 `session.execute_write` 트랜잭션 하나다. 부분 반영을 막는다.

**예외를 종류로 가른다.** `ServiceUnavailable`·`SessionExpired`·`TransientError`는
`ConnectionError`로 올려 Airflow가 재시도하게 두고, `ClientError`를 비롯한 나머지
`Neo4jError`는 `GraphError`다(인증·제약 위반·쿼리 오류 — 다시 불러도 같은 답이다).
원래 예외는 `raise ... from error`로 잇는다.

`apps/core/graph.py`는 만들지 않는다. 조회 API가 Neo4j를 원본으로 채택할 때 만든다. 그때까지
직접 읽는 방법은 Neo4j Browser나 `cypher-shell`이다.

## 4. DAG — `market_causal_weekly`의 `sync_graph`

`build_causal_graph >> sync_graph`. **투영이 붙는 자리가 인과 DAG인 이유는 그 데이터를 만드는
DAG이 그것이기 때문이다.** thesis DAG에는 안 붙인다.

- 앞 태스크의 XCom 요약에서 `week_start`를 받아 그 주만 민다. 주 하나가 경로 수십 개라
  왕복이 트랜잭션 하나다.
- **`NEO4J_URI`가 비어 있으면 `AirflowSkipException`이다.** 인스턴스가 서기 전에도
  `build_causal_graph`는 정상이어야 하고, 설정 누락으로 매주 빨간 태스크를 만들면 진짜 실패가
  묻힌다. URI가 있는데 계정이 없는 것은 설정 실수라 `AirflowFailException`이고, 접속이 안 되는
  것은 skip이 아니라 `ConnectionError` 재시도다.
- **`sync_only` Param — 초기 적재와 밀린 주 복구가 이것 하나다.** 켜면 `build_causal_graph`가
  `AirflowSkipException`으로 빠지고(비용이 있는 쪽이다), `sync_graph`가 `select_weeks.sql`로
  저장된 주 전부를 오름차순으로 민다. MERGE라 몇 번을 돌려도 같은 그래프다.
  - **`sync_graph`의 `trigger_rule="none_failed"`.** 기본 `all_success`는 upstream이 skip이면
    downstream도 skip이다. 그러면 `sync_only`가 아무 일도 안 한다.
  - 주를 못 정하면(요약도 없고 `sync_only`도 아니면) `AirflowFailException`이다. 어느 주를
    밀지 모르는 채로 도는 것보다 죽는 편이 낫다.
- 별도 reconciliation DAG나 lag·checksum 관측은 만들지 않는다 — 어긋나면 `sync_only`로 전부
  다시 민다. 그 비용이 몇 초다.

## 5. 환경과 인프라

- `NEO4J_URI`·`NEO4J_USER`·`NEO4J_PASSWORD` — Slack 토큰과 같은 방식으로 `os.environ.get`.
  새 Airflow Connection/Provider를 만들지 않는다(값 세 개뿐이라 오버킬).
- `neo4j` 드라이버와 `langchain-neo4j`를 `compose/local/airflow/requirements.txt`,
  `compose/prod/airflow/requirements.txt`, `pyproject.toml`의 `dev` 그룹에 넣었다.
  **prod는 이미지 재빌드·배포가 필요하다.**
- **`langchain-neo4j`는 아직 아무도 import하지 않는다**(2026-08-30). §8의 조회 층에 쓰려고
  미리 넣었다. **무게가 있다** — `neo4j-graphrag`를 거쳐 `scipy`(70MB)·`pypdf`가 함께
  들어와 이미지가 약 80MB 는다(실측). 쓸 때가 오기 전에 이미지를 가볍게 두고 싶으면
  이 한 줄만 빼면 되고, `modules/graph.py`는 그것과 무관하다.
- **로컬**: `compose/local/docker-compose.yaml`에 이미 있다(`neo4j:5.26.29-community`,
  호스트 포트 17474·17687, named volume). `NEO4J_URI=bolt://localhost:17687`.
- **prod: NAS의 `database` 스택에 둔다. 이 저장소의 `compose/prod/`에는 넣지 않는다.**
  Postgres·Redis가 사는 그 compose(저장소 밖)에 서비스 하나를 더한다. Airflow prod compose가
  `database` 네트워크에 external로 붙어 있으므로 **컨테이너 이름으로 닿는다**:
  `NEO4J_URI=bolt://neo4j:7687`. 호스트 포트 17474·17687은 브라우저로 볼 때만 쓴다.

### 5.1 NAS에서 밟은 덫 셋 (2026-08-30)

전부 컨테이너가 안 뜨거나 조용히 인증만 실패하는 모양이라 적어 둔다.

- **`/data` 소유권.** neo4j 이미지는 Dockerfile에 `USER neo4j`(uid 7474)가 박혀 있어
  **비root로 시작한다.** postgres·redis는 root로 시작해 스스로 `chown` 하고 권한을 내리므로
  같은 바인드 마운트에서 문제가 없다. neo4j는 그 단계가 없어서
  `AccessDeniedException: .../auth.ini.tmp`로 죽는다. 바인드로 두려면 호스트 폴더 소유를
  맞추고(`user: "1026:100"` + `chown`), 아니면 named volume을 쓴다.
- **헬스체크의 `$`.** compose가 `${NEO4J_PWD}`를 문자열로 치환한 뒤 **컨테이너 셸이 다시
  해석한다.** 비밀번호에 `$$`가 있으면 셸이 그것을 자기 PID로 바꿔 조용히 틀린 비밀번호를
  보낸다. 작은따옴표로 감싸야 한다: `-p '${NEO4J_PWD}'`.
- **`NEO4J_AUTH`는 첫 기동에만 먹는다.** `/data`가 비어 있지 않으면 무시된다. 비밀번호를
  바꾸려면 볼륨을 비우고 다시 올린다.

브라우저로 붙을 때는 **`bolt://<NAS 주소>:17687`**이다. `neo4j://`는 라우팅 탐색을 하는데
서버가 자기 주소를 내부 포트(7687)로 알려 줘서 실패한다. `neo4j://`를 쓰려면
`NEO4J_server_bolt_advertised__address`를 매핑한 포트로 준다.

## 6. 테스트

실제 Neo4j는 안 띄운다(테스트로 실서비스 기동 안 한다는 프로젝트 관례).

- `tests/modules/test_graph.py` — 가짜 드라이버/세션/트랜잭션으로 **무엇이 실렸는지**를 본다.
  체인 1단·2단이 엣지 N+1개가 되는지, 대상 출발 경로가 `Target`에서 시작하는지, 채널이 여러
  경로에서 노드 하나인지, **모든 엣지가 `path_id`·`week_start`를 싣는지**, 제약이 MERGE보다
  먼저 같은 세션에 실리는지, 제약문이 `IS UNIQUE`이고 `NODE KEY`가 아닌지,
  `max_transaction_retry_time=0`인지, `Decimal`이 `float`로·`date`가 그대로 가는지, 빈 행
  묶음을 안 보내는지, transient → `ConnectionError` / client → `GraphError`인지.
  단계가 없거나 출발점이 없는 경로는 `GraphError`다.
- **SQL 컬럼 순서 대조.** `read_week`가 인덱스로 읽으므로 SQL의 SELECT 목록이 바뀌면 조용히
  값이 밀린다. SELECT 목록의 이름과 모델 필드 순서를 대조한다 — 수집기 테스트가 INSERT
  컬럼을 모델 metadata와 대조하는 것과 같은 자리다.
- `tests/dags/test_market_causal_weekly.py`의 `TestProjection` — `NEO4J_URI` 누락 시 skip,
  계정 누락 시 fail, 요약에서 주를 뽑는지, `sync_only`가 저장된 주 전부를 미는지, 주를 못
  정하면 fail인지, `sync_only`가 `build_causal_graph`를 skip시키는지. DAG 모양 쪽에는
  `build_causal_graph >> sync_graph`와 `trigger_rule == "none_failed"`가 있다.

## 7. 학습용 로컬 실행 (2026-08-30)

**§1~§6은 운영 투영이고 이 절은 학습이다.** 한 문서에 두는 이유는 같은 Neo4j를 두 번
계획하지 않기 위해서다. 이 절은 **운영에 아무것도 붙이지 않는다** — `sync_graph` 태스크도,
`airflow/modules/graph.py`도, prod 인스턴스도, Airflow 이미지 재빌드도 없다. 그래서 §5의
선행 조건이 이 절에는 걸리지 않는다.

### 7.1 무엇이 §1~§6과 다른가

| | §1~§6 (운영 투영) | §7 (학습) |
| --- | --- | --- |
| 데이터 | `thesis`·`thesis_evidence` | `market_causal_*` |
| 넣는 것 | Airflow `sync_graph` 태스크 | 스크래치패드 스크립트 한 번 |
| 선행 조건 | prod Neo4j 인스턴스 | 없음 |
| 저장소 산출물 | `graph.py`, 테스트, requirements, DAG 변경 | `compose/local`의 서비스 하나 |
| 되돌리기 | 배포 | 컨테이너 삭제 |
| 상태 | 이 실행의 결론으로 구현했다 | 이 절 |

### 7.2 왜 causal 데이터인가

**thesis 그래프로는 배울 것이 안 나온다.** 137노드·514엣지에 근거 허브 둘이 추론의 절반씩을
물고 있어 2홉이면 거의 전체가 돌아온다(위 상태 항목). 그래프 DB가 이기는 장면이 안 생기는
데이터를 넣고 그래프 DB를 배우는 것은 앞뒤가 안 맞는다.

`market_causal_*`는 노드 종류가 셋(`Event → Channel → Target`)이고 채널이 주를 잇도록
설계돼 있다([market-causal-graph.md](../market-causal-graph.md) §4). 다중 홉이 실제로
생기는 쪽이다. **2026-08-30 실측으로 51경로다**(08-10 주 24, 08-17 주 27).

### 7.3 그래프 모양

노드 셋. **Postgres의 자연키를 그대로 키로 쓴다** — 학습 실행은 재적재가 잦은데, 자연키면
몇 번을 다시 넣어도 같은 노드다. `id`는 안 옮긴다(§2가 `thesis.id`를 옮긴 것과 다른 판단이고,
이유는 저쪽이 운영 갱신을 받아야 하기 때문이다).

```cypher
(:Event   {title, occurred_on})
(:Channel {name})
(:Target  {kind, code})
```

엣지 둘. 경로 헤더 한 행 + 단계 N행이 엣지 N+1개가 된다
([market-causal-graph.md](../market-causal-graph.md) §7의 투영과 같은 규칙).

```cypher
(:Event)-[:LEADS_TO {path_id, week_start, position}]->(:Channel)
(:Channel)-[:LEADS_TO {path_id, week_start, position}]->(:Channel)
(:Channel)-[:HITS {path_id, week_start, sign, confidence,
                   return_week_change, return_unit, reasoning}]->(:Target)
```

**경로 수준 속성은 `HITS`에 싣는다** — 주장이 착지하는 자리가 거기다. `return_unit`을 함께
싣는 것은 원본과 같은 이유다: percent와 basis_point가 한 칸에 섞이면 크기 비교가 조용히
무의미해진다.

**MERGE 키에 `path_id`와 `position`을 넣는다.** 재적재가 엣지를 누적하지 않게 하는 장치다.

제약은 §2와 같은 판단이다(community 5.x, `NODE KEY`는 Enterprise 전용이라 안 쓴다).

```cypher
CREATE CONSTRAINT IF NOT EXISTS FOR (e:Event)   REQUIRE (e.title, e.occurred_on) IS UNIQUE;
CREATE CONSTRAINT IF NOT EXISTS FOR (c:Channel) REQUIRE c.name IS UNIQUE;
CREATE CONSTRAINT IF NOT EXISTS FOR (t:Target)  REQUIRE (t.kind, t.code) IS UNIQUE;
```

### 7.4 넣는 법

- **스크립트는 저장소 밖(스크래치패드)에 둔다.** 학습용이고 운영 경로가 아니다. DSN은
  `config.yaml`에서 읽는다.
- **운영 DB는 읽기 전용이다.** `market_causal_path`·`market_causal_step`·`market_event`·
  `market_channel`을 SELECT만 한다.
- **전량 적재.** 24경로라 증분·배치가 필요 없다. 다시 넣고 싶으면 그냥 다시 넣는다.
- 로컬 Neo4j는 **`compose/local/docker-compose.yaml`에 이미 있다**(`neo4j:5.26.29-community`,
  호스트 포트 17474·17687, named volume `neo4j`). §5가 "추가한다"라고 적었지만 그 서비스는
  2026-08-21에 이미 들어가 있었다 — **이 절은 저장소를 안 바꾼다.**

### 7.5 무엇을 재나

같은 질문 넷을 **재귀 CTE로 한 번, Cypher로 한 번** 쓴다. 재는 것은 셋 — 쿼리 줄 수,
첫 응답 시간, 결과 일치 여부.

| # | 질문 | 예상 |
| --- | --- | --- |
| 1 | `할인율`을 지난 경로가 닿은 대상 전부 | 둘 다 짧다 |
| 2 | 사건 하나에서 3홉 이내로 닿는 대상과 방향 | CTE도 된다 |
| 3 | 서로 다른 주의 두 대상을 잇는 최단 채널 경로 | Cypher `shortestPath`가 한 줄 |
| 4 | 가장 많은 경로가 지나간 채널 상위 5 | 둘 다 집계. Cypher가 짧다 |

**3번이 갈림길이다.** 나머지 셋은 CTE가 비슷한 길이로 낸다.

### 7.6 무엇을 결론으로 삼나

- 넷 다 CTE가 같은 답을 비슷한 길이로 내면 →
  [market-causal-graph.md](../market-causal-graph.md) §7 유지(Neo4j 안 쓴다). 학습은
  학습대로 끝나고 저장소는 안 바뀐다.
- 3·4에서 Cypher가 확실히 짧고 CTE가 지저분하면 → **§1~§6을 causal 데이터 기준으로 다시
  쓴다.** (2026-08-30에 그렇게 했다 — 다만 결정을 만든 것은 3·4의 줄 수가 아니라 아래
  발견 ③이다.)
- 어느 쪽이든 **숫자가 남는 것이 이 절의 산출물이다.** 지금 보류 판단에 없는 것이 그 숫자다.

### 7.7 안 하는 것

- `airflow/modules/graph.py`·`sync_graph` 태스크·`sync_only` Param — §1~§6의 것이다.
- prod Neo4j 인스턴스·Airflow 이미지 재빌드 — §5의 선행 조건. 로컬만 쓰므로 안 걸린다.
- `langchain-neo4j`·`GraphCypherQAChain` — 이 절에서는 안 쓴다. (패키지 자체는 2026-08-30에
  이미지에 넣어 뒀다. 설계는 §8이다.)
- `apps/core/graph.py` — §3의 판단 그대로.
- **Apache AGE** — 2026-08-30에 다시 검토했고 §1의 결론이 그대로다. AGE를 원본으로 삼는
  안까지 봤는데, 데이터 DB가 `paradedb/paradedb:0.25.2-pg17`이라 커스텀 이미지를 구워야
  하고, `cypher()`가 쿼리 리터럴을 요구해 LLM이 만든 한국어 사건 제목을 인라인하게 되며,
  Alembic·자연키 `ON CONFLICT`·SQL 실현값 계산을 함께 잃는다. 학습 목적으로도 Cypher
  부분집합과 `agtype` 문법은 밖으로 안 옮겨진다.

### 7.8 실행 결과 (2026-08-30)

운영 DB를 읽기 전용으로 읽어 로컬 Neo4j에 넣고 §7.5의 질문 넷을 양쪽으로 돌렸다.
스크립트는 스크래치패드(`load.py`·`compare.py`·`shape.py`·`saturate.py`)에 있고
저장소는 안 바뀌었다.

**적재분** — 경로 51(08-10 주 24, 08-17 주 27), 단계 78.

| | 수 |
| --- | --- |
| `Event` | 11 |
| `Channel` | 9 |
| `Target` | 8 |
| `LEADS_TO` | 78 |
| `HITS` | 51 |

노드 28·엣지 129다. 출발점은 사건 35, 대상 16이다(§11.4의 링커분).

#### 질문 넷 — 결과는 넷 다 일치했다

`REPEATS = 5`의 최소값이다. 왕복 기준선은 Postgres(NAS) 3.5ms, Neo4j(로컬) 2.1ms다.

| # | 질문 | CTE | Cypher | 행 |
| --- | --- | --- | --- | --- |
| 1 | `할인율`을 지난 경로가 닿은 대상 | 5줄 13.5ms | 4줄 6.4ms | 6 = 6 |
| 2 | 사건에서 3홉 이내 대상과 방향 | 10줄 12.1ms | 4줄 3.9ms | 6 = 6 |
| 3 | `US10Y` → `005930` 최단 채널 경로 | 11줄 35.8ms | 6줄 2.6ms | 1 = 1 |
| 4 | 경로가 가장 많이 지난 채널 상위 5 | 4줄 6.3ms | 3줄 2.7ms | 5 = 5 |

**CTE 줄 수는 엣지 CTE 28줄을 뺀 값이다.** 그 28줄은 질문마다 다시 붙는다 — 운영 DB가
읽기 전용이라 뷰를 만들 수 없어서다. §7 조회를 실제로 만들 때는 뷰 하나로 한 번만 쓴다.

**시간은 판단 근거가 아니다.** 51경로에서는 둘 다 40ms 아래이고 그중 절반이 왕복이다.
`shortestPath`가 3번에서 눈에 띄게 빠르지만(2.6 vs 35.8ms) 절대값이 작아 뜻이 없다.

**Cypher가 짧다. 압도적이지는 않다.** 3번만 차이가 성격을 갖는다 — CTE는 사이클을 막을
배열(`NOT e.dst = ANY(w.trail)`)을 손으로 들고 다녀야 하고 Cypher는 `shortestPath`가
그것을 판다.

#### 발견 ① — 가변 길이 매치는 경로 경계를 넘는다

**채널 노드가 모든 경로에 공유되므로**, `path_id` 제약 없이 걸으면 서로 다른 주장이
`할인율`에서 섞인다. 1·2번은 다음 줄이 있어야 SQL과 답이 같아진다.

```cypher
WHERE all(r IN rels WHERE r.path_id = rels[0].path_id)
```

**이 실수는 SQL 쪽이 하기 어렵다.** 재귀 CTE는 조인 조건에
`AND e.path_id = w.path_id`가 자연스럽게 들어가고, 빼먹으면 결과가 눈에 띄게 커진다.
Cypher는 안 쓰면 조용히 더 많은 답을 준다.

#### 발견 ② — 시각이 역행하는 경로가 생긴다 (스키마 문제)

`Target` 노드는 주를 넘어 하나다. 그래서 **08-17 주에 닿은 `SOX`가 08-10 주의 원인으로
이어지는 경로**가 실제로 만들어진다. 무제약 탐색은 미래에서 과거로 흐르는 인과를 낸다.

**Neo4j 문제가 아니다.** Postgres 재귀 CTE도 같은 답을 낸다 — 노드를 주 사이에 공유하는
것이 §1의 목적 그 자체이기 때문이다. 처방은 탐색에 단조 증가를 거는 것이고, 엣지가
`week_start`를 이미 싣고 있어 지금도 걸 수 있다.

```cypher
WHERE all(i IN range(1, size(rels) - 1)
          WHERE rels[i].week_start >= rels[i - 1].week_start)
```

**§7 조회를 만들 때 이 조건이 기본값이어야 한다.** 옵션으로 두면 안 건 사람이 미래→과거
인과를 읽는다.

#### 발견 ③ — 다중 홉이 실제로 값을 낸다

사건 열하나에서 출발해 k홉 안에 닿는 서로 다른 노드 수(전체 28).

| 홉 | 경로 무시 | 같은 경로만 | 시각 순서 지킴 |
| --- | --- | --- | --- |
| 1 | 1.1 | 1.1 | 1.1 |
| 2 | 4.5 | 2.9 | 4.1 |
| 3 | 8.0 | 5.1 | 6.9 |
| 4 | 9.5 | 5.1 | 7.9 |
| 6 | 10.0 | 5.1 | 8.1 |

**thesis 그래프의 "2홉이면 거의 전체"와 다르다.** 1홉 1.1에서 3홉 8.0으로 일곱 배가 되고
4홉에서 포화한다. 6홉에서도 전체 28 중 10(36%)이다 — 다중 홉이 **새 것을 데려오면서
전부를 데려오지는 않는다.** 노드를 쪼갠 것이 실제로 작동했다는 뜻이다.

`같은 경로만` 열이 3홉에서 5.1로 멈추는 것은 당연하다 — 체인이 최대 2단이라 저장된
주장 하나는 4홉을 못 넘는다. **홉은 주장 안이 아니라 주장 사이에서 생긴다**(§1과 같은 말).

#### 발견 ④ — 허브가 근거에서 채널로 옮겨갔다

차수 상위: `할인율` 46, `투자심리` 34, `금리 기대` 22, `이익 기대` 20, `000660` 13,
`005930` 13. `할인율` 하나가 엣지 129개 중 46개(36%)를 문다.

**이것은 설계 의도다**(§4 자라는 어휘, §8.1 어휘는 수렴한다). 채널이 재사용돼야 주가
이어진다. 다만 탐색이 `할인율`로 몰리므로 조회에 깊이 상한이 필요하다는 근거이기도 하다
([market-causal-graph.md](../market-causal-graph.md) §7의 `MAX_QUERY_DEPTH = 6`).

#### 그래서 무엇을 할 것인가

**운영 배포는 아직이다.** 이유가 둘이다.

1. **소비자가 없다.** §7 조회 API는 14단계 화면과 함께 나간다. 지금 붙이면 `sync_graph`가
   아무도 안 읽는 그래프를 유지한다.
2. **발견 ②가 먼저다.** 시각 역행은 저장 쪽이 아니라 조회 쪽 문제이고, 조회를 만들 때
   기본값으로 들어가야 한다. 그 전에 그래프만 세우면 틀린 답을 내는 창구가 먼저 선다.

**Neo4j가 값어치를 낸다는 근거는 나왔다.** 발견 ③이 그것이다 — 이 문서가 2026-08-27에
보류한 이유("2홉이면 거의 전체")가 causal 데이터에서는 성립하지 않는다. 화면을 만들 때
다시 본다. 그때 §1~§6을 causal 기준으로 다시 쓴다(§7.6).

**지금 저장소에 남는 것은 없다.** 로컬 Neo4j는 이미 compose에 있었고, 스크립트는
스크래치패드에 있으며, 이 문서만 늘었다.

## 8. LLM이 Cypher를 쓰는 조회 층 (예정, 2026-08-30)

**미구현이다.** 패키지(`langchain-neo4j`)만 넣어 뒀고 import하는 코드는 없다. 여기 적는 것은
그때 이미 정해져 있는 제약들이다 — §2·§7.8이 만든 것이라 설계를 다시 하지 않는다.

### 8.1 community 판에는 읽기 전용 계정이 없다

**Neo4j community는 사용자가 `neo4j` 하나뿐이고 역할 기반 권한(RBAC)이 Enterprise 전용이다.**
그래서 "LLM에게는 읽기 전용 계정을 준다"가 **이 배포에서는 불가능하다.**

`GraphCypherQAChain`은 LLM이 만든 Cypher를 그대로 실행하고, 그래서 생성자에
`allow_dangerous_requests=True`를 요구한다. 이름이 경고다.

가드를 코드에 둬야 한다. 순서는 이렇다.

1. **생성된 Cypher를 실행 전에 검사한다.** `CREATE`·`MERGE`·`DELETE`·`SET`·`REMOVE`·`DROP`·
   `LOAD CSV`·`CALL apoc.*`·`CALL db.*`가 있으면 거절한다. 통과 목록이 아니라 **거절 목록으로
   시작하지 않는다** — `MATCH`로 시작하고 `RETURN`으로 끝나는 것만 통과시키는 편이 좁다.
2. **거절은 조용한 실패가 아니라 예외다.** 삼키고 빈 결과를 내면 "그런 경로가 없다"와
   "우리가 막았다"를 부르는 쪽이 구별 못 한다.
3. Enterprise로 갈 일이 생기면 그때 읽기 전용 role로 옮기고 이 검사를 남긴다(둘은 배타가
   아니다).

### 8.2 프롬프트가 아니라 코드가 걸어야 하는 것 둘

§2.1의 둘이다. **프롬프트로 부탁하지 않는다** — LLM이 빠뜨리면 조용히 더 많은/틀린 답이 된다.

- **경로 경계**(`path_id`) — 안 걸면 서로 다른 주장이 `할인율`에서 섞인다.
- **시각 단조 증가**(`week_start`) — 안 걸면 미래에서 과거로 흐르는 인과가 나온다.

방법은 둘 중 하나다. **생성된 Cypher에 후처리로 조건을 끼워 넣거나**, 아니면 LLM에게 Cypher
전체를 맡기지 않고 **파라미터만 뽑게 하고 쿼리는 우리가 갖는다.** 후자가 이 저장소의
`thesis` 툴 방식과 같고, 질문 종류가 대여섯이면 그쪽이 짧다. §7.5의 질문 넷이 이미 그
후보다.

### 8.3 소비자는 둘이다 (2026-08-30 결정)

| 소비자 | 자리 | 의존성 |
| --- | --- | --- |
| 조회 API·화면 | `apps/api/`의 기존 층(`routes`→`service`→`repository`) | `pyproject.toml` |
| Airflow DAG | `airflow/modules/` | Airflow 이미지 requirements |

**그래서 양쪽 requirements에 다 들어간다.** Airflow 이미지가 약 80MB 는 것은 알고 무는
비용이다(§5).

**두 트리는 서로를 import하지 않는다**(저장소 규칙). 그러면 §8.1의 Cypher 검사와 §8.2의 두
조건이 양쪽에 한 벌씩 생긴다. 저장소는 그때 **중복을 허용하되 테스트로 대조하는** 쪽을
이미 쓰고 있다(`tests/realtime/test_kis_realtime.py`의 `*_match_the_airflow_collector`).
같은 형태를 쓴다 — 거절 목록과 조건 이름이 두 트리에서 같은지 테스트가 본다.

**둘이 같은 질문을 하지는 않을 것이다.** API는 화면이 누르는 것(대상 하나에서 출발하는 경로,
채널 하나가 닿은 대상)이고 DAG는 브리핑에 실을 것(그 주에 새로 생긴 연결)이다. 그래서 쿼리
목록을 공유하려 애쓰지 않는다 — 공유해야 하는 것은 **가드**이지 질문이 아니다.

### 검토 기록

- **2026-08-30** — "그래프를 Postgres 확장(AGE)으로 갈아탈까"라는 질문을 받아 이 문서와
  [market-causal-graph.md](../market-causal-graph.md) §7을 다시 읽었다. AGE는 §1에서
  이미 한 번 뺐고(그때 이유는 "relational+AGE+Neo4j 셋이 된다"), 이번에는 **AGE를 원본으로
  삼는 안**까지 봤으나 위 §7.7의 이유로 접었다. 대신 실제 목적이 **학습**이라는 것이
  드러나 §7을 새로 썼다 — 운영 경로에 매달지 않고 로컬에서 causal 데이터로 돌린다.
  §1~§6은 손대지 않았다. 이 문서가 2026-08-21에 "사용자 결정으로 관례를 깨고 넣는다"로
  시작해 미착수로 남은 이유가 선행 조건(prod 인스턴스·이미지 재빌드)이었고, §7은 그
  조건을 아예 안 만드는 형태다.
- **2026-08-30(같은 날, 뒤)** — §7을 실제로 돌렸다(§7.8). 그래프가 섰고 질문 넷이 양쪽에서
  같은 답을 냈다. **§7.5의 예상 중 하나가 틀렸다** — 3번만 갈림길일 줄 알았는데 성능은
  넷 다 무의미했고(40ms 아래), 대신 예상에 없던 것 둘이 나왔다: 가변 길이 매치가 경로
  경계를 넘는 것(발견 ①)과 시각이 역행하는 경로(발견 ②)다. 둘 다 조회를 만들 때
  기본값으로 막아야 하는 것이라 §7 조회 설계에 직접 들어간다. **보류 근거였던 "2홉이면
  거의 전체"는 causal 데이터에서 성립하지 않는다**(발견 ③) — 그것이 이 실행의 결론이다.
- **2026-08-30(같은 날, 셋째)** — 운영 인스턴스를 세우고 §1~§6을 causal 기준으로 다시 썼다.
  NAS에서 밟은 덫 셋을 §5.1에 남겼다(`/data` 소유권 — neo4j 이미지만 비root 시작, 헬스체크의
  `$$`가 셸에서 PID로 바뀌는 것, `NEO4J_AUTH`가 첫 기동에만 먹는 것). 셋 다 조용히 실패하는
  모양이라 다음 사람이 같은 자리에서 반나절을 쓴다. **`langchain-neo4j`를 미리 넣고 §8을
  썼다** — 거기서 하나가 드러났다: **community 판에는 읽기 전용 계정이 없다**(RBAC가
  Enterprise 전용). 그래서 "LLM에게 읽기 전용 계정을 준다"는 이 배포에서 성립하지 않고,
  가드가 코드로 가야 한다.
