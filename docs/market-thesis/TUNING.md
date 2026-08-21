# 고도화 — 무엇을 재고, 언제 보고, 어느 손잡이를 당기나

- 대상: 시장 추론 전 단계 (`market_thesis_analysis` DAG와 `modules/thesis.py`)
- 성격: 단계 문서가 아니다. 1~6이 "무엇을 만드나"의 순서라면 이 문서는 **만든 뒤에 쓰는
  운영 규칙**이다. [README.md](README.md)와 같은 층이다.
- 상태: **캘린더의 0일이 아직 안 왔다.** [3-dag-slack.md](3-dag-slack.md) 7절의 선행 조건
  둘(운영 DB에 테이블 없음, `XAI_API_KEY` 무효)이 남아 있어 **지금은 4주 검증을 하려 해도
  할 수 없다.** 배포일이 4절 캘린더의 기준점이다. (`config.yaml` 파손은 2026-08-21에
  해소됐고 애초에 gitignore 대상이라 배포 산출물이 아니었다.)

기능은 다 만들었다. 남은 일은 "무엇을 더 만드나"가 아니라 **쌓이는 숫자를 보고 어느 상수를
어느 방향으로 당기나**다. 그 판단 재료가 다섯 문서와 코드 주석에 흩어져 있어 한 곳에 모은다.

---

## 1. 방법 넷

일반론이 아니다. 2026-08-18~21 반복에서 **실제로 쓴** 규칙이다.

### 추측하지 않고 실측한다

사후 해설 프롬프트에 가격 결과를 주느냐(`informed`) 마느냐(`blind`)를 A/B로 갈랐다
([5-followup.md](5-followup.md) 12절, `notebooks/narrator_ab.ipynb`). 그 과정에서 **설계
전제 하나가 깨졌다** — "`blind`는 결과를 모른다"가 거짓이었다. 후속 기사가 지수 등락을
그대로 싣기 때문에 프롬프트에서 빼도 모델이 읽는다.

가설을 그대로 뒀으면 있지도 않은 오염 경로를 막으려고 프롬프트를 계속 비틀었을 것이다.
**실측이 깬 것은 값 하나가 아니라 방어의 방향이다.**

### 되돌릴 수 있게 만든다

진 변형을 지우지 않았다. `NarrativeVariant.BLIND`가 남아 있고 `prompt_version`에
`1/informed` 형태로 어느 변형이었는지 실린다. 분기 재검증이 노트북 재실행으로 끝난다.

`past_theses` 툴도 같은 모양이다 — 효과가 없으면 툴 하나를 빼면 되돌아간다
([5-followup.md](5-followup.md) 5절). **되돌릴 수 있음이 그 방식을 고른 이유다.**

### 독립 사건으로 센다

2회차 표본은 7건이었지만 **독립 사건은 둘뿐이다.** 08-18 하루에 다섯 대상이 같은 방향으로
움직였다 — 시장 하나가 움직인 것을 다섯 번 센 것이다. 지평 셋(T+1·3·5)도 같은 추론
하나에서 나오므로 서로 독립이 아니다.

행 수가 아니라 **날짜 수**를 센다.

### 한 번에 한 손잡이

모델과 프롬프트를 같이 바꾸면 어느 쪽 효과인지 못 가른다. 둘 다 바꿔야 하면
`PROMPT_VERSION`을 올리고 그 전후를 따로 센다. `ops.py`의 창(`THESIS_WINDOW_DAYS`, 28일)이
판을 섞어 보여 주므로 판을 올린 직후에는 그 창이 두 판에 걸친 구간이라는 것을 알고 본다.

---

## 2. 지금 재는 것과 못 재는 것

[README.md](README.md) 5절이 4주 검증 항목 다섯을 지정했다. **그중 자동으로 나오는 것은
절반뿐이다.** 나머지를 손으로 어떻게 읽는지가 아래 표다.

