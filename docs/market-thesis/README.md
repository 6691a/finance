# 시장 추론(thesis) 기록 설계 — 개요

- 날짜: 2026-08-20 (2026-08-21 리뷰 반영 후 단계별 문서로 분리, 2026-08-22 6·7단계 추가,
  2026-08-24 8단계 추가)
- 상태: 1·2·3·5·6·7·8단계 구현 완료(6·7단계는 2026-08-22, 8단계는 2026-08-24,
  리비전 넷 운영 반영 전), 4단계(그래프) 미착수. 운영 배포 전 선행 조건은 5절

한 문서로 쓰기엔 범위가 커서(모델·리비전, 모듈 둘, DAG, SQL 열 개, 테스트 넷, compose·
requirements) **배포 단위(worktree/PR 하나)마다 문서를 나눴다.** 이 파일은 공통 원칙과
단계 순서만 갖는다. 세부는 각 단계 문서가 갖고, 서로는 파일 링크로 참조한다.

## 0. 문제 — 데이터는 쌓이는데 추론이 없다

분봉·공시·평가된 문서·매크로 시세가 전부 쌓이고 있지만, 그것들을 놓고 "그래서 시장이
왜 움직였나 / 오늘 어떻게 움직일 것 같나"를 말하는 층이 없다. 필요한 것은:

- **장후**: "지수·종목이 오늘 올랐다 → 이유는 이것 같다" — 사후 해석(review)
- **장전**: "오늘 오를 것 같다 → 근거는 이런 밤사이 지수·기사" — 전망(forecast)

**맞고 틀림은 목적이 아니다.** 정답은 시간이 지나야 알고, 맞추기도 어렵다. 목적은
"어떤 정보를 근거로 어떤 결론을 냈다"가 기록으로 남는 것이다. 추론과 근거가 노드·엣지로
쌓이고, **Postgres에 쓰인 뒤 Neo4j에도 투영**돼 추론 이력을 그래프로 바로
탐색할 수 있다. 채점이 누적되면 정확도를 잰다.

산출물은 **Postgres가 원본, Neo4j가 탐색용 projection이다**([4-graph.md](4-graph.md)). 소비자는 Slack으로
보는 사람과, 그래프로 추론 이력을 탐색하는 사람·정확도 집계 둘 다다.

## 1. 원칙 — 근거는 고정 풀이 아니라 모델이 조회한다

- 프롬프트에는 **관측 상태만** 준다. "코스피 +1.61%", "SK하이닉스 전일 -2.1%".
  관측 상태는 전부 SQL이 계산한다.
- 왜인지 알아내는 데 필요한 정보는 모델이 **읽기 전용 툴을 호출해** 스스로 가져온다.
  최근 문서, 공시, 매크로 변화 — 어떤 것을 얼마나 볼지는 모델이 정한다.
- 모델이 실제로 인용한 근거만 저장한다. 툴이 돌려준 항목에는 전부 `ref`가 붙어 있고,
  답변의 `claims`(ref·방향·경로)는 툴 결과 레지스트리로 검증한다. 목록 밖 ref는 버린다.
- **모든 조회의 기준 시각은 벽시계가 아니라 슬롯이 정하는 `as_of_at`이다.** 이것은
  **event-time cutoff**다 — 현재 DB에서 확인 가능한 범위에서 `as_of_at` 이후 감지·평가·갱신된
  행을 뺀다. 과거 시점을 완전히 복원하지는 못한다: `document`는 본문·평가를 같은 행에
  덮어쓰고(`document/upsert.sql`, `assessed_*`) 버전 이력을 두지 않기 때문이다. 재실행이
  분 단위 뒤의 Airflow 재시도뿐이라 그 차이는 무시한다.
- **첫 성공본은 불변이다.** 같은 (날짜, 슬롯)에 추론 행이 이미 있으면 LLM을 다시 부르지
  않는다. LLM은 재호출마다 답이 달라서 덮어쓰면 최초 판단이 사라진다. 재실행은 기존 행을
  읽어 다음 태스크로 넘길 뿐이다.
- 숫자 규칙은 기존 LLM 기능과 같다 — 등락률·시각·채점은 전부 SQL이 만들고, 모델은
  방향별 확률·방향별 이유 문장·근거 인용만 만든다.
- **잘못된 판단도 고치지 않는다.** 틀린 추론이 그대로 남는 것이 기록의 목적이다.
  승인·보류 상태 머신도, 사람이 DB 행을 UPDATE하는 경로도 두지 않는다.

`airflow/modules/llm.py`의 기존 원칙이 이 구조를 그대로 지원한다:
**조사(툴만 바인딩) → 답변(툴 빼고 `response_format` 강제)** 두 단계. 툴과 스키마를
한 요청에 섞지 않는다. `invoke(model, messages, tools=...)`가 이미 있다.

## 2. 단계와 의존

