# 시장 추론(thesis) 기록 설계

- 날짜: 2026-08-20
- 상태: 제안 (미구현)

## 0. 문제 — 데이터는 쌓이는데 추론이 없다

분봉·공시·평가된 문서·매크로 시세가 전부 쌓이고 있지만, 그것들을 놓고 "그래서 시장이
왜 움직였나 / 오늘 어떻게 움직일 것 같나"를 말하는 층이 없다. 필요한 것은:

- **장후**: "지수·종목이 오늘 올랐다 → 이유는 이것 같다" — 사후 해석(review)
- **장전**: "오늘 오를 것 같다 → 근거는 이런 밤사이 지수·기사" — 전망(forecast)

**맞고 틀림은 목적이 아니다.** 정답은 시간이 지나야 알고, 맞추기도 어렵다. 목적은
"어떤 정보를 근거로 어떤 결론을 냈다"가 기록으로 남는 것이다. 추론과 근거가 노드·엣지로
쌓이면 나중에 그래프 DB로 옮겨 추론 이력을 탐색하고, 채점이 누적되면 정확도를 잰다.

산출물은 **DB 누적뿐이다.** Slack 발송은 만들지 않는다. 소비자는 사람이 아니라
이후의 그래프 DB·정확도 집계다.

## 1. 원칙 — 근거는 고정 풀이 아니라 모델이 조회한다

- 프롬프트에는 **관측 상태만** 준다. "코스피 +1.61%", "SK하이닉스 전일 -2.1%".
  관측 상태는 전부 SQL이 계산한다.
- 왜인지 알아내는 데 필요한 정보는 모델이 **읽기 전용 툴을 호출해** 스스로 가져온다.
  최근 문서, 공시, 매크로 변화 — 어떤 것을 얼마나 볼지는 모델이 정한다.
- 모델이 실제로 인용한 근거만 저장한다. 툴이 돌려준 항목에는 전부 `ref`가 붙어 있고,
  답변의 `evidence_refs`는 툴 결과 레지스트리로 검증한다. 목록 밖 ref는 버린다.

`airflow/modules/llm.py`의 기존 원칙이 이 구조를 그대로 지원한다:
**조사(툴만 바인딩) → 답변(툴 빼고 `response_format` 강제)** 두 단계. 툴과 스키마를
한 요청에 섞지 않는다. `invoke(model, messages, tools=...)`가 이미 있다.

숫자 규칙은 기존 LLM 기능과 같다 — 등락률·시각·채점은 전부 SQL이 만들고, 모델은
방향·신뢰도·이유 문장·근거 인용만 만든다.

## 2. 저장 — `analysis.thesis` / `analysis.thesis_evidence`

새 모델 파일 `apps/models/analysis.py`. 노드·엣지 구조다(그래프 DB 이관 대상).

```sql
analysis.thesis (                 -- 추론 하나 = 노드
    run_slot        -- CHECK ('post_close','pre_open')
    run_date date   -- KST 세션 날짜
    thesis_kind     -- CHECK ('forecast','review'). 장전=forecast, 장후=review
    subject_kind    -- CHECK ('index','stock')
    subject_code    -- KOSPI / 000660
    label           -- 표시 이름 스냅샷
    direction       -- CHECK ('up','down','flat')
    confidence      -- CHECK ('high','medium','low')
    reasoning text  -- 모델의 이유 문장(한국어, 상한 500자)
    input_state jsonb   -- 프롬프트에 준 관측 상태 스냅샷. "무엇을 보고 추론했나"의 절반
    tool_rounds integer -- 모델이 툴을 몇 왕복 불렀나
    llm_model, prompt_version
    -- 자동 채점 (forecast만, 같은 날 장후 실행이 채운다)
    evaluated_at timestamptz NULL
    actual_return_pct numeric(8,4) NULL
    outcome         -- CHECK ('hit','miss','flat'), NULL = 미채점
    UNIQUE (run_date, run_slot, subject_kind, subject_code)   -- 멱등키
)

analysis.thesis_evidence (        -- 추론 → 근거 = 엣지
    thesis_id FK → thesis ON DELETE CASCADE
    evidence_kind   -- CHECK ('document','disclosure','macro_change')
    evidence_ref    -- 툴 결과의 ref 그대로: document:123,
                    -- disclosure:dart:<rcept_no>, macro:SP500_FUT
    evidence_title  -- 그래프 조회용 제목 스냅샷
    detail jsonb    -- 툴이 준 수치 스냅샷(등락률, 점수 등)
    rank integer    -- 모델이 인용한 순서
    UNIQUE (thesis_id, evidence_kind, evidence_ref)
)
```

