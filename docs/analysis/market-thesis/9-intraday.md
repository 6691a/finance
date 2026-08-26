# 9단계 — 장중 슬롯 넷: `market_thesis_intraday`

- 상위: [README.md](README.md)
- 날짜: 2026-08-26
- 상태: 구현 완료 (2026-08-26). 리비전은 운영 반영 전
- 의존: [1-storage.md](1-storage.md), [2-agent.md](2-agent.md), [3-dag-slack.md](3-dag-slack.md),
  [5-followup.md](5-followup.md)
- 산출물: `RunSlot`에 슬롯 넷(두 곳)과 수기 리비전, `airflow/modules/thesis_intraday.py`,
  `airflow/dags/market_thesis_intraday.py`, SQL 다섯, 채점·해설 슬롯 목록 파라미터화,
  `tests/dags/test_market_thesis_intraday.py`

## 0. 왜 — 아침 한 번으로는 장중을 못 따라간다

3단계까지 예측은 장전 08:35 하나였다. 09:00 개장 뒤에 나온 공시·기사·수급·가격 움직임이
그날 판단에 전혀 반영되지 않고, 다음 판단이 다음 날 아침이다. 데이터는 5분마다 쌓이는데
그것을 읽는 층이 하루 한 번만 도는 셈이다.

[README.md](README.md) 4절이 "**장중 추론** — 장전·장후 두 번이 이번 범위다"라고 명시적
out-of-scope로 적어 뒀던 항목이고, 이 단계가 그것을 연다.

**예측 슬롯이 하나에서 다섯이 된다.** 그리고 예측이 늘어난 만큼 채점·해설의 대상도 다섯으로
넓힌다 — 지금까지는 `pre_open`만 채점되고 `pre_open`·`post_close`만 해설이 붙었다.

## 1. 슬롯 넷

| 슬롯 | KST | 왜 그 시각인가 |
| --- | --- | --- |
| `intraday_morning` | 10:35 | `document_assessment_hourly`(매시 :25) 뒤라 09:05 수집분이 근거 후보에 든다 |
| `intraday_midday` | 12:35 | 같은 이유 |
| `intraday_afternoon` | 14:35 | 같은 이유 |
| `pre_close` | 15:00 | **예외.** 목적이 "마감 30분 전"이라 시각이 먼저 정해진다. 문서는 14:25 평가분까지 본다 |

`:35`는 장전이 08:35인 것과 같은 이유다. 정시(:00)에 돌면 직전 평가가 :25분 것이라 그
사이 55분의 기사가 통째로 빠진다.

**값은 시각이 아니라 뜻으로 짓는다.** 슬롯 시각은 [TUNING.md](TUNING.md) 3절이 당기라고
적어 둔 손잡이라, `intraday_1035` 같은 이름은 시각을 30분 옮기는 순간 거짓이 된다. 반대로
사람이 읽는 라벨(`SLOT_LABELS`)에는 시각이 들어간다 — 하루 다섯 건이 같은 Slack 채널에
쌓이므로 "언제 기준인가"가 값의 절반이다. 라벨은 `INTRADAY_SLOT_TIMES` 표에서 만들어
스케줄을 옮기면 따라온다.

시각의 원본은 `thesis_state.INTRADAY_SLOT_TIMES` 하나이고 DAG의 cron이 그것과 같아야 한다.
어긋나면 `resolve_slot`이 슬롯을 못 찾아 실행이 죽는다 — 조용히 다른 슬롯으로 떨어지는
것보다 낫고, 애초에 어긋나지 않게 `test_the_schedule_matches_the_slot_table`이 둘을 묶는다.

## 2. 기준가 — 전일 종가가 아니라 지금 가격

**장중 예측이 맞히는 것은 "지금 이 가격에서 마감까지"다.** 10:35 슬롯이 전일 종가 대비를
맞힌다면 그 시점에 이미 결정된 등락이 예측에 섞이고, 14:35 슬롯은 사실상 답을 아는 상태가
된다.

