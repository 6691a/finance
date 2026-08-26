# 4단계 — 그래프 projection: Postgres가 원본, Neo4j는 탐색용 투영

- 상위: [README.md](README.md)
- 의존: [1-storage.md](1-storage.md). [3-dag-slack.md](3-dag-slack.md)와 독립이라 병렬 진행 가능.
  단 `sync_graph` 태스크를 붙이는 자리가 3단계 DAG라 **배포는 3 뒤**다.
- 산출물: `airflow/modules/graph.py`, DAG의 `sync_graph` 태스크와 `sync_only` Param,
  `compose/local` Neo4j 서비스, requirements, `pyproject.toml` dev 의존성,
  `tests/modules/test_graph.py`
- **선행 조건: prod Neo4j 인스턴스.** 이 저장소 코드만으로 끝나지 않는다(5절).

## 1. 왜 Neo4j인가, 왜 지금인가

이 저장소 관례는 원래 스테이트풀 저장소를 안 늘리는 쪽이다(전문 검색도 외부 엔진 대신
ParadeDB in-DB BM25를 쓴다). 여기서는 그 관례를 깨고 실제 그래프 DB(Neo4j)를 붙인다 —
추론·근거를 그래프로 바로 탐색하려는 목적이 명확해서다.

지금 관계는 `Thesis -CITES-> Evidence` 한 종류라 Postgres 조인으로도 같은 질문에 답한다.
그래프가 이기는 다중 홉 질문(공유 근거로 이어진 추론 체인 등)은 아직 없다. **외부 리뷰
2회 모두 이 단계 보류를 권했다.** 그래도 지금 넣는 것은 사용자 결정(2026-08-21)이다.
4주 검증([README.md](README.md) 5절) 뒤 유지 여부를 다시 본다.

**"미러"가 아니라 "projection"이다.** Postgres 행 전부를 옮기지 않는다 — `input_state`,
`llm_model`·`prompt_version`, `thesis_evidence.detail`은 그래프에 싣지 않고, 다른 추론이
더는 인용하지 않는 Evidence 노드도 지우지 않는다(고아로 남는다). 그래프로 답하는 질문은
"무엇이 무엇을 인용했나"까지이고, 그 이상은 Postgres를 본다.

**검토했다 뺀 것 — Apache AGE.** Postgres 안에서 Cypher를 쓰는 확장이라 relational
upsert와 같은 트랜잭션으로 그래프 쓰기까지 끝낼 수 있다는 장점은 있지만, 이미 relational
테이블이 원본이라 AGE를 끼우면 표현이 relational+AGE+Neo4j 셋으로 늘고 AGE→Neo4j 동기화도
relational→Neo4j와 복잡도가 같다. Cypher 방언만 둘(AGE의 openCypher 부분집합 vs Neo4j
정식 Cypher) 유지하게 돼 뺐다. relational이 그대로 원본, Neo4j가 유일한 projection이다.

**동기화는 별도 태스크, 별도 트랜잭션이다.** 두 스토어를 같은 태스크에서 같이 쓰지
않는다 — Neo4j 쓰기가 실패해도 Postgres 쓰기는 이미 커밋된 채로 남는다(Slack 발송
실패가 DB 쓰기를 되돌리지 않는 것과 같은 이유. 분산 트랜잭션을 만들지 않는다).

## 2. 그래프 모양

- `(:Thesis {id, run_date, run_slot, as_of_at, subject_kind, subject_code, label,
  prob_up, prob_down, prob_flat, up_reasoning, down_reasoning, flat_reasoning,
  evaluated_at, actual_return_pct, actual_outcome, brier_score})` — Postgres `thesis.id`를
  그대로 키로 쓴다. `MERGE (t:Thesis {id: $id}) SET t += $props`로 멱등 갱신. 추론 컬럼은
  불변이고([1-storage.md](1-storage.md) 2절) 채점 컬럼만 나중에 채워지므로, 같은 노드를
  두 번 MERGE하는 경우는 채점 갱신뿐이다.
