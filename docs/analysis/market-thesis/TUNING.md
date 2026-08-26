# 고도화 — 무엇을 재고, 언제 보고, 어느 손잡이를 당기나

- 대상: 시장 추론 전 단계 (`market_thesis_forecast`·`market_thesis_review` DAG와 `modules/thesis*.py`)
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

과거 추론 프리페치도 같은 모양이다 — 효과가 없으면 `PREFETCHED_PAST_THESES = 0`으로 되돌아간다
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

**B. `tool_rounds` 분포** — 값이 `MAX_TOOL_ROUNDS`(3)에 붙어 있으면 상한이 조사를 자르고
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
`thesis_generation.py`가 하지만 **버린 건수를 로그로만 남기고 저장하지 않는다.** 남은 건수
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
| `FLAT_THRESHOLD_PCT` | `{0:0.3, 1:0.3, 3:0.5, 5:0.7}` | `thesis_domain.py` | 지평별 **그리고 슬롯별** `flat` 비율 | 한 지평만 5% 아래면 그 값을 **올리고** 60% 위면 **낮춘다**(2026-08-25 정정 — 반대로 적혀 있었다. 임계를 낮추면 `flat`이 더 희귀해진다). 고치면 `FLAT_BASE_RATE_PCT`도 다시 잰다. **실측이 아니라 `0.3 × sqrt(N)` 반올림이다** — 조정 조건은 코드 주석이 원본. **2026-08-26부터 T+0 창이 슬롯마다 다르다**(390·295·175·55·30분). 6세션 예비 실측에서 창이 짧을수록 `flat`이 늘었다(코스피 0%→83%). **표본이 모자라 값은 안 고쳤다** — 근거·쿼리·발동 조건은 [9-intraday.md](9-intraday.md) 11절 |
| `INDEX_SUBJECTS` + `instrument.is_watched` | KOSPI·코스닥 + watched 종목 | `thesis_store.py` / `instrument` 테이블 | 표본 수 | **표본을 늘리는 가장 싼 손잡이다.** LLM 호출 수는 그대로고 날짜당 건수만 는다([5-followup.md](5-followup.md) 12절). 단 독립 사건 수는 안 는다 — 1절 |
| `NarrativeVariant` 기본 | `INFORMED` | `thesis_outcomes.py`, `FollowupNarrator.__init__` | 분기 Brier + `verdict` 분포 | 노트북 재실행으로 재검증. `BLIND`가 남아 있어 되돌리기가 인자 하나다 |
| `HORIZON_DAYS` | `(0,1,3,5)` | `thesis_domain.py` `HORIZON_DAYS`·`NARRATED_HORIZON_DAYS`, `ops.py` `THESIS_HORIZONS`, DB CHECK — **네 곳** | LLM 호출 비용 | 비용이 문제면 **해설만** T+5 하나로 줄인다. 채점은 SQL이라 공짜다. 네 곳을 같은 커밋에서 만진다 |
| `PREFETCHED_PAST_THESES` | 2 | `thesis_domain.py` | 도입 전후 지평별 Brier 추이 | 장전·장중 프롬프트에 미리 싣는 과거 추론 수. **슬롯마다다** — 슬롯이 여섯이라 최대 12행이고 프롬프트 길이도 그만큼이다. 5에서 2로 내린 것이 2026-08-26 장중 슬롯 추가 때다(그대로 두면 30행). **효과가 관측되지 않으면 0으로 끈다**([5-followup.md](5-followup.md) 5절) — 절은 `(없음)`이 되고 `thesis_precedent` 엣지도 안 남는다. `past_theses` 툴은 그대로다. 분기 판단 |
| 툴 개수 | 14 | 같은 곳 | **어떤 툴을 실제로 부르는지**(LangSmith `run_name = build_theses`. DB로는 못 읽는다 — [10-multi-agent.md](10-multi-agent.md) 2절)와 `tool_rounds` 분포 | 한 번도 안 불리는 툴은 뺀다(문맥만 먹는다). 반대로 상한에 붙어 있으면 왕복을 늘린다. **툴을 더 열기 전에 `MAX_TOOL_CALLS`부터 본다** — 현실적 조사가 26호출인데 상한이 20이다(5절 실측). 서브 에이전트로 나누는 판단은 [10-multi-agent.md](10-multi-agent.md)가 갖는다 |
| `verdict` 값 셋 | `supported`/`contradicted`/`unresolved` | `apps/models/analysis/thesis.py` + CHECK | `contradicted` 비율 | 60% 위가 유지되면 "반박"과 "다른 원인 지목"을 가를지 본다. **지금은 안 가른다** |
| `MAX_TOOL_ROUNDS` / `MAX_TOOL_CALLS` / `MAX_TOOL_RESULT_CHARS` | 3 / 20 / 100,000 | `thesis_domain.py` | 쿼리 B의 분포와 Airflow 로그의 `budget exhausted` 경고 | 상한에 붙어 있으면 올린다. **값이 인자 모델(`RecentDocumentsArgs` 등)의 `Field(description=...)`에 f-string으로 실려 프롬프트가 자동으로 따라간다.** 문자 상한은 폭주만 받는 안전망이라 **한 바퀴 실측치(5절)보다 커야 한다** |
| `PROMPT_VERSION` / `NARRATIVE_PROMPT_VERSION` | `"6"` / `"2"` | `thesis_domain.py` / `thesis_outcomes.py` | — | 프롬프트를 고치면 올린다. 올린 뒤 28일은 ops 창이 두 판에 걸친다. `"3"`은 기술적 보조지표(2026-08-24), `"4"`는 과거 추론 절에 장후 리뷰가 실린 판(2026-08-25), `"5"`는 `## 확률` 절이 `prob_flat`의 뜻과 base rate를 정의한 판(2026-08-25)이다 |
| `RULE_VERSION` | `"1"` | `technical.py` | `kind`·`direction`별 지평 적중률(기술지표 문서 12.6절) | 신호 검출 규칙을 고치면 올린다. `PROMPT_VERSION`과 같은 역할이고 축이 다르다 — 저쪽은 "모델이 잘 읽었나", 이쪽은 "신호가 좋았나"다 |
| `RSI_OVERBOUGHT` / `RSI_OVERSOLD` | 70 / 30 | `technical.py` | 같은 것 | 검출과 프롬프트가 **같은 상수**를 본다. `rsi_reversal` 건수가 너무 적거나 많으면 여기서 당긴다 |
| `SIGNAL_STATE_DAYS` / `MAX_STATE_SIGNALS` | 30일 / 3건 | `thesis_common.py` | 프롬프트 길이 | 관측 상태에 싣는 신호의 창과 개수. 툴(`SIGNAL_HISTORY_DAYS`, 90일)보다 짧다 |
| `THESIS_WINDOW_DAYS` | 28 | `ops.py` | — | 판을 올린 직후엔 짧게 줄여 새 판만 본다 |
| 스케줄 | 08:35 / 20:30 KST | `market_thesis_forecast.py` / `market_thesis_review.py` | 쿼리 C + readiness 재시도 | 재시도가 잦으면 늦춘다. 08:35는 문서 평가(매시 25분) 뒤, 20:30은 확정 종가(18:10) 뒤라는 제약이 있다 |
| 장중 스케줄 | 10:35 / 12:35 / 14:35 / 15:00 KST | `thesis_state.INTRADAY_SLOT_TIMES` + `market_thesis_intraday.SCHEDULE` — **두 곳** | 같은 것 + 슬롯별 발행률 | 앞의 셋은 문서 평가(:25) 뒤라 :35다. 15:00은 "마감 30분 전"이 목적이라 그 제약을 안 받는다. **두 곳을 같은 커밋에서 만진다** — 테스트가 대조하고, 어긋나면 `resolve_slot`이 실행을 죽인다. 슬롯 라벨은 표에서 만들어져 따라온다 |
| `BAR_STALENESS` | 15분 | `thesis_intraday.py` | `ThesisNotReady`의 "older than" 건수 | 정상인 날 guard가 막으면 늘린다. 지수는 `*/5`, 종목은 WebSocket이라 정상이면 5분 안이다. 반대로 이 값이 크면 오래된 가격을 "지금"으로 읽는다 |
| 장중 `retries` / `execution_timeout` | 1 × 5분 / 15분 | `market_thesis_intraday.py` | `AirflowTaskTimeout` 건수와 슬롯 간 밀림 | 최악 40분에 묶어 앞 슬롯이 다음 슬롯을 막지 않게 한 값이다. 늘리려면 **슬롯 간격 2시간 안에** 들어와야 한다. 장전·장후(3 × 10분 / 30분)와 일부러 다르다 |
| `ASSESSMENT_LAG` | 20분 | 같은 파일 | 같은 것 | 평가가 정상인데 guard가 막으면 늘린다 |
| `thesis_model()` | `grok-4.6` | `llm.py` | 분기 Brier | 교체는 `PROMPT_VERSION`과 **함께** 올린다(1절 넷째) |
| `THESIS_TIMEOUT_SECONDS` | 1800 | `llm.py` | 타임아웃 실패 건수 | 2026-08-21 첫 실행이 300초에서 죽어 900으로, 툴이 11개로 늘면서 2026-08-22에 1800으로 올렸다. **1800은 관측이 아니라 예방이다** — 900에서 죽은 실행은 아직 없다. 다음 실행들의 실제 소요를 보고 되돌릴 여지가 있다. 또 걸리면 툴 상한(`MAX_TOOL_ROUNDS`)을 먼저 의심한다 — 왕복이 늘수록 한 요청이 길어진다. 문서 태깅의 `REQUEST_TIMEOUT_SECONDS`(300)는 따로다 |
| `BUILD_TIMEOUT` | 30분 | `thesis_common.py` | `build_thesis`의 `AirflowTaskTimeout` 건수와 성공 실행의 소요 분포 | 요청 타임아웃의 바깥 울타리. 한 빌드는 모델을 최대 왕복 3 + 답변 + 교정 = 6번 부른다. 장전이 09:00 개장 전에 닿아야 해서 이 값이고, 걸리면 `MAX_TOOL_ROUNDS`를 먼저 의심한다. 재시도 셋은 그대로라 최악 4회 × (30 + 10)분이다 |
| `SLACK_EVIDENCE_LIMIT` | 3 | `thesis_render.py` | 사람 눈 | 줄이 길어 안 읽히면 줄인다. 조회 상한(`EVIDENCE_FETCH_LIMIT`, 12)은 따로다 — 결론 방향으로 거른 뒤에도 이만큼 남아야 한다 |
| `FLAT_BASE_RATE_PCT` | `{KOSPI:6, KOSDAQ:11, stock:6}` | `thesis_domain.py` | 프롬프트에 실리는 `flat` 기준선 | **실측이다**(2026-08-25, `index_daily` 132거래일·`stock_investor_trade_daily` 123거래일). `FLAT_THRESHOLD_PCT[0]`을 고치면 같이 다시 잰다 — 임계가 이 빈도의 정의다 |
| `VERDICT_TIE_GAP` | `0.05` | `thesis_render.py` | 결론이 둘 이상 나오는 비율 | 최고 확률에서 이만큼 안에 붙은 방향을 Slack에 함께 보인다. **실측이 아니라 시작값이다** — 매번 둘이 나오면 좁히고 한 번도 안 나오면 넓힌다. `PROMPT_VERSION` 5가 확률을 벌려 놓으므로 그 뒤 분포로 판단한다 |
| `MEET_BAND_PCT` | `5.0` | `expectation.py` | `stock_event_outcome.verdict` 분포 | 기대 대비 발표를 `meet`로 볼 폭(퍼센트). **실측이 아니라 시작값이다** — `meet`가 사실상 없거나 대부분이면 그 폭이 틀린 것이다. `FLAT_THRESHOLD_PCT`와 같은 성격이고 판단 시점도 같다(+4주) |
| 추출 `PROMPT_VERSION` | `"1"` | `expectation.py` | 버림 로그의 사유 분포 | 프롬프트를 고치면 올린다. **올리면 이미 뽑은 문서가 전부 재추출 대상이 된다** — `document.prompt_version`과 같은 장치다. 버림 사유가 한 유형에 몰리면 프롬프트 예시를 보강할지 `StockEventType`을 늘릴지를 그 로그가 정한다 |
| 추출 `DEFAULT_BATCH_SIZE` | 50 | `expectation.py` | 대상 문서 백로그 | 대상이 종목 태그 문서뿐이라 지금 물량에서는 남는다. watched가 크게 늘면 이 값과 대상 조건(`value_score` 하한 추가)이 손잡이다 |

