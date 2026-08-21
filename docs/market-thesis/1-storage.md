# 1단계 — 저장과 채점: `thesis` / `thesis_evidence`

- 상위: [README.md](README.md)
- 의존: 없음. LLM 없음. 이 단계만으로 운영에 나가도 아무 것도 하지 않는 빈 테이블이다.
- 산출물: `apps/models/analysis.py`, 수기 리비전, `airflow/sql/postgres/thesis/*.sql`·
  `thesis_evidence/*.sql`, 세션 등락률 SQL, `airflow/modules/thesis.py`(채점 순수 함수만),
  `tests/migrations/test_thesis_schema.py`, `tests/modules/test_thesis.py`(채점 부분)

## 1. 테이블

새 모델 파일 `apps/models/analysis.py`. 노드·엣지 구조이자 원본이다 — 4단계에서 Neo4j로도
반영된다. 파일 이름이 `analysis`인 것은 도메인 구분일 뿐이고 테이블은 저장소 규칙대로
**스키마를 지정하지 않는다**(연결의 `search_path`, 기본 `public`).

```sql
thesis (                          -- 추론 하나 = 노드
    run_slot        -- CHECK ('pre_open','post_close'). pre_open=forecast(장전 전망),
                    -- post_close=review(장후 리뷰). 슬롯이 곧 종류라 별도 kind 컬럼은 없다
    run_date date   -- KST 세션 날짜
    as_of_at timestamptz -- 관측 상태·툴 조회의 기준 시각(UTC). 벽시계가 아니라 슬롯이 정한다
                    -- (장전 = 당일 08:35 KST, 장후 = 당일 15:30 KST). event-time cutoff —
                    -- 이 시각 이후 감지·평가·갱신된 행은 조회에서 뺀다(README 1절)
    dag_run_id text -- 이 행을 쓴 Airflow dag_run_id. "같은 실행의 재시도"를 DB가 증명한다
    subject_kind    -- CHECK ('index','stock')
    subject_code    -- KOSPI / 000660
    label           -- 표시 이름 스냅샷
    prob_up   numeric(5,4)  -- 상승 확률 0~1
    prob_down numeric(5,4)  -- 하락 확률 0~1
    prob_flat numeric(5,4)  -- 횡보 확률 0~1
                    -- CHECK: 셋 다 [0,1], |prob_up+prob_down+prob_flat - 1| < 0.001
                    -- (저장 전 애플리케이션이 이미 ±0.02 오차를 정규화해 정확히 1로 맞춘다
                    -- [2-agent.md](2-agent.md) 3절 — 이 CHECK는 그 뒤의 최종 안전장치다)
    up_reasoning   text  -- 상승 쪽 이유(한국어, 상한 500자)
    down_reasoning text  -- 하락 쪽 이유(한국어, 상한 500자)
    flat_reasoning text  -- 횡보 쪽 이유(한국어, 상한 500자)
    input_state jsonb   -- 프롬프트에 준 관측 상태 스냅샷. "무엇을 보고 추론했나"의 절반
    tool_rounds integer -- 모델이 툴을 몇 왕복 불렀나
    llm_model, prompt_version
    -- 자동 채점 (pre_open만, 같은 날 장후 실행이 채운다)
    evaluated_at timestamptz NULL
    actual_return_pct numeric(8,4) NULL
    actual_outcome  -- CHECK ('up','down','flat'), NULL = 미채점. 실제 등락률의 분류
    brier_score numeric(6,5) NULL  -- 3-class Brier: 각 확률과 원-핫 실제값의 차 제곱합(0=완벽, 2=최악)
                    -- CHECK: 채점 컬럼 넷은 전부 NULL이거나 전부 NOT NULL (all-or-none)
                    -- CHECK: brier_score BETWEEN 0 AND 2
    UNIQUE (run_date, run_slot, subject_kind, subject_code)   -- 멱등키
)

thesis_evidence (                 -- 추론 → 근거 = 엣지
    thesis_id FK → thesis ON DELETE CASCADE
    evidence_kind   -- CHECK ('document','disclosure','macro_change')
    evidence_ref    -- 툴 결과의 ref 그대로. kind 무관 `<kind>:<id>` 2단 고정
                    -- (예: document:123, disclosure:<rcept_no>, macro:SP500_FUT).
                    -- kind는 evidence_kind 컬럼과 중복되므로 ref 안에 소스를 다시 넣지 않는다
    evidence_title  -- 그래프 조회용 제목 스냅샷
    evidence_url    -- 문서면 canonical_url, 공시면 DART 뷰어 URL, 매크로면 NULL. Slack 링크용
    detail jsonb    -- 툴이 준 수치 스냅샷(등락률, 점수 등)
    rank integer    -- 모델이 인용한 순서. CHECK (rank > 0)
    UNIQUE (thesis_id, evidence_kind, evidence_ref)
    UNIQUE (thesis_id, rank)
)
```