- **그 넷을 읽을 조회가 지금 없다.** `thesis_outcome/select_by_thesis_ids.sql`과
  `ThesisStore.stored_outcomes()`는 소비자가 없어 2026-08-26에 지웠다. 4단계를 구현할 때
  **지평 0만 읽는 조회를 새로 만든다** — 아래 각주대로 노드에 실리는 것이 그 하나뿐이라
  옛 파일(13컬럼 전부)을 되살리는 것보다 좁게 짜는 편이 맞다.
- **채점 속성 넷은 지평 0의 값이다**(2026-08-26 추가). 이 문단을 쓸 때는 채점이 `thesis`의
  컬럼이었지만 5단계가 그것을 `thesis_outcome`의 **지평 넷(0·1·3·5)짜리 다중 행**으로
  옮겼다([README.md](README.md) 2절). 노드는 단수 속성이라 어느 지평을 실을지 정해야 하고,
  **지평 0**이다 — 채점된 추론이면 항상 있는 유일한 지평이라 노드 모양이 시간에 따라
  변하지 않는다. 같은 규칙을 [12-api.md](12-api.md) 1.3절의 그래프 응답이 쓴다.
- **타입 변환.** 드라이버 매핑에 `Decimal`이 없다. `prob_*`·`actual_return_pct`·`brier_score`는
  `float`로, `run_date`는 `neo4j.time.Date`로 바꿔 넘긴다. aware `datetime`(`as_of_at`·
  `evaluated_at`)은 그대로 간다. 변환은 `graph.py`의 Pydantic 모델이 한다 — 부르는 쪽은 모른다.
- `(:Evidence {kind, ref, title, url})` — `MERGE (e:Evidence {kind: $kind, ref: $ref})
  SET e.title = $title, e.url = $url`.
- `(:Thesis)-[:INFORMED_BY]->(:Thesis)` — `thesis_precedent` 한 행당 관계 하나. 장전 추론이
  프롬프트에서 본 과거 추론이다(5-followup.md 5절). 양 끝이 다 `Thesis` 노드라 새 노드가 없고,
  양쪽 다 FK라 문자열 파싱도 없다.
- `(:Thesis)-[:CITES {rank, direction, mechanism}]->(:Evidence)` — 원 추론의
  `thesis_evidence`(`outcome_horizon_days IS NULL`) 한 행당 관계 하나. 해설의 인용은
  원 판단을 만든 근거가 아니고 지평마다 같은 ref가 반복될 수 있어 Postgres에만 남긴다.
  **exact-set으로 맞춘다**: 같은 트랜잭션 안에서 먼저
  `MATCH (t:Thesis {id: $id})-[c:CITES]->() DELETE c`로 그 추론의 기존 관계를 전부 지우고
  현재 목록을 MERGE한다. Postgres 쪽 근거는 불변이라 보통 같은 집합이 다시 들어가지만,
  부분 반영된 뒤 재시도하는 경우에도 관계가 누적되지 않게 하는 장치다. Evidence 노드는
  지우지 않는다 — 다른 추론이 인용하고 있을 수 있고, 고아 노드는 해가 없다.
- 유니크 제약도 Neo4j 쪽에 건다: `(t:Thesis)` `id` UNIQUE, `(e:Evidence)`
  `(kind, ref)` **복합 속성 유일성**(`REQUIRE (e.kind, e.ref) IS UNIQUE`). NODE KEY를
  쓰지 않는다 — Enterprise 전용이라 로컬 `neo4j:5-community`에서 `CREATE CONSTRAINT`가
  거절된다. 유일성 제약은 존재성을 강제하지 않으므로 `kind`·`ref`가 비어 있지 않은 것은
  `write_theses`가 MERGE 전에 Pydantic으로 거른다. Neo4j는 Alembic 대상이 아니라 마이그레이션
  스크립트로 관리하지 않는다 — `graph.py`가 연결 시 `CREATE CONSTRAINT IF NOT EXISTS`로
  멱등하게 보장한다.

## 3. `airflow/modules/graph.py`

`slack.py`와 같은 자리다(Airflow를 import하지 않는다, 재시도 핸들러를 안 붙인다 —
재시도는 Airflow가 한다).