| 항목 | 읽는 법 | 자동 |
| --- | --- | --- |
| 지평별 평균 Brier vs 0.667 | ops 브리핑 "추론 품질" 표 | ● |
| 지평별 `flat` 비율 | 같은 표 (`flat_outcomes / graded`) | ● |
| `verdict` 분포 | 같은 표 (지지/반박/보류) | ● |
| 채점·해설 적체 | ops 브리핑 "추론 적체" | ● |
| subject 커버리지 | 아래 쿼리 A | 손 |
| `tool_rounds` 분포 | 아래 쿼리 B | 손 |
| 슬롯별 정시 발행률 | 아래 쿼리 C (근사) 또는 Airflow 메타DB | 손 |
| readiness guard 재시도 | Airflow 로그·메타DB만 | 손 |
| **근거 유효율** | **못 읽는다** — 아래 참고 | ✗ |

ops 브리핑은 매일 08:00 OPS 채널로 나간다(`slack_ops_briefing`). 지평별 한 줄이고
`beats_uniform`이 `✓`/`✗`로 baseline 비교를 대신해 준다 —
`airflow/modules/briefing/ops.py`의 `THESIS_CALIBRATION`·`THESIS_BACKLOG`가 그 원본이다.

### 손으로 읽는 쿼리 셋

4주 뒤에 다시 짜지 않도록 그대로 둔다. 운영 DB **읽기 전용**으로 돈다. 셋 다 로컬에서
문법·컬럼명을 확인했다(2026-08-21). **운영에는 아직 `thesis` 테이블이 없어 지금 돌리면
`UndefinedTable`이다** — [3-dag-slack.md](3-dag-slack.md) 7절 선행 조건 1.

**A. subject 커버리지** — 모델이 목록 밖 답을 내거나 확률 합이 어긋나면 그 subject가
조용히 빠진다. 슬롯마다 몇 개가 살아남았는지 본다.

```sql
SELECT run_date, run_slot, count(*) AS subjects
FROM thesis
WHERE run_date >= current_date - 28
GROUP BY run_date, run_slot
ORDER BY run_date DESC, run_slot;
```

**B. `tool_rounds` 분포** — 값이 `MAX_TOOL_ROUNDS`(4)에 붙어 있으면 상한이 조사를 자르고
있다는 뜻이다. 1~2에 몰려 있으면 상한이 놀고 있다.

```sql
SELECT tool_rounds, count(*)
FROM thesis
WHERE run_date >= current_date - 28
GROUP BY tool_rounds
ORDER BY tool_rounds;
```

**C. 정시 발행률(근사)** — `created_at`이 그 슬롯 시각에서 얼마나 밀렸나. 재시도가
readiness guard에서 돌면 여기가 늘어난다. **정확한 값은 Airflow 메타DB에 있고 이건 근사다.**

```sql
SELECT run_slot,
       count(*) AS runs,
       round(avg(extract(epoch FROM created_at - as_of_at) / 60)) AS avg_delay_min,
       max(extract(epoch FROM created_at - as_of_at) / 60) AS max_delay_min
FROM thesis
WHERE run_date >= current_date - 28
GROUP BY run_slot;
```

### 근거 유효율은 왜 못 읽나

모델이 낸 `evidence_refs` 중 **후보 목록 밖이라 버린 것**의 비율이다. 버리는 것은
`thesis.py`가 하지만 **버린 건수를 로그로만 남기고 저장하지 않는다.** 남은 건수
(`thesis_evidence` 행 수)만 있고 분모가 없다.

읽으려면 코드가 필요하다. 두 방법의 값이 다르다.

| 방법 | 정확도 | 비용 |
| --- | --- | --- |
| `thesis.rejected_ref_count` 컬럼 | 정확, 영구 | 리비전 한 벌 + 저장 경로 수정 |
| Airflow 로그 grep | 대략, 보존 기간에 묶임 | 공짜 |

**지금은 만들지 않는다.** 4주 검증에서 근거 0건 thesis가 눈에 띄게 늘면 — 그것이 이
문제의 눈에 보이는 증상이다 — 그때 첫 번째를 넣는다.

---

## 3. 손잡이 장부

**"어느 방향" 칸이 이 표의 값어치다.** 값만 적으면 코드를 읽으면 되는 일이다.

