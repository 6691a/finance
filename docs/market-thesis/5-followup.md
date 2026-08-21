# 5단계 — 다지평 채점과 사후 해설: `thesis_outcome`

- 상위: [README.md](README.md)
- 날짜: 2026-08-21
- 상태: 구현 중 (스키마·채점·해설 완료, DAG 미착수)
- 의존: [1-storage.md](1-storage.md), [2-agent.md](2-agent.md), [3-dag-slack.md](3-dag-slack.md).
  4단계와는 병렬 가능하나 그래프 반영 절(6절)은 [4-graph.md](4-graph.md)를 전제한다.
- 산출물: `apps/models/analysis.py`에 `ThesisOutcome` 추가와 `thesis`·`thesis_evidence` 수정,
  수기 리비전, `thesis_outcome/*.sql`과 T+N 등락률 SQL, `thesis.py`에 `FollowupNarrator`와
  `past_theses` 툴, `market_thesis_analysis.py`에 태스크 둘, 테스트

## 0. 왜 — 하루로는 "왜"를 모른다

3단계까지의 채점은 예측일 세션 하나로 끝난다. 그날 종가가 나오면 `brier_score`가 확정되고
그 추론은 더 볼 일이 없다. 그런데 **그날 시장이 왜 그렇게 움직였는지는 며칠 뒤에 알려진다.**
사건 당일 기사는 "무슨 일이 있었다"까지고, 원인 해석과 시장 참여자의 정리된 판단은
그 뒤 며칠에 걸쳐 뉴스에 쌓인다.

그래서 이 단계가 두 가지를 더한다.

1. **다지평 채점** — 같은 확률 예측을 T+1·T+3·T+5 누적 등락률로도 채점한다. "하루는
   틀렸는데 방향은 맞았다"가 숫자로 남는다. LLM 없이 SQL·Python만 쓴다.
2. **사후 해설** — 각 지평에서 그동안 새로 쌓인 문서·공시를 근거로 "왜 그렇게 움직였나"를
   쓴다. 원 추론 행은 건드리지 않고 별도 행으로 붙는다.

그리고 이 둘이 쌓이면 **피드백 루프**가 가능해진다. 장전 추론이 `past_theses` 툴로 과거
자기 예측과 그 채점·해설을 조회한다(5절).

### 기존 원칙과의 관계

[README.md](README.md) 1절의 **첫 성공본 불변**과 "잘못된 판단도 고치지 않는다"는 그대로다.
이 단계는 원 추론 행의 어떤 컬럼도 UPDATE하지 않는다. 사후에 알게 된 것은 전부 새 행이다.

[README.md](README.md) 4절의 "**추론 재시도·재평가**를 만들지 않는다"는 **좁혀서 유지한다** —
막는 것은 *같은 슬롯의 추론을 다시 생성해 덮는 것*이다. 지나간 예측에 사후 관측을 덧붙이는
것은 덮어쓰기가 아니라 누적이므로 이 문서가 그 예외를 명시한다. README 4절에 그 한 줄을
추가한다.

### 이 단계가 바꾸는 것 (기존 문서 대비)

기존 문서는 그대로 두되, 아래 항목은 **이 문서가 우선한다.**

| 문서 | 절 | 무엇이 바뀌나 |
| --- | --- | --- |
| [1-storage.md](1-storage.md) | 1절 `thesis` 테이블 | 채점 컬럼 넷(`evaluated_at`·`actual_return_pct`·`actual_outcome`·`brier_score`)과 그 CHECK 둘을 `thesis`에서 **뺀다.** `thesis_outcome`으로 옮긴다 |
| [1-storage.md](1-storage.md) | 1절 `thesis_evidence` | `outcome_horizon_days` 컬럼 추가, UNIQUE 둘의 키에 포함 |
| [1-storage.md](1-storage.md) | 3절 자동 채점 | `update_outcome.sql` → `thesis_outcome/insert_grade.sql`. `FLAT_THRESHOLD_PCT`가 상수 하나에서 지평별 값으로 |
| [1-storage.md](1-storage.md) | 4절 SQL 파일 | `thesis/update_outcome.sql` 삭제, `thesis_outcome/*` 추가 |
| [2-agent.md](2-agent.md) | 1절 ThesisToolbox | 툴 네 번째 `past_theses` 추가 |
| [3-dag-slack.md](3-dag-slack.md) | 2절 태스크 | `build_thesis >> grade_followups >> narrate_followups >> notify_slack` |
| [4-graph.md](4-graph.md) | 그래프 반영 | `(:Outcome)` 노드와 엣지 둘 추가 (6절) |

1단계가 아직 미구현이라 이 이동의 비용은 문서 수정과 리비전 한 벌이다. 구현이 시작된 뒤라면
데이터 이관이 붙는다 — **1단계 구현 전에 이 문서의 채택 여부를 정한다.**

## 1. 테이블