```python
class GraphError(RuntimeError):
    """Neo4j가 거절했고 다시 불러도 같은 결과다(제약 위반 등)."""

def write_theses(uri, auth, *, theses, evidence) -> None: ...
```

공식 `neo4j` 파이썬 드라이버(Bolt)를 직접 쓴다 — HTTP를 손으로 안 짜는 이유와 같다.
Neo4j 예외를 분류한다: 인증·제약 위반처럼 다시 불러도 같은 것 → `GraphError`,
연결 실패(`ServiceUnavailable` 등) → `ConnectionError`로 다시 올려 Airflow가 재시도.
이번 실행 몫의 Thesis·Evidence MERGE, 기존 CITES DELETE, 새 CITES MERGE를 트랜잭션
하나(`session.execute_write`)에 담아 부분 반영을 막는다.

**드라이버 자체 재시도를 끈다.** `execute_write`는 transient 오류를 기본 30초 동안 스스로
재시도한다. "재시도는 Airflow가 한다"(`slack.py`·`llm.py`와 같은 원칙)에 맞춰
`GraphDatabase.driver(..., max_transaction_retry_time=0)`으로 만든다. 그래야 태스크 로그의
시도 횟수와 실제 호출 횟수가 같다.

`apps/core/graph.py`는 만들지 않는다. `database.py`·`redis.py`와 같은 모양(alias별
`GraphConfig`)으로 확장할 자리는 있지만 12단계 API는 같은 모양을 **Postgres에서** 읽는다.
API가 Neo4j를 조회 원본으로 채택할 때 이 파일을 만든다. 그때까지 Neo4j를 직접 읽는 방법은
Neo4j Browser나 `cypher-shell`이다.

## 4. DAG — `sync_graph` 태스크

3단계 DAG를 `build_thesis >> [notify_slack, sync_graph]`로 바꾼다.

- XCom의 슬롯 목록마다 `thesis/select_by_run.sql`(id 포함) +
  `thesis_evidence/select_by_thesis_ids.sql`로 조회해 Neo4j에 반영한다. post_close 실행이
  그날 아침 forecast의 채점 갱신까지 그래프에 반영하는 건 이 태스크뿐이다 — `notify_slack`은
  그 슬롯을 안 본다.
- **`NEO4J_URI`가 비어 있으면 `AirflowSkipException`으로 건너뛴다.** prod 인스턴스가
  서기 전에도 `build_thesis`·`notify_slack`은 정상이어야 하고, 설정 누락으로 매 실행 빨간
  태스크를 만들면 진짜 실패가 묻힌다. URI가 있는데 접속이 안 되는 것은 skip이 아니라
  `ConnectionError` 재시도다.
- **놓친 슬롯 재동기화 — `sync_only` Param.** `sync_graph`가 재시도까지 실패하면 그 슬롯은
  그래프에 없는 채로 다음 슬롯이 지나간다. 수동 트리거 때 `sync_only`(`run_date_from`·
  `run_date_to`)를 주면 `build_thesis`·`notify_slack`은 `AirflowSkipException`으로 건너뛰고
  `sync_graph`만 돈다. 이때 두 가지를 지켜야 한다:
  - **`sync_graph`의 `trigger_rule="none_failed"`.** Airflow 기본 `all_success`는 upstream이
    skip이면 downstream도 skip이다. `none_failed`라야 upstream이 성공이든 skip이든 돈다.
  - **슬롯 목록은 Param이 있으면 Param에서, 없으면 XCom에서.** skip된 `build_thesis`는
    XCom을 안 남기므로 `sync_only`일 때 XCom을 기다리면 영영 못 돈다. 범위 안의 (run_date,
    run_slot) 조합을 SQL로 뽑는다.
  - **초기 적재도 이것으로.** 3단계가 먼저 배포돼 쌓인 행은 슬롯별로 돌리지 않고
    `run_date_from`을 첫날로 주어 한 번에 넣는다. 데이터가 작고 MERGE라 멱등이다.
  별도 reconciliation DAG나 lag·checksum 관측은 만들지 않는다 — API가 Neo4j를 조회
  원본으로 채택해 어긋남이 사용자 화면에 영향을 줄 때 만든다.