| 손잡이 | 지금 값 | 사는 곳 | 무엇을 보고 | 어느 방향 |
| --- | --- | --- | --- | --- |
| `FLAT_THRESHOLD_PCT` | `{0:0.3, 1:0.3, 3:0.5, 5:0.7}` | `thesis.py` | 지평별 `flat` 비율 | 한 지평만 5% 아래면 그 값을 낮추고 60% 위면 올린다. **실측이 아니라 `0.3 × sqrt(N)` 반올림이다** — 조정 조건은 코드 주석이 원본 |
| `INDEX_SUBJECTS` + `instrument.is_watched` | KOSPI·코스닥 + watched 종목 | `thesis.py` / `instrument` 테이블 | 표본 수 | **표본을 늘리는 가장 싼 손잡이다.** LLM 호출 수는 그대로고 날짜당 건수만 는다([5-followup.md](5-followup.md) 12절). 단 독립 사건 수는 안 는다 — 1절 |
| `NarrativeVariant` 기본 | `INFORMED` | `thesis.py`, `FollowupNarrator.__init__` | 분기 Brier + `verdict` 분포 | 노트북 재실행으로 재검증. `BLIND`가 남아 있어 되돌리기가 인자 하나다 |
| `HORIZON_DAYS` | `(0,1,3,5)` | `thesis.py` `HORIZON_DAYS`·`NARRATED_HORIZON_DAYS`, `ops.py` `THESIS_HORIZONS`, DB CHECK — **네 곳** | LLM 호출 비용 | 비용이 문제면 **해설만** T+5 하나로 줄인다. 채점은 SQL이라 공짜다. 네 곳을 같은 커밋에서 만진다 |
| `past_theses` 툴 | 켜짐 | `thesis.py` `ThesisToolbox._build_tools` | 도입 전후 지평별 Brier 추이 | **효과가 관측되지 않으면 툴을 뺀다**([5-followup.md](5-followup.md) 5절). 분기 판단 |
| 툴 개수 | 11 | 같은 곳 | **어떤 툴을 실제로 부르는지**와 `tool_rounds` 분포 | 한 번도 안 불리는 툴은 뺀다(문맥만 먹는다). 반대로 상한에 붙어 있으면 왕복을 늘린다. **서브 에이전트로 나누는 것은 여기서 판단한다** — 아래 참고 |
| `verdict` 값 셋 | `supported`/`contradicted`/`unresolved` | `analysis.py` + CHECK | `contradicted` 비율 | 60% 위가 유지되면 "반박"과 "다른 원인 지목"을 가를지 본다. **지금은 안 가른다** |
| `MAX_TOOL_ROUNDS` / `MAX_TOOL_CALLS` / `MAX_TOOL_RESULT_CHARS` | 4 / 12 / 24,000 | `thesis.py` | 쿼리 B의 분포 | 상한에 붙어 있으면 올린다. **값이 인자 모델(`RecentDocumentsArgs` 등)의 `Field(description=...)`에 f-string으로 실려 프롬프트가 자동으로 따라간다** |
| `PROMPT_VERSION` / `NARRATIVE_PROMPT_VERSION` | `"1"` / `"1"` | `thesis.py` | — | 프롬프트를 고치면 올린다. 올린 뒤 28일은 ops 창이 두 판에 걸친다 |
| `THESIS_WINDOW_DAYS` | 28 | `ops.py` | — | 판을 올린 직후엔 짧게 줄여 새 판만 본다 |
| `SCHEDULE` | 08:35 / 20:30 KST | `market_thesis_analysis.py` | 쿼리 C + readiness 재시도 | 재시도가 잦으면 늦춘다. 08:35는 문서 평가(매시 25분) 뒤, 20:30은 확정 종가(18:10) 뒤라는 제약이 있다 |
| `ASSESSMENT_LAG` | 20분 | 같은 파일 | 같은 것 | 평가가 정상인데 guard가 막으면 늘린다 |
| `thesis_model()` | `grok-4.6` | `llm.py` | 분기 Brier | 교체는 `PROMPT_VERSION`과 **함께** 올린다(1절 넷째) |
| `THESIS_TIMEOUT_SECONDS` | 900 | `llm.py` | 타임아웃 실패 건수 | 2026-08-21 첫 실행이 300초에서 죽어 900으로 올렸다(노트북과 같은 값). 또 걸리면 툴 상한(`MAX_TOOL_ROUNDS`)을 먼저 의심한다 — 왕복이 늘수록 한 요청이 길어진다. 문서 태깅의 `REQUEST_TIMEOUT_SECONDS`(300)는 따로다 |
| `SLACK_EVIDENCE_LIMIT` | 3 | `thesis.py` | 사람 눈 | 줄이 길어 안 읽히면 줄인다 |

