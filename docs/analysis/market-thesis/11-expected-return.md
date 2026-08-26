# 11단계 — 확률에 크기를 붙인다: 방향별 기대 등락률

- 상위: [README.md](README.md)
- 날짜: 2026-08-26
- 상태: **구현 완료**(2026-08-26). 리비전 `f4b19c6ea283`은 **운영 반영 전**이고 올릴 시각은
  7절이다. 검증은 `uv run pytest tests -q`(2,027건)와 `uv run ruff check`.
- 의존: [1-storage.md](1-storage.md)(`thesis` 테이블과 불변 원칙),
  [2-agent.md](2-agent.md)(프롬프트와 출력 스키마), [3-dag-slack.md](3-dag-slack.md)(Slack 렌더),
  [9-intraday.md](9-intraday.md)(장중 슬롯의 기준가). [12-api.md](12-api.md)가 이 두 칸을
  응답에 싣는다 — 순서는 11 → 12지만 12는 칸이 비어도 뜬다.
- 산출물(예정): `thesis` 컬럼 둘 + `thesis_outcome` 컬럼 둘과 CHECK 둘, 수기 리비전 하나,
  `thesis_generation`의 출력 스키마·프롬프트 절, 크기 채점 순수 함수 하나,
  `thesis_render`의 결론 줄, ops 브리핑 한 칸, SQL 넷, 테스트

## 0. 왜 — 확률만으로는 "얼마나"를 못 읽는다

지금 Slack에 나가는 결론 줄은 이렇다.

```
📉 하락 40%
```

읽는 사람이 다음에 묻는 것은 언제나 "얼마나"다. 0.4% 빠질 것과 2.5% 빠질 것이 같은
줄로 나온다. 확률만 있으면 **대응이 갈리지 않는다** — 40%짜리 0.3% 하락과 40%짜리
3% 하락은 완전히 다른 이야기인데 기록에도 화면에도 그 구분이 없다.

목표 형태(사용자 요청):

```
📉 하락 1.2% 예상 (40%)
```

## 1. 무엇을 저장하나

### 결정: 방향별 조건부 크기 두 칸

| 컬럼 | 타입 | 뜻 |
| --- | --- | --- |
| `up_return_pct` | `Numeric(5,2)`, nullable | **상승한다면** 채점 창에서 몇 % 오를지. 양수 |
| `down_return_pct` | `Numeric(5,2)`, nullable | **하락한다면** 몇 % 빠질지. 양수(크기) |

`flat_return_pct`는 두지 않는다. `flat`의 정의가 이미 "±`FLAT_THRESHOLD_PCT[0]`% 안"이라
크기가 정의에 들어 있다.

**단일 기대값 한 칸(부호 포함)이 아닌 이유**는 렌더가 방향을 둘 보일 수 있어서다.
`_verdicts`가 최고 확률에서 `VERDICT_TIE_GAP` 안에 붙은 방향을 다 낸다(하락 41%·횡보 38%).
단일 칸이면 그 두 줄이 같은 숫자를 나눠 쓰거나 한 줄에만 붙는다. 조건부 크기는 줄마다
자기 숫자를 갖는다.

덤으로 확률과 곱하면 기대값이 나온다 — `prob_up × up_return_pct − prob_down × down_return_pct`.
칸을 따로 만들지 않고 필요할 때 계산한다.

### 창은 확률과 같다

크기의 기준 창은 **확률과 글자 그대로 같은 창**이다(지평 0). 장전·장후는 그 세션 하루,
장중은 기준 시각 가격에서 마감까지다. 지평 1·3·5의 크기는 받지 않는다 — 확률도 지평
0에서만 받고 나머지 지평은 같은 확률을 재사용한다.

### nullable인 이유

`thesis` 행은 **어떤 컬럼도 나중에 갱신되지 않는다**(1-storage.md). 이미 쌓인 행을
채울 방법이 없으므로 `NOT NULL`을 걸 수 없다. 리비전 뒤 새로 만들어지는 행은 항상 채워진다.

### CHECK