```sql
thesis_outcome (                   -- 추론 하나의 한 지평 = 채점 + (선택) 사후 해설
    thesis_id       -- FK → thesis ON DELETE CASCADE
    horizon_days    -- CHECK (0,1,3,5). 지평 길이(KRX 영업일 수). 달력일이 아니다.
                    -- 0 = 예측일 세션 하나(1단계의 기존 채점과 같은 값)
    as_of_at        -- 이 지평의 기준 시각(UTC). 그 영업일 장후 15:30 KST
    dag_run_id      -- 이 행을 쓴 Airflow dag_run_id

    -- 채점. SQL과 Python 순수 함수만 쓴다. LLM 없음. **pre_open 행에만 찬다**
    evaluated_at        timestamptz NULL
    actual_return_pct   numeric(8,4) NULL  -- 예측 시점 기준가 대비 이 지평 종가의 누적 등락률(%)
    actual_outcome      NULL               -- CHECK ('up','down','flat'). ThesisDirection 재사용
    brier_score         numeric(6,5) NULL  -- 원 추론의 세 확률을 이 지평 결과로 채점. CHECK 0~2

    -- 사후 해설. LLM. horizon_days = 0 이면 항상 NULL. **두 슬롯 모두**
    narrative       text NULL         -- "왜 그렇게 움직였나"(한국어, 상한 1000자)
    verdict         NULL              -- CHECK ('supported','contradicted','unresolved').
                                      -- 원 추론의 **이유**가 이후 보도로 지지됐나
    narrative_at    timestamptz NULL
    llm_model       text NULL
    prompt_version  text NULL         -- "<판>/<변형>". 변형은 informed 또는 blind(4절)
    -- CHECK: 채점 넷은 전부 NULL이거나 전부 NOT NULL (all-or-none)
    -- CHECK: 해설 다섯 칸은 전부 NULL이거나 전부 NOT NULL (all-or-none)
    -- CHECK: horizon_days = 0 이면 해설 다섯 칸 전부 NULL
    -- CHECK: 채점과 해설이 둘 다 비어 있는 행 금지
    UNIQUE (thesis_id, horizon_days)  -- 멱등키
)
```

- **`verdict`는 `brier_score`와 다른 것을 잰다**(2026-08-21 추가). Brier는 "시장이 그 방향으로
  움직였나"고 `verdict`는 "그 **이유**가 맞았나"다. 방향만 우연히 맞은 추론과 이유까지 맞은
  추론을 가르는 것이 이 칸이고, `narrative`는 자유 서술이라 그것을 셀 수 없다.
  **둘을 합친 종합 점수를 만들지 않는다** — 섞으면 둘 다 못 읽는다.
- `verdict`를 해설 all-or-none에 함께 넣는다. 해설 없이 판정만 있으면 근거를 되짚을 수 없다.
- **채점 칸이 nullable이다**(2026-08-21 변경). 초판은 "행 자체가 채점의 산물"이라 NOT NULL을
  전제했는데, 해설을 `post_close`에도 붙이기로 하면서(4절) 채점 없이 해설만 있는 행이
  정상이 됐다. 대신 **둘 다 비어 있는 행을 금지한다** — 채점도 해설도 없으면 그 행은 없는
  것과 같다.
- **"`post_close` 행에는 채점이 없다"는 CHECK로 못 막는다.** 다른 테이블(`thesis.run_slot`)을
  봐야 하기 때문이다. 코드와 테스트가 지킨다 — `thesis_evidence`가 마스터로 FK를 걸지 않고
  테스트가 대조하는 것과 같은 판단이다.
- `thesis`에는 채점 컬럼이 남지 않는다. 확률 예측과 그 근거만 갖는다.
- `horizon_days`가 `days`인데 영업일인 것은 T+N 관용 표기를 따른 것이다. **컬럼 주석에
  거래일임을 못 박는다.** 달력일로 읽고 쿼리를 짜면 조용히 다른 값이 나온다.

`thesis_evidence`에는 컬럼 하나만 더한다. 해설이 인용한 근거도 구조가 같아서
(`kind`·`ref`·`title`·`url`·`detail`·`rank`) 테이블을 복제하지 않는다.

```sql
outcome_horizon_days integer NULL  -- NULL = 원 추론이 인용한 근거,
                                   -- 1/3/5 = 그 지평 해설이 인용한 근거
                                   -- CHECK (outcome_horizon_days IS NULL
                                   --        OR outcome_horizon_days IN (1,3,5))
UNIQUE (thesis_id, outcome_horizon_days, evidence_kind, evidence_ref)
UNIQUE (thesis_id, outcome_horizon_days, rank)
```

`thesis_outcome`으로 FK를 걸지 않는다. nullable FK 둘에 XOR CHECK를 얹는 형태보다 조용히
틀릴 여지가 적고, Neo4j에서도 `(kind, ref)` 노드 키를 그대로 재사용한다.

## 2. 무엇을 채점하나 — 기준가와 지평

`pre_open` 추론만 채점한다. `post_close` 리뷰는 이미 일어난 일의 해석이라 예측이 아니다
(1단계와 같다).

- **기준가는 예측 시점의 값, 즉 예측일 전 영업일 종가다.** T+0부터 T+5까지 기준가가 같아야
  누적 등락률이 연속된다. 기준가를 지평마다 옮기면(예: T+3을 T+2 종가 대비로) 그건 다른
  질문의 답이 된다.
- `actual_return_pct(N) = (T+N 종가 - 전 영업일 종가) / 전 영업일 종가 × 100`
- 종가 원본은 1단계 3절과 같다. 종목은 `stock_investor_trade_daily.close_price`(18:10 확정),
  지수는 `index_bar` 15:30 봉 close. **분봉으로 종가를 잡지 않는다.**
- T+N의 N은 `market_session`의 `krx_open_day`로 센 KRX 영업일이다
  (`airflow/modules/market_session.py`). 휴장일은 건너뛴다.