---

## 4. 판단 캘린더

배포일이 0일이다. 시작 기준은 [3-dag-slack.md](3-dag-slack.md) 8절.

### 매일 — 자동

ops 브리핑의 **추론 적체** 한 줄. **여기서 즉시 대응하는 것은 이것 하나다.** 목표 영업일이
지났는데도 안 된 것만 세므로 0이 정상이다. 나머지 숫자는 눈으로 지나간다.

### +4주 — 운영 지표. 이걸로 손잡이를 당긴다

2절의 손으로 읽는 넷 + `flat` 분포. 대상 손잡이:

- `FLAT_THRESHOLD_PCT` — 지평별·슬롯별 `flat` 비율
- `MAX_TOOL_*` — 쿼리 B
- `SCHEDULE` / `ASSESSMENT_LAG` / 장중 스케줄 / `BAR_STALENESS` — 쿼리 C
- **슬롯별 T+0 Brier** — 여기서 처음 "어느 시간대 예측이 나은가"가 보인다. 15:00이
  08:35보다 나은 것은 당연하고(남은 시간이 짧다), 볼 것은 격차가 시간에 비례하는지
  아니면 특정 슬롯이 유독 나쁜지다
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
| 장중 한 슬롯만 `flat` > 60% | T+0 창 길이가 슬롯마다 다른 것이 임계에 안 반영됐다 | 슬롯 축 임계 도입 검토(9-intraday.md 10절) |
| 장중 슬롯이 readiness로 자주 죽는다 | 봉 또는 문서 평가가 그 시각에 안 온다 | 장중 스케줄 또는 `BAR_STALENESS` |
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
- **8단계 이벤트 기대치** — 구현됐다(2026-08-24, [8-expectation.md](8-expectation.md)).
  운영 관측 셋: ① 추출 버림 로그의 사유 분포 — 한 유형에 몰리면 프롬프트 예시나
  `StockEventType`을 늘린다. ② `verdict` 분포 — `meet`가 사실상 없거나 대부분이면
  `MEET_BAND_PCT`를 조정한다(3절). ③ 실제값 주장 불일치로 판정이 보류되는 빈도 —
  잦은 이벤트 유형은 `metric` 정의를 쪼개는 신호다. 컨센서스 수집기(4단계)는 출처
  실측부터이고, 그것이 붙으면 대표 기대치가 중앙값에서 컨센서스로 바뀐다.
