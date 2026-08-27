# 12단계 — 조회 API: 기록 전체를 밖에서 읽는다

- 상위: [README.md](README.md)
- 날짜: 2026-08-26
- 상태: **구현 완료**(2026-08-27). 마이그레이션이 없어 코드 배포만으로 뜬다.
  검증은 `uv run pytest tests -q`와 `uv run ruff check`.
- 의존: [1-storage.md](1-storage.md)(테이블 넷), [5-followup.md](5-followup.md)(채점·해설),
  [11-expected-return.md](11-expected-return.md)(기대 등락률 두 칸 — 없어도 뜨지만 응답에
  칸이 빈다), [4-graph.md](4-graph.md)(그래프 응답이 그 노드·엣지 이름을 그대로 쓴다),
  [13-llm-ledger.md](13-llm-ledger.md)(툴 호출 원장 — **13을 먼저 하는 것이 낫다.** 나중에
  하면 상세 응답 모델을 다시 고친다)
- 산출물: 새 패키지 `apps/api/`(모듈 여섯 + `schemas/` 패키지), `compose/prod/api/`·`compose/local/api/`(각 넷),
  `justfile` 태스크 여섯, `pyproject.toml`에 fastapi·uvicorn, `tests/api/`와
  `tests/config/test_api_stack.py`, **`.claude/CLAUDE.md`·`.codex/AGENTS.md` 구조 표의
  `apps/api/` 행**(두 문서는 함께 갱신한다)과 루트 `README.md` 배포 절의 스택 행.
  **마이그레이션은 없다**(6절 "확인 끝")
- **화면은 이번 범위가 아니다.** JSON API까지다(사용자 결정 2026-08-26). 프론트는 다음
  배포 단위다.

## 0. 왜 — 쌓는 것의 대부분을 볼 길이 없다

Slack이 보여 주는 것은 **채택된 방향 하나의 확률·이유, 그리고 근거 제목 세 개**다
(`thesis.render.render_blocks`, `SLACK_EVIDENCE_LIMIT = 3`). 나머지는 전부 DB에만 있다.

| DB에 있는 것 | Slack | 지금 보는 법 |
| --- | --- | --- |
| 세 방향 확률과 이유 셋 전부 | 채택 방향만 | `psql` |
| 인용 근거 전부(`evidence_kind`·`ref`·`detail`·반대 방향 근거) | 상위 3개, 제목·URL만 | `psql` |
| 지평별 채점(`actual_return_pct`·`brier_score`) | **안 나간다**(2026-08-21 결정) | ops 브리핑의 **집계**만 |
| 사후 해설·판정(`narrative`·`verdict`) | **안 나간다** | ops 브리핑의 **건수**만 |
| 프롬프트에 실린 관측 상태(`input_state`) | 안 나간다 | `psql` |
| 본 과거 추론(`thesis_precedent`) | 안 나간다 | `psql` |
| `tool_rounds`·`llm_model`·`prompt_version` | 안 나간다 | `psql` |