- `evidence_ref`는 마스터로 외래키를 걸지 않는다(`document_instrument` 선례).
  원본이 지워져도 엣지는 남고, 그래프 이관 때 kind+ref로 외부 노드를 해석한다.
- 재실행은 멱등 upsert. 같은 (날짜, 슬롯, subject)를 다시 돌리면 행이 갱신된다.
- 상태 enum(`ThesisKind`·`ThesisDirection`·`ThesisOutcome`·`ThesisEvidenceKind`)은
  `StrEnum` + `native_enum=False` + CHECK 규칙 그대로.
- 마이그레이션은 **수기 리비전**이다. `config.yaml`이 운영 DB를 가리켜 autogenerate를
  돌리지 않는다. 새 리비전 ID는 기존 파일과 중복 확인 필수. 검증은 오프라인
  `head_sql` 기반 `tests/migrations/`가 한다.

## 3. 추론 에이전트 — `airflow/modules/thesis.py`

`picks.py`(문서 선별)·`assessment.py`(태깅) 계보의 흐름 클래스다. 새로 생기는 것은
툴 왕복이다.

### ThesisToolbox

DB 연결과 분석 창을 들고 읽기 전용 툴 3개를 실행한다. 결과의 모든 항목에 `ref`를 붙이고
`ref → (kind, title, detail)` 레지스트리에 등록한다. 이 레지스트리가 답변 검증과
`thesis_evidence` 저장의 원본이다.

| 툴 | 반환 | SQL |
| --- | --- | --- |
| `recent_documents(hours, min_score)` | value_score 상위 문서(제목·점수·방향·티커) | `document/select_recent_top.sql` |
| `recent_disclosures(hours)` | 추적 종목 공시(회사·제목·감지 시각) | `disclosure_event/select_recent.sql` |
| `macro_changes()` | 분석 창의 지수·선물·환율 변화(첫봉 대비 마지막봉) | `quote_bar/select_window_changes.sql` |

툴은 여기서 늘린다(수급 스냅샷, 금리 스프레드 등). 웹 검색처럼 출처를 통제할 수 없는
툴은 별도 결정 전까지 넣지 않는다.

### ThesisBuilder — LangGraph

```
investigate → (tool_calls 있으면) tools → investigate → … → answer → (형식 실패) repair → answer
```

- `investigate`: `llm.invoke(model, messages, tools=TOOL_SCHEMAS)`. 스키마 없음.
- `tools`: tool_call마다 Toolbox 실행, `ToolMessage`로 대화에 추가. 왕복 상한
  `MAX_TOOL_ROUNDS = 4` — 넘으면 조사를 끝내고 답변으로 넘어간다.
- `answer`: 툴을 빼고 `response_format` 강제. 스키마 미지원 제공처는 검증 폴백
  (`UnsupportedResponseFormat` → 스키마 없이 재호출 + Pydantic 검증).
- `repair`: 1회. 목록 밖 subject·ref만 남았거나 JSON이 깨졌을 때. 두 번째 실패는
  `ThesisError`로 올린다.
- 상태는 `TypedDict`(messages, tool_rounds, theses, error, attempts). 연결·설정
  객체는 상태에 넣지 않는다 — 상태는 트레이스 입력으로 나간다.
- **실행당 대화 하나에 모든 subject를 한 번에** 준다(건별 호출 금지 규칙).

### 답변 스키마

