# 2단계 — 추론 에이전트: `airflow/modules/thesis.py`

- 상위: [README.md](README.md)
- 의존: [1-storage.md](1-storage.md)(저장할 테이블·insert SQL·채점 함수)
- 산출물: `airflow/modules/thesis.py`에 ThesisToolbox·ThesisBuilder·저장 추가(1단계가 만든
  채점 함수 옆), 툴 SQL 3개, `modules/llm.py`의 `thesis_model()`과 `invoke` tools+schema 가드,
  `tests/modules/test_thesis.py`
- 이 단계엔 DAG가 없다. 모듈과 테스트까지다. 운영 호출은 [3-dag-slack.md](3-dag-slack.md).

`picks.py`(문서 선별)·`assessment.py`(태깅) 계보의 흐름 클래스다. 새로 생기는 것은
툴 왕복이다.

## 1. ThesisToolbox

DB 연결과 **기준 시각 `as_of_at`**을 들고 읽기 전용 툴 3개를 실행한다. 결과의 모든 항목에
`ref`를 붙이고 `ref → (kind, title, url, detail)` 레지스트리에 등록한다. 이 레지스트리가 답변
검증과 `thesis_evidence` 저장의 원본이다.

| 툴 | 반환 | SQL |
| --- | --- | --- |
| `recent_documents(hours, min_score)` | value_score 상위 문서(제목·URL·발행시각·점수·방향·티커·`new_facts`·`reason`) | `document/select_recent_top.sql` |
| `recent_disclosures(hours)` | 추적 종목 공시(회사·제목·URL·감지 시각) | `disclosure_event/select_recent.sql` |
| `macro_changes()` | 분석 창의 지수·선물·환율 변화(첫봉 대비 마지막봉) | `quote_bar/select_window_changes.sql`(뷰는 읽기 전용 — 조회만) |

- **모든 창의 끝은 `as_of_at`이다.** `hours`는 `as_of_at`에서 거슬러 올라가는 길이이지
  `now()`에서가 아니다. `macro_changes()`의 창은 `[전 개장일 15:30, as_of_at]`(장전) /
  `[당일 09:00, as_of_at]`(장후). SQL 술어는 event-time 컬럼으로 건다:
  - 문서: `detected_at <= as_of_at AND assessed_at <= as_of_at AND updated_at <= as_of_at`.
    `updated_at`까지 거는 것은 보수적 선택이다 — 본문이 갱신된 문서는 과거 상태를 알 수 없으니
    뺀다. 재실행은 분 단위 뒤의 Airflow 재시도뿐이라 빠지는 문서는 거의 없다.
  - 공시: `detected_at <= as_of_at`.
  - 봉: `bar_at + interval '1 minute' <= as_of_at`. `bar_at`은 봉의 **시작** 시각이라
    `bar_at <= as_of_at`이면 경계 봉의 미래 1분이 섞인다.
  이것이 보장하는 것은 event-time cutoff까지다(README 1절).
- **상한은 코드 상수로 강제한다.** 모델이 인자를 넘겨도 잘라서 실행한다:
  `1 <= hours <= 72`, `0 <= min_score <= 100`, 툴 호출당 결과 ≤ 20건, 항목당
  `new_facts`·`reason` 합쳐 ≤ 600자, 실행당 tool call 총 ≤ 12회(왕복 4 × 회당 3),
  실행당 툴 결과 누적 ≤ 24,000자. 넘는 호출은 빈 결과가 아니라 "상한 초과" `ToolMessage`로
  돌려 모델이 알게 한다.

### 툴 호출 계약

- tool_call마다 같은 `tool_call_id`의 `ToolMessage`가 **정확히 하나** 대화에 들어간다.
  빠지거나 둘이면 제공처가 요청을 거절한다.
- 모르는 툴 이름, 깨진 JSON 인자, 범위 밖 인자, 상한 초과는 전부 **오류 `ToolMessage`**로
  답한다(예외로 올리지 않는다 — 모델이 고쳐 부를 기회를 준다). 내용은 한 줄: 무엇이 왜
  거절됐는지.