### `flat` 임계는 지평마다 다르다

1단계는 `FLAT_THRESHOLD_PCT = 0.3` 상수 하나다. 그 값을 5영업일 누적에 그대로 쓰면
`flat`이 사실상 사라져 `prob_flat`이 항상 틀린 쪽에 붙는다. Brier가 조용히 왜곡된다.

```python
FLAT_THRESHOLD_PCT = {0: 0.3, 1: 0.3, 3: 0.5, 5: 0.7}
```

- 값의 근거는 `0.3 × sqrt(N)`을 반올림한 것뿐이다. **실측이 아니다.** 4주 뒤 지평별
  `actual_outcome` 분포를 보고 조정한다 — `flat` 비율이 한 지평에서만 5% 아래거나 60% 위면
  그 값이 틀린 것이다.
- 상수는 `airflow/modules/thesis.py`에 두고 `classify_outcome(return_pct, horizon_days)`가
  받는다. 1단계의 시그니처에 인자 하나가 붙는다.

## 3. 채점 태스크 — `grade_followups`

`post_close` 슬롯에서만 돈다. LLM 없음.

```
select_pending_grades.sql  →  T+N 등락률 조회  →  classify_outcome / brier_score
                           →  thesis_outcome/insert_grade.sql
```

- **대상**: `thesis.run_slot = 'pre_open'`인 행 중, 지평 넷 각각에 대해 `thesis_outcome` 행이
  아직 없고 해당 T+N 영업일이 이미 지난 것 전부. 날짜 상한을 두지 않는다 — 장후가 실패했던
  날의 것도 다음 실행이 회수한다(1단계 `select_forecasts_to_grade.sql`과 같은 이유).
- **종가가 없으면 행을 만들지 않는다.** 미채점으로 남고 다음 실행이 다시 집는다. 0으로
  꾸미지 않는다. 상장폐지 등으로 영구 결측이면 영원히 재조회되므로 `(thesis_id,
  horizon_days)` 인덱스로 대비하고, 누적이 문제가 되면 그때 상한을 둔다.
- 쓰기는 조건부 upsert다(`WHERE thesis_outcome.evaluated_at IS NULL`). 재실행 멱등이고 이미
  매긴 점수를 덮지 않는다. **`DO NOTHING`이 아닌 이유**: 채점이 종가 결측으로 실패한 날
  해설만 돌면 행이 먼저 생기는데, `DO NOTHING`이면 그 지평이 영영 채점되지 않는다.
- T+0 채점도 여기서 한다. 기존 `build_thesis` 안의 채점 호출(3단계 2절 1-4)은 이 태스크로
  옮긴다.

## 4. 해설 태스크 — `narrate_followups`

`post_close` 슬롯 실행에서 돈다. `grade_followups` 뒤에 붙는다.

- **대상은 두 슬롯 모두다**(2026-08-21 변경). 채점(3절)은 `pre_open`만이지만 해설은
  `post_close` 리뷰에도 붙인다. 장후 리뷰는 "오늘 이래서 움직였다"는 **인과 주장**이라
  며칠 뒤 보도로 검증할 값어치가 오히려 크다. `post_close` 대상은 채점 넷이 NULL인 채로
  `thesis_outcome` 행이 새로 생긴다(1절).
- **지평마다 별도 LLM 호출이다.** T+1·T+3·T+5 각각 한 번, 하루 최대 3회 추가. 툴 조회의
  기준 시각 `as_of_at`이 지평마다 달라서 한 대화에 섞을 수 없다. **한 호출 안에서는 그
  지평의 모든 subject를 한 번에** 준다(건별 호출 금지 규칙 그대로).
- 대상은 그 지평에서 `narrative IS NULL`인 것(`thesis_outcome` 행이 아직 없는 `post_close`
  추론 포함). 해설 LLM이 실패한 날의 것도 다음 실행이 회수한다.
- **쓰기는 조건부 upsert다**(`WHERE thesis_outcome.narrative IS NULL`). 첫 성공본 불변.
  순수 UPDATE가 아닌 이유는 `post_close`에 채점 행이 없어 해설이 행을 새로 만들기
  때문이다. `RETURNING`이 0행이면 근거도 넣지 않는다 — 남의 해설과 어긋난 인용이 남는다.
  근거는
  `thesis_evidence`에 `outcome_horizon_days`를 채워 INSERT하고, UPDATE와 한 트랜잭션에 넣는다.

### 흐름 — `FollowupNarrator`

`ThesisBuilder`와 같은 LangGraph 형태를 재사용한다.

```
investigate → (tool_calls 있으면) tools → investigate → … → answer → (형식 실패) repair → answer
```

Toolbox는 2단계의 것을 그대로 쓴다. `as_of_at`만 그 지평의 장후 15:30 KST로 바꿔 만든다.
상한도 그대로(왕복 4, tool call 12회, 결과 누적 24,000자).

### 프롬프트에 주는 것 — 변형 둘을 실측으로 가른다

- 원 추론의 세 확률과 세 이유 문장, 그리고 그때 인용한 근거 목록
- 지평(`T+3` 등)과 기준 시각
- **실제 결과** — `actual_return_pct`, `actual_outcome`, `brier_score`.
  **이것을 주느냐 마느냐가 변형을 가른다.**