```
(up_return_pct IS NULL OR up_return_pct BETWEEN 0 AND 30)
AND (down_return_pct IS NULL OR down_return_pct BETWEEN 0 AND 30)
```

상한 30%는 폭주만 받는 안전망이다. "임계보다 커야 한다" 같은 정합성은 CHECK가 아니라
프롬프트와 저장 전 검증이 본다 — DB 제약으로 걸면 모델이 경계값을 낼 때 행 전체가
사라진다.

## 2. 프롬프트

`PROMPT_VERSION`을 `6` → `7`로 올린다. 판이 갈려야 크기가 붙기 전후의 채점을 나눠 읽는다.

`## 확률` 절 뒤에 `## 크기` 절을 붙인다. 담을 것:

- `up_return_pct`는 **상승한다는 조건에서의** 등락률이지 기대값이 아니다. 확률을 이미
  곱한 값을 여기 쓰면 두 번 곱해진다.
- 둘 다 `FLAT_THRESHOLD_PCT[0]`(0.3)보다 커야 한다. 그보다 작으면 그것은 `flat`이다.
- 근거 없이 큰 값을 쓰지 마라. 기준선은 **최근 실현 변동폭**이고 그것을 보려면
  `daily_history` 툴의 일봉(`high`·`low`·`close`)을 읽어야 한다. 그보다 크게 쓰려면
  `claims`에 그날에 한정된 근거를 대야 한다.
- 장중 슬롯은 **남은 시간이 짧을수록 크기도 작다.** 14:35 슬롯의 마감까지 남은 55분에
  하루치 변동폭을 쓰면 거짓이다.

출력 형식 예시도 두 칸을 포함하도록 고친다.

**관측 상태에는 변동폭이 없다**(확인 2026-08-26). `TechnicalObservation`이 가진 것은
`close_vs_sma20_pct`·`sma20_vs_sma60_pct`·`rsi14`·`macd_histogram`·`volume_ratio20`뿐이고
ATR·일봉 range·표준편차가 없다. 그래서 위 문장이 `daily_history` 툴을 가리킨다 —
`DailyBar`가 `high`·`low`·`close`를 준다. **관측 상태에 변동폭 칸을 새로 만들지 않는다.**
크기를 부르려는 모델은 어차피 그 툴을 부르고, 칸을 늘리면 프롬프트가 길어지는 만큼
`MAX_TOOL_RESULT_CHARS` 예산과 별개로 매 슬롯 비용이 는다.

## 3. 렌더

`thesis_render._thesis_section`의 결론 줄 하나만 바뀐다.

```
현재: *📉 하락 40%*
이후: *📉 하락 1.2% 예상 (40%)*
```

- 소수 첫째 자리까지. "대략"이 요청이라 둘째 자리는 거짓 정밀도다.
- 값이 `NULL`이면(리비전 전 행) 지금 형태로 떨어진다. 옛 행을 다시 그리는 자리가
  ops 브리핑과 T+5 해설에 있어 분기 하나는 필요하다.
- `flat` 줄에는 크기를 붙이지 않는다 — `*⏸️ 횡보 38%*` 그대로다.

## 4. 채점 — 크기도 점수를 받는다

방향은 `brier_score`가 채점한다. 크기는 그것과 **독립으로** 채점한다. 둘을 합친 종합
점수는 만들지 않는다(1-storage.md의 판단과 같다 — 섞으면 둘 다 못 읽는다).

### 4.1 무엇을 재나

`up_return_pct`는 "**상승한다면** 얼마"라는 조건부 추정이다. 그러므로 채점도 조건부로
한다 — **실제로 실현된 방향의 추정만** 실제와 대조한다. 방향을 틀렸는지는 Brier가 이미
답했으므로 여기서 또 벌점을 주면 같은 실수를 두 번 세는 것이다.

```
방향이 up  → up_return_pct 를   |actual_return_pct| 와 대조
방향이 down → down_return_pct 를 |actual_return_pct| 와 대조
방향이 flat → 채점하지 않는다 (flat은 정의상 크기가 ±임계 안이다)
```

`|actual_return_pct|`인 이유는 저장한 크기가 부호 없는 크기라서다. 하락이면 실제값은
음수이고 추정은 양수다.