- `evidence_ref`는 마스터로 외래키를 걸지 않는다(`document_instrument` 선례).
  원본이 지워져도 엣지는 남고, Neo4j에서도 `(kind, ref)`를 그대로 노드 키로 써서
  외부 노드를 해석한다([4-graph.md](4-graph.md)).
- 상태 enum은 `RunSlot`(`pre_open`/`post_close`)·`ThesisDirection`(`up`/`down`/`flat`)·
  `ThesisEvidenceKind`. `ThesisDirection`을 `actual_outcome` 채점에도 그대로 재사용한다 —
  예측·실제가 같은 세 값을 쓰므로 `hit`/`miss` 같은 비교 결과 enum은 더 필요 없다.
- 모든 테이블·컬럼에 한국어 주석. 리비전에도 같은 주석을 넣는다(프로젝트 규칙).

## 2. 쓰기 규칙 — 첫 성공본 불변

- **추론은 `INSERT ... ON CONFLICT DO NOTHING`이다.** 같은 (날짜, 슬롯, subject)에 행이
  이미 있으면 아무 것도 바꾸지 않는다. upsert로 덮어쓰면 LLM이 재호출마다 다른 답을 내서
  최초 판단이 사라지고, 옛 확률로 매긴 Brier가 새 확률 옆에 남는다. 둘 다 "판단 기록 보존"과
  어긋난다. 부르는 쪽([2-agent.md](2-agent.md) 5절)은 행이 있으면 LLM을 부르지 않는다.
- **근거도 INSERT만 한다.** thesis 행이 불변이라 근거를 교체할 일이 없다. thesis와 evidence는
  한 트랜잭션에 쓴다 — thesis만 들어가고 evidence가 빠진 상태를 남기지 않는다.
- **채점은 별도 UPDATE다.** `update_outcome.sql`이 채점 컬럼 넷만 채운다(3절). 추론 컬럼은
  건드리지 않는다.
- **잘못된 판단도 고치지 않는다.** 사람이 DB 행을 UPDATE하는 경로를 두지 않는다
  (운영 DB 직접 수정 금지 규칙과도 같다).

## 3. 자동 채점 — LLM 없음

장후(post_close) 실행이 **미채점 forecast 전부**를 채점한다(보통 같은 날 아침 것. 장후가
실패한 날의 것도 다음 실행이 회수한다). 호출은 3단계 DAG가 하지만 원본·수식은 여기서 정한다.

- **실제 세션 등락률 원본.**
  - 종목: `stock_investor_trade_daily.close_price`(`stck_clpr`, `kis_investor_trade_daily`가
    18:10에 넣는 확정 종가) vs 같은 테이블의 전 영업일 행(`LAG(close_price) OVER (PARTITION BY
    ticker ORDER BY business_date)`). **`stock_bar` 분봉은 쓰지 않는다.** `is_final`은 REST
    응답이라는 뜻이지 세션 완결이 아니다 — 2026-08-13 005930은 15:19가 마지막 봉이고 마감
    동시호가가 빠져 있었다(`collectors/kis.py` `fetch_stock_bars` docstring 실측).
  - 지수: `index_bar`의 15:30 봉 close vs 그 봉의 `previous_close`. `kis_quote_intraday`가
    `*/5 8-16`으로 돌아 16:00이면 확정이다.
- **수식은 Python 순수 함수다.** `airflow/modules/thesis.py`의 `classify_outcome(return_pct)`·
  `brier_score(prob_up, prob_down, prob_flat, outcome)`. SQL에 넣으면 DB 없이 경계값을
  테스트할 수 없다(테스트에서 실 DB를 쓰지 않는 프로젝트 규칙). `select_session_return.sql`이
  등락률을 주고, `update_outcome.sql`은 계산된 값 넷을 쓰기만 한다.
- **`actual_outcome` 분류**: |실제 등락률| < `FLAT_THRESHOLD_PCT`(0.3) = `flat`,
  아니면 부호로 `up`/`down`. 예측과 무관하게 실제 움직임만 분류한다(과거 `hit`/`miss`처럼
  예측과 비교하지 않는다 — 비교는 브라이어 점수가 대신한다).