- **6단계 애널리스트** — 구현됐다(2026-08-22, [6-analyst.md](6-analyst.md)). 남은 것은
  운영 관측이다: ① 08:05~08:35 사이에 올라오는 모닝 리포트는 당일 장전에 안 든다 — 수집
  주기를 앞당길지는 실측 뒤(평가 DAG도 같이 당겨야 하고 LLM 비용이 따라온다). ②
  `recent_documents`에서 리포트의 `tickers`가 비면 수집 단계 선태깅을 연다(6-analyst.md 3.4절).
  ③ 첫 실행 백로그(6 × 30건 평가)가 뉴스 평가를 밀면 `document_assessment_hourly`를
  `batch_size`를 키워 수동 트리거한다.
- **새 툴** — 2026-08-21에 일곱을 열어 11개가 됐고, 2026-08-22에 `us_market_close`와
  `analyst_opinions`가 붙어 13개, 2026-08-24에 `event_surprises`가 붙어 14개다
  ([2-agent.md](2-agent.md)). **툴 수가 tool call 상한(12)을 넘어 있었다.** 모델이 툴마다
  한 번씩도 못 부른다는 뜻이라 2026-08-25에 `MAX_TOOL_CALLS`를 20으로 올렸고,
  2026-08-26에 32로 다시 올렸다 — 장중 슬롯이 21번째 호출에서 걸렸는데 그때 누적이
  54,555자로 문자 예산의 절반뿐이었다. 아래 실측(현실적 조사 26호출)이 그 근거다.
  남은 후보는 `earnings_fact`(지금 6행뿐)인데, 실적 숫자는 `event_surprises`가 기대와
  함께 주므로 그 툴이 실제로 불리는지를 먼저 본다.
  **툴을 늘리면 `MAX_TOOL_CALLS`뿐 아니라 `MAX_TOOL_RESULT_CHARS`도 같이 본다** — 아래
  실측 기준선이 그 판단의 근거다.

  ### 한 바퀴 실측 (2026-08-26)

  운영 DB에 읽기 전용으로 붙어 장전 `as_of_at`(`2026-08-25T23:35:00Z`), 대상
  KOSPI·KOSDAQ·000660·005930으로 툴을 하나씩 불러 결과 문자 수를 잰 값이다.
  `tests/modules/test_thesis_pipeline.py`의 `MEASURED_FULL_SWEEP_CHARS`가 이 숫자를 들고
  있다. **툴을 늘리거나 툴 SQL을 고치면 다시 잰다.**

  | 툴 | 자 |
  | --- | --- |
  | `recent_documents` | 12,793 |
  | `macro_indicators(government_bond)` | 10,987 |
  | `macro_changes` | 5,410 |
  | `stock_investor_flows` | 3,876 (종목당) |
  | `us_market_close` | 3,146 |
  | `daily_history` | 2,807~3,198 (심볼당) |
  | `market_funds` | 2,336 |
  | `past_theses` | 1,370~1,586 (대상당) |
  | `event_surprises` | 264~1,298 (종목당) |
  | `macro_indicators(activity / price_index / money_market)` | 933 / 646 / 329 |
  | `short_and_credit` | 744 |
  | `recent_disclosures` | 312 |
  | `analyst_opinions` | 40 (종목당, 그날 데이터 없음) |
  | `market_investor_flows` / `market_breadth` | 2 / 2 |

  - **툴 14개를 한 번씩 = 44,340자.** 장전 실질 12개도 44,336자다.
  - 대상별 툴까지 부르는 현실적 조사(26호출) = **64,694자**.
  - 예산을 먹는 자리는 `recent_documents`와 `macro_indicators(government_bond)` 둘이고
    합쳐 54%다. 상한이 다시 문제가 되면 이 둘의 기본 인자부터 본다.
  - `market_investor_flows`·`market_breadth`가 2자(`[]`)인 것은 결함이 아니다.
    `SNAPSHOT_LOOKBACK`이 12시간인데 장전 08:35에서 직전 세션 마감은 17시간 전이라
    **장전 슬롯에서는 구조적으로 빈다.** 장후 슬롯에서는 값이 있다.
  **금리는 퍼센트 변화가 아니라 bp 차이로 준다** —
  `BASIS_POINT_KINDS`와 `BASIS_POINT_INDICATOR_KINDS`가 그 규칙을 안다. 웹 검색은 출처를
  통제할 수 없어 별도 결정 전까지 안 넣는다.