```json
{"theses": [{"subject_code": "KOSPI", "direction": "up", "confidence": "medium",
             "reasoning": "…", "evidence_refs": ["macro:SP500_FUT", "document:123"]}]}
```

- subject_code가 요청 목록 밖이면 그 항목만 버린다. 전부 버려지면 repair 1회.
- evidence_refs는 레지스트리로 검증. 근거 0건 thesis는 허용한다(관측 상태만으로 쓴
  추론) — 억지 인용이 더 나쁘다. 버린 건수는 로그로 남긴다.
- reasoning 상한 500자. 넘으면 그 건만 자른다.
- 프롬프트 규칙: 입력·툴 결과에 없는 사실·숫자 금지, 투자 조언·매수 매도 권유 금지,
  "예측이 아니라 가설이며 채점은 시스템이 한다"를 명시.

### subjects와 관측 상태

- 지수: KOSPI, KOSDAQ. 종목: `instrument.is_watched` 전부(현재 005930·000660).
- 장후(review): 당일 세션 등락률(세션 마지막 close vs previous_close).
- 장전(forecast): 전일 세션 등락률 + 밤사이 해외 매크로 변화 요약.
- 관측 상태 dict가 그대로 `thesis.input_state`에 저장된다.

### 모델

`modules/llm.py`에 `thesis_model()`을 추가한다(`max_retries=0`, 재시도는 Airflow).
제공처는 구현 시점에 그 함수 안에서 정한다 — 문서 태깅은 `ChatOpenAI`(gpt-5.6-luna,
2026-08-20 전환), 브리핑 선별은 `ChatXAI`(grok-4.6)를 쓰고 있다. 툴 호출 왕복이
많은 작업이라 툴 호출 품질로 고른다. 어느 쪽이든 부르는 쪽은 `BaseChatModel`만 안다.

## 4. 자동 채점 — 순수 SQL, LLM 없음

장후(post_close) 실행이 **같은 날 장전 forecast**를 채점한다.

- 실제 세션 등락률: 종목은 `stock_bar`(KRX) 세션 마지막 close vs previous_close,
  지수는 `quote_bar`(index kind) 동일 방식(매크로 봉에도 previous_close가 있다).
- 판정: 방향 부호 일치 = `hit`, 불일치 = `miss`,
  |실제 등락률| < `FLAT_THRESHOLD_PCT`(0.3) = `flat`(방향 무관).
- `thesis/update_outcome.sql`이 `evaluated_at`·`actual_return_pct`·`outcome`을 채운다.
  재실행 멱등. 봉이 없어 등락률을 못 구하면 미채점(NULL)으로 남긴다 — 0으로 꾸미지 않는다.

정확도 집계는 이 단계에서 만들지 않는다. outcome이 쌓이면
`GROUP BY subject_code, confidence` 한 줄 쿼리다. 화면(Grafana·주간 리포트)은 다음 몫이다.

## 5. DAG — `dags/market_thesis_analysis.py` (신규)

```python
SCHEDULE = MultipleCronTriggerTimetable(
    "20 8 * * 1-5",  # KST 평일 08:20 장전 = UTC 일~목 23:20
    "0 16 * * 1-5",  # KST 평일 16:00 장후 = UTC 07:00
    timezone=KST_TIMEZONE,
)
```

- 슬롯은 logical time으로 판정(정오 전 = pre_open). 휴장 판정은 `krx_open_day` —
  모르면 돌린다. 장전 창의 시작은 전 개장일 15:30(달력을 되짚어 찾는다).
- 태스크 하나 `build_thesis`:
  1. 관측 상태 계산(SQL) → ThesisBuilder 실행 → `thesis`·`thesis_evidence` upsert
  2. post_close면 당일 아침 forecast 채점
- **Slack 발송 없음.** 산출물은 DB 행이다. 실패 판정은 프로젝트 규칙 그대로 —
  `LlmError`·`ThesisError`는 `AirflowFailException`(재시도해도 같음),
  `RetryableLlmError`(429·5xx·네트워크)는 그대로 올려 Airflow가 재시도한다.
  발송이 없으므로 재시도가 중복 부작용을 만들지 않는다(저장은 멱등 upsert).