- **`brier_score` 계산**: `actual_outcome`을 원-핫 벡터로 바꿔(예: `up`이면
  `(y_up,y_down,y_flat)=(1,0,0)`) `(prob_up-y_up)^2 + (prob_down-y_down)^2 +
  (prob_flat-y_flat)^2`. 0에 가까울수록 실제와 가까운 확률을 냈다는 뜻이고, 방향만 맞고
  확신(확률)이 지나치게 낮았거나 틀린 방향에 높은 확률을 준 경우를 함께 잡아낸다 —
  hit/miss 이분법이 놓치던 "얼마나 확신 있게 맞았나/틀렸나"를 점수화한다.
- `thesis/update_outcome.sql`이 `evaluated_at`·`actual_return_pct`·`actual_outcome`·
  `brier_score`를 채운다. `WHERE evaluated_at IS NULL`이라 재실행 멱등. 종가 행이 없어
  등락률을 못 구하면 미채점(NULL 전부)으로 남긴다 — 0으로 꾸미지 않는다.

정확도 집계는 만들지 않는다. `brier_score`가 쌓이면 `GROUP BY subject_code` 평균 한 줄 쿼리다.

## 4. SQL 파일

| 파일 | 용도 |
| --- | --- |
| `thesis/insert.sql` | `INSERT ... ON CONFLICT DO NOTHING RETURNING id`. 충돌이면 0행 |
| `thesis/select_by_run.sql` | (run_date, run_slot)의 행 전부, `id`·`dag_run_id` 포함. 재실행 판정·3단계 Slack·4단계 그래프가 쓴다 |
| `thesis/select_forecasts_to_grade.sql` | `run_slot = 'pre_open' AND evaluated_at IS NULL` 전부(날짜 제한 없음). 종가가 영구 결측(상장폐지 등)이면 영원히 미채점으로 남아 매 장후마다 재조회된다 — `evaluated_at` 인덱스로 대비하고, 누적이 문제가 되면 그때 상한을 둔다 |
| `thesis/update_outcome.sql` | 채점 4컬럼 채움. `WHERE id = %s AND evaluated_at IS NULL` |
| `thesis_evidence/insert.sql` | 근거 INSERT |
| `thesis_evidence/select_by_thesis_ids.sql` | 4단계 그래프 동기화 조회 |
| `thesis_evidence/select_top_by_thesis_ids.sql` | 3단계 Slack 상위 근거(rank 상위 N) |
| `stock_investor_trade_daily/select_session_return.sql` | 종목 세션 등락률(확정 종가 vs 전 영업일 종가) |
| `index_bar/select_session_return.sql` | 지수 세션 등락률(15:30 봉 close vs previous_close). 봉 시각은 파라미터로 받는다 — KST 경계 계산은 파이썬이 한다 |

파라미터는 저장소의 다른 SQL과 같이 psycopg 위치 인자 `%s`다. 목록은 `= ANY(%s)`로 받는다.
**모든 조회 SQL의 시간 창은 `as_of_at`(또는 그것에서 계산한 시각)을 파라미터로 받고 `now()`를
쓰지 않는다.** 조회의 기준 시각은 벽시계가 아니라 슬롯이 정한다.

## 5. 테스트

- `tests/migrations/test_thesis_schema.py` — `head_sql` 사실 검증: UNIQUE 둘, all-or-none
  CHECK, Brier 범위 CHECK, `rank > 0`, CASCADE, 주석, 테이블이 `public`에 스키마 접두 없이
  생기는지.
- `tests/models/test_analysis_models.py` — `apps/models/__init__.py` `__all__`에 등록, enum 값과
  CHECK 문자열 일치.
- SQL 컬럼 vs 모델 metadata 대조(`tests/collectors/test_kis.py`의 `inserted_columns` 패턴):
  `thesis/insert.sql`, `thesis_evidence/insert.sql`, `select_by_run.sql`, `select_by_thesis_ids.sql`.
  `thesis/insert.sql`에 `ON CONFLICT DO NOTHING`이 있고 `DO UPDATE`가 없는지.
  `select_session_return.sql`이 `stock_investor_trade_daily`를 보고 `stock_bar`를 안 보는지.
- `tests/modules/test_thesis.py`(채점 부분) — `classify_outcome`: 0.29 → `flat`, 0.30 → `up`,
  −0.30 → `down`, −0.29 → `flat`. `brier_score`: 완벽 예측 0, 반대 방향 확신 2에 가까움,
  균등 확률 0.667(상수), 합이 1인 어떤 분포든 0 ≤ 값 ≤ 2.