### 4.2 새 컬럼 둘 (`thesis_outcome`)

| 컬럼 | 타입 | 뜻 |
| --- | --- | --- |
| `predicted_return_pct` | `Numeric(5,2)`, nullable | 실현 방향에 대응하는 조건부 크기 **스냅샷**. `thesis`에서 골라 온 값 |
| `return_error_pct` | `Numeric(8,4)`, nullable | `abs(actual_return_pct) - predicted_return_pct`. **부호를 유지한다** |

**부호를 살리는 것이 이 설계의 핵심이다.** 절댓값만 저장하면 "얼마나 틀렸나"는 알아도
"어느 쪽으로 틀리나"를 못 읽는다. 모델이 늘 크게 부르는지(음수 쏠림) 작게 부르는지
(양수 쏠림)가 프롬프트를 고칠 방향을 정한다. 평균 절대 오차(MAE)는 조회가
`avg(abs(return_error_pct))`로 만든다 — 칸을 따로 두지 않는다.

**0~1로 정규화한 점수를 만들지 않는다.** Brier와 나란히 두려고 정규화하면 스케일
상수를 하나 더 발명해야 하고(무엇으로 나누나), 그 상수가 곧 거짓말이 된다. 퍼센트포인트
오차는 그 자체로 읽힌다 — "평균 0.4%p 과소"가 "0.83점"보다 정확히 더 많은 것을 말한다.

### 4.3 언제 채우나

- **지평 0에서만.** 크기의 창이 지평 0이다(1절). 지평 1·3·5 행은 둘 다 `NULL`이다.
  같은 크기를 5영업일 누적에 대조하면 항상 과소로 나온다.
- `actual_outcome`이 `flat`이면 `NULL`.
- `thesis` 행의 해당 크기가 `NULL`이면(리비전 전 행) `NULL`.
- 채점 자체와 같은 트랜잭션에서 같은 순수 함수가 만든다. LLM이 끼지 않는다.

### 4.4 CHECK

기존 `ck_thesis_outcome_grade_all_or_none`(채점 넷) 그룹에 **넣지 않는다.** 위 세 경우에
정상적으로 비어 있어 넷과 짝이 안 맞는다. 대신 둘만의 제약을 따로 둔다.

```
(return_error_pct IS NULL) = (predicted_return_pct IS NULL)
AND (predicted_return_pct IS NULL OR evaluated_at IS NOT NULL)
```

첫 줄은 둘이 항상 함께 있거나 함께 없다는 것, 둘째 줄은 채점하지 않은 행에 크기 오차만
있을 수 없다는 것이다.

### 4.5 "어떻게 산출했나"가 행에 남는가

남는다. 한 행이 산출에 들어간 값을 **전부** 갖는다.

| 알고 싶은 것 | 읽는 칸 |
| --- | --- |
| 실제 등락률 | `actual_return_pct` |
| 실제 방향 | `actual_outcome` |
| 그 방향에 대응하는 예측 크기 | `predicted_return_pct` |
| 오차와 그 부호 | `return_error_pct` |
| 원래 예측 셋 전부 | `thesis.up_return_pct`·`down_return_pct`·확률 셋 |
| 왜 그렇게 예측했나 | `thesis.up_reasoning`·`down_reasoning`·`thesis_evidence` |

식의 원본은 순수 함수 하나(`thesis_domain.return_error`)와 그 docstring이다. SQL에 두지
않는 이유는 Brier 수식을 SQL이 아니라 Python에 둔 것과 같다 — DB 없이 경계값을 테스트한다.

### 4.6 "왜 틀렸나"는 해설이 말한다

숫자만으로는 과대·과소의 **이유**가 안 남는다. 그 자리는 이미 있다 — 사후 해설
(`narrative`)이다. 해설 프롬프트가 지금 받는 `- **실제 결과**: +1.2% (up)` 줄에 예측
크기와 오차를 함께 실어, 크기가 크게 어긋난 날은 해설이 그것을 다루게 한다.

컬럼을 더 만들지 않는다. `verdict`는 "이유가 지지됐나"를 답하는 칸이고 크기 오차의
사유는 그 문장 안에 든다.