- **툴 그룹별 서브 에이전트** — **지금은 만들지 않는다.** 판정과 사다리(칸 0~5), 발동
  조건을 읽는 법, 툴 묶음 실측은 전부 [10-multi-agent.md](10-multi-agent.md)로 옮겼다
  (2026-08-26). **여기 요약을 두지 않는다** — 같은 판단을 두 곳에 두면 한쪽만 낡는다
  (6절 규칙). 이 항목이 다시 열리는 신호는 그 문서 7절이 갖는다.
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
| 2026-08-21 | DAG 구조 | `market_thesis_analysis` 하나 → `market_thesis_forecast`·`market_thesis_review` 둘 | — (슬롯이 `logical_date`의 시각에서 나와 수동 실행이 벽시계로 떨어졌다) | 모드로 갈리던 함수도 `thesis_common`·`thesis_forecast`·`thesis_review`로 나눴다 |
| 2026-08-21 | 툴 개수 | 4 → 11 | 국채 349행·수급 490행·시장폭 748행이 모델에게 안 보이고 있었다 | 운영 DB 실행으로 결함 둘을 잡았다(공매도 당일 0행, 국내 지수 일봉 부재) |
| 2026-08-22 | `THESIS_TIMEOUT_SECONDS` | 900 → 1800 | **없다.** 900에서 죽은 실행은 아직 없고, 툴이 11개로 늘어 왕복이 길어질 것을 보고 미리 올렸다 | 이 표의 규칙("본 숫자가 없는 행은 기억이다")을 어기는 행이라 그 사실을 적어 둔다. 실제 소요 분포를 보고 되돌릴 후보다 |
| 2026-08-23 | 해설 호출 단위 | 지평마다 하나(슬롯 `PRE_OPEN` 고정) → (지평, 슬롯)마다 하나 | — (코드 읽기로 잡았다. 응답을 `subject_code`로 대상에 되돌리는데 같은 날 장전·장후가 같은 대상이라 장후 추론은 해설을 한 번도 못 받았다) | 해설 LLM 호출이 날마다 최대 3 → 6으로 는다. 장후 해설이 실제로 쌓이는지는 `SELECT t.run_slot, count(o.narrative) FROM thesis t JOIN thesis_outcome o ON o.thesis_id = t.id GROUP BY 1`로 본다 — `post_close`가 0이면 안 풀린 것이다 |
| 2026-08-24 | `PROMPT_VERSION` + 기술적 보조지표 | `"2"` → `"3"`. 관측 상태에 `technical` 블록, `daily_history`에 `technical_snapshot`·`recent_signals` | **없다.** 지표가 추론에 도움이 되는지는 아직 재지 않았고, 이 판을 올리는 것이 그것을 재기 위한 조건이다 | 비교는 배포 4주 뒤 `prompt_version` 2 대 3의 지평별 Brier와 [../market-technical-indicators.md](../market-technical-indicators.md) 14.4절 세 SQL이다. 개선이 없으면 관측 상태의 push를 빼고 툴만 남긴다 |
| 2026-08-24 | `RULE_VERSION` | (없음) → `"1"` | — (첫 판) | 신호 셋(`sma_cross`·`macd_cross`·`rsi_reversal`)의 지평 T+1·5·20 적중률을 4주 뒤 기술지표 문서 12.6절 SQL로 본다. 그 결과가 신호를 늘리거나 줄이는 유일한 근거다 |
| 2026-08-23 | `MAX_TOOL_ROUNDS` / `build_thesis` `execution_timeout` | 4 → 3 / (없음) → 30분 | **없다.** 요청 타임아웃 1800초 × 호출 최대 7번이면 한 시도가 3시간 넘게 갈 수 있는데 태스크 울타리가 없었다 | 왕복을 하나 줄여 호출 최대 6번으로, 태스크에 30분 울타리를 둔다. `MAX_TOOL_CALLS` 12는 그대로라 한 왕복에 여러 툴을 묶어 부르면 보는 양은 같다. 재시도 셋 유지 |
| 2026-08-25 | `MAX_TOOL_CALLS` / `MAX_TOOL_RESULT_CHARS` | 12 → 20 / 24,000 → 40,000 | **있다.** 운영에서 `ToolLimitExceeded`(상한 초과)가 관측됐다. 툴은 14개인데 호출 상한이 12라 모델이 툴마다 한 번씩도 못 불렀다 | `MAX_TOOL_ROUNDS`는 3 그대로다 — 왕복은 모델 호출 수라 `BUILD_TIMEOUT`(30분)과 장전 09:00 마감에 직접 걸리고, 2026-08-23에 그 이유로 내린 값이다. 호출만 늘리면 한 왕복에 여러 툴을 묶어 부르는 쪽으로 는다. 다음 실행에서 상한 초과가 사라지는지와 `build_thesis` 소요 분포가 확인 대상이다 |
| 2026-08-26 | `MAX_TOOL_RESULT_CHARS` + `_charge()` 경고 로그 | 40,000 → 100,000 / (없음) → `logger.warning` 두 줄 | **있다.** 운영 `market_thesis_forecast`(2026-08-25T23:35:36Z, `run_slot: pre_open`, 합 201,338토큰 $0.370)에서 다시 `ToolLimitExceeded`가 관측됐다. 같은 `as_of_at`으로 운영 DB에 읽기 전용으로 붙어 재 보니 **툴 14개를 한 번씩 부르면 44,340자**로 상한 40,000을 이미 넘었다 — 모델이 한 바퀴를 끝낼 수 없었고, 어느 툴이 잘리는지가 중요도가 아니라 호출 순서 운이었다. 초기 프롬프트 33,655자(16,619토큰)는 이 예산에 안 들어가므로 "컨텍스트 보호"라는 명분 자체가 일관되지 않았다 | `MAX_TOOL_ROUNDS`는 3 그대로다 — 상한 3에 붙은 것은 운영 8빌드 중 이 한 건뿐이라 신호가 약하고, 실제 소요 시간을 DB에서 복원할 수 없다(`thesis.created_at`은 `now()`라 트랜잭션 시작 시각이다). 문자 상한이 풀리면 한 왕복에 더 담을 수 있어 왕복 압박이 저절로 줄 수도 있다. **상한 초과는 `ToolMessage`가 되어 태스크가 성공으로 끝나고 `thesis` 행에도 안 남아** 지금까지 조용했다 — `_charge()`에 경고를 달아 다음부터는 Airflow 로그로 답한다. 확인 대상은 ① `tool result budget exhausted`가 사라지는가 ② `tool call budget exhausted`가 뜨는가(뜨면 다음 손잡이는 `MAX_TOOL_CALLS`) ③ `tool_rounds`가 계속 3에 붙는가 ④ distinct `evidence_ref`가 최근 11건 수준에서 느는가 |
| 2026-08-25 | `PROMPT_VERSION` + Slack 표시 | `"4"` → `"5"`. 프롬프트에 `## 확률` 절, Slack은 세 확률·세 이유 → 결론(붙어 있으면 여럿)과 그 방향 근거 | **있다.** 실현 `flat` 비율이 코스피 6.1%·코스닥 11.4%·005930 4.9%·000660 6.5%인데 모델은 30~36%를 줬다. `pre_open` 12건 전부 최고 확률이 0.32~0.44, 지평 0 Brier 평균 0.643/0.722(균등 baseline 0.667) | 프롬프트에서 "확신이 없으면 확률을 고르게 두면 된다"와 "횡보 이유에는 … '방향 정보 없음'"을 빼고 임계·base rate를 실었다. `FLAT_THRESHOLD_PCT`는 안 건드렸다 — outcome 8건으로는 임계를 못 정한다(+4주 그대로). 다음 실행의 `prob_flat`이 10% 아래로 내려오고 최고 확률이 0.5를 넘는지가 첫 확인이다 |

---

## 쓰지 않는 것

- **자동 리포트·대시보드.** ops 브리핑이 매일 낸다. 이 문서는 그 숫자를 **해석하는 규칙**이지
  새 관측 층이 아니다.
- **손잡이별 목표 수치.** "Brier 0.55 달성" 같은 것. 표본이 그 결론을 못 받친다.
- **자동 손잡이 조정.** 값을 고치는 것은 사람이다([5-followup.md](5-followup.md) 10절의
  "`prompt_version` 자동 승급을 만들지 않는다"와 같은 판단).