---

## 4. 판단 캘린더

배포일이 0일이다. 시작 기준은 [3-dag-slack.md](3-dag-slack.md) 8절.

### 매일 — 자동

ops 브리핑의 **추론 적체** 한 줄. **여기서 즉시 대응하는 것은 이것 하나다.** 목표 영업일이
지났는데도 안 된 것만 세므로 0이 정상이다. 나머지 숫자는 눈으로 지나간다.

### +4주 — 운영 지표. 이걸로 손잡이를 당긴다

2절의 손으로 읽는 넷 + `flat` 분포. 대상 손잡이:

- `FLAT_THRESHOLD_PCT` — 지평별 `flat` 비율
- `MAX_TOOL_*` — 쿼리 B
- `SCHEDULE` / `ASSESSMENT_LAG` — 쿼리 C
- `INDEX_SUBJECTS` — 쿼리 A로 커버리지를 확인한 뒤 표본을 늘릴지
- **4단계 Neo4j 유지 여부** — [4-graph.md](4-graph.md)가 이 시점을 지목한다

### 분기 — 예측 품질. 여기서 처음 결론 낸다

4주로는 결론이 안 난다(subject당 표본 20개 안팎, 독립 사건은 그보다 훨씬 적다).
누적만 하다 분기에 본다.

- Brier 추이 vs 균등확률 0.667
- `informed` / `blind` 재검증 — 노트북 재실행
- `past_theses` 효과 — 도입 전후 지평별 Brier
- `thesis_model` 교체 검토

### 조건 발동 — 달력과 무관

| 신호 | 뜻 | 손잡이 |
| --- | --- | --- |
| 한 지평만 `flat` < 5% 또는 > 60% | 그 지평의 임계가 틀렸다 | `FLAT_THRESHOLD_PCT[해당 지평]` |
| `unresolved` < 20% | 모델이 억지로 판정 중 | 해설 프롬프트 + `NARRATIVE_PROMPT_VERSION` |
| `contradicted` > 60% 유지 | "반박"과 "다른 원인 지목"이 뭉개짐 | `verdict` 4번째 값 검토 |
| Slack 블록 45 근접 | 한도 50 임박 | 메시지 분할([3-dag-slack.md](3-dag-slack.md) 4절) |
| 장전 이유 문장이 과거 해설을 그대로 베낌 | `past_theses`가 서사를 재생산(사후확신 순환) | 툴 제거([5-followup.md](5-followup.md) 5절) |

마지막 줄은 **사람이 눈으로 본다.** 자동 탐지를 만들지 않는다 — 베낀 것과 같은 사실을
다시 짚은 것을 기계가 못 가른다.

---

## 5. 다음에 붙일 것

기능 설명이 아니라 **선행 조건과 발동 시점**이다. 상세는 각 문서에 있다.

- **4단계 Neo4j** — prod 인스턴스가 선행 조건이고 이 저장소 밖 작업이다. 외부 리뷰 2회
  모두 보류를 권했고 사용자 결정으로 넣었다. **+4주에 유지 여부를 다시 본다**
  ([4-graph.md](4-graph.md)).
- **6단계 애널리스트** — 첫 작업이 코드가 아니라 **KIS OpenAPI가 투자의견·목표주가를
  주는지 확인하는 spike**다. 저장소에 관련 코드가 0건이라 출처부터 미확인이다.
- **새 툴** — 2026-08-21에 일곱을 열어 11개가 됐다([2-agent.md](2-agent.md)).
  남은 후보는 `earnings_fact`(지금 6행뿐)와 증권사 리서치 리포트(소스 자체가 없다,
  [5-followup.md](5-followup.md) 11절). **금리는 퍼센트 변화가 아니라 bp 차이로 준다** —
  `BASIS_POINT_KINDS`와 `BASIS_POINT_INDICATOR_KINDS`가 그 규칙을 안다. 웹 검색은 출처를
  통제할 수 없어 별도 결정 전까지 안 넣는다.