`narrative`("왜 그렇게 움직였나")를 쓰려면 얼마나 움직였는지 알아야 한다. 그런데 그것을
알고 `verdict`("원 추론의 **이유**가 맞았나")를 판정하면 모델이 결과를 보고 역산한다.
사후확신 편향 경고는 프롬프트에 적을 수 있지만 검사 가능한 규칙이 아니다.

**어느 쪽이 나은지 추측하지 않고 실측한다.** `FollowupNarrator`가 `include_outcome` 플래그를
받고, 그 값이 `prompt_version`에 실린다(`assessment.py`의 `LlmSettings.prompt_revision`이
관점을 판에 싣는 것과 같은 방식 — 새 컬럼을 만들지 않는다).

| 변형 | `prompt_version` | 프롬프트에 결과를 |
| --- | --- | --- |
| `informed` | `1/informed` | 준다(초판) |
| `blind` | `1/blind` | 주지 않는다 |

실험 절차와 판정 지표는 12절에 있다.

### `verdict`에 근거 인용을 강제한다

- 프롬프트 규칙: **`supported`나 `contradicted`를 고르면 그 판단의 근거가 된 ref를 반드시
  인용하라. 인용할 문서가 없으면 `unresolved`다.**
- **그리고 저장 전에 코드가 다시 검사한다.** `evidence_refs`가 비었는데 `verdict`가
  `unresolved`가 아니면 `unresolved`로 내리고 건수를 로그로 남긴다. 프롬프트 규칙만으로는
  역산을 못 막지만 이 검사는 막는다.
- 인용이 남으면 나중에 사람이 그 문서를 열어 판정이 실제로 문서에서 나왔는지 확인할 수 있다.
  **오염을 없애는 장치가 아니라 되짚을 수 있게 만드는 장치다.**
- **1회차 실측(12절) 뒤 이 검사의 무게가 커졌다.** 프롬프트에서 결과를 빼도 후속 기사가
  지수 등락을 그대로 싣고 있어 모델이 읽어낸다. 즉 `blind`도 결과를 안다. 그러면 남는
  방어는 프롬프트 변형이 아니라 **"판정하려면 문서를 대라"** 하나뿐이다.

### 답변 스키마

```json
{"narratives": [{"subject_code": "KOSPI",
                 "narrative": "…",
                 "verdict": "unresolved",
                 "evidence_refs": ["document:456", "disclosure:20260821000123"]}]}
```

- `verdict`는 `Literal["supported","contradicted","unresolved"]`로 선언해 스키마에 enum으로
  실린다(`assessment.py`의 `direction`과 같은 방식 — 검증기가 아니라 타입으로 막는다).
  **`unresolved`가 기본이자 가장 흔한 답이어야 한다.** 후속 보도가 원 추론의 이유를 직접
  다루지 않으면 그것이 정답이다. 억지 판정이 무판정보다 나쁘다는 것을 프롬프트에 적는다.
- `narrative` 상한 1000자. 넘으면 자른다.
- `subject_code`가 대상 목록 밖이면 그 항목만 버린다. 전부 버려지면 repair 1회, 두 번째
  실패는 `ThesisError`.
- `evidence_refs`는 2단계와 같이 레지스트리로 검증하고 목록 밖 ref는 버린다. 근거 0건도
  허용한다.
- **확률을 다시 내지 않는다.** 원 추론의 확률은 불변이고, 사후에 확률을 새로 매기면 그건
  예측이 아니라 결과를 아는 상태의 후험 확률이라 채점할 수 없다.
- 프롬프트 규칙: 툴 결과에 없는 사실·숫자 금지, 투자 조언 금지, 그리고 **"결과를 알고 쓰는
  글이라 사후확신 편향이 들어간다"를 명시**하고 근거 없는 단정 대신 "이 기사들은 …라고
  본다" 형태를 쓰게 한다.

## 5. 피드백 루프 — `past_theses` 툴

2단계 ThesisToolbox에 네 번째 툴을 더한다. 새 층이나 새 테이블 없이 기존 툴 셋 옆에 하나
붙는 것뿐이다.

| 툴 | 반환 | SQL |
| --- | --- | --- |
| `past_theses(subject_code, n)` | 그 subject의 최근 `pre_open` 추론 `n`건: 예측일, 세 확률, 세 이유, 지평별 `actual_outcome`·`brier_score`, 지평별 `narrative` | `thesis/select_past_with_outcomes.sql` |

- 상한은 코드 상수로 강제한다: `1 <= n <= 10`, `subject_code`는 이번 실행의 subject 목록
  안의 값만. 넘으면 잘라 실행하거나 "상한 초과" `ToolMessage`로 돌린다(2단계 계약 그대로).
- **창의 끝은 여기서도 `as_of_at`이다.** `thesis.run_date < as_of_at의 KST 날짜`,
  `thesis_outcome.evaluated_at <= as_of_at`, `narrative_at <= as_of_at`. 이게 없으면
  장전 슬롯을 재실행할 때 그날 저녁의 채점 결과가 아침 예측에 섞인다.
- 이 툴이 돌려준 항목에는 `ref`를 붙이지 않는다. 자기 과거 추론은 `thesis_evidence`에
  근거로 저장할 대상이 아니다 — 근거 종류는 `document`·`disclosure`·`macro_change` 셋
  그대로 둔다.

### 이 루프가 효과가 있다는 보장은 없다