**주의**: 해설은 지평 1·3·5에만 붙고 크기 채점은 지평 0이다. 지평 1 해설 프롬프트가
지평 0의 크기 오차를 읽어야 하므로 조회 하나가 지평을 건너 조인한다
(`select_pending_narratives.sql`).

### 4.7 어디서 보나

ops 브리핑의 "추론 품질" 표(`briefing/ops.py`의 `THESIS_CALIBRATION`)에 지평 0 행에만
붙는 칸 하나를 더한다.

```
크기 오차: 평균 +0.42%p (과소, n=37)
```

부호 평균이 양수면 과소, 음수면 과대다. 표본 수를 함께 적는다 — flat과 미채점이
빠져서 Brier의 n과 다르다.

## 5. 안 하는 것

- **신뢰구간·분위수**(P10/P50/P90). 세 확률에 두 크기까지가 지금 모델이 근거를 댈 수 있는
  한계다. 분위수는 채점 방법(pinball loss)까지 새로 정해야 한다.
- **지평 1·3·5의 크기.** 확률도 지평 0에서만 받는다. 여기만 늘리면 둘의 정의가 어긋난다.
- **목표가.** 프롬프트가 이미 금지한다. 등락률은 방향의 크기이고 목표가는 매매 권유다.
- **`PastThesis`에 크기 싣기.** 과거 추론을 프롬프트에 되먹이는 절에 크기를 넣으면
  모델이 자기 과거 숫자에 앵커링한다. 4주 뒤 채점을 보고 정한다.

## 6. 산출물

| 파일 | 무엇 |
| --- | --- |
| `apps/models/analysis/thesis.py` | `Thesis` 컬럼 둘 + `ck_thesis_return_pct_range`, `ThesisOutcome` 컬럼 둘 + CHECK 둘 |
| `migrations/versions/<신규>.py` | 수기 리비전 하나. `down_revision = "e3b7c14da902"`. 두 테이블 + `thesis_precedent.precedent_id` 인덱스 |
| `airflow/modules/thesis_domain.py` | `MAX_EXPECTED_RETURN_PCT = 30`, `PROMPT_VERSION = "7"`, 순수 함수 `return_error` |
| `airflow/modules/thesis_generation.py` | `ThesisAnswer`·`ThesisDraft` 필드 둘, `## 크기` 절, 출력 예시 |
| `airflow/modules/thesis_store.py` | 저장·조회 모양(`StoredThesis`) |
| `airflow/modules/thesis_outcomes.py` | 채점에서 `return_error` 호출, 해설 프롬프트에 예측 크기·오차 줄 |
| `airflow/modules/thesis_render.py` | 결론 줄 |
| `airflow/modules/briefing/ops.py` | `THESIS_CALIBRATION` 지평 0 행의 크기 오차 칸 |
| `airflow/sql/postgres/thesis/insert.sql`·`select_by_run.sql` | 컬럼 둘 |
| `airflow/sql/postgres/thesis_outcome/*` | 채점 upsert에 컬럼 둘, `select_pending_narratives.sql`에 지평 0 조인 |
| `tests/models/test_analysis_models.py`·`tests/migrations/test_thesis_schema.py`·`tests/modules/test_thesis_pipeline.py` | 컬럼·CHECK·`return_error` 경계값·렌더·검증 |

## 7. 확인한 것과 리비전 시각

### 확인 끝

- **`thesis/*.sql`에 `SELECT *`가 없다.** 넷 다 컬럼 열거라 새 칸이 저절로 새지 않는다.
  `select_by_run.sql`에만 두 칸을 더한다. `select_past_with_outcomes.sql`과
  `select_same_day.sql`은 **손대지 않는다** — 과거 추론을 프롬프트에 되먹이는 자리라
  크기를 실으면 모델이 자기 과거 숫자에 앵커링한다(5절).