- **툴 그룹별 서브 에이전트** — 툴이 11개가 되면서 나온 이야기다. **지금은 만들지 않는다.**
  단일 에이전트가 11개를 못 다룬다는 관측이 아직 없고, 서브 에이전트는 LLM 호출 수를
  곱한다(이미 `THESIS_TIMEOUT_SECONDS`를 900으로 올린 참이다). 되돌리기 비용도 반대다 —
  툴은 목록에서 빼면 끝이지만 supervisor 구조는 그래프를 다시 짜야 한다.
  **발동 조건**: 모델이 부르는 툴이 특정 몇 개에 고정되고 나머지가 문맥만 먹거나,
  `tool_rounds`가 상한(4)에 붙어 있는데 답변이 얕을 때. 그때의 순서는
  ① 단일 에이전트 + 툴 그룹 → ② 서브그래프를 툴로 감싸기 → ③ 완전한 multi-agent이고,
  ②에서 멈추는 경우가 대부분이다.
- **근거 유효율 계측** — 2절 참고. 4주 검증에서 필요가 보이면.

---

## 6. 이 문서를 갱신하는 규칙

**규칙이 표보다 먼저다.** 3절 표는 읽는 순간 낡기 시작하고, 이 규칙이 그것을 버티는 유일한
장치다.

**손잡이를 당기면 3절의 값과 아래 기록을 같은 커밋에서 고친다.** 안 그러면 석 달 뒤에 왜
0.5인지 아무도 모른다. 한 줄에 담을 것은 날짜 / 손잡이 / 이전→이후 / **본 숫자** / 근거다.
본 숫자가 없는 행은 기록이 아니라 기억이다.

| 날짜 | 손잡이 | 변경 | 본 숫자 | 근거 |
| --- | --- | --- | --- | --- |
| 2026-08-21 | `NarrativeVariant` 기본 | (없음) → `INFORMED` | 독립 사건 2, 툴 호출·레지스트리 동일, 판정만 갈림 | [5-followup.md](5-followup.md) 12절 |
| 2026-08-21 | 채점·판정의 Slack 위치 | 시장 메시지 → ops 브리핑 | — (읽는 사람이 다르다는 판단) | [3-dag-slack.md](3-dag-slack.md) 4절 |
| 2026-08-21 | 프롬프트의 기준 시각 표기 | UTC isoformat → `2026-08-21 08:35 KST` | 장전 as_of_at이 UTC로 **전날** 23:35라 "오늘 장이 열리기 전"과 날짜가 어긋났다 | `thesis.kst_label`. 판을 안 올렸다 — 그때 `thesis`가 0행이라 갈릴 판이 없었다 |
| 2026-08-21 | `THESIS_TIMEOUT_SECONDS` | (`REQUEST_TIMEOUT_SECONDS` 300 공용) → 900 전용 | 운영 첫 실행 `RetryableLlmError: Request timed out` 1건 | 표본 1건이다. 다음 실행들에서 안 걸리는지 확인이 남았다 |
| 2026-08-21 | 툴 정의·실행 방식 | 손으로 쓴 `TOOL_SCHEMAS` dict → `StructuredTool` + `ToolNode` | — (동작 동일. 회귀 테스트로 `handle_tool_errors` 기본값이 DB 오류를 삼키는 것을 확인) | 저장소 규칙과 어긋나 있던 것을 맞췄다 |
| 2026-08-21 | 툴 개수 | 4 → 11 | 국채 349행·수급 490행·시장폭 748행이 모델에게 안 보이고 있었다 | 운영 DB 실행으로 결함 둘을 잡았다(공매도 당일 0행, 국내 지수 일봉 부재) |

---

## 쓰지 않는 것

- **자동 리포트·대시보드.** ops 브리핑이 매일 낸다. 이 문서는 그 숫자를 **해석하는 규칙**이지
  새 관측 층이 아니다.
- **손잡이별 목표 수치.** "Brier 0.55 달성" 같은 것. 표본이 그 결론을 못 받친다.
- **자동 손잡이 조정.** 값을 고치는 것은 사람이다([5-followup.md](5-followup.md) 10절의
  "`prompt_version` 자동 승급을 만들지 않는다"와 같은 판단).