- 과거 성적을 문맥에 넣는 것으로 언어 모델의 확률 캘리브레이션이 개선된다는 근거는 약하다.
  **효과 없음이 관측되면 툴을 뺀다.** 판단 기준은 도입 전후 지평별 Brier 추이이고, 분기
  단위로 본다(4주 표본으로는 결론이 안 난다 — README 5절).
- **사후확신 편향이 순환할 수 있다.** 결과를 알고 쓴 해설이 다음 예측의 문맥에 들어가면
  모델이 "그때는 이런 이유로 올랐으니 지금도"라는 서사를 재생산한다. 프롬프트에서 과거
  해설을 **사실이 아니라 그때의 해석**으로 제시하고, 4주 검증에서 장전 추론의 이유 문장이
  과거 해설 문장을 그대로 베끼는지 사람이 눈으로 본다.
- 두 위험 모두 툴 하나를 빼면 되돌아간다. 그 되돌릴 수 있음이 이 방식을 고른 이유다.

## 6. 그래프 반영 (4단계 뒤)

[4-graph.md](4-graph.md)의 동기화에 노드 하나와 엣지 둘을 더한다.

- `(:Outcome {thesis_id, horizon_days})` — 채점과 해설을 담는다. 키는 `(thesis_id, horizon_days)`.
- `(:Thesis)-[:GRADED_AT]->(:Outcome)`
- `(:Outcome)-[:CITES {rank}]->(:Evidence)` — `outcome_horizon_days`가 NULL이 아닌 엣지.
  기존 `(:Thesis)-[:CITES]->(:Evidence)`는 NULL인 것만 태운다.

`sync_graph`가 쓰는 XCom 슬롯 목록에 **채점·해설로 바뀐 `thesis`의 (run_date, 'pre_open')도
넣는다.** 3단계 2절이 이미 채점된 forecast를 목록에 싣고 있어 형태는 같다.

## 7. DAG

3단계 `market_thesis_analysis.py`를 고친다. 새 DAG를 만들지 않는다 — 스케줄과 readiness
guard를 복제하게 되고, 채점은 어차피 `post_close` 슬롯의 종가가 준비된 뒤라야 한다.

```
build_thesis >> grade_followups >> narrate_followups >> notify_slack
```

- `grade_followups`·`narrate_followups`는 `pre_open` 슬롯이면 즉시 반환한다.
- 실패 판정은 3단계 3절 그대로. `LlmError`·`ThesisError` → `AirflowFailException`,
  `RetryableLlmError`·`ConnectionError`는 올려 재시도.
- `narrate_followups`가 실패해도 `grade_followups`의 채점은 이미 커밋돼 있다. 다음 실행이
  `narrative IS NULL`로 회수한다.
- XCom 슬롯 목록은 세 태스크의 결과를 합친다.

### Slack

**T+5 해설만 보낸다.** T+1·T+3까지 매일 보내면 하루 세 덩이가 더 붙어 원래 알림이 묻힌다.
T+5가 해설이 가장 굳은 시점이기도 하다. `notify_slack`의 기존 메시지 뒤에 섹션 하나:

```
*5영업일 뒤 되돌아보기 — {run_date} 장전 전망*
*{label}*  예측 ▲{prob_up:.0%} · 실제 {actual_return_pct:+.2f}% ({actual_outcome})  Brier {brier_score:.2f}
{narrative}
근거: <{url}|{title}> · <{url}|{title}>
```

- 대상이 0건이면 이 섹션을 아예 넣지 않는다.
- 블록 50개·section 3,000자 한도는 3단계 4절 그대로. 이 섹션도 `render_blocks`가 자른다.

## 8. SQL 파일

| 파일 | 용도 |
| --- | --- |
| `thesis_outcome/select_pending_grades.sql` | 채점할 (thesis_id, horizon_days) 전부. `thesis_outcome` 행이 없고 T+N 영업일이 지난 것 |
| `thesis_outcome/insert_grade.sql` | 채점 행 INSERT. **조건부 upsert다** — `DO NOTHING`이면 해설이 먼저 만든 행을 영영 채점하지 못한다. `WHERE thesis_outcome.evaluated_at IS NULL` |
| `thesis_outcome/select_pending_narratives.sql` | 한 지평에서 `narrative IS NULL`인 대상 + 원 추론의 확률·이유. **`thesis`에서 LEFT JOIN한다** — `post_close`는 채점을 안 받아 행이 아예 없다 |
| `thesis_outcome/insert_narrative.sql` | 해설 다섯 칸 채움. **UPDATE가 아니라 조건부 upsert다** — `post_close`는 해설이 행을 새로 만든다. `WHERE thesis_outcome.narrative IS NULL` |
| `thesis_outcome/select_by_thesis_ids.sql` | 4단계 그래프 동기화, Slack T+5 섹션 |
| `thesis/select_past_with_outcomes.sql` | `past_theses` 툴. 지평별 결과를 jsonb 배열로 접어 추론당 한 행을 지킨다 |
| `stock_investor_trade_daily/select_horizon_return.sql` | 종목 T+N 누적 등락률(기준가 = 예측일 전 영업일 종가) |
| `index_bar/select_horizon_return.sql` | 지수 T+N 누적 등락률 |

1단계의 `thesis/update_outcome.sql`은 만들지 않는다. 모든 조회 SQL의 시간 창은
`%(as_of_at)s`를 끝으로 쓰고 `now()`를 쓰지 않는다.

## 9. 테스트