- LangChain·LangGraph import는 태스크 함수 안에서 한다(DagBag 30초 타임아웃, 2026-08-19 실측).
- 트랜잭션 경계는 `contextlib.closing` + `modules.utility.atomic`(2026-08-20 리팩토링 패턴).
- 필요 환경: 모델 키(`OPENAI_API_KEY` 또는 `XAI_API_KEY` — `thesis_model()`이 정하는
  클래스가 스스로 읽는다), `CONNECTION_ID` 연결. Slack 환경변수는 필요 없다.

## 6. 신규 SQL 파일

| 파일 | 용도 |
| --- | --- |
| `thesis/{upsert,select_forecasts_to_grade,update_outcome}.sql` | 추론 저장·채점 |
| `thesis_evidence/upsert.sql` | 근거 엣지 |
| `document/select_recent_top.sql` | 툴: 최근 상위 문서 |
| `disclosure_event/select_recent.sql` | 툴: 최근 공시 |
| `quote_bar/select_window_changes.sql` | 툴: 매크로 변화(뷰는 읽기 전용 — 조회만) |
| `stock_bar/select_session_return.sql`, `quote_bar/select_session_return.sql` | 관측 상태·채점 |

`thesis/upsert.sql`은 `RETURNING id`로 엣지 저장에 쓸 id를 돌려주고, 재추론 갱신 시
기존 채점 컬럼은 건드리지 않는다(갱신 전 채점이 이미 있으면 보존).

## 7. 테스트

- `tests/modules/test_thesis.py` — ScriptedModel(tool_calls 붙인 `AIMessage` 지원)로:
  - 툴 왕복: tool_calls → Toolbox 실행 → `ToolMessage` 추가 → 재호출 → 최종 답변
  - `MAX_TOOL_ROUNDS` 초과 시 강제로 답변 단계 진입
  - 목록 밖 subject_code·evidence_ref 버림, 전부 버려지면 repair 1회, 두 번째 실패는 `ThesisError`
  - 근거 0건 허용, reasoning 자름
  - 채점 함수 단위 테스트: hit/miss/flat 경계(±0.3%)
- `tests/migrations/test_thesis_schema.py` — `head_sql` 사실 검증(UNIQUE·CHECK·CASCADE·주석)
- `tests/dags/test_market_thesis_analysis.py` — 스케줄 고정, upsert SQL 컬럼 vs 모델 metadata 대조
  (`tests/collectors/test_kis.py`의 `inserted_columns` 패턴)

## 8. 만들지 않는 것

- **Slack 발송·렌더링** — 산출물은 DB 행이다. 사람에게 보여 주는 화면·알림은 별도 결정.
- **정확도 대시보드·집계 테이블** — outcome 컬럼이 쌓이면 쿼리로 충분하다.
- **급변 구간 탐지기** — 이전 반복(2026-08-20)에서 만들었다 접었다. 추론 근거는 세션·창
  단위 등락률로 충분히 시작할 수 있고, 분 단위 급변 탐지가 다시 필요해지면 그때
  Toolbox 툴 하나로 붙인다.
- **장중 추론** — 장전·장후 두 번이 이번 범위다.
- **추론 재시도·재평가** — 실패한 추론은 그 슬롯에 없던 것으로 남는다. 다음 슬롯이 새로 쓴다.
- **체크포인터** — 재실행 단위는 Airflow 태스크다(프로젝트 공통 규칙).

## 9. 남은 확인

- **모델 키** — `compose/prod/airflow/.env`의 `XAI_API_KEY`가 무효였다(2026-08-20 실측,
  `Incorrect API key provided`). 문서 태깅이 쓰는 `OPENAI_API_KEY`는 운영에서 살아 있으므로
  `thesis_model()`을 OpenAI로 두면 키 문제가 없다. Grok을 쓰려면 유효한 키 확보가 먼저다.
- LangSmith 추적을 켜면 프롬프트와 툴 결과(문서 제목·공시명)가 외부로 나간다 —
  기존 문서 태깅과 같은 조건이다.
