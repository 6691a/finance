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

DB 연결과 **기준 시각 `as_of_at`**을 들고 읽기 전용 툴 13개를 실행한다.
**툴 수가 tool call 상한(`MAX_TOOL_CALLS` 12)을 넘었다** — 모델이 툴마다 한 번씩도 못 부른다.
상한에 붙는 실행이 보이면 그 숫자부터 올린다([TUNING.md](TUNING.md) 5절).

툴은 **근거를 만드는 것과 문맥만 주는 것**으로 갈린다. 근거 툴의 결과 항목에는 `ref`가 붙고
`ref → (kind, title, url, detail)` 레지스트리에 등록된다. 이 레지스트리가 답변 검증과
`thesis_evidence` 저장의 원본이다. 문맥 툴은 레지스트리에 넣지 않는다 — **시장 상태는 인용할
"출처"가 아니라 관측이다.** 넣으면 근거 표에 "코스피 상승 종목 수"가 실린다.

### 근거를 만드는 툴 넷

| 툴 | 반환 | SQL |
| --- | --- | --- |
| `recent_documents(hours, min_score)` | value_score 상위 문서(제목·URL·발행시각·점수·방향·티커·`new_facts`·`reason`) | `document/select_recent_top.sql` |
| `recent_disclosures(hours)` | 추적 종목 공시(회사·제목·URL·감지 시각) | `disclosure_event/select_recent.sql` |
| `macro_changes()` | 분석 창의 지수·선물·환율 변화(첫봉 대비 마지막봉) | `quote_bar/select_window_changes.sql`(뷰는 읽기 전용 — 조회만) |
| `us_market_close()` | 밤사이 미국장 마감 종가와 **전일 종가 대비** 등락 | `quote_bar/select_thesis_us_close.sql`(뷰는 읽기 전용 — 조회만) |

### 문맥만 주는 툴 아홉

`past_theses`를 뺀 일곱은 2026-08-21에 열었다. 그전까지 모델이 볼 수 있는 것은 문서·공시·분봉
창 변화뿐이어서 **수집 중인 것의 대부분이 보이지 않았다** — 특히 `indicator_observation`에
9개국 국채 곡선이 쌓여 있는데 금리를 못 보면서 "왜 움직였나"를 묻고 있었다.
`analyst_opinions`는 6단계(2026-08-22, [6-analyst.md](6-analyst.md))가 열었다.

| 툴 | 반환 | SQL |
| --- | --- | --- |
| `past_theses(subject_code, n)` | 그 대상의 지난 장전 추론과 지평별 채점·해설. 장전은 같은 조회 `PREFETCHED_PAST_THESES`건을 프롬프트에 **미리 싣고** `thesis_precedent`에 남긴다(5-followup.md 5절). 툴은 더 보고 싶을 때의 길이고 툴로 본 것은 기록되지 않는다 | `thesis/select_past_with_outcomes.sql` |
| `macro_indicators(kind)` | 각국 국채 곡선·물가·실물활동의 최신값과 직전 대비 변화 | `indicator_observation/select_thesis_latest.sql` |
| `market_investor_flows()` | 코스피·코스닥의 외국인·기관·개인 장중 누적 순매수 | `market_investor_flow_snapshot/select_thesis_latest.sql` |
| `market_breadth()` | 상승·보합·하락 종목 수와 상·하한가 수 | `market_movement_snapshot/select_thesis_latest.sql` |
| `stock_investor_flows(days)` | 추적 종목의 확정 수급 며칠치 + 장중 추정치 | `stock_investor_trade_daily/select_thesis_flows.sql`, `stock_investor_estimate_snapshot/select_thesis_latest.sql` |
| `market_funds(days)` | 고객예탁금·신용융자·미수금 추이 | `krx_market_funds_daily/select_thesis_recent.sql` |
| `daily_history(symbol, days)` | 심볼 하나의 일봉 추세 + 기술적 보조지표(SMA20/60·RSI14·MACD·거래량 비율) + 최근 매매 신호 | `technical/select_history.sql`, `technical/select_symbols.sql`, `technical_signal/select_thesis_recent.sql` |
| `short_and_credit()` | 공매도 수량·비중, 대차 잔고, 신용융자 잔고 | `krx_stock_short_sale_daily/select_thesis_latest.sql` |
| `analyst_opinions(ticker)` | 추적 종목 하나의 증권사별 투자의견·목표주가·직전 의견과 그 사유(같은 날 리포트 요약) | `stock_analyst_opinion/select_thesis_recent.sql` |