| 순서 | 문서 | 산출물 | 의존 | LLM |
| --- | --- | --- | --- | --- |
| 1 | [1-storage.md](1-storage.md) | `apps/models/analysis.py`, 수기 리비전, `thesis/*.sql`·`thesis_evidence/*.sql`, 세션 등락률 SQL, `thesis.py`의 채점 순수 함수, 스키마·채점 테스트 | 없음 | 없음 |
| 2 | [2-agent.md](2-agent.md) | `thesis.py`에 Toolbox·Builder·저장, 툴 SQL 3개, `thesis_model()`, `llm.invoke` tools+schema 가드, `tests/modules/test_thesis.py` | 1 | 있음 |
| 3 | [3-dag-slack.md](3-dag-slack.md) | `dags/market_thesis_forecast.py`·`market_thesis_review.py`, 스케줄, 채점 호출, Slack 렌더링·발송, DAG 테스트. **여기서 첫 운영 발송** | 1, 2 | 있음 |
| 4 | [4-graph.md](4-graph.md) | `airflow/modules/graph.py`, `sync_graph` 태스크, `sync_only` Param, compose·requirements, `tests/modules/test_graph.py` | 1 (3과 병렬 가능) | 없음 |
| 5 | [5-followup.md](5-followup.md) | `thesis_outcome` 테이블, 다지평(T+0·1·3·5) 채점, `FollowupNarrator` 사후 해설과 `verdict`, `past_theses` 툴 | 1, 2, 3 | 있음 |
| 6 | [6-analyst.md](6-analyst.md) | `stock_analyst_opinion` 테이블과 리비전, `collectors/kis_analyst_opinion.py`, `dags/kis_analyst_opinion_daily.py`, `analyst_opinions` 툴, `SourceKind.research`와 네이버 리서치 출처 여섯(`document_listings.py`의 `enrich` 단계), 테스트 | 5 | 없음(리포트는 기존 문서 평가가 읽는다) |
| 7 | [7-nxt-review.md](7-nxt-review.md) | `post_nxt_close` 슬롯, `thesis_nxt_review.py`, `market_thesis_nxt_review` DAG, 애프터마켓 조회 SQL, 수기 리비전(CHECK 확장) | 1, 2, 3 | 있음 |
| 8 | [8-expectation.md](8-expectation.md) | `stock_event_claim`·`stock_event_extraction`·`stock_event_outcome`과 수기 리비전, `modules/expectation_domain.py`·`expectation_extraction.py`·`expectation_judgment.py`, `event_expectation_hourly` DAG, `event_surprises` 툴, 컨센서스 수집기(후행) | 2, 6 | 추출만 |

**5단계는 1단계의 `thesis` 채점 컬럼을 `thesis_outcome`으로 옮긴다.** 채택했으므로
(2026-08-21) 그 이동을 1·2단계 코드에 먼저 반영한다. 무엇이 바뀌는지는
[5-followup.md](5-followup.md) 0절의 표에 있다. 테이블이 아직 운영에 없어 데이터 이관은 없다.

- 1→2→3은 순서대로. 4는 1 뒤 언제든 — **구현**은 3과 독립이라 병렬로 진행해도 된다.
  단 `sync_graph` 태스크를 붙이는 자리가 3단계 DAG라 **배포는 3 뒤**다([4-graph.md](4-graph.md)).
- **4는 prod Neo4j 인스턴스가 선행 조건이다.** 이 저장소 코드만으로 끝나지 않는다
  (NAS 쪽 컨테이너, Airflow 이미지 재빌드). 그 전까지 3까지만 운영에 나가고 `sync_graph`는
  `NEO4J_URI` 미설정으로 skip이다.
- **7은 5와 독립이다.** 애프터마켓 리뷰는 예측이 아니라 채점 대상이 아니고 해설도 붙이지
  않아, 5단계의 채점·해설 루프와 만나지 않는다. 다만 그 루프가 새 슬롯을 **자동으로** 집지
  않도록 두 SQL에 슬롯 목록을 걸어야 한다([7-nxt-review.md](7-nxt-review.md) 3절).
- 한 단계가 끝날 때마다 그 단계 문서의 "테스트" 절이 통과해야 다음으로 간다.

**[TUNING.md](TUNING.md)는 단계가 아니다.** 다 만든 뒤에 쓰는 운영 규칙이라 번호가 없다 —
손잡이 장부, 판단 캘린더, 그리고 **자동으로 안 나오는 지표를 손으로 읽는 쿼리**를 갖는다.

## 3. 공통 규칙

- 마이그레이션은 **수기 리비전**이다. `config.yaml`이 운영 DB를 가리켜 autogenerate를
  돌리지 않는다. 새 리비전 ID는 기존 파일과 중복 확인 필수. 검증은 오프라인
  `head_sql` 기반 `tests/migrations/`가 한다.
- 테이블은 저장소 규칙대로 **스키마를 지정하지 않는다**(연결의 `search_path`, 기본 `public`).
  모델 파일 이름 `analysis.py`는 도메인 구분일 뿐이다.