- **DB 오류는 위장하지 않는다.** 연결 끊김·SQL 오류는 빈 결과로 바꾸지 않고 그대로 올려
  태스크를 실패시킨다. 빈 결과는 "그 창에 문서가 없다"는 뜻이어야 한다.
- `evidence_refs`는 순서를 보존한 채 중복을 제거한다(첫 등장 rank). 레지스트리에 없는 ref는
  버리고 건수를 로그로 남긴다.
- 답변에 같은 `subject_code`가 두 번 오면 그 subject를 거절한다(어느 쪽이 진짜인지 알 수 없다).
  요청 목록에 있는데 답변에 없는 subject는 그 슬롯에 없던 것으로 남긴다 — 재요청하지 않는다.
- 문서 툴이 제목·점수만 주면 이유 문장을 쓸 재료가 없다. `document_assessment`가 이미 만든
  `new_facts`·`reason`과 발행시각·`canonical_url`을 함께 준다. URL은 Slack 링크에도 쓴다.
- 툴은 여기서 늘린다(수급 스냅샷, 금리 스프레드 등). **금리 계열을 넣을 때는 퍼센트 변화가
  아니라 bp 차이로 준다**(`briefing/market.py`의 `QUOTED_KINDS` 주석과 같은 이유 — 4.65→4.70을
  `+1.08%`로 주면 모델이 급등으로 읽는다). 웹 검색처럼 출처를 통제할 수 없는 툴은 별도 결정
  전까지 넣지 않는다.

## 2. ThesisBuilder — LangGraph

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

## 3. 답변 스키마

```json
{"theses": [{"subject_code": "KOSPI",
             "prob_up": 0.62, "prob_down": 0.23, "prob_flat": 0.15,
             "up_reasoning": "…", "down_reasoning": "…", "flat_reasoning": "…",
             "evidence_refs": ["macro:SP500_FUT", "document:123"]}]}
```

- subject_code가 요청 목록 밖이면 그 항목만 버린다. 전부 버려지면 repair 1회.
- **확률 검증(Pydantic)**: `prob_up`·`prob_down`·`prob_flat` 각각 `[0, 1]`. 합이 1에서
  ±0.02 안이면(반올림·형식 오차) 비율을 유지한 채 정규화해 정확히 1로 맞춰 저장한다.
  ±0.02를 넘으면(셋 다 낮게 부르는 등 모델이 규칙을 안 지킨 경우) 그 subject만 버린다.
  전부 버려지면 repair 1회, 두 번째도 벗어나면 `ThesisError`.
- evidence_refs는 레지스트리로 검증. 근거 0건 thesis는 허용한다(관측 상태만으로 쓴
  추론) — 억지 인용이 더 나쁘다. 버린 건수는 로그로 남긴다. 세 방향이 같은 근거 목록을
  공유한다 — 방향별로 evidence_refs를 나누지 않는다(요청 범위 최소화).
- `up_reasoning`·`down_reasoning`·`flat_reasoning` 각각 상한 500자. 넘으면 그 필드만 자른다.
- 프롬프트 규칙: 입력·툴 결과에 없는 사실·숫자 금지, 투자 조언·매수 매도 권유 금지,
  "예측이 아니라 가설이며 채점은 시스템이 한다"를 명시. 세 확률이 1에 가깝게 합쳐지도록
  프롬프트에 명시한다(모델이 직접 계산하게 하고 SQL로 강제하지 않는다 — 강제 정규화는
  검증 실패일 때만 쓴다).

## 4. subjects와 관측 상태

- 지수: KOSPI, KOSDAQ. 종목: `instrument.is_watched` 전부(현재 005930·000660).
- 장후(review): 당일 세션 등락률. 종목은 `stock_investor_trade_daily`의 확정 종가(18:10),
  지수는 `index_bar` 15:30 봉 — 채점과 같은 원본([1-storage.md](1-storage.md) 3절). 분봉으로
  종가를 잡지 않는다(마감 동시호가가 빠진 날이 있다).