- **`us_market_close`는 `macro_changes`와 비교 대상이 다르다.** 저쪽은 분석 창의 첫 봉 대비이고
  이쪽은 봉이 들고 온 `previous_close`(전일 정규장 종가) 대비다. KIS 해외지수 현물은 마감 직전
  두 시간만 쌓여서(`kis_overseas_index_close`) 창 변화로는 밤사이 등락이 거의 0으로 보인다.
  ref도 갈라 둔다 — 같은 심볼이 `macro_change:SP500_FUT`(창 변화)와
  `macro_change:SP500_FUT@close`(마감)로 따로 등록된다. 겹치면 나중에 부른 툴이 앞의 근거를
  조용히 덮는다. 장후 슬롯의 창은 당일 09:00부터라 이 툴은 빈 배열이고, 그 뜻을 툴 설명이 밝힌다.
- **`macro_indicators`는 `kind`를 한 번에 하나만 본다.** 국채 금리(Percent)와 물가지수
  (Index 1982-1984=100)가 한 표에 섞이면 모델이 조용히 거짓을 읽는다. 금리 변화는 퍼센트가
  아니라 bp다(`BASIS_POINT_INDICATOR_KINDS`).
- **`stock_investor_flows`는 확정과 추정을 다른 칸에 담는다.** 확정은 마감 뒤 18:10 값이고
  추정은 장중 값이라 어긋난다. 한 칸에 담으면 모델이 그 차이를 모른 채 읽는다.
- **`short_and_credit`은 당일 행을 뺀다.** KIS가 장중에 당일 공매도를 0으로 보낸다
  (2026-08-21 실측). 확정은 다음 영업일 갱신이 채운다.
- **`daily_history`는 0행이면 쓸 수 있는 심볼을 함께 준다.** 빈 배열만 주면 모델이
  "이력이 없다"가 아니라 "움직임이 없었다"로 읽는다.
- **`daily_history`는 지표와 신호를 함께 준다**(2026-08-24). 국내 지수 일봉은
  `kis_index_daily`가 넣고 국내 종목은 `stock_investor_trade_daily`의 확정 종가를 쓴다.
  `technical_snapshot`은 마지막 확정 일봉 기준이고 표본이 60봉에 못 미치거나 하루 35퍼센트가
  넘는 가격 단절이 있으면 `null`이다 — 0으로 채우지 않는다. `recent_signals`의 각 항목은
  `technical_signal:<id>` ref를 갖는 **인용 가능한 근거**다. 지표값 자체는 문맥이라
  레지스트리에 넣지 않는다. 설계는 [../market-technical-indicators.md](../market-technical-indicators.md)
  7.1·12.5·14절이다.
- **`analyst_opinions`는 당일 행을 빼지 않는다.** 투자의견은 아침에 발표되는 당일 사건이
  정상값이다. 추적 목록 밖 종목은 `past_theses`의 `subject_code`처럼 거절한다. KIS가 사유를
  안 주므로 같은 날 같은 증권사 리포트 요약을 `reason`으로 붙인다(못 찾으면 칸이 없다).
  인용할 `ref`가 붙은 리포트 전문은 이 툴이 아니라 `recent_documents`의 `naver_research_*`
  문서에 있다(6단계).
- 열지 않은 것: `earnings_fact`(6행뿐이라 지금 열면 빈 결과만 준다), `market_session`
  (관측 상태가 이미 세션 날짜를 준다).

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
  `new_facts`·`reason` 합쳐 ≤ 600자, 실행당 tool call 총 ≤ 12회(왕복 3 × 회당 4),
  실행당 툴 결과 누적 ≤ 24,000자. 넘는 호출은 빈 결과가 아니라 "상한 초과" `ToolMessage`로
  돌려 모델이 알게 한다.

### 툴은 LangChain이 정의하고 LangGraph가 돌린다

**JSON Schema를 손으로 쓰지 않는다**(2026-08-21 전환). 인자는 Pydantic 모델
(`RecentDocumentsArgs` 등)이고 `StructuredTool.from_function(args_schema=...)`이 스키마를
뽑는다. 이전에는 `{"type": "function", "function": {...}}` dict를 직접 썼는데, 그건 제공처
wire format이라 이름·타입이 코드와 어긋나도 아무도 못 잡았다.

툴 함수는 **바인드된 메서드**다. 모듈 수준 `@tool`을 쓸 수 없는 이유는 툴이 연결·`as_of_at`·
레지스트리·상한 같은 `ThesisToolbox`의 상태를 봐야 하기 때문이다.

실행은 `langgraph.prebuilt.ToolNode`다. 손으로 짜던 dispatch 루프를 지웠다.

```python
ToolNode(toolbox.tools, handle_tool_errors=(ToolLimitExceeded,))
```

`ThesisToolbox.run(name, arguments)`는 남아 있지만 **운영 경로가 아니다.** 툴 하나를 따로
확인할 때(테스트·노트북) 쓰고, 같은 `StructuredTool`을 지나므로 판정이 어긋나지 않는다.