- 실패 판정: `GraphError` → `AirflowFailException`, `ConnectionError` → 재시도. MERGE +
  exact-set이라 재시도가 중복 부작용을 안 만든다.
- `notify_slack`과 `sync_graph`는 서로 독립이라 병렬로 돌고, 하나가 실패해도 다른 하나는
  그대로 나간다.

## 5. 환경과 인프라

- `NEO4J_URI`·`NEO4J_USER`·`NEO4J_PASSWORD` — Slack 토큰과 같은 방식으로 `os.environ.get`.
  새 Airflow Connection/Provider를 따로 안 만든다(값 세 개뿐이라 오버킬).
- `neo4j` 파이썬 드라이버를 `compose/local/airflow/requirements.txt`와
  `compose/prod/airflow/requirements.txt`, 그리고 `pyproject.toml`의 `dev` 그룹에 추가한다.
  dev 그룹이 필요한 이유: `tests/modules/test_graph.py`가 가짜 세션을 쓰더라도 `graph.py`가
  `neo4j` 예외 타입을 import한다. **prod는 이미지 재빌드·배포가 필요하다.**
- **로컬 dev**: `compose/local/docker-compose.yaml`의 `db`/`redis` 옆에
  `neo4j` 서비스를 추가한다(공식 `neo4j:5-community` 이미지, 7474/7687 포트, named
  volume, healthcheck). prod compose·Dockerfile은 그대로 둔다.
- **compose 간 네트워크는 만들지 않는다.** 로컬 Airflow compose는 자기 메타 DB만 갖고
  데이터 DB에는 호스트 포트로 붙는다(network 정의가 없다). Neo4j도 같은 방식 —
  `NEO4J_URI=bolt://host.docker.internal:7687`. "로컬 Compose·Dockerfile은 건드리지 않는다"는
  저장소 규칙과도 맞다.
- **prod Neo4j 인스턴스 — 선행 조건.** 아직 없다. prod의 `database` 스택은 이 저장소 밖에
  있어(`compose/prod/*/docker-compose.yaml`이 `external: true` 네트워크로만 참조) 컨테이너를
  실제로 세우는 일은 NAS 쪽에서 따로 한다. 누가 세울지, `NEO4J_URI`가 컨테이너 이름으로
  갈지 고정 호스트로 갈지 정해야 `compose/prod/airflow`가 어느 네트워크에 붙을지 확정된다.
  그때까지 prod의 `sync_graph`는 `NEO4J_URI` 미설정으로 skip이고, Postgres 원본과 Slack은
  먼저 돈다. 인스턴스가 서면 `sync_only` Param으로 밀린 슬롯을 채운다.

## 6. 테스트

- `tests/modules/test_graph.py` — 가짜 Neo4j 드라이버/세션(`FakeWebClient` 패턴)으로:
  MERGE 쿼리에 실린 파라미터 모양(Thesis 속성 전체, 원 추론 Evidence kind+ref+url,
  CITES rank)과 해설 인용이 제외되는지,
  CITES DELETE가 MERGE보다 먼저 같은 트랜잭션에 실리는지, 제약 문장이 `IS UNIQUE`이고
  `NODE KEY`가 아닌지, kind·ref 빈 값이 MERGE 전에 거절되는지, `Decimal`이 `float`로·`date`가
  `neo4j.time.Date`로 바뀌어 실리는지, 드라이버가 `max_transaction_retry_time=0`으로 만들어지는지,
  제약 위반 등 → `GraphError`, 연결 실패 → `ConnectionError`. 실제 Neo4j는 안 띄운다(테스트로
  실서비스 기동 안 한다는 프로젝트 관례).
- `tests/dags/test_market_thesis_review.py`에 추가 — `build_thesis >> [notify_slack,
  sync_graph]` 구조, `sync_graph.trigger_rule == "none_failed"`, **`NEO4J_URI` 누락 시
  `sync_graph`만 skip**, `sync_only` Param으로 `build_thesis`·`notify_slack`이 skip되고
  `sync_graph`가 Param 범위로 슬롯을 뽑는지, `select_by_run`·`select_by_thesis_ids` SQL
  컬럼 vs 모델 metadata 대조.