- 관측 상태의 `intraday[대상]`이 `price`(그 봉의 종가)·`return_pct`(전일 종가 대비 여기까지)·
  `bar_at`을 담는다. `price`가 예측의 기준가이고 `return_pct`는 맥락이다.
- 봉은 `as_of_at` **직전**의 최신 것을 고른다(`select_latest_before.sql` 둘). 둘 다 1분봉이지만
  봉 시각이 구간의 시작이라 10:35 시점의 10:35 봉은 아직 안 끝났고, 수집 주기가 다르다 —
  지수는 `kis_quote_intraday`가 5분마다 REST로 채우고 종목은 WebSocket이 실시간으로 쌓는다.
  고정 오프셋으로 집으면 한쪽이 늘 어긋난다. 2026-08-25 실측으로 10:35 슬롯이 보는 봉은
  지수·종목 모두 10:34였다.
- **하한은 당일 개장이다.** 없으면 수집이 통째로 죽은 날에도 어제 15:30 봉이 "지금 가격"으로
  실린다.
- `is_final`은 보지 않는다. 장중 봉은 WebSocket 잠정이 정상이고 `kis_equity_bar_reconcile`이
  매시 :05·:35에 REST 확정으로 갈아 끼운다. 확정을 기다리면 장중 추론이 영영 서지 않는다.
- 종목은 **KRX만**이다. NXT는 같은 종목의 별도 체결이라 섞으면 같은 시각에 값이 둘이 된다.

`ThesisRun.observed_state()`는 확정 종가와 15:30 마감 봉을 읽으므로 장중에 쓸 수 없다.
그래서 관측 상태를 `IntradayForecast`가 따로 만들고, `session`·`index`·`stock`은 비운다 —
채우면 모델이 오늘 종가로 읽는다.

## 3. readiness guard

세 판정이다. 앞의 둘이 장중만의 것이고 셋째는 장전과 글자 그대로 같다.

1. **봉 0건 → `ThesisNotReady`.** 대상 하나라도 당일 봉이 없으면 수집이 멈춘 것이다.
2. **봉이 `BAR_STALENESS`(15분)보다 오래됨 → `ThesisNotReady`.** 지연이다. 메시지를 나눠
   로그에서 0건과 갈린다.
3. **문서 평가가 `ASSESSMENT_LAG`(20분)을 못 따라옴 → `ThesisNotReady`.** "직전 1시간 0건"은
   최근 24시간에 문서가 하나라도 있어야 인정한다(장전 guard와 같은 이유).

**`require_settled_closes()`는 부르지 않는다.** `stock_investor_trade_daily`는 18:10에
들어오고 KIS가 15:40 전 당일 확정 조회를 `TIME LIMIT`으로 거절한다. 장중에 요구하면 이
DAG는 영영 돌지 않는다.

guard가 조회한 봉을 그대로 돌려주고 관측 상태가 그것을 쓴다. 다시 읽으면 그 사이 들어온
봉 때문에 guard가 본 것과 프롬프트에 실리는 것이 달라진다.

## 4. 오늘 앞 슬롯 되짚기 — 저장하지 않는다

다음 슬롯이 자기 아침·직전 판단을 보고 판단하는 것이 이 기능의 절반이다. 프롬프트의
`## 오늘 앞 슬롯` 절이 그 자리다.

```json
{"KOSPI": [{"run_slot": "pre_open", "prob_up": 0.62,
            "base_price": 3125.0, "current_price": 3100.0, "return_pct": -0.8}]}
```

기준가는 **그 슬롯이 채점될 때 쓰일 값과 같다** — `pre_open`은 전일 종가, 장중은 그 슬롯
`as_of_at` 직전 봉의 종가다. 여기서 다른 기준을 쓰면 프롬프트가 보여 준 성적과 밤에
매겨질 점수가 어긋난다.