- **관측 상태에 실현 변동폭이 없다.** 2절이 `daily_history` 툴을 가리키는 이유다.
- **`insert_grade.sql`은 `DO UPDATE ... WHERE thesis_outcome.evaluated_at IS NULL`이다.**
  그래서 **리비전 전에 이미 채점된 지평 0 행은 크기 오차가 영원히 빈다.** 되채우지 않는다 —
  첫 성공본 불변 원칙과 같은 자리다. 표본은 리비전 뒤부터 쌓인다.

### 리비전을 올릴 시각 — **구현 뒤에 다시 정한다**

> 아래는 지금까지 확인한 재료다. **배포 시각은 구현이 끝난 뒤 다시 다룬다**(사용자 2026-08-26).
> 코드가 없는 지금 시각을 확정해 봐야 구현 중에 바뀐다.

#### 지금까지 확인한 것 — 주말이 가장 쉽다

**추천: 토요일이나 일요일 아무 때.** 추론 DAG 넷이 전부 평일만 돈다(cron이 `* * 1-5`) —
장전 08:35, 장중 10:35·12:35·14:35·15:00, 장후 20:30, 애프터마켓 21:00(전부 KST).
주말에는 이 테이블을 건드리는 실행이 하나도 없다.

차선은 **평일 밤**이다. 창이 매일 열 시간쯤 있다 — 그날 마지막 두 DAG이 끝난 뒤부터
다음 장전(08:35) 전까지다.

**20시대는 오히려 가장 나쁜 창이다.** 20:30 장후 리뷰와 21:00 애프터마켓 리뷰가 바로
뒤에 붙어 있다. 정상 종료면 21:30쯤 끝나지만 `DEFAULT_ARGS`가 `retries=3`,
`retry_delay=10분`이고 `BUILD_TIMEOUT`이 30분이라 **최악 23:30까지 간다.**
시각으로 재는 것보다 **Airflow UI에서 `market_thesis_review`·`market_thesis_nxt_review`가
success인 것을 보고** 올리는 편이 확실하다.

잠금 자체는 짧다 — `ADD COLUMN ... NULL` 넷은 PostgreSQL 11 이후 메타데이터만 바꾸고,
`thesis_precedent` 인덱스는 그 테이블이 40kB라 밀리초다(2026-08-26 실측). **그런데
`ACCESS EXCLUSIVE` lock을 잡는다** — 짧아도 그 순간 lock queue가 생기면 뒤따르는 DAG
쿼리가 전부 밀린다. 시각을 고르는 이유는 잠금 시간이 아니라 그것이다.

### 마이그레이션과 코드 배포는 분리한다

**조용한 창이 필요한 것은 마이그레이션뿐이다.** 새 칸이 전부 nullable이라 둘을 떼어 놓을
수 있다.

```
평일 밤 : 마이그레이션만 올린다   ← 창이 필요한 건 여기뿐
아무 때 : 코드 배포               ← 옛 코드는 새 칸을 모른 채 그대로 돈다
```

그 사이에 만들어진 행은 크기가 빈 채로 남고, 그게 전부다. **순서만 지키면 된다** —
코드가 먼저 나가면 컬럼이 없어 INSERT가 죽는다. 시간 제약이 아니라 순서 제약이다.

**둘을 붙여서 하면 판 경계가 깨끗하다는 이점이 하나 있다.** 이 단계는
`PROMPT_VERSION`을 6 → 7로 올리므로 코드 배포 시점이 곧 판이 갈리는 시점이다. 주 중간에
올리면 그 주가 두 판에 걸쳐 표본을 반씩 쪼개 세야 한다(`TUNING.md` 1절 "한 번에 한 손잡이").
주말에 둘 다 올리면 월요일 장전부터 판 7이 깨끗하게 시작한다.

### 이 리비전에 함께 들어가는 것

**`thesis_precedent.precedent_id` 단독 인덱스.** UNIQUE가 `(thesis_id, precedent_id)`라
선두 컬럼만 커버한다. [12-api.md](12-api.md)의 이웃 그래프가 **들어오는** `INFORMED_BY`를
읽으므로 인덱스가 필요하고, 어차피 이 리비전이 같은 테이블 군을 건드리므로 여기 넣는다.
`thesis` 계열에는 지금 명시 인덱스가 하나도 없다(UNIQUE가 주는 것이 전부다).