### 툴 호출 계약

- tool_call마다 같은 `tool_call_id`의 `ToolMessage`가 **정확히 하나** 대화에 들어간다.
  빠지거나 둘이면 제공처가 요청을 거절한다. **이 보장은 `ToolNode`가 한다.**
- 모르는 툴 이름과 상한 초과는 **오류 `ToolMessage`**로 답한다(예외로 올리지 않는다 —
  모델이 고쳐 부를 기회를 준다). 모르는 툴의 문구는 `ToolNode`의 것이고 쓸 수 있는 툴
  이름을 함께 싣는다.
- **범위 밖 인자와 못 읽는 인자는 거절하지 않고 자른다.** 숫자 범위는 `_clamp_int`가,
  타입이 안 맞는 값(`"bad"`, null)은 `ToolArgs`의 before-validator가 필드 기본값으로
  되돌린다. 왕복 상한이 4뿐이라 그중 하나를 오타에 쓰지 않는다.
- **DB 오류는 위장하지 않는다.** 연결 끊김·SQL 오류는 빈 결과로 바꾸지 않고 그대로 올려
  태스크를 실패시킨다. 빈 결과는 "그 창에 문서가 없다"는 뜻이어야 한다.
  **`handle_tool_errors`에 타입을 주는 이유가 이것이다.** 기본값(`True`)은 모든 예외를
  `ToolMessage`로 바꿔 연결 끊김을 "결과 없음"으로 위장한다. 회귀를 잡는 테스트는
  `test_database_failures_survive_the_tool_node`다.
- `claims`는 ref 순서를 보존한 채 중복을 제거한다(첫 등장 rank, 첫 인용의 방향·경로). 레지스트리에
  없는 ref는 버리고 건수를 로그로 남긴다.
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

- `investigate`: `llm.invoke(model, messages, tools=toolbox.tools)`. 스키마 없음.
- `tools`: tool_call마다 Toolbox 실행, `ToolMessage`로 대화에 추가. 왕복 상한
  `MAX_TOOL_ROUNDS = 3` — 넘으면 조사를 끝내고 답변으로 넘어간다. 왕복 하나가 모델 호출
  하나라 이 값이 빌드 한 번의 길이를 정한다. 바깥 울타리는 태스크의 `execution_timeout`
  (`thesis_common.BUILD_TIMEOUT` 30분)이다.
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
             "claims": [{"ref": "macro_change:SP500_FUT", "direction": "up", "mechanism": "위험선호 회복"},
                        {"ref": "document:123", "direction": "down", "mechanism": "외국인 매도 압력"}]}]}
```

- subject_code가 요청 목록 밖이면 그 항목만 버린다. 전부 버려지면 repair 1회.
- **확률 검증(Pydantic)**: `prob_up`·`prob_down`·`prob_flat` 각각 `[0, 1]`. 합이 1에서
  ±0.02 안이면(반올림·형식 오차) 비율을 유지한 채 정규화해 정확히 1로 맞춰 저장한다.
  ±0.02를 넘으면(셋 다 낮게 부르는 등 모델이 규칙을 안 지킨 경우) 그 subject만 버린다.
  전부 버려지면 repair 1회, 두 번째도 벗어나면 `ThesisError`.
- `claims`의 `ref`는 레지스트리로 검증. 근거 0건 thesis는 허용한다(관측 상태만으로 쓴
  추론) — 억지 인용이 더 나쁘다. 버린 건수는 로그로 남긴다.
- **인용마다 `direction`(`up`/`down`/`flat`)과 `mechanism`(200자)을 받는다**(2026-08-21).
  처음엔 `evidence_refs` 목록 하나를 세 방향이 공유했는데, 그러면 "이 근거가 어느 쪽으로
  왜 작용했나"가 산문 이유 안에만 있어 그래프 엣지에 실을 수 없었다. 이제
  `thesis_evidence.direction`·`mechanism`이 그 엣지 속성이다. 같은 ref를 두 번 인용하면
  첫 것이 남는다(행이 ref당 하나). `detail.direction`은 문서 평가 때의 문서 자체 방향이라
  다른 값이다.
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
**`ChatXAI`(grok-4.6)다** — 브리핑 선별과 같은 모델이지만 함수를 나눠 둔다. 선별은 목록을
읽고 고르는 일이고 추론은 툴을 여러 번 돌며 가설을 세우는 일이라, 한쪽만 옮기고 싶어질 때
그 함수만 고친다. 부르는 쪽은 `BaseChatModel`만 안다.

**키가 선행 조건이다.** `XAI_API_KEY`는 2026-08-20 실측에서 운영 `.env` 값이 무효였다
([README.md](README.md) 5절). 유효한 키를 넣기 전에는 이 DAG가 매 슬롯 실패한다.

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