### 왜 `thesis_outcome`에 안 쓰나

**장중에 정식 채점은 물리적으로 못 한다.** 확정 종가가 18:10이고 지수 마감 봉도 16:00
확정이라 T+0조차 장중에는 설 수 없다. 장중에 채점 가능한 유일한 목표는 뒤쪽 봉인데,
그것은 "KRX 영업일 수"와 단위가 다른 새 지평 축이라 따라오는 것이 셋이다.

- `thesis_outcome.horizon_days` CHECK(0,1,3,5) 확장과 마이그레이션
- 2시간 창용 `FLAT_THRESHOLD_PCT` — **실측이 없는 추측값**이다. 하루 임계 0.3%를 두 시간에
  쓰면 사실상 전부 `flat`이 된다
- `select_calibration.sql` 분리 — 단위가 다른 지평이 한 표에 섞이면 화면이 조용히 거짓말을 한다

그리고 **다음 슬롯이 실제로 필요한 것은 `Brier 0.41`이 아니라 "아침에 상승 62%라 했는데
지금 -0.4%" 한 줄이다.** 그건 봉만 있으면 프롬프트 조립 시점에 계산된다. 값어치는 그
한 줄에 다 있고 비용은 위 셋이라, 저장하지 않는 쪽을 골랐다(2026-08-26 사용자 결정).

슬롯별 Brier 집계가 실제로 필요해지면 그때 지평 축을 연다. 그 전까지는 T+0(당일 마감
기준)이 슬롯마다 따로 쌓이므로 **"어느 시간대 예측이 나은가"는 그 값으로도 읽을 수 있다.**

### `select_past_with_outcomes.sql`을 재사용하지 않는 이유

그 파일은 `thesis.run_date < 오늘`로 당일을 통째로 막는다. 그래야 장전 예측에 그날 저녁의
채점이 안 섞인다. 술어를 완화하면 장전 피드백 루프가 조용히 따라 바뀌므로 새 파일
(`thesis/select_same_day.sql`)을 만든다 — 저장소 규칙 "툴을 늘릴 때 조회 SQL은 새 파일로
만든다"와 같은 판단이다.

`same_day`는 `thesis_precedent` 엣지를 남기지 않는다. 그 표는 "어느 **과거 추론**을 보여
줬나"를 남기는 자리이고, 같은 날 앞 슬롯은 `run_date`로 이미 이어져 있다.

## 5. 채점과 해설 — 장후 1회 그대로, 대상만 넓힌다

`market_thesis_review`(20:30)의 태스크 둘이 그대로 하고 장중 DAG에는 채점·해설 태스크가
없다. 지평도 T+0·1·3·5 KRX 영업일 그대로다.

**해설을 장중에 돌리지 않는 이유**는 `verdict`가 묻는 것이 "그 이유가 이후 보도로
지지됐나"이기 때문이다. 두 시간이면 새 문서가 몇 건뿐이라 `unresolved`가 거의 확정이고,
[5-followup.md](5-followup.md)는 하루 창인 T+1에서도 `unresolved`가 기본값이어야 한다고
적어 뒀다. 호출만 배가 되고 얻는 것이 없다.

### 슬롯 목록을 리터럴에서 파라미터로

7단계가 새 슬롯이 루프에 **자동으로** 들어오지 않도록 SQL에 슬롯 목록을 리터럴로 박아
뒀다([7-nxt-review.md](7-nxt-review.md) 3절). 슬롯이 일곱이 되면서 그 리터럴이 네 파일에
흩어지므로 파라미터로 바꾼다. 원본은 `thesis_state`의 상수 둘이다.

| 파일 | 목록 |
| --- | --- |
| `thesis_outcome/select_pending_grades.sql` | `FORECAST_SLOTS`(장전 + 장중 넷) |
| `thesis_outcome/select_pending_narratives.sql` | `NARRATED_SLOTS`(예측 다섯 + `post_close`) |
| `thesis_outcome/select_backlog.sql` | 위 둘 다(ungraded / unnarrated) |
| `thesis/select_past_with_outcomes.sql` | `NARRATED_SLOTS` |