- `tests/migrations/test_thesis_schema.py`에 추가 — `thesis_outcome`의 UNIQUE,
  `horizon_days` CHECK, Brier 범위 CHECK, `verdict` 값 집합 CHECK, `horizon_days=0`이면
  해설 다섯 칸 NULL CHECK, 해설 all-or-none CHECK, CASCADE, 주석. `thesis`에 채점 컬럼이
  **없는지**. `thesis_evidence`의 UNIQUE 둘에 `outcome_horizon_days`가 들어갔는지.
- SQL 컬럼 vs 모델 metadata 대조 — `insert_grade.sql`, `insert_narrative.sql`,
  `select_by_thesis_ids.sql`. `insert_grade.sql`에 `ON CONFLICT DO NOTHING`이 있고
  `DO UPDATE`가 없는지. `select_horizon_return.sql`이 `stock_investor_trade_daily`를 보고
  `stock_bar`를 안 보는지.
- `tests/modules/test_thesis.py`에 추가:
  - `classify_outcome(return_pct, horizon_days)` 경계 — 지평 0·1에서 0.29 `flat`/0.30 `up`,
    지평 3에서 0.49 `flat`/0.50 `up`, 지평 5에서 0.69 `flat`/0.70 `up`, 음수 대칭.
  - 지평 상수가 `{0,1,3,5}`이고 CHECK 문자열과 일치하는지.
  - T+N 등락률의 기준가가 **예측일 전 영업일 종가**인지(지평이 달라도 기준가가 같은지).
  - `FollowupNarrator`: 지평마다 별도 호출인지, 한 호출 안에서 subject가 한 번에 가는지,
    목록 밖 subject·ref 버림, 전부 버려지면 repair 1회·두 번째는 `ThesisError`,
    `narrative` 1000자 자름, 근거 0건 허용, 목록 밖 `verdict` 값이 그 항목을 버리는지.
  - **`verdict` 인용 강제**: 근거 0건인데 `supported`·`contradicted`가 오면 `unresolved`로
    내려가는지, `unresolved`는 근거 0건이어도 그대로인지.
  - **프롬프트 변형**: `include_outcome=False`면 프롬프트에 `actual_return_pct`·
    `actual_outcome`·`brier_score`가 **없는지**, `True`면 있는지. `prompt_version`이
    `"<판>/informed"`·`"<판>/blind"`로 갈리는지.
  - `post_close` 추론도 해설 대상에 드는지, 그 행의 채점 넷이 NULL로 남는지.
  - 쓰기: `narrative IS NULL`인 행만 UPDATE되는지, 이미 해설이 있으면 모델을 부르지 않는지,
    UPDATE와 evidence INSERT가 한 트랜잭션인지.
  - `past_theses`: `n` 0·11이 잘리는지, subject 목록 밖 값이 오류 `ToolMessage`인지,
    SQL에 넘어가는 창의 끝이 `as_of_at`이고 `evaluated_at`·`narrative_at` 술어가 실리는지.
  - 렌더링: T+5 섹션이 예측 확률·실제 등락률·Brier·해설·근거를 담는지, 0건이면 섹션이
    아예 없는지.
- `tests/dags/test_market_thesis_analysis.py`에 추가 — 태스크 순서
  `build_thesis >> grade_followups >> narrate_followups >> notify_slack`,
  `pre_open` 실행에서 뒤 두 태스크가 즉시 반환하는지, XCom 슬롯 목록이 세 태스크의 결과를
  합치는지, `narrate_followups` 실패가 채점을 되돌리지 않는지.

## 10. 만들지 않는 것

- **T+10 이상의 지평** — 5영업일을 넘기면 그날의 예측과 인과가 끊긴다. 넷으로 시작한다.
- **`post_close` 리뷰의 채점** — 리뷰는 예측이 아니다. 채점할 대상이 없다.
  (**해설과 `verdict`는 붙인다** — 4절.)
- **세 번째 프롬프트 변형(`direction_only`)** — 방향만 주고 크기·Brier를 빼는 중간값이다.
  둘로 먼저 재고, 오염이 확인되면 그때 붙인다. 플래그 하나라 나중에 싸다.
- **해설의 재해설** — 지평마다 한 번, 첫 성공본 불변. T+5 해설이 마음에 안 들어도 고치지
  않는다.
- **지평별 Slack 알림 분리** — T+5 하나만 보낸다. 소음이 늘면 이 섹션부터 뺀다.
- **`prompt_version` 자동 승급** — 피드백 루프는 툴 하나뿐이고, 프롬프트를 고치는 것은
  사람이다. 모델이 자기 프롬프트를 고치는 층은 만들지 않는다.
- **교훈·규칙 추출 테이블** — 해설에서 "이런 패턴이면 이렇게 봐라"를 뽑아 쌓는 자가 메모리
  층. 규칙이 틀리면 예측을 조용히 망친다. `past_theses`로 원문을 그대로 보여 주는 것으로
  시작하고, 그것이 문맥 한도에 부딪히면 그때 본다.
- **지평별 정확도 대시보드** — `thesis_outcome`이 쌓이면
  `GROUP BY horizon_days, subject_code` 한 줄 쿼리다.
- **Brier와 `verdict`를 합친 종합 점수** — 둘은 다른 것을 잰다(1절). 섞으면 둘 다 못 읽는다.
- **`verdict`의 신뢰도 점수** — 셋 중 하나면 충분하다.

## 11. 남은 확인