- 장전(forecast): 전일 세션 등락률 + 밤사이 해외 매크로 변화 요약.
- 관측 상태 dict가 `as_of_at`과 함께 그대로 `thesis.input_state`에 저장된다.
- 관측 상태 SQL은 1단계의 `stock_investor_trade_daily/select_session_return.sql`·
  `index_bar/select_session_return.sql`.

## 5. 저장

`store_theses(connection, *, run_date, run_slot, as_of_at, dag_run_id, theses, registry)`가
한 트랜잭션에서 `thesis/insert.sql`(`ON CONFLICT DO NOTHING RETURNING id`) →
`thesis_evidence/insert.sql` 순으로 쓴다. 규칙은 [1-storage.md](1-storage.md) 2절.

**부르기 전에 먼저 본다.** `thesis/select_by_run.sql`로 (run_date, run_slot)에 행이 있으면
LLM을 부르지 않고 그 행들을 돌려준다(첫 성공본 불변). 행이 없을 때만 Builder를 돌린다.
삽입 직전에 다른 실행이 먼저 넣어 `RETURNING`이 0행이면 그것도 기존 행을 읽어 돌려준다.

## 6. 모델

`modules/llm.py`에 `thesis_model()`을 추가한다(`max_retries=0`, 재시도는 Airflow).
제공처는 구현 시점에 그 함수 안에서 정한다 — 문서 태깅은 `ChatOpenAI`(gpt-5.6-luna,
2026-08-20 전환), 브리핑 선별은 `ChatXAI`(grok-4.6)를 쓰고 있다. 툴 호출 왕복이
많은 작업이라 툴 호출 품질로 고른다. 어느 쪽이든 부르는 쪽은 `BaseChatModel`만 안다.
운영 키 상태는 [README.md](README.md) 5절.

**코드 할 일 — `llm.invoke` 가드.** 현재 `invoke(model, messages, schema=, tools=)`는 둘을
동시에 넘겨도 막지 않는다(`bind_tools` 뒤 `bind(response_format=)`를 그냥 한다). "툴과
스키마를 한 요청에 섞지 않는다"는 원칙이 코드 계약이 되도록 `if tools and schema: raise
ValueError(...)` 한 줄과 `tests/modules/test_llm.py` 케이스를 이 단계에서 넣는다.

## 7. 테스트 — `tests/modules/test_thesis.py`

ScriptedModel(tool_calls 붙인 `AIMessage` 지원)로:

- 툴 왕복: tool_calls → Toolbox 실행 → `ToolMessage` 추가 → 재호출 → 최종 답변
- `MAX_TOOL_ROUNDS` 초과 시 강제로 답변 단계 진입
- 목록 밖 subject_code·evidence_ref 버림, 전부 버려지면 repair 1회, 두 번째 실패는 `ThesisError`
- 확률 합 검증: ±0.02 안이면 정규화, 벗어나면 그 subject 버림, 전부 벗어나면 repair 1회
- 근거 0건 허용, 세 `*_reasoning` 필드 각각 자름
- 툴 상한: `hours` 0·73, `min_score` −1·101, 결과 20건 초과, tool call 12회 초과, 누적
  24,000자 초과가 잘리거나 "상한 초과" `ToolMessage`로 돌아오는지
- 툴 계약: 모르는 툴·깨진 인자가 오류 `ToolMessage`로 돌아오고 `tool_call_id`마다
  `ToolMessage`가 정확히 하나인지, DB 예외가 그대로 올라오는지
- `evidence_refs` 중복이 첫 등장 rank로 합쳐지는지, 중복 `subject_code`가 거절되는지,
  누락 subject가 재요청되지 않는지
- `as_of_at` 고정: 툴 SQL에 넘어가는 창의 끝이 벽시계가 아니라 슬롯 시각인지, 술어에
  `assessed_at`·`updated_at`·`bar_at + 1분`이 실리는지
- `store_theses`: FakeConnection으로 기존 행이 있으면 모델을 부르지 않는지, insert →
  evidence insert가 한 트랜잭션인지, `RETURNING` 0행이면 기존 행을 읽는지
- `llm.invoke(tools=..., schema=...)`가 `ValueError`인지(`test_llm.py`)