`select_pending_grades.sql`이 이미 지평 목록을 파라미터로 받는 이유("상수를 SQL과 파이썬
두 곳에 두면 한쪽만 고쳐지는 날이 온다")와 같다. `post_nxt_close`는 여전히 어느 목록에도
없다.

### 장중 슬롯의 채점 분모

`PendingGrade`에 `run_slot`과 `base_price`가 붙고 `_horizon_return`이 그것으로 조회를
고른다. 기준가는 **추론 행의 `input_state`에서** 온다.

```sql
thesis.input_state #>> ARRAY['intraday', thesis.subject_code, 'price']
```

봉에서 다시 뽑지 않는 이유는 그 사이 `kis_quote_intraday` 재실행이 없던 봉을 채워 "직전
봉"이 달라질 수 있기 때문이다. `input_state`의 값은 **모델이 실제로 본 가격**이고 그 행은
불변이다 — 첫 성공본 불변과 같은 이유다.

기존 `select_horizon_return.sql` 둘은 **건드리지 않는다.** 잘 돌고 있는 `pre_open` 채점
경로에 분기를 얹으면 앞으로의 점수가 조용히 다른 원본에서 나온다. 대신 파일을 나눴다.

| 파일 | 분모 | 분자 |
| --- | --- | --- |
| `index_bar/select_intraday_horizon_return.sql` | 파라미터로 받은 기준가 | 목표 영업일 15:30 봉의 close |
| `stock_investor_trade_daily/select_intraday_horizon_return.sql` | 같음 | 목표 영업일 확정 종가 |

종목 쪽이 `stock_bar`가 아니라 `stock_investor_trade_daily`에 있는 것은 **목표가의 표가
거기**이기 때문이다. 기준가는 이미 파라미터라 어느 표에서 왔는지를 그 조회가 알 필요가 없다.

## 6. DAG — 왜 슬롯 넷이 하나인가

저장소 규칙은 "슬롯·모드로 갈리는 DAG는 나눈다"이고, 2026-08-21에 `market_thesis_analysis`를
장전·장후로 가른 것이 그 규칙의 출처다. **그때 문제는 시각이 여럿인 것이 아니라 앞단
데이터와 실패 성격이 다른 둘을 시계로 뭉뚱그린 것이었다.** 장중 넷은 같은 봉과 같은 문서
평가를 같은 이유로 기다린다 — `slack_kr_market_briefing`이 `MultipleCronTriggerTimetable`
하나로 남아 있는 것과 같은 경우다(2026-08-26 사용자 결정).

그때 실제로 사고를 낸 것("`logical_date`가 없는 수동 실행이 벽시계로 떨어져 조용히 다른
모드를 돈다")은 `resolve_slot`이 막는다.

1. Param `run_slot`이 있으면 그것 — 수동 실행의 정식 경로
2. 없으면 `logical_date`의 KST 시각을 슬롯 표에서 역조회
3. 둘 다 아니면 `AirflowFailException`. **벽시계로 떨어지지 않는다**

가까운 슬롯으로 반올림하지도 않는다. 11:00에 clear해 돌리면 어느 슬롯도 아니고 실패다.

### 재시도·타임아웃은 장전·장후와 다르다

공유 `DEFAULT_ARGS`(재시도 3 × 10분) + `BUILD_TIMEOUT`(30분)이면 최악 두 시간이라
`max_active_runs=1` 아래에서 10:35 실행이 12:35 실행을 막는다. 장중은 재시도 1 × 5분에
`execution_timeout` 15분으로 최악 40분에 묶는다.

근거는 수집 DAG의 판정과 같다 — **다음 슬롯이 두 시간 뒤에 같은 창을 다시 본다.** 실패한
슬롯을 오래 붙들 값어치가 없고, 그 슬롯은 없던 것으로 남는다([README.md](README.md) 4절의
"추론 재시도·재평가를 만들지 않는다" 그대로).

## 7. Slack

**매 슬롯 발송이다**(2026-08-26 사용자 결정). 하루 일곱 건(장전·장중 넷·장후·애프터마켓)이고
`notify_slack`을 그대로 재사용한다. 헤더가 `⏱ 장중 전망(10:35)` 형태로 시각을 담는 것이
이 결정의 전제다 — 같은 채널에 쌓이므로 기준 시각이 없으면 어느 것이 언제 판단인지 못 읽는다
(차트·표 표기 규칙과 같은 이유).

"방향이 바뀐 슬롯만 보내기"는 만들지 않았다. 발송량이 문제가 되면 그때가 그 손잡이를 다는
시점이다.

## 8. 프롬프트

`PROMPT_VERSION`이 `"5"`에서 `"6"`으로 올라간다. 바뀐 것 셋:

- `SLOT_INSTRUCTION`에 장중 넷 — "지금 가격 대비이지 전일 종가 대비가 아니다"를 못 박는다
- `## 오늘 앞 슬롯` 절 — 4절
- `## 확률`의 `prob_flat` 설명에 창 길이 — 장중은 남은 시간이 짧아 실제 `flat` 빈도가
  기준선(코스피 6%·코스닥 11%·종목 6%)보다 높다

`PREFETCHED_PAST_THESES`를 5에서 2로 내린다. 그 상한은 **슬롯마다**라 슬롯이 여섯이 되면
최대 30행이 프롬프트에 실린다. 2면 최대 12행이고 전과 비슷한 길이다.

## 9. 테스트

```bash
uv run pytest tests -q
uv run ruff check apps airflow migrations tests
uv run pyrefly check
```

- `tests/dags/test_market_thesis_intraday.py` — cron과 슬롯 표 대조, 태스크 둘,
  최악 소요가 두 시간 미만, `resolve_slot` 세 경로, 봉 조회의 상·하한, guard의 0건/지연
  구분, 관측 상태가 확정 칸을 비우는 것, 되짚기의 기준가 둘
- `tests/dags/test_market_thesis_review.py` — `_horizon_return`이 슬롯으로 조회를 고르는 것,
  기준가 없는 장중 추론이 미채점으로 남는 것
- `tests/modules/test_thesis_pipeline.py` — 새 SQL 다섯의 텍스트(파라미터 모양, `now()` 없음,
  슬롯 리터럴 없음), 슬롯 목록 상수 셋의 관계
- `tests/models/test_analysis_models.py` — 두 `RunSlot` enum 대조(자동)
- `tests/migrations/test_thesis_schema.py` — `ck_thesis_run_slot`이 일곱 값으로 끝나는 것과
  ALTER 순서

**새 SQL 다섯은 운영 DB에 읽기 전용으로 한 번 돌려 봤다**(2026-08-26, 세션 2026-08-25).
테스트는 가짜 연결을 쓰므로 컬럼 이름과 조인 조건이 틀려도 통과한다.

| 확인 | 결과 |
| --- | --- |
| 봉 조회 둘 | 네 슬롯 모두 **직전 1분봉**을 골랐다(10:35 → 10:34). `BAR_STALENESS` 15분은 넉넉하다 |
| `is_final` 분포 | 지난 세션은 381봉 전부 `true`, 진행 중인 세션은 30 false / 35 true. **확정을 기다리면 장중에 못 돈다**는 판단이 맞다 |
| `select_same_day.sql` | 그날 앞 슬롯(`pre_open`)만 돌아왔다 |
| 장중 채점 둘 | KOSPI 10:34 6,529.67 → 마감 6,742.73 = **+3.26%**, 000660 1,582,000 → 확정 종가 1,678,000 = **+6.07%** |
| 슬롯 목록 파라미터 넷 | `pending_grades` 44행, `pending_narratives` 8행, `backlog` (0, 0), `past_with_outcomes` 4행 |

봉 시각이 예상(5분 경계)과 달라 1분봉임이 드러난 것이 이 확인의 소득이다 —
`BAR_STALENESS` 주석과 2절이 그 실측으로 고쳐졌다.

## 10. 배포 뒤에 볼 것

[TUNING.md](TUNING.md) 3절에 손잡이 넷이 늘었다. +4주에 본다.

- **`FLAT_THRESHOLD_PCT[0]`** — 아래 11절. **슬롯별로** `flat` 비율을 본다
- **장중 스케줄** — readiness 재시도가 잦으면 늦춘다
- **`BAR_STALENESS`** — guard가 정상인 날 막으면 늘린다
- **`PREFETCHED_PAST_THESES`** — 2로 내린 것이 예측을 나쁘게 했는지. 슬롯별 Brier 추이

그리고 **슬롯별 T+0 Brier**가 처음으로 "어느 시간대 예측이 나은가"를 말해 준다. 15:00
슬롯이 08:35보다 나은 것은 당연하고(남은 시간이 짧다), 볼 것은 그 격차가 시간에 비례하는지
아니면 특정 슬롯이 유독 나쁜지다.

## 11. `FLAT_THRESHOLD_PCT[0]`이 슬롯마다 안 맞는다 (미해결)

**임계 하나가 창 길이 다섯 개를 재고 있다.** `classify_outcome`은 `|누적 등락률| < 0.3%`면
실제 결과를 `flat`으로 찍는데, 그 0.3%는 **하루 창**(전일 종가 → 당일 종가, 390분) 기준으로
정해진 값이다. 장중 슬롯이 붙으면서 같은 지평 0의 창이 다섯 가지가 됐다.

| 슬롯 | T+0 창 | 길이 |
| --- | --- | --- |
| `pre_open` | 전일 종가 → 당일 종가 | 390분 |
| `intraday_morning` | 10:34 가격 → 마감 | 295분 |
| `intraday_midday` | 12:34 → 마감 | 175분 |
| `intraday_afternoon` | 14:34 → 마감 | 55분 |
| `pre_close` | 14:59 → 마감 | 30분 |

가격 변동폭은 대략 시간의 제곱근을 따른다. 30분 창은 하루의 1/13이라 전형적 변동이
`sqrt(1/13) ≈ 0.28`배다. 하루 |등락| 중앙값이 0.8%면 30분 창은 0.22%이고, **임계 0.3%
아래라 절반 넘게 `flat`으로 찍힌다.**

### 왜 문제인가

- **Brier가 왜곡된다.** 프롬프트는 `FLAT_BASE_RATE_PCT`로 "코스피 6%·코스닥 11%·종목 6%가
  실제 `flat` 빈도"라고 알려 준다. 그 값은 **하루 창 실측**이다(`index_daily` 132거래일).
  15:00 슬롯의 실제 빈도가 50%인데 모델이 기준선대로 `prob_flat ≈ 0.06`을 주면, 방향을
  맞혔든 아니든 점수가 나빠진다. **모델이 틀린 것이 아니라 라벨 정의가 창과 안 맞는 것이다.**
- **슬롯 비교가 깨진다.** 슬롯 다섯을 만든 목적 중 하나가 "어느 시간대 예측이 나은가"인데,
  임계가 늦은 슬롯을 대부분 `flat`으로 만들면 그 비교는 모델이 아니라 임계를 재게 된다.

### 실측 (2026-08-26, 표본 6세션 — **결론 아님**)

`index_bar` 1분봉이 2026-08-18에 시작해 지금 7세션뿐이다. 방향은 보이지만 값을 정하기엔
모자란다. 게다가 이 주는 변동이 컸다 — KOSPI 하루 창 |등락| 중앙값이 2.34%로,
`FLAT_BASE_RATE_PCT`를 잰 132거래일 평균보다 훨씬 높다.

| 슬롯 | KOSPI 중앙 \|등락\| | KOSPI `flat` | KOSDAQ 중앙 \|등락\| | KOSDAQ `flat` |
| --- | --- | --- | --- | --- |
| `pre_open` (390분) | 2.337% | 0% | 1.851% | 0% |
| `intraday_morning` (295분) | 1.309% | 0% | 0.711% | 33% |
| `intraday_midday` (175분) | 0.192% | 83% | 0.677% | 50% |
| `intraday_afternoon` (55분) | 0.224% | 67% | 0.370% | 50% |
| `pre_close` (30분) | 0.296% | 50% | 0.176% | 67% |

**창이 짧아질수록 `flat`이 는다는 방향은 둘 다 같다.** 다만 KOSPI의 midday(83%) > afternoon
(67%) > pre_close(50%)는 창 길이와 반대라, 6세션으로는 순서를 못 가린다.

### 왜 지금 안 고쳤나

1. **값이 실측이 아니라 추측이 된다.** 기존 지평별 값도 `0.3 × sqrt(N)` 반올림이라고 코드가
   밝혀 뒀지만, `FLAT_BASE_RATE_PCT`는 **실측**이다. 슬롯 축 임계를 넣으려면 그 base rate도
   슬롯마다 다시 재야 프롬프트가 거짓말을 안 한다 — 상수 하나가 아니라 측정 두 벌이다.
2. **축을 늘리는 것이 상수 수정이 아니다.** `FLAT_THRESHOLD_PCT`는 `horizon_days` 키다.
   슬롯 축을 더하면 `classify_outcome`이 슬롯을 받고, `store_grade`가 넘기고,
   `select_calibration.sql`이 슬롯으로 그룹하고, 테스트 경계값이 다섯 배가 된다.
3. **한 번에 한 손잡이**([TUNING.md](TUNING.md) 1절). 슬롯 넷·프롬프트 판 6·`PREFETCHED`
   5→2가 같은 배포에 들어간다. 여기에 임계까지 흔들면 4주 뒤 어느 것이 무엇을 바꿨는지
   못 가린다.

지금 한 것은 **프롬프트에 창 길이를 알린 것 하나**다(`## 확률` 절). 모델이 "남은 시간이
짧으면 `flat`이 기준선보다 흔하다"를 읽고 스스로 올릴 수 있다. 숫자는 안 준다 — 없으니까.

### +4주에 무엇을 보고 무엇을 당기나

```sql
SELECT thesis.run_slot,
       count(*)                                                    AS graded,
       round(avg((outcome.actual_outcome = 'flat')::int) * 100)     AS flat_pct,
       round(avg(outcome.brier_score), 3)                           AS mean_brier
FROM thesis_outcome AS outcome
JOIN thesis ON thesis.id = outcome.thesis_id
WHERE outcome.horizon_days = 0
  AND outcome.evaluated_at IS NOT NULL
GROUP BY thesis.run_slot
ORDER BY thesis.run_slot;
```

- **`flat_pct`가 슬롯 간에 20%p 넘게 벌어지면** 임계가 창을 안 따라가고 있다는 뜻이다.
  그때 슬롯 축 임계를 넣고 `FLAT_BASE_RATE_PCT`도 슬롯마다 다시 잰다.
- **벌어지지 않으면 아무 것도 안 한다.** 코스피 하루 변동이 지금보다 잠잠해지면 `flat`이
  전 슬롯에서 고르게 늘 수도 있고, 그건 임계가 아니라 시장 이야기다.
- `mean_brier`는 `flat_pct`를 본 **뒤에** 읽는다. 순서를 바꾸면 라벨 왜곡을 모델 성능으로
  잘못 읽는다.