- **`flat` 임계값** — 2절의 `{0: 0.3, 1: 0.3, 3: 0.5, 5: 0.7}`은 실측이 아니다. 배포 4주 뒤
  지평별 `actual_outcome` 분포를 보고 정한다.
- **해설의 근거가 될 문서 소스** — 현재 수집 중인 것은 `einfomax`·`cnbc`·`bbc_business`와
  KRX·FSS 공시, 각국 정부·중앙은행 발표다. **증권사 리서치 리포트 소스는 없다.** 전문가
  해설은 뉴스에 섞여 들어오는 만큼만 잡힌다. 리포트를 근거로 쓰려면 소스 추가가 선행이고
  그것은 이 문서의 범위 밖이다 — **6단계로 뺐다**(2026-08-21, 사용자 요청.
  [README.md](README.md) 2절 표). 그 단계의 첫 작업은 코드가 아니라 KIS OpenAPI가
  종목투자의견을 주는지 확인하는 spike다. 저장소에 관련 코드가 0건이라 출처부터 미확인이다.
- **LLM 호출량** — 하루 최대 3회 추가(지평 셋). 3단계까지가 하루 2회이므로 2.5배가 된다.
  비용이 문제가 되면 지평을 T+5 하나로 줄이는 것이 첫 번째 손잡이다 — 채점(2·3절)은 셋을
  유지해도 되고, 줄어드는 것은 해설뿐이다.
- **`include_outcome` 기본값 — 정해졌다(2026-08-21).** `informed`를 유지한다. 근거는
  12절 2회차다: 두 변형의 툴 호출·레지스트리·서술 질이 같고 판정만 갈리는데, 4절 정의대로면
  `informed`가 맞고 `blind`는 과소 판정이다. 독립 사건 둘뿐이라 분기 단위로 다시 본다.
- **`contradicted`가 너무 쉽게 나오는가** — 2회차에서 다섯 중 넷이었다. 그날 보도가 실제로
  다른 원인(금리)을 지목했으니 이번엔 맞다. 다만 이 비율이 유지되면 "반박"과 "다른 원인을
  지목"을 값 둘로 가를지 다시 본다. **지금은 가르지 않는다** — 셋으로 시작한다.

## 12. 실험 — `informed` vs `blind`

4절의 변형 둘 중 무엇을 기본값으로 둘지 **추측하지 않고 실제 호출로 정한다.**

### 하네스

`notebooks/narrator_ab.ipynb`다. 셀 하나에 LLM 호출 하나라 타임아웃이 나도 그 셀만 다시
돌린다(grok-4.6이 느려 300초 기본 타임아웃에 걸린다 — 노트북은 900초로 올려 둔다).
**DB에 아무 것도 쓰지 않는다** — 실험 결과는 `thesis_outcome`에 남기지 않고 사람이 읽는다.

- 입력은 **운영 DB에서 읽는다**(SELECT만, `read_only` 연결). 로컬 DB에는 평가된 문서가
  손에 꼽아 현실적인 비교가 안 된다(2026-08-21 실측: 로컬 348건 중 평가 5건).
- 변형마다 `ThesisToolbox`를 새로 만든다. 레지스트리를 공유하면 뒤에 도는 변형이 앞의
  툴 예산을 물려받아 조사를 못 한다. **각 변형이 제 예산으로 실제 한 번씩 돈다.**
- **대상을 늘리는 것은 거의 공짜다.** 추론도 해설도 대상 전부를 한 대화에 한꺼번에 주므로
  대상을 둘에서 다섯(지수 셋 + 종목 둘)으로 늘려도 호출 수가 그대로다. 표본만 2.5배가 된다.

### 판정 지표

`narrative` 문장의 좋고 나쁨은 사람이 읽고 정한다. 그 위에 계산되는 숫자 셋을 함께 낸다.

1. **`verdict`가 가격 방향을 그대로 따라가는가.** `informed`에서 `verdict = supported`가
   "원 추론의 최빈 방향 == `actual_outcome`"과 거의 일치하면, 그 판정은 이유를 잰 것이
   아니라 **Brier를 다른 말로 반복한 것**이다.
2. **`unresolved` 비율.** 후속 보도가 원 추론의 이유를 직접 다루는 경우는 흔하지 않다.
   `informed`에서만 이 비율이 크게 낮으면 결과를 보고 억지 판정하는 것이다.
3. **툴 호출 수와 인용 근거 수.** 1회차에서 갈린 것이 이 값이라 결과에 남긴다(아래).

### 1회차 실측 (2026-08-21, grok-4.6, 08-19 T+1, 대상 2건)

| 변형 | 대상 | verdict | 인용 근거 |
| --- | --- | --- | --- |
| `informed` | KOSPI | `unresolved` | 0건 |
| `informed` | KOSDAQ | `unresolved` | 0건 |
| `blind` | KOSPI | `contradicted` | **5건** |
| `blind` | KOSDAQ | `unresolved` | 0건 |

표본 2건/변형이라 **결론이 아니다.** 그래도 설계 전제 하나가 깨졌다.
(2회차에서 이 회차의 방향이 뒤집힌다 — 아래.)