- 상태 enum은 `StrEnum` + `native_enum=False` + CHECK 규칙 그대로.
- LangChain·LangGraph import는 태스크 함수 안에서 한다(DagBag 30초 타임아웃, 2026-08-19 실측).
- 트랜잭션 경계는 `contextlib.closing` + `modules.utility.atomic`(2026-08-20 리팩토링 패턴).
- 모델 키는 `thesis_model()`이 정하는 LangChain 클래스가 스스로 읽는다(`OPENAI_API_KEY`
  또는 `XAI_API_KEY`). 우리 설정 객체에 담지 않는다.
- LangSmith 추적을 켜면 프롬프트와 툴 결과(문서 제목·공시명)가 외부로 나간다 —
  기존 문서 태깅과 같은 조건이다.

## 4. 만들지 않는 것 (전 단계 공통)

- **Slack 인터랙티브 수정(버튼·스레드 피드백 수집)과 사람의 사후 수정** — 틀린 판단은
  틀린 채로 남긴다. 인터랙티브를 만들려면 Slack Events/Interactivity 엔드포인트를 새로
  올려야 하는데, 지금 프로젝트에는 그 상주 서버가 없다.
- **추론 판 번호(revision)** — 첫 성공본 불변(`INSERT ... ON CONFLICT DO NOTHING`,
  [1-storage.md](1-storage.md))으로 충분하다. 수동 재추론을 기록으로 남겨야 할 때 넣는다.
- **`document` 버전 이력** — 과거 시점의 본문·평가를 복원하려면 불변 버전 테이블이 필요한데,
  그걸 요구하는 소비자가 없다. `as_of_at`은 event-time cutoff까지만 보장한다(1절).
- **정확도 대시보드·집계 테이블·baseline 비교** — `brier_score` 컬럼이 쌓이면 쿼리로 충분하다.
  균등확률 baseline은 상수 0.667, 전일 방향 baseline은 `LAG()` 한 줄이다.
- **Postgres↔Neo4j 정합성 관측(lag·checksum)과 reconciliation DAG** — 그래프를 읽는 소비자가
  아직 없어 어긋남이 아무에게도 보이지 않는다. 놓친 슬롯은 `sync_only` Param으로 손으로
  맞춘다([4-graph.md](4-graph.md)). 소비자가 생기면 그때 `thesis` 건수 vs `(:Thesis)` 건수
  대조부터 넣는다.
- **급변 구간 탐지기** — 이전 반복(2026-08-20)에서 만들었다 접었다. 추론 근거는 세션·창
  단위 등락률로 충분히 시작할 수 있고, 분 단위 급변 탐지가 다시 필요해지면 그때
  Toolbox 툴 하나로 붙인다.
- **장중 추론** — 장전·장후 두 번이 이번 범위다.
- **추론 재시도·재평가** — 실패한 추론은 그 슬롯에 없던 것으로 남는다. 다음 슬롯이 새로 쓴다.
- **체크포인터** — 재실행 단위는 Airflow 태스크다(프로젝트 공통 규칙).
- **`apps/core/graph.py`** — 그래프를 읽는 소비자(대시보드·API)가 생길 때 만든다.

## 5. 남은 확인

- **모델 키 — 3단계 배포의 선행 조건.** `thesis_model()`은 `ChatXAI`(grok-4.6)이고 키는
  `XAI_API_KEY`다. 그런데 `compose/prod/airflow/.env`의 값이 무효였다(2026-08-20 실측,
  `Incorrect API key provided`). **유효한 키를 넣기 전에는 DAG가 매 슬롯 실패한다.**
  키를 못 구하면 `thesis_model()`을 `ChatOpenAI`(gpt-5.6-luna, `OPENAI_API_KEY`는 운영에서
  살아 있다)로 되돌리는 것이 한 줄 수정이다.
- **Neo4j prod 인스턴스** — 아직 없다. 상세는 [4-graph.md](4-graph.md).
- **4주 검증** — 3단계 배포 뒤 한 달간 본다. 두 묶음을 섞지 않는다.
  - **운영 지표**(이걸로 판단한다): 슬롯별 정시 발행률, readiness guard 재시도 횟수, subject
    커버리지(버려진 subject 비율), 근거 유효율(목록 밖이라 버린 ref 비율), (4단계 뒤)
    `sync_graph` 성공률. 이 숫자로 프롬프트·툴 상한·스케줄·Neo4j 유지 여부를 다시 본다.
  - **예측 품질**(누적만 한다): Brier 추이와 균등확률 0.667 대비. 4주면 subject당 표본이
    20개 안팎이라 모델 예측력을 결론 내리지 못한다. 분기 단위로 다시 본다.
  - **이 다섯 중 자동으로 나오는 것은 절반뿐이다.** ops 브리핑(`slack_ops_briefing`)이
    지평별 Brier·`flat`·`verdict`·적체를 내고, 나머지는 손으로 읽는다. 쿼리와
    "근거 유효율은 왜 못 읽나"는 [TUNING.md](TUNING.md) 2절에 있다.
    어느 숫자가 어느 상수를 당기는지는 같은 문서 3·4절이다.