Slack에 이것들을 더 싣는 것은 답이 아니다. **읽는 사람이 다르다** —
`render_blocks` 도크스트링이 그 결정을 이미 적어 뒀다("이 메시지는 오늘 시장을 보는 사람이
읽고, 우리 추론이 잘 맞고 있나는 운영자가 본다"). 필요한 것은 파고들 수 있는 **다른 자리**다.

`README.md` 4절이 `apps/core/graph.py`를 "그래프를 **읽는** 소비자가 생길 때 만든다"고
미뤄 뒀다. 이 API는 먼저 Postgres에서 같은 응답 모양을 만들고, Neo4j를 조회 원본으로
채택할 때 그 adapter를 붙인다. 동시에 `apps/realtime/__init__.py`가 예고해 둔
"(앞으로 올) FastAPI"의 첫 실물이다.

## 1. 엔드포인트 넷

```
GET /healthz
GET /api/theses?from=&to=&slot=&subject_code=&limit=&offset=
GET /api/theses/{thesis_id}
GET /api/theses/{thesis_id}/graph
```

**상세와 평가를 나누지 않는다.** 상세 화면은 언제나 둘 다 필요하다. 라우트를 가르면
클라이언트가 매번 두 번 부르고 우리는 조인을 두 번 쓴다.

### 1.1 목록

| 파라미터 | 뜻 | 기본 |
| --- | --- | --- |
| `from`·`to` | `run_date` 구간(KST 날짜, 양끝 포함) | `to` = 오늘, `from` = `to` − 13일 |
| `slot` | `RunSlot` 값. 여러 번 줄 수 있다 | 전부 |
| `subject_code` | 대상 코드. 여러 번 줄 수 있다 | 전부 |
| `limit`·`offset` | 쪽 나누기 | 50 / 0 (`limit` 상한 200) |

정렬은 `as_of_at DESC, subject_kind, subject_code, id`다. **`run_slot`으로 정렬하면 안 된다** —
문자열이라 `intraday_afternoon` → `intraday_midday` → `intraday_morning` → `post_close` →
`pre_close` → `pre_open` 순이 되어 **시간이 뒤집힌다.** 슬롯의 진짜 시간 키는 `as_of_at`이다.

응답 항목은 **목록에 필요한 것만** 담는다. `input_state`·이유 셋·근거는 싣지 않는다.

```json
{"items": [{"id": 812, "run_date": "2026-08-26", "run_slot": "intraday_midday",
  "as_of_at": "2026-08-26T03:35:00Z", "subject_kind": "index", "subject_code": "KOSPI",
  "label": "코스피", "prob_up": 0.34, "prob_down": 0.44, "prob_flat": 0.22,
  "up_return_pct": 0.8, "down_return_pct": 1.2,
  "graded_horizons": 2, "narrated_horizons": 1, "mean_brier": 0.51}],
 "limit": 50, "offset": 0, "has_more": true}
```

`has_more`는 `limit + 1`건을 읽어 판단한다. `count(*)`를 따로 세지 않는다 — 총 건수를
쓰는 화면이 아직 없다.

`graded_horizons`·`narrated_horizons`·`mean_brier`는 `thesis_outcome`을 왼쪽 조인해 집계한
요약이다. 목록에서 "평가가 붙었나"를 봐야 상세로 들어갈 이유가 생긴다.

### 1.2 상세

한 응답에 다섯 묶음을 담는다.

```
thesis     — 목록 항목 + 이유 셋 + input_state + tool_rounds·llm_model·prompt_version·dag_run_id
evidence   — 원 추론이 인용한 근거 전부(outcome_horizon_days IS NULL). rank 순
outcomes   — 지평별 채점과 해설. 그 해설이 인용한 근거를 각 지평 안에 중첩한다
precedents — 프롬프트에서 본 과거 추론(id·run_date·run_slot·label과 그 확률)
llm_run    — 이 추론을 만든 대화(13단계). id·모델·판·왕복·호출 수·상태
```

툴 호출은 thesis가 아니라 `llm_run`에 속한다. 대화 하나가 여러 thesis를 만들고 실패
대화에는 thesis가 없으므로 같은 호출 배열을 모든 상세에 복제하지 않는다. 이 응답은
`llm_run.id`만 연결하고, 실행 목록·툴 단건 API는 [14-web-ui.md](14-web-ui.md)가 실행
단위로 추가한다. 13단계가 없거나 리비전 전 행이면 `llm_run`은 `null`이다.

해설 대화(`thesis_outcome.narration_run_id`)도 id와 요약만 각 지평 안에 중첩한다.

`evidence` 항목은 **DB에 있는 칸을 다 낸다**: `kind`, `ref`, `title`, `url`, `direction`,
`mechanism`, `detail`(JSONB 그대로), `rank`. Slack이 버리는 `kind`·`ref`·`detail`이 여기서는
핵심이다 — 근거가 기사인지 공시인지 매크로 변화인지, 당시 수치가 얼마였는지가 그 셋에 있다.

`outcomes` 항목은 채점 넷(`evaluated_at`·`actual_return_pct`·`actual_outcome`·`brier_score`),
11단계의 둘(`predicted_return_pct`·`return_error_pct`), 해설 다섯(`narrative`·`verdict`·
`narrative_at`·`llm_model`·`prompt_version`), 그리고 그 지평 해설이 인용한 근거 배열이다.

**`url`이 붙는 근거는 `document`와 `disclosure` 둘뿐이다.** `macro_change`와
`technical_signal`은 링크할 곳이 없어 항상 `null`이다(`thesis.toolbox`가 그렇게 만든다).
클라이언트가 그것을 알 수 있게 `kind`를 함께 주는 것이지, `url`이 없다고 항목을 감추지 않는다.

### 1.3 이웃 그래프

[4-graph.md](4-graph.md) 2절이 노드·엣지를 이미 못 박아 뒀다. **응답 스키마가 그 이름을
글자 그대로 쓴다.** 지금은 Postgres에서 읽고, Neo4j를 API 조회 원본으로 채택할 때 읽는
곳만 갈아끼운다. 그때 응답이 바뀌면 클라이언트를 같이 고쳐야 하므로 처음부터 그쪽
모양으로 낸다.

```json
{"nodes": [
   {"id": "thesis:812", "labels": ["Thesis"], "properties": {"id": 812, "run_date": "2026-08-26"}},
   {"id": "document:4471", "labels": ["Evidence"],
    "properties": {"kind": "document", "ref": "document:4471", "title": "...", "url": "https://..."}}],
 "edges": [
   {"type": "CITES", "start": "thesis:812", "end": "document:4471",
    "properties": {"rank": 1, "direction": "down", "mechanism": "..."}},
   {"type": "INFORMED_BY", "start": "thesis:812", "end": "thesis:790", "properties": {}}]}
```

- **노드 id 규약은 4-graph.md가 안 정한 유일한 칸이다.** Neo4j element id는 불투명해서
  응답에 실을 수 없고, 두 라벨의 키 모양도 다르다(`Thesis.id` 정수 vs `Evidence (kind, ref)`
  복합). 저장소에 이미 있는 문법을 재사용한다 — **`Evidence` 노드 id는 `evidence_ref` 그
  자체**(`document:4471`)이고 **`Thesis`는 `thesis:812`**다. `evidence_ref`의 접두가
  `evidence_kind`와 글자 그대로 같다는 것을 모델 주석이 이미 보장하고, `thesis`는
  `ThesisEvidenceKind` 값 넷에 없어 충돌하지 않는다.
- **`(:Thesis)` 속성은 4-graph.md 2절 목록 그대로다.** `input_state`·`llm_model`·
  `prompt_version`·`thesis_evidence.detail`은 싣지 않는다 — 그 문서가 "미러가 아니라
  projection"이라고 정한 그대로다. 상세 응답이 이미 준다.
- **채점 속성 넷은 지평 0의 값이다.** 4-graph.md 2절이 `(:Thesis)`에 `evaluated_at`·
  `actual_return_pct`·`actual_outcome`·`brier_score`를 **단수로** 실었는데, 5단계가 채점을
  `thesis_outcome`의 **지평 넷짜리 다중 행**으로 옮기면서 그 문장이 낡아 있었다.
  지평 0으로 채운다 — 채점된 추론이면 항상 있는 유일한 지평이라 노드 모양이 시간에 따라
  변하지 않는다. **4-graph.md 2절에 같은 각주를 달아 뒀다**(2026-08-26).
- **범위는 1홉이다.** 중심 추론, 원 판단이 인용한 `Evidence`
  (`outcome_horizon_days IS NULL`), `INFORMED_BY`로 이어진 과거
  추론(**나가는 것과 들어오는 것 양쪽**). 나가는 쪽만 주면 "이 판단을 누가 참고했나"를 못 본다.
- 해설이 인용한 근거(`outcome_horizon_days` NOT NULL)는 상세 outcome에만 낸다. 같은 ref가
  지평 1·3·5에 반복될 수 있고 원 판단의 근거가 아니므로 이 화면의 `CITES`에 섞지 않는다.
- 응답 안에서 `(type, start, end)`는 유일하다. API edge에는 id를 만들지 않고 프런트가 이
  세 값을 이어 안정적인 Cytoscape id를 만들 수 있게 한다.
- **`(:Outcome)` 노드는 만들지 않는다.** 5-followup.md 7절이 4단계 뒤로 미뤄 둔 것이고,
  평가는 상세 응답이 이미 전부 준다. 그래프에 넣으면 4단계 구현 전에 우리가 먼저 모양을
  정해 버리는 셈이 된다.

### 1.4 `/healthz`

프로세스가 살아 있으면 `{"status": "ok"}`. 컨테이너 healthcheck가 이것을 친다.

**DB를 치지 않는다.** API를 재시작해도 DB 장애는 안 고쳐지는데 healthcheck가 DB를 보면
DB 깜빡임이 컨테이너 재시작 루프가 된다. DB가 죽었는지는 요청이 500으로 답해 알리고,
그것을 보는 자리는 Sentry다.

## 2. 서비스 자리와 모양

`apps/` 아래 새 패키지 `apps/api/`. `apps/realtime/`의 관례를 그대로 따른다.

| 파일 | 무엇 | 무엇을 아나 |
| --- | --- | --- |
| `apps/api/__init__.py` | 무엇이고 왜 이렇게 배포되는지 | |
| `apps/api/main.py` | 진입점. 설정을 읽고 Sentry를 붙이고 컨테이너를 채운다 | 설정 |
| `apps/api/container.py` | dependency-injector 컨테이너. **composition root** | 조립 |
| `apps/api/app.py` | `create_app(container)`. lifespan이 엔진을 정리한다 | 조립 |
| `apps/api/routes/` | 리소스마다 `APIRouter` 하나. `@inject`로 서비스를 받는다 | **HTTP만** |
| `apps/api/service/` | 행을 응답 계약으로. `build_detail`·`project_graph` | **계약만** |
| `apps/api/repository/` | 세션을 열고 행 묶음을 준다 | **store만** |
| `apps/api/schemas/` | 응답 계약 | |

**뒤의 넷은 패키지이고 리소스마다 파일 하나다** — 네 폴더에 `thesis.py`가 하나씩,
그 층의 리소스들이 공유하는 것만 `common.py`, `__init__.py`는 재수출만. 지금 리소스가
하나뿐이라도 파일 하나로 두면 둘째 리소스가 남의 코드 위에 쌓인다.

**층을 셋으로 가른 이유**는 리포지토리가 응답 모양을 알면 store를 갈아끼울 때 계약까지
함께 흔들려서다 — 이 기능의 전제(4-graph.md의 "Neo4j로 갈아끼워도 응답은 그대로")가
그 경계 위에 선다. 지금 서비스는 "조회 하나 → 매핑 하나"라 얇지만, 통과 층이 되지 않게
매핑을 모듈 수준 순수 함수로 두고 클래스는 순서만 엮는다.

**서비스 이름은 `api`다**(compose 서비스·이미지·컨테이너). 리소스는 늘어날 예정이라
`thesis`는 라우트(`/api/theses`)와 파일 이름(`<층>/thesis.py`), 컨테이너 provider
(`thesis_repository`·`thesis_service`)에만 둔다.

**리소스를 더할 때 손대는 곳은 네 폴더의 새 파일과 `routes/__init__.py`의 `routers`뿐이다.**
wiring이 `packages=["apps.api.routes"]`라 `container.py`는 provider만 늘고, `app.py`는
`routers`를 순회하므로 안 갈린다.

- **`settings` import는 함수 안에서** 한다. `apps/realtime/main.py`가 그렇게 하는 이유와
  같다 — 테스트가 `config.yaml` 없이 이 모듈을 import한다. `settings`는 모듈 싱글턴이라
  import 순간 파일을 읽고 검증한다.
- **`create_app()`이 컨테이너를 인자로 받는다.** 컨테이너는 `settings`·`db_alias`를
  `providers.Dependency()`로 밖에서 받아 import만으로 `config.yaml`을 요구하지 않는다.
  그래서 테스트가 접속 없이 앱을 통째로 세운다 — `create_async_engine`은 연결하지 않는다.
- **의존성은 생성자로 주입한다.** 컨테이너가 `Factory(ThesisReadRepository,
  session_factory=...)`와 `Factory(ThesisReadService, repository=...)`를 선언하고 라우터가
  `@inject`로 서비스를 받는다. 업무 코드가 `container.thesis_service()`를 직접 부르면 그건
  Service Locator이지 주입이 아니다 — 컨테이너 이름이 보이는 자리는 `WiringConfiguration`이
  지정한 `routes.py` 하나다.
- **provider 수명이 뜻을 갖는다.** 엔진 풀만 `Singleton`(프로세스에 한 벌)이고 리포지토리·
  서비스는 `Factory`다. `Singleton`으로 두면 나중에 요청 상태를 담게 될 때 조용히 새어 나간다.
- **리포지토리는 세션 팩토리를 받는다**(세션이 아니다). 조회 단위로 열고 닫으며,
  응답 하나는 **한 세션 안**이다 — 상세가 여섯 번 묻지만 커넥션은 한 번 빌린다.
  `apps/realtime/repository.py`가 같은 모양이다. 커밋은 없다.
- `apps/core/container.py`는 **쓰지 않는다.** 본문에서 `settings`를 import해 파일 없이는
  import조차 안 되고, `default_session_factory`가 별칭을 `default`로 못 박아 뒀다 —
  이 서비스가 쓸 별칭이 아니다.
- `sentry_sdk.init(settings.sentry_*)`을 조립 직전에 부른다. 값 세트는 realtime과 같다
  (`.claude/CLAUDE.md`: "새 상주 서비스(FastAPI 등)도 같은 `settings.sentry_*`로 init한다").
- **조회는 클래스가 쥔다.** 세션 팩토리와 별칭이 여러 호출에 걸쳐 안 변한다 — 저장소의
  "클래스와 함수를 가르는 기준" 그대로다. `apps/realtime/repository.py`의
  `RealtimeRepository`가 같은 모양이다.
- **읽기 전용을 연결 층에서 강제한다.** 쓰기 라우트를 안 만드는 것으로 그치지 않는다.
  `config.yaml`에 `read_only: true`인 `prod` 별칭이 **이미 있고**, `main.py`의 상수
  `DB_ALIAS`가 그것을 가리킨다. `read_only`가 아니면 **시작을 거부한다** — realtime이
  대상 별칭에 `read_only: false`를 요구하는 가드의 정확한 반대다. `apps/core/database.py`의
  `_connect_args_for`가 그 연결에 `default_transaction_read_only = on`을 걸어, 실수로
  쓰기가 들어가도 Postgres가 거절한다.
- **별칭은 환경변수가 아니다.** `read_only` 별칭이 하나뿐이라 개발·운영 어디서나 값이
  같다 — 손잡이가 아닌 것을 환경변수로 두면 `.env` 파일 둘과 그 정합성 검사가 딸려 온다.
  로컬 DB를 가리키는 read_only 별칭이 생기면 그때 상수를 고치거나 환경변수를 다시 넣는다.
- **포트·바인드만 `os.environ`이다.** `API_HOST`(기본 `0.0.0.0`), `API_PORT`(기본 `8000`).
  `Settings`는 yaml만 읽고 그 파일은 컨테이너 여럿이 공유하는 접속 정보라, "이 컨테이너가
  어디에 바인드하나"는 거기 속하지 않는다. **둘도 `.env.sample`에 넣지 않는다** —
  컨테이너 안 바인드는 늘 `0.0.0.0:8000`이고 밖으로 보이는 포트는 compose의 `ports:`가
  정한다. 손잡이를 두 곳에 두면 어긋난다. **어느 스택도 `.env`를 갖지 않는다.**
- **`uvicorn`을 코드에서 띄운다**(`uvicorn.run(create_app(...))`). `uvicorn apps.api.app:app`
  형태는 모듈 수준 `app = create_app()`을 요구하고, 그러면 import만으로 `config.yaml`이
  필요해져 위의 지연 import 규칙이 그 자리에서 깨진다. `log_config=None`을 준다 — uvicorn
  기본 dictConfig가 root 핸들러를 갈아치워 realtime과 로그 형식이 갈린다.

### 2.1 조회는 ORM으로 짠다

`airflow/sql/`은 Airflow 전용이라 공유하지 않는다(`modules/sql.py`가 `AIRFLOW_HOME` 아래를
읽는다). 그리고 지금 있는 SQL 어느 것도 이 API가 필요한 조회가 아니다 —
`(run_date, run_slot)` 정확 일치, 대상별 과거, 당일 앞 슬롯 셋뿐이고 **날짜 구간 목록도
단건 조회도 없다.**

`apps/models/`가 이미 ORM이므로 `select()`로 짠다.

**`selectinload`를 쓰지 않는다.** 그것은 모델에 `relationship()`을 다는 것을 전제하는데
지금 `thesis` 계열에는 관계 선언이 하나도 없고, 불변 테이블 모듈을 조회 편의로 고치는
자리가 아니다(6절 1번). 대신 **`WHERE thesis_id = ANY(:ids)` 배치 조회 + 파이썬 그룹핑**이다.
`airflow/sql/postgres/thesis_evidence/select_by_thesis_ids.sql`이 이미 정확히 그 계약이다.

| 응답 | 왕복 |
| --- | --- |
| 목록 | 1 (`thesis`) + 1 (`thesis_outcome` 집계) |
| 상세 | `thesis` 1 + 근거 1 + precedent 엣지 1 + precedent 라벨용 `thesis` 1 + `thesis_outcome` 1 |
| 그래프 | precedent 엣지 1 → id 집합 → `thesis` 1 + 근거 1 |

**id 개수와 무관하게 왕복 수가 고정이다.** 상세는 id 하나짜리 목록을 넘긴 같은 코드
경로라 함수가 갈리지 않는다.

### 2.2 숫자와 시각의 표현

- **확률·등락률·점수는 JSON number로 낸다.** `Decimal`을 그대로 두면 Pydantic이 문자열로
  직렬화해 클라이언트가 매번 파싱한다. 4-graph.md도 Neo4j로 보낼 때 `Decimal`을 `float`으로
  바꾸기로 이미 정했고, 유효자리가 `Numeric(5,4)`·`Numeric(8,4)`라 왕복이 안전하다.
  **네 응답에 같은 규칙을 쓴다** — 그래프만 `float`, 나머지는 `Decimal`로 가르면 같은 값이
  라우트마다 다른 타입으로 나간다.
- **시각은 UTC ISO 8601에 `Z`다.** 변환하지 않는다. 시간대 변환은 프론트 몫이라는 것이
  프로젝트 규칙이다. `run_date`만 KST 세션 날짜이고 그 사실을 필드 주석에 적는다.
  **`Z`는 공짜가 아니다** — Pydantic 기본 직렬화는 `+00:00`이다. `PlainSerializer`를 단
  annotated 타입을 만들어 aware datetime 필드마다 쓰고, 테스트가 그것을 검사한다.

## 3. 배포

`compose/prod/`는 지금 `kis-realtime` 하나이고 **포트를 하나도 열지 않는다.** 웹은 열어야
하므로 스택을 따로 둔다 — `compose/prod/api/`와 `compose/local/api/` 한 쌍이다.
`compose/prod/airflow/`가 이미 그 형태다.

- 코드는 이미지에 굽지 않는다. `${CODE_DIR}/apps:/app/apps:ro`와 `config.yaml:ro` 바인드
  마운트. **경로 깊이가 `compose/prod/`와 다르다** — 한 칸 더 내려가므로 `../../..`이고,
  `compose/local/realtime/`이 이미 그 깊이라 그쪽을 베낀다. `apps/`가 바인드 마운트라
  `just deploy-api`은 `up -d` 뒤 `restart`를 불러야 새 코드가 뜬다(realtime과 같다).
- 네트워크는 external `database`. `config.yaml`의 DB 주소가 그 스택의 컨테이너 이름이다.
- 포트는 직접 매핑이다. 저장소에 reverse proxy가 없고 Airflow(8080)도 그렇게 뜬다. 운영 `8000:8000`, 로컬 `18000:8000`(로컬은 앞자리 1을 붙이는 관례).
  **`127.0.0.1:8000:8000`으로 묶지 않는다** — NAS 자기 자신에서만 보이게 되어 같은 망의
  다른 기기가 못 본다. 사설망 한정은 앱이 아니라 NAS 방화벽·공유기가 지킨다.
- healthcheck는 slim 이미지에 `curl`이 없으므로
  `python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=5)"`다.
- **인증을 붙이지 않는다.** 내부는 LAN, 외부는 **Tailscale**로만 닿는다는 결정
  (2026-08-26)이다. 경계를 지키는 것은 앱이 아니라 tailnet과 NAS 방화벽이고, 같은 망의
  Airflow UI도 같은 조건이다.
- **`.env` 파일이 없다.** 읽을 별칭은 `main.py`의 상수이고 나머지는 `config.yaml`이다.
  `test_api_stack.py`가 어느 스택도 서비스 전용 환경변수를 안 갖는 것을 확인한다.
- `justfile`에 태스크 여섯(`web`·`web-down`·`web-prod`·`web-prod-down`·`build-api`·`deploy-api`)과
  `deploy` 의존에 `deploy-api`을 더한다. 이름과 모양은 realtime 짝을 그대로 따른다.
- `tests/config/test_realtime_stack.py`가 local·prod의 `requirements.txt`·`Dockerfile`·
  `.env.sample`이 주석 빼고 같을 것을 강제한다. **`test_api_stack.py`를 같은 모양으로 둔다.**
  거기에 둘을 더한다 — 운영 compose가 포트를 열고 있는지, Dockerfile `CMD`가
  `python -m apps.api.main`인지.

### 3.1 의존성

`pyproject.toml`에 `fastapi`와 `uvicorn` 둘만 는다. `httpx`는 이미 루트 의존성이라
`ASGITransport` 테스트가 새 의존성 없이 돈다.

**`uvicorn[standard]`가 아니다.** uvloop·httptools·watchfiles·python-dotenv·colorama 다섯을
더 끌고 오는데, 사설망에서 초당 한 자릿수 요청을 받는 읽기 API에 잴 수 있는 이득이 없다.
`fastapi[standard]`도 같은 이유로 안 쓴다.

compose의 `requirements.txt`는 realtime 것에서 `websockets`와 `tzdata`를 빼고 `fastapi`·
`uvicorn`을 넣은 것이다. WebSocket을 안 쓰고, 시각을 UTC로만 내보내 `zoneinfo`를 부를 일이
없다. **`redis`는 남긴다** — `apps.core.config`가 `apps.core.redis`의 `RedisConfig`를
import해서 없으면 설정 로딩이 죽는다. 연결은 하지 않는다.

## 4. 테스트

실 DB 없이, DAG 실행 없이 도는 것만 만든다(저장소 규칙).

- `tests/api/test_routes.py` — `httpx.ASGITransport`로 앱을 두드린다. 라우트가 있는지,
  응답 모델이 맞는지, 없는 id가 404인지, `limit` 상한이 걸리는지. 라우트 목록 자체
  (`{(r.path, r.methods)}`)도 리터럴과 대조한다 — 라우터를 등록 안 한 실수를 잡는 것이
  DAG 구조 테스트와 같은 성격이다.
- **가짜는 세션이 아니라 리포지토리에 둔다.** 가짜 `AsyncSession.execute()`가 SQLAlchemy
  `Result`를 흉내 내려면 `.scalars().all()` 체인을 다 구현해야 하는데 그건 SQLAlchemy를
  테스트하는 것이다. 버그가 사는 자리는 라우팅·매핑·직렬화이고 `dependency_overrides`로
  리포지토리를 갈아끼우면 셋을 다 지나간다.
- `tests/api/test_graph_schema.py` — 그래프 응답이 4-graph.md의 라벨·관계 이름과 속성
  집합을 그대로 쓰고, 원 추론 근거만 포함하며, `(type, start, end)`가 응답 안에서
  유일한지. **라벨·관계 이름을 상수로 못 박고 테스트가 그 문서 값과 대조한다.**
- `tests/config/test_api_stack.py` — local·prod 스택 정합성.
- `tests/api/test_repository.py` — 쿼리는 `stmt.compile(dialect=postgresql.dialect())`를
  문자열로 만들어 WHERE·ORDER BY·`= ANY`·LIMIT이 들어갔는지 본다. **"목록 쿼리에 LIMIT이
  있다"를 따로 건다** — 빠졌을 때의 사고 크기가 다르다.
- `tests/api/test_main.py` — `config.yaml` 없이 `apps.api.main`을 import할 수 있는지,
  `read_only`가 아닌 별칭에서 시작이 거부되는지. 별칭 가드를 작은 순수 함수로 빼면
  그대로 부를 수 있다.
- 실제 결과는 운영 DB **읽기 전용** 확인으로 대신한다(사용자가 돌린다).

## 5. 만들지 않는 것

- **인증·세션·사용자 테이블** — 사설망 결정. 인터넷에 열게 되면 그때가 별도 단계다.
- **커서 페이지네이션** — `limit`/`offset`으로 충분하다. 추론은 슬롯당 대상 몇 개라
  하루 수십 행이다.
- **캐시·레이트리밋** — 읽는 사람이 한 자리 수다.
- **쓰기 경로** — 기록은 불변이다(1-storage.md). 사람이 행을 고치는 경로를 두지 않는 것이
  이 기능의 원칙이다.
- **`(:Outcome)` 그래프 노드** — 1.3절.
- **프론트엔드** — 다음 배포 단위. 다만 FastAPI가 공짜로 주는 `/docs`(Swagger UI)는
  **끄지 않는다.** 14단계 화면 전에는 유일한 화면이고, 그 뒤에도 API 계약 확인에 쓴다.
- **`apps/core/graph.py`** — Neo4j를 아직 안 읽는다. Neo4j를 API 조회 원본으로 채택해 이
  API의 그래프 라우트가 읽는 곳을 갈아끼울 때 만든다.

## 6. 남은 확인 (spike)

1. **모델에 `relationship`을 붙일지.** 지금 `apps/models/analysis/thesis.py`에 관계 선언이
   없다. 붙이면 `selectinload` 한 줄로 끝나지만 ORM 모델이 웹 때문에 바뀐다. 안 붙이면
   `IN` 조회 셋을 리포지토리가 짠다. **후자를 권한다** — 모델은 마이그레이션의 원본이라
   조회 편의로 건드리는 자리가 아니다.
2. **`thesis_precedent.precedent_id` 단독 인덱스.** UNIQUE가 `(thesis_id, precedent_id)`라
   선두만 커버한다. 이웃 그래프가 **들어오는** `INFORMED_BY`를 읽으므로 인덱스가 필요하다.
   [11-expected-return.md](11-expected-return.md)의 리비전에 함께 넣는다.

### 확인 끝 (2026-08-26 운영 DB 읽기 전용 실측 포함)

- **죽은 코드 셋을 지웠다**(2026-08-26). `thesis.render.SLACK_REVIEW_HORIZON`,
  `ThesisStore.stored_outcomes()`와 `StoredOutcome`, `thesis_outcome/select_by_thesis_ids.sql`
  넷 다 호출자가 없었다 — 5-followup.md 7절의 Slack T+5 해설 섹션이 구현되지 않은
  흔적이다. 이 API는 `apps/models`의 ORM으로 읽어 그 SQL과 무관하다.
- **`input_state`는 작다.** 38행 기준 평균 599B, p95·최대 **825B**다. 상세 응답에 통째로
  실어도 무해하니 별도 라우트로 떼지 않는다. 툴 결과 전문은 애초에 이 응답에 없다
  (실행별 단건 API가 낸다).
- **인용 근거는 추론당 평균 5.8건, 최대 11건**이고 `detail`은 평균 420B·최대 1,241B다.
  상세 응답에 근거를 전부 실어도 수십 KB를 넘지 않는다.
- **노출 범위가 정해졌다**(사용자 2026-08-26): **내부는 LAN, 외부는 Tailscale**이다.
  둘 다 NAS 호스트에 직접 닿으므로 `ports: ["8000:8000"]` 직접 매핑이 그대로 답이고
  reverse proxy도 인증도 필요 없다. **`127.0.0.1:8000:8000`으로 묶으면 안 된다** —
  LAN도 tailnet도 NAS 자기 자신이 아니라 밖에서 들어온다.
- **4-graph.md 2절에 각주를 달았다**(2026-08-26). `(:Thesis)`의 채점 속성 넷이 지평 0의
  값이라는 것을 그 문서가 직접 말한다 — 4단계를 구현할 사람이 이 API를 안 읽어도 어긋나지
  않는다.
- **`config.yaml`에 `read_only: true`인 `prod` 별칭이 이미 있다.** 별칭을 새로 만들 필요가
  없고 `main.py`의 상수 `DB_ALIAS`가 그것을 가리킨다.
- **로컬도 그 별칭을 본다.** `just dev`의 DB를 보는 `default`는 쓰기가 열려 있어 이 서비스가
  거부한다. 로컬에서 로컬 DB를 보려면 `config.yaml`에 read_only 별칭을 더하고 `DB_ALIAS`를
  고친다 — 그때 `migrations/env.py`가 마이그레이션 설정이 없는 별칭을 어떻게 보는지 먼저
  확인한다.
- **`thesis` 계열에 새 인덱스가 필요 없다**(2번의 `precedent_id` 하나를 빼면).
  목록은 `uq_thesis_natural_key`의 `run_date` 선두를 타고, 나머지 조회는 전부
  `thesis_id` 선두 UNIQUE를 탄다. **이 단계는 마이그레이션이 없다.**