- **`blind`는 blind가 아니다.** blind KOSPI 해설이 "코스피가 5.80% 내린 6,471.17에 마감",
  "다음날 … 코스피가 5%대 강세에 매수 사이드카가 발동"이라고 썼다. 프롬프트에서 결과를
  뺐는데 **후속 기사가 지수 등락을 그대로 싣고 있어 모델이 읽어냈다.** 후속 보도가 하는
  일이 그날 시장이 얼마나 움직였는지 쓰는 것이므로 당연하다.
  → **오염 경로는 프롬프트가 아니라 근거 자체다.** 프롬프트에서 숫자를 빼는 것으로는
  역산을 막지 못한다. 델타 C(근거 인용 강제)가 그래서 더 중요해진다.
- **예상과 반대로 `informed`가 덜 조사했다.** 근거 0건 대 5건이고, 해설의 질도 blind가
  나았다(급락 → 바이백 → 자사주 소각 → 반등의 인과 사슬 대 사실 나열). 가설: **답을 알면
  찾을 이유가 줄어든다.** 확인하려면 툴 호출 수가 결과에 남아야 해서 2회차부터 기록한다.
- `_grounded_verdict` 강등은 한 번도 발동하지 않았다. 근거 0건인 것들은 이미 스스로
  `unresolved`였다.

### 2회차 실측 (2026-08-21, grok-4.6, 08-18 T+1, 대상 5건) — **`informed`로 정한다**

08-18은 전 종목이 크게 빠진 날이다(KOSPI −7.27%, 005930 −9.84%). 추론은 다섯 대상 모두
`down`을 최빈으로 골랐고 **방향은 전부 맞았다.**

| 변형 | 툴 | 레지스트리 | verdict | 인용 근거 |
| --- | --- | --- | --- | --- |
| `informed` | 3회 | 40건 | `contradicted` 4 / `unresolved` 1 | 대상당 3건 |
| `blind` | 3회 | 40건 | `unresolved` 5 | 0건 |

**조사량이 똑같다.** 툴 호출 3회, 레지스트리 40건이 두 변형에서 같다. 1회차의
"결과를 알면 덜 조사한다" 가설은 **틀렸다** — 그때 갈린 것은 조사량이 아니라 인용이었다.

**서술의 질도 차이가 없다.** 둘 다 같은 사건(미 30년물 19년 만 최고, 매도 사이드카,
KDI 성장률 상향)을 같은 수준으로 짚는다. `blind`가 "왜 움직였나"를 못 쓸까 걱정했는데
그렇지 않다 — 기사가 등락을 알려 주기 때문이다.

**갈린 것은 판정 하나이고, 규칙대로면 `informed`가 맞다.** 같은 사실을 찾아놓고 라벨만
다르게 붙였다.

> `informed` — "원 추론이 든 미 지수선물·VIX, 전 세션 반도체 차익매물, WTI 비용 경로는
> **원인으로 지목되지 않는다**" → `contradicted`
>
> `blind` — "원 추론이 든 … 경로를 **직접 검증하거나 반박한 기사는 보이지 않는다**"
> → `unresolved`

보도는 금리 급등을 원인으로 지목했다. 4절의 정의가 "반박했거나 **다른 원인을 지목**했다"
이므로 `contradicted`가 맞고 `blind`는 과소 판정이다.

**오염은 관측되지 않았다.** `informed`의 결정적 판정 넷은 전부 `contradicted`인데,
그 넷 모두 **방향이 맞은** 추론이다. 가격을 따라 읽었다면 `supported`가 나왔어야 한다.
누적 `verdict_follows_price`가 `informed` 0/7, `blind` 1/7이다.

그리고 이 회차가 **`verdict` 축이 왜 필요한지를 보여 준다.** Brier는 0.51~0.58로 나쁘지
않은데(방향이 맞았으니) 판정은 `contradicted`다 — **맞았지만 이유는 틀렸다**를 Brier는
말할 수 없다.

**결론: `include_outcome=True`(`informed`)를 기본값으로 유지한다.** `blind`가 사는 것이
없다 — 툴도 서술도 같고, 가격은 어차피 기사로 새어 들어오며, 판정만 체계적으로 약해진다.

**남는 제약.** 표본이 7건/변형이지만 **독립 사건은 둘뿐이다**(08-18 하루에 다섯 대상이
같은 방향으로 움직였다). 1회차(08-19)는 방향이 반대였다. 모델 하나, 지평 하나(T+1)다.
분기 단위로 다시 본다.

### 표본을 어떻게 늘리나

지금 병목은 호출 수가 아니라 **데이터 구간**이다(운영 문서가 2026-08-17부터라 T+5가 지난
추론일이 없다). 싼 것부터 한다.

1. **대상 늘리기(공짜)** — 지수 셋 + 종목 둘. 날짜당 2건이 5건이 된다.
2. **날짜 늘리기** — 마감 봉과 확정 종가가 둘 다 있는 날만 쓴다. 날짜마다 LLM 호출이
   한 벌 더 든다.
3. **지평 늘리기** — 날이 지나면 T+3, 그 뒤 T+5가 열린다. 같은 추론을 다시 써서 해설만
   더 받으므로 추론 생성 호출은 안 는다.

### 필요한 것

- **LLM 키.** 이 저장소에는 없다(`compose/*/airflow/.env`는 NAS에 있고 `.env.sample`만 커밋돼
  있다). `XAI_API_KEY`를 환경에 넣어야 돈다. 노트북은 `getpass`로 받아 파일에 남기지 않는다.
- **운영 DB 읽기.** `config.yaml`의 `prod` 별칭(`read_only: true`, 마이그레이션 꺼짐).
