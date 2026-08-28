# 15단계 — 등락률의 축을 데이터로 남기고, 크기에 앵커와 오차를 붙인다

- 상위: [README.md](README.md)
- 날짜: 2026-08-28
- 상태: **구현 완료**(2026-08-28, 브랜치 `worktree-feature-return-basis`). 운영 반영은 아직이다 —
  리비전 `b2e75f9c41a8`을 사용자가 올린다. 검증은 `uv run pytest tests -q`(2,702건)와
  `uv run ruff check apps airflow migrations tests`.
- 의존: [11-expected-return.md](11-expected-return.md)(방향별 기대 등락률 두 칸과 크기 채점),
  [9-intraday.md](9-intraday.md)(장중 슬롯의 기준가), [10-base-rate.md](10-base-rate.md)
  (`technical/base_rate.py`와 무조건 기저 SQL), [3-dag-slack.md](3-dag-slack.md)(Slack 렌더),
  [12-api.md](12-api.md)(응답 계약)
- 산출물(예정): `thesis`에 축 세 칸 + 오차 두 칸, `thesis_outcome`에 오차 스냅샷 한 칸,
  CHECK 넷, 수기 리비전 하나, 새 툴 `typical_move`, 프롬프트 `## 크기` 절 교체와
  `PROMPT_VERSION` 두 번, `NARRATIVE_PROMPT_VERSION` 한 번,
  `select_window_changes.sql`의 국내 지수 제외, Slack 기준 줄, API 다섯 칸, 테스트

## 0. 왜 — 같은 퍼센트가 세 가지 뜻으로 나가고 있다

2026-08-28 장중 슬롯의 Slack 메시지가 `▼ 하락 0.7% 예상 (50%)`이었다. 그날 코스피는 하루
−1.79% 빠졌다. 읽는 사람이 "왜 이렇게 낮게 잡았나"라고 물은 것이 이 단계의 출발점이고,
파 보니 성격이 다른 문제가 셋이었다. 여기에 **네 번째 요구**가 붙었다(0.4절).

### 0.1 축이 값과 함께 다니지 않는다 — 0.7%는 하루 등락이 아니다

장중 슬롯의 `*_return_pct`는 **"그 슬롯이 본 봉의 종가 → 마감"** 조건부 크기다. 프롬프트가
그렇게 지시하고(`prompts/thesis_generation.yaml`의 `intraday` variant) 채점도 같은 축을 쓴다
(`thesis/review.py`의 `_horizon_return`).

| 대상 | 12:35 값 (전일 대비) | 마감 (전일 대비) | 실제 12:35→마감 | 모델 예측 |
| --- | --- | --- | --- | --- |
| 코스피 | 6,825.11 (−1.26%) | 6,788.79 (−1.79%) | **−0.53%** | 0.70% |
| 삼성전자 | 259,500 (−2.44%) | 257,000 (−3.38%) | **−0.96%** | 1.10% |
| SK하이닉스 | 1,687,000 (−2.49%) | 1,653,000 (−4.45%) | **−2.02%** | 1.30% |

**값은 틀리지 않았다.** 코스피·삼성은 오히려 크게 불렀다. 틀린 것은 그 값이 무엇 대비인지가
어디에도 안 적힌다는 것이다. `thesis/render.py`의 `_verdict_label`은 확률과 크기만 그리고,
채점 SQL은 축을 알지만 `input_state` JSONB를 캐내서 안다
(`thesis_outcome/select_pending_grades.sql`의 `#>> ARRAY['intraday', subject_code, 'price']`).
API는 `input_state: dict[str, Any]`를 그대로 내보내며 그 설명이 "모양은 슬롯마다 다르다"다.

즉 **축을 아는 것은 슬롯 규칙을 아는 코드뿐**이고, 사람도 다른 모델도 알 길이 없다.
[11-expected-return.md](11-expected-return.md) 1절이 "창은 확률과 같다"고 정해 두었지만
그 창은 문서에만 있고 행에는 없다.

### 0.2 크기 앵커가 없어 장전 예측이 구조적으로 과소다

장전 슬롯은 축이 전일 종가 대비 하루라 직접 비교된다.

| 대상 | 08-28 장전 예측 | 실제 하루 |
| --- | --- | --- |
| 코스피 | 0.90% | −1.79% |
| 삼성전자 | 1.50% | −3.38% |
| SK하이닉스 | 2.00% | −4.45% |

프롬프트 `## 크기` 절이 "기준선은 **최근 실현 변동폭**이다"라고 해 놓고 **관측 상태에 변동폭
숫자가 없다.** [11-expected-return.md](11-expected-return.md) 2절이 "관측 상태에 변동폭 칸을
새로 만들지 않는다. 크기를 부르려는 모델은 어차피 `daily_history` 툴을 부른다"고 의도적으로
뺐다. 그 판단의 결과가 위 표다 — 모델은 일봉 OHLC를 눈대중해 크기를 부른다.

눈대중이 어느 쪽으로 기우는지는 재 볼 수 있다. 코스피 최근 250거래일 |일간 등락|의
중앙값이 **1.53%**, 평균 2.31%, p90 5.73%다. 장전 예측 0.90%는 **모델이 실제로 조회한 재료의
중앙값보다도 낮다**(그날 `daily_history`를 네 번 불렀다).

프롬프트의 브레이크가 한쪽에만 걸려 있는 것도 같은 방향으로 민다.

> 그보다 크게 쓰려면 그날에 한정된 근거를 `claims`에 대야 한다.

작게 쓰는 데는 아무 조건이 없다. 게다가 장중 슬롯에는 "남은 시간이 짧을수록 크기도 작다"가
더해져 축소 압력이 하나 더 있다 — **방향만 말하고 배수를 안 줘서 모델이 두 번 깎는다.**

채점도 같은 방향을 가리킨다. `thesis_outcome`의 지평 0 크기 채점이 아직 3건뿐이지만
**3건 전부 과소**이고 `return_error_pct` 평균이 **+0.50%p**다.
[11-expected-return.md](11-expected-return.md) 4.2절이 "부호를 살리는 것이 이 설계의
핵심"이라 적어 둔 그 부호가 지금 답을 하고 있다.

### 0.3 `macro_changes`가 국내 지수를 09:00 기준으로 준다 — 부호까지 뒤집힌다

`quote_bar/select_window_changes.sql`은 **분석 창 첫 봉의 종가**를 분모로 쓴다. 그 선택 자체는
근거가 있다(SQL 머리말: "밤사이 얼마나 움직였나"와 구간이 다르다). 문제는 장중·장후 슬롯의
창 시작이 **당일 09:00**이라(`intraday.py`·`review.py`의 `macro_window_start`) 국내 정규장의
개장 갭이 통째로 빠지는데, `domain.py`의 `MACRO_KINDS`에 `"index"`가 있고 SQL에 country
필터가 없어 **코스피·코스닥이 이 툴에 섞인다**는 것이다.

| 실행 | `thesis_evidence`에 저장된 근거 | 그 시점 실제(전일 종가 대비) |
| --- | --- | --- |
| 08-28 intraday_midday | `코스피 -0.09%` | −1.26% |
| 08-27 post_close | `코스피 -1.15%` | **+1.53%** |

08-27은 6,808.21 → 6,974.07(갭 +2.44%) → 6,912.12로 끝난 날이다. 창 첫 봉 기준이면 −1.15%,
전일 종가 기준이면 +1.53%다. **부호가 뒤집힌다.** 모델은 한 프롬프트 안에서 같은 코스피를
관측 상태의 −1.05%와 툴의 −0.09%로 두 번 보고, Slack 근거 줄에는 툴 쪽이 찍힌다
(`render.py`가 `evidence_title`을 그대로 낸다).

툴 설명(`toolbox.py`의 `TOOL_DESCRIPTIONS["macro_changes"]`)은 이 툴이 "**해외** 지수·선물·
환율"이라고 말한다. 비교 대상인 `us_market_close`는 `select_thesis_us_close.sql`에서
`symbol.country = 'US'`로 제대로 거른다. **코드와 설명이 갈라진 것이지 설계가 그런 것이
아니다.**

장전 슬롯은 창이 전 개장일 15:30부터라(`forecast.py`의 `macro_window_start`) 첫 봉이 곧
전일 종가여서 왜곡이 없다. **장중과 장후만이다.**

### 0.4 점추정 하나로는 못 읽는다 — ± 오차를 붙인다 (사용자 2026-08-28)

> "하락이 기존 1퍼 2퍼 이렇게 하는데 실제 그게 정확히 맞지 않을 것 같은데 ± 오차 값도
> 추가해줘"

맞는 지적이고 지금 데이터가 그것을 뒷받침한다. 크기 채점 3건의 `return_error_pct`가 평균
+0.50%p이고 개별로는 더 벌어진다. **`1.30%`라고 적어 놓고 실제가 2.02%였는데도 화면은
정확한 한 점처럼 보인다**(08-28 하이닉스). 점추정만 내면 읽는 쪽이 그 값을 실제보다 믿고,
채점도 "얼마나 벗어났나"만 재고 **"벗어날 줄 알았나"는 못 잰다.**

그래서 크기마다 ± 폭을 함께 받는다. 자세한 것은 4절.

### 0.5 이 단계가 지키는 것

1. 등락률의 **축이 데이터로 남아** Slack·API·다른 LLM 어디서 읽어도 슬롯 규칙을 몰라도
   되게 한다.
2. 크기 앵커는 **관측 상태에 칸을 늘리지 말고 새 툴 하나**로 준다.
3. 크기는 **점이 아니라 구간**으로 나가고, 그 구간이 맞았는지도 채점된다.

---

## 1. 축을 데이터로 남긴다 — `thesis`에 세 칸

### 1.1 왜 `input_state`로는 부족한가

1. **장전 축이 아예 없다.** `input_state`에는 장중의 `intraday.<code>.price`만 있다.
   `pre_open`의 기준가는 추론 행 어디에도 없고 채점이 그때그때 다시 찾는다.
2. **읽는 쪽이 슬롯 규칙을 알아야 한다.** `select_pending_grades.sql`의 JSONB 경로는
   `thesis/state.py`의 **Pydantic 필드 이름에 결합**돼 있다. 요구 1과 정면으로 어긋난다.
3. **subject별 값이 공용 블롭에 묻힌다.** `store_theses`가 같은 `observed_state` JSON을 행마다
   통째로 넣으므로, "이 행의 기준가"를 얻으려면 자기 `subject_code`로 블롭을 파야 한다.
4. **API 계약이 없다.** `input_state: dict[str, Any]`에 외부 소비자가 기댈 수 없다.
5. **행은 불변이라 칸을 더해도 충돌이 없다.** nullable로 더하면 기존 행은 그대로 NULL이다.

### 1.2 칸 셋

| 칸 | 타입 | 뜻 |
| --- | --- | --- |
| `base_price` | `Numeric(18, 8)` nullable | 확률 셋과 `up_return_pct`·`down_return_pct`의 **분모** |
| `base_at` | `timestamptz` nullable | 그 가격이 나온 시각(UTC) |
| `base_return_pct` | `Numeric(8, 4)` nullable | 직전 확정 종가 → `base_price`까지 **이미 온** 등락(퍼센트) |

- 타입 `Numeric(18, 8)`은 `index_bar.close`·`previous_close`와 같다. 반올림을 끼워 넣지 않는다.
- **`base_at`이 `as_of_at`에서 유도되지 않는다.** 08-28 midday 실측: `as_of_at`이 03:35Z인데
  코스피가 실제로 본 봉은 **03:30Z**였다. `BAR_STALENESS`가 15분까지 봐 주므로 둘의 차이는
  최대 15분이다. 이 칸이 없으면 "12:35 기준"이라고 적어 놓고 실제로는 12:30 값을 보여 준다.
- **`base_return_pct`가 셋째 칸인 이유가 사용자의 첫 질문 그 자체다.** 12:35 기준
  `base_return_pct = -1.26`, 예측 하락 `0.70` → 하루로 읽으면 약 −1.95%. 이 한 줄이
  "왜 −1.79%인데 0.7%라 했나"를 없앤다. 두 칸만 두면 읽는 쪽이 전일 종가를 따로 찾아야 한다.
- **축 종류 enum 칸은 두지 않는다.** `run_slot`이 이미 그것을 말하고 `base_at`이 "언제부터"를
  값으로 말한다. 축의 끝(그날 정규장 마감)은 슬롯과 무관하게 하나라 칸이 필요 없다 —
  컬럼 주석과 API 설명이 그 사실을 적는다.

### 1.3 슬롯마다 무엇이 들어가나

| 슬롯 | `base_price` | `base_at` | `base_return_pct` |
| --- | --- | --- | --- |
| `pre_open` | 전 개장일 확정 종가 | 그 세션 15:30 KST | `0` (정의상) |
| `intraday_*` | 그 슬롯이 본 봉의 종가 | 그 봉의 `bar_at` | 그 봉의 전일 종가 대비 등락 |
| `post_close`·`post_nxt_close` | (셋 다 `NULL`) | | |

**전부 이미 조회한 관측 상태에서 파생한다. 쿼리를 새로 만들지 않는다** — 새로 조회하면 모델이
본 값과 저장한 값이 갈릴 수 있다([9-intraday.md](9-intraday.md)가 `check_ready`의 봉 dict를
그대로 넘기게 만든 이유와 같다). 예측 슬롯 둘은 그 값이 **이미 손에 있다**:
`pre_open`은 `observed_state(previous_open_day)`가 준 확정 종가가 곧 기준가이고,
장중은 `IntradayObservation`의 `price`·`bar_at`·`return_pct` 셋이 그대로 축이다.

**장후 둘은 안 채운다**(구현 중 결정, 2026-08-28). 그 슬롯의 축을 정의대로 채우려면 조회가
하나 더 필요한데(`post_close`의 기준가는 *직전* 세션 종가인데 관측 상태에는 *당일* 종가가
있다) 그 값을 쓰는 소비자가 없다 — 장후는 채점 대상이 아니다. **남는 문제는 적어 둔다**:
장후 리뷰의 `up_return_pct`는 예측이 아니라 실현값의 재진술인데(08-27 post_close 코스피가
`1.53`이고 그날 실제가 정확히 +1.53%다) Slack에는 "예상"으로 나간다. 축을 채우는 것보다
그 문구를 고치는 것이 맞는 해법이고, 이 단계의 범위 밖이다.

### 1.4 값을 만드는 자리 — 순수 함수 둘

새 Pydantic 모델을 `thesis/domain.py`에 둔다(`Subject` 옆). **`state.py`가 아니다** — 그 모듈은
"프롬프트에 들어가고 `input_state`에 저장되는 상태의 모양"이고 이 값은 둘 다 아니다.

```python
class ForecastBaseline(BaseModel):
    """예측의 축. 이 가격·이 시각에서 그날 정규장 마감까지가 확률과 크기의 창이다."""
    model_config = ConfigDict(frozen=True)
    price: Decimal
    at: AwareDatetime
    return_pct: Decimal
```

- `thesis/common.py`에 모듈 함수 `session_baselines(observed, session)` — 관측 상태의
  `IndexObservation.close`·`StockObservation.close`를 읽고 `at = close_at(session)`.
- `thesis/intraday.py`에 모듈 함수 `intraday_baselines(observed)` — `IntradayObservation`의
  `price`·`bar_at`·`return_pct`를 그대로 옮긴다.

감쌀 상태가 없어 둘 다 함수다(저장소 규칙). DB 없이 테스트된다.

### 1.5 CHECK

`f4b19c6ea283`의 `ck_thesis_outcome_grade_all_or_none` 형식을 그대로 따른다.

```
ck_thesis_base_all_or_none:
  (base_price IS NULL AND base_at IS NULL AND base_return_pct IS NULL)
  OR (base_price IS NOT NULL AND base_at IS NOT NULL AND base_return_pct IS NOT NULL)
ck_thesis_base_price_positive:
  base_price IS NULL OR base_price > 0
```

### 1.6 채점 — JSONB 캐내기는 대체하되 **지우지 않는다**

`select_pending_grades.sql`의 장중 기준가를 이렇게 바꾼다.

```sql
coalesce(
    thesis.base_price,
    (thesis.input_state #>> ARRAY['intraday', thesis.subject_code, 'price'])::numeric
) AS base_price
```

**JSONB 갈래를 남기는 이유:** 그 SQL에 날짜 상한이 없어(파일 머리말) 리비전 전에 저장된
장중 행의 미채점 지평 1·3·5가 계속 돌아온다. 컬럼만 읽으면 그 행들이 **조용히 영영 미채점**이
된다. 주석에 "리비전 전 미채점 행이 다 소진되면 이 갈래를 지운다"를 적는다.

부수 효과 하나: 지금까지 `PendingGrade.base_price`는 장전이면 NULL이었는데 이제 장전도 값이
온다. `review.py`의 `_horizon_return`이 `run_slot in INTRADAY_SLOTS`로 갈리므로 **동작은 안
바뀐다.** 다만 그 SQL 주석과 `store.py`의 `PendingGrade.base_price` docstring("장전 슬롯은 이
칸이 NULL이고")이 거짓이 되므로 함께 고친다.

**장전 채점의 축은 갈아끼우지 않는다.** 지금도 SQL이 직접 전일 종가를 찾고
(`index_bar/select_horizon_return.sql`은 마감봉의 `previous_close`,
`stock_investor_trade_daily/select_horizon_return.sql`은 LATERAL로 직전 거래일 종가) 그것이
맞다. 여기서 축을 바꾸면 **판 경계에서 채점 기준이 갈린다** — 크기 오차 표본이 이제 막 쌓이기
시작한 지금 그 일을 하면 그 표본을 읽을 수 없다. 다른 손잡이다.

> **선택(권장):** 장전 **지수** 경로에서 저장된 `base_price`와 채점 분모가 어긋나는지 로그로
> 감시한다. 종목은 원본이 같지만(`stock_investor_trade_daily.close_price`) 지수는 관측이
> "전 세션 15:30 봉의 close", 채점이 "당일 15:30 봉의 `previous_close`"로 원본이 갈린다.
> `select_horizon_return.sql`이 이미 `base_close`를 SELECT하므로 다르면 `logger.warning`만
> 남기면 된다. **채점 분모는 바꾸지 않는다.**

### 1.7 Slack 렌더

`thesis/render.py`의 `_thesis_section`에 줄 하나를 더하는 `_baseline_line(thesis) -> str | None`.

```
*코스피*
*▼ 하락 0.7% ±0.4%p 예상 (50%)*   *– 횡보 (16%)*
_12:35 KST 6,825.11 기준 · 오늘 여기까지 -1.26%_
> (하락 이유 …)
```

기준 줄은 장전이면 `_전일 종가 6,912.32 기준 (08-27 15:30 KST)_`다. 셋 중 하나라도 NULL이면
(리비전 전 행) 줄을 통째로 뺀다 — 지금 모양 그대로 떨어진다. 천 단위 쉼표와 시각 표기는
`modules/briefing/blocks`의 기존 헬퍼를 따른다. `±` 부분은 4.4절.

**예상 마감가는 찍지 않는다** — 프롬프트가 모델에게 금지한 목표가를 우리가 계산해서 내보내는
꼴이 된다.

### 1.8 API

`apps/api/schemas/thesis.py`의 `ThesisSummary`(→ `ThesisDetail`이 상속)에 축 세 칸과 오차 두
칸을 더한다. **목록에도 넣는다** — 소비자가 목록만 보고도 슬롯 규칙 없이 축을 읽어야 한다는
것이 요구 1이다. `description`에 축의 끝(그날 정규장 마감)과 NULL 조건을 적고,
`up_return_pct`·`down_return_pct`의 설명에 **"`base_price` 대비"**를 명시한다.

`service/thesis.py::summary_of`에 다섯 줄이 는다. 리포지토리는 ORM 엔티티를 그대로 넘기므로
`repository/thesis.py`는 **안 고친다.**

### 1.9 해설 모델도 축을 본다 (요구 1의 "다른 모델")

사후 해설(`FollowupNarrator`)이 바로 그 다른 모델이다. 지금 주는 줄은
`- **실제 결과**: -0.53% (down)` 하나뿐이라 **어느 축의 −0.53%인지 모른다.**

- `select_pending_narratives.sql` — 축 세 칸과 오차 스냅샷 추가
- `thesis/outcomes.py` — `NarrativeTarget`에 옵셔널 칸, `_render_target`에 한 줄:
  `- 기준: 12:35 KST 6,825.11 (직전 종가 대비 -1.26퍼센트) → 그날 마감까지`
- `NARRATIVE_PROMPT_VERSION`을 `"2"` → `"3"`. **YAML은 안 바뀐다** — 판 7·8이 같은 해시를
  공유한 전례대로 `PROMPT_HASHES`에 `("thesis_narrative", "3")` 키를 **같은 해시로** 더한다.

**생성 프롬프트는 이 단계에서 안 건드린다.** baselines는 `ObservedState`에 넣지 않고 별도
인자로 흐르므로 모델이 보는 글자가 그대로다 → `PROMPT_VERSION` 12 유지.

---

## 2. `macro_changes`에서 국내 지수를 뺀다

### 2.1 셋을 견주고 하나를 고른다

| 대안 | 판단 |
| --- | --- |
| **SQL에 국내 지수 제외 술어** | **채택.** 툴 설명("해외")과 코드가 비로소 일치하고, `select_thesis_us_close.sql`의 `country = 'US'`와 같은 모양이며, 정보 손실이 없다 |
| 국내만 `previous_close` 기준으로 계산 | 기각. 한 표에서 두 축이 섞인다 — **그것이 정확히 0.1절의 병이다.** 국내 행의 `first_close`·`bar_count`도 뜻을 잃는다 |
| 창 시작을 전일 종가로 옮긴다 | 기각. `macro_window_start`는 `us_market_close`가 **함께** 쓴다. 그 툴 설명이 "장후 슬롯의 창은 당일 09:00부터라 미국 세션이 창 밖이다"라고 의도적으로 적어 둔 것을 뒤집는다. 해외 심볼에는 지금 창이 옳다 |

**정보 손실이 없다**는 것이 결정적이다. 코스피·코스닥은 `INDEX_SUBJECTS`라 관측 상태가 이미
전일 종가 대비 등락을 준다. 툴이 주던 것은 같은 대상의 **틀린 축**이었다.

### 2.2 술어의 정확한 모양 — `country <> 'KR'`만으로는 **틀린다**

시드에서 **`USDKRW`·`JPYKRW`의 `country`가 `KR`이다**(2026-08-28 운영 DB 확인). 국가만으로
거르면 **원/달러가 사라진다** — 08-28 midday 추론이 실제로 인용한 근거다("원/달러 창 −0.28%
원화 강세"). 걸러야 할 것은 "정규장이 창 안(09:00)에서 시작해 개장 갭이 통째로 빠지는 것"이다.

```python
# `macro_changes`가 빼는 심볼. 국내 정규장은 창 안(09:00)에서 시작해 개장 갭이 통째로
# 빠진다 — 2026-08-27 실측: 코스피 창 변화 -1.15퍼센트, 전일 종가 대비 +1.53퍼센트로 부호가
# 뒤집혔다. 국내 지수는 관측 상태가 올바른 축으로 이미 준다.
DOMESTIC_COUNTRY = "KR"
DOMESTIC_SESSION_KINDS: tuple[str, ...] = ("index",)
```

**국내 지수선물은 빼지 않는다.** `KOSPI200_FUT`·`KOSDAQ150_FUT`는 **24시간 봉이 쌓인다**
(2026-08-28 확인: 08-27~28 이틀에 807봉, `bar_at`이 00:00Z부터 23:59Z까지 연속). 야간 세션이
09:00 개장을 이어 주므로 창 첫 봉과 개장 사이에 갭이 없다 — **이 왜곡을 안 탄다.** 환율도
같은 이유(24시간 호가)로 남긴다.

### 2.3 SQL과 툴

- `quote_bar/select_window_changes.sql` — 파일을 **named 파라미터로 바꾸고**(psycopg는 한
  문장에서 위치·이름을 섞지 못한다. 같은 디렉터리 `select_thesis_us_close.sql`과 맞춘다)
  술어를 더한다.

  ```sql
  WHERE symbol.kind = ANY(%(kinds)s)
    AND NOT (symbol.country = %(domestic_country)s AND symbol.kind = ANY(%(domestic_kinds)s))
  ```

  주석에 **퍼센트 기호를 쓰지 않는다**(`tests/modules/test_sql_placeholders.py`가 잡는다).
- `toolbox.py::_macro_changes` — 호출을 dict로 바꾼다.
- `TOOL_DESCRIPTIONS["macro_changes"]` — 두 군데. ⓐ "해외 지수·선물·환율"이 실제
  `MACRO_KINDS`(금리·채권선물·원자재·암호화폐 포함)를 안 덮으니 바로잡고, ⓑ **"국내 지수는
  여기 안 나온다 — 관측 상태가 전일 종가 기준으로 준다"**를 명시한다.

### 2.4 장전 창도 함께 조용해진다

`PreOpenForecast.macro_window_start()`는 전 개장일 15:30이고 술어가 `bar_at >= window_start`라
직전 세션 마감 봉이 창의 첫 봉으로 들어온다. 국내 지수 봉은 그 뒤로 없으므로
`first_close ≈ last_close` → 약 `+0.00%`. **거짓은 아니다**(장이 닫혀 있었다). 다만 관측
상태가 "코스피 −1.05%"라고 말하는 옆에 "코스피 +0.00%"가 놓이는 잡음이고, 같은 술어가
이것도 없앤다.

`PROMPT_VERSION` **12 → 13.** YAML은 안 바뀌므로 `PROMPT_HASHES`에
`("thesis_generation", "13")`을 **12와 같은 해시로** 더한다. 판을 가르는 이유는 툴이 돌려주는
행이 달라져 **모델이 보는 글자가 달라지기** 때문이다.

---

## 3. 크기 앵커 — 새 툴 `typical_move`

### 3.1 이름과 인자

"이 대상이 하루에 보통 얼마나 움직이나". 인자는 `symbol` 하나다.

**대상 목록 밖은 `ToolLimitExceeded`로 거절한다** — `past_theses`의 전례 그대로다. 크기 앵커는
예측 대상에만 뜻이 있고, SP500의 변동폭을 물어보게 두면 예산만 쓰고 엉뚱한 자산에
앵커링한다. 덤으로 "없는 심볼" 분기가 사라진다.

### 3.2 SQL은 새로 만들지 않는다 — 그 판단의 근거

`writing-llm-flows` 스킬은 "툴을 늘릴 때 조회 SQL은 새 파일로 만든다"고 하고, 근거로
**"브리핑은 지금까지를 보고 추론 툴은 기준 시각까지만 본다"**를 든다.

`technical_signal/select_unconditional_returns.sql`은 브리핑 쿼리가 아니다. 이미 `as_of_date`
컷오프와 look-ahead 방지를 갖고 있고 **이미 추론 경로가 매 실행 부른다**
(`thesis/common.py::flat_base_rate` → `base_rate.flat_base_rates` → 같은 파일). 두 소비자가
원하는 행이 글자 그대로 같고(심볼별 모든 거래일의 N거래일 뒤 등락률) 새 파라미터가 하나도
필요 없다. 파일을 복제하면 규칙이 막으려던 것과 **반대 방향의** 고장이 생긴다 — 컷오프·
look-ahead 규칙이 두 파일로 갈려 한쪽만 고쳐지는 날이 온다. **재사용하고 그 판단을 새 함수
docstring에 적는다.**

`quote_daily`가 코스피를 2016-08-16부터 2,460행 갖고 있어 250봉 창에 충분하다.

### 3.3 요약 함수 — `technical/base_rate.py`

```python
# 크기 앵커의 기준선 창(거래일). FLAT_BASE_RATE_BARS와 같은 값에서 출발하지만
# **다른 손잡이다** — flat 임계를 만지는 것이 크기 앵커를 조용히 움직이면 안 된다.
MOVE_SIZE_BARS = 250
# "지금 체제" 창. 기준선과 나란히 줘서 지금이 평소보다 큰 구간인지 모델이 읽는다.
RECENT_MOVE_BARS = 20
```

- 분류는 채점과 같은 함수(`classify_outcome`)를 쓴다.
- **표본이 `MIN_BASE_RATE_SAMPLE`(20) 미만이면 통계는 전부 `None`이고 `sample_size`만 준다.**
  0으로 채우지 않는다 — "재지 않았다"와 "0이다"는 다른 뜻이고 모델은 숫자가 보이면 쓴다.
- 백분위는 정렬 후 선형 보간하는 모듈 내 순수 헬퍼 하나. DB 없이 경계값을 테스트한다.

### 3.4 응답 모델 — `thesis/tools.py`

`state.py`가 아니라 `tools.py`다. 이 값은 프롬프트로만 나가고 `input_state` JSONB에는 안
들어가므로 `state.py`의 존재 이유에 해당하지 않는다.

```python
class MoveWindow(ToolModel):
    bars: int
    sample_size: int
    median_abs_pct: float | None
    p25_abs_pct: float | None
    p75_abs_pct: float | None
    p90_abs_pct: float | None
    up_days: int
    up_median_pct: float | None     # 오른 날만의 등락 중앙값 — up_return_pct의 짝
    down_days: int
    down_median_pct: float | None   # 내린 날만의 등락 중앙값 — down_return_pct의 짝

class TypicalMovePayload(ToolModel):
    symbol: str
    label: str
    as_of_date: date
    axis: str        # "직전 세션 종가 → 그 세션 종가(1거래일)"
    recent: MoveWindow    # RECENT_MOVE_BARS
    baseline: MoveWindow  # MOVE_SIZE_BARS
    note: str
```

**방향을 나눠 주는 것이 이 툴의 핵심이다.** `up_return_pct`가 "상승한다면 얼마"인 조건부
값이라 앵커도 조건부여야 짝이 맞는다. 무방향 중앙값 하나만 주면 모델이 또 어림한다.

**`p25`가 있는 이유는 4절이다.** `p75 − p25`의 절반이 오차 폭의 출발점이다. p90만으로는
"평소 흩어짐"을 못 재고 꼬리만 본다.

**`axis`와 `note`가 값과 함께 다닌다** — 1절의 원칙을 툴 응답에도 적용한다. `note`는 못 재는
것을 명시한다:

> 장중 잔여 구간(그 슬롯 가격 → 마감)의 실현 분포는 **주지 않는다.** 분봉 이력이 짧아
> (`index_bar`의 코스피가 2026-08-18부터 9거래일) 표본이 `MIN_BASE_RATE_SAMPLE`에 못 미친다.
> 위 값은 **하루 전체** 등락이다.

**없는 표본으로 숫자를 지어내지 않는다.** 잔여 구간 분포는 분봉이 20거래일을 넘긴 뒤
(현재 속도로 2026년 9월 중순) 두 번째 블록으로 더한다 — 조건과 함께 6절에 남긴다.

### 3.5 툴 설명

크기를 쓰기 전에 부르는 기준선이라는 것, 방향별로 나눠 준다는 것, `sample_size`가 모자라면
`null`이라는 것("재지 않았다"이지 0이 아니다), **장중 잔여 구간은 주지 않는다**는 것을 담는다.

### 3.6 호출 예산

툴이 14 → 15개가 되고 대상마다 한 번씩 부르면 최대 4회가 는다. `MAX_TOOL_CALLS`가 32이고
현실적 조사 실측이 26호출이었다(`TUNING.md` 5절) → 30. 여유 2는 **앵커가 상한에 걸려 못
불리는** 사고를 부른다. **`MAX_TOOL_CALLS`를 36으로 올린다**(주석에 26+4+여유의 근거).
이 상수는 어떤 `Field(description=...)`에도 안 실려 프롬프트 글자를 바꾸지 않는다.
`MAX_TOOL_RESULT_CHARS`(250,000)는 그대로 둔다 — 호출당 평균 4,767자 × 36 ≈ 172,000이다.

---

## 4. 크기에 ± 오차를 붙인다

### 4.1 결정: 방향마다 폭 한 칸. 하한·상한 두 칸이 아니다

| 컬럼 (`thesis`) | 타입 | 뜻 |
| --- | --- | --- |
| `up_return_band_pct` | `Numeric(5, 2)` nullable | `up_return_pct`의 **± 폭**(퍼센트포인트, 양수) |
| `down_return_band_pct` | `Numeric(5, 2)` nullable | `down_return_pct`의 ± 폭 |

**하한·상한 네 칸이 아닌 이유:** 중심값이 이미 `*_return_pct`에 있어 하한·상한을 두면 셋 중
하나가 남는 값이 되고, 셋이 서로 어긋날 수 있는 상태가 생긴다(`low <= mid <= high`를 CHECK로
지켜야 한다). 폭 한 칸이면 구간은 언제나 `mid ± band`이고 어긋날 방법이 없다. 화면도
`0.7% ±0.4%p`가 `0.3~1.1%`보다 "중심이 어디인가"를 바로 말한다.

**퍼센트포인트다.** `up_return_pct`가 퍼센트라 그 오차는 퍼센트포인트이고, 컬럼 주석과 API
설명에 그것을 적는다. 화면 표기도 `%p`로 갈라 쓴다 — `0.7% ±0.4%`라고 쓰면 "0.7의 0.4%"로
읽힌다.

**대칭이다.** 비대칭 오차(위로 +0.6, 아래로 −0.2)는 분포를 하나 더 발명하는 것이고, 채점도
새로 정해야 한다. 크기 자체가 이제 막 표본을 쌓기 시작한 값이라 거기까지 가지 않는다.

### 4.2 CHECK

```
ck_thesis_return_band_range:
  (up_return_band_pct IS NULL OR up_return_band_pct BETWEEN 0 AND 30)
  AND (down_return_band_pct IS NULL OR down_return_band_pct BETWEEN 0 AND 30)
ck_thesis_return_band_needs_center:
  (up_return_band_pct IS NULL OR up_return_pct IS NOT NULL)
  AND (down_return_band_pct IS NULL OR down_return_pct IS NOT NULL)
```

- 상한 30은 `MAX_EXPECTED_RETURN_PCT`와 같은 폭주 안전망이다. "중심보다 크면 안 된다" 같은
  정합성은 **CHECK가 아니라 저장 전 검증이 본다** — DB로 막으면 모델이 경계값을 낼 때 행
  전체가 사라진다([11-expected-return.md](11-expected-return.md) 1절의 판단 그대로).
- 둘째 CHECK는 중심 없는 오차를 막는다. 반대(중심만 있고 오차가 없는 것)는 **허용한다** —
  리비전 전 행과 모델이 오차만 규칙을 어긴 경우다.

### 4.3 저장 전 검증 — 어긴 칸만 버린다

`generation.py`의 `normalize_return_pct` 옆에 `normalize_band_pct`를 둔다. 버리는 조건:

- `0` 이하 — 폭이 없다는 것은 "정확히 맞힌다"는 뜻이라 거짓이다.
- `MAX_EXPECTED_RETURN_PCT` 초과 — 폭주. **자르지 않는다**(clamp하면 상한이 거짓 신호가 된다).
- **중심보다 크다** — `mid ± band`의 하한이 0 아래로 내려가 방향이 뒤집힌다.

**중심은 살리고 오차만 버린다.** 기존 규칙("크기 하나가 규칙을 어겨도 추론은 살린다")과 같은
자리이고, 어긴 값은 `logger.warning`으로 남긴다.

### 4.4 렌더

`_verdict_label`을 고친다. 지금 `*▼ 하락 0.7% 예상 (50%)*` → `*▼ 하락 0.7% ±0.4%p 예상 (50%)*`.

- **오차가 NULL이면 지금 문구 그대로 떨어진다.** 리비전 전 행과 판 13까지의 행이 그 모양이다.
- `flat`에는 붙이지 않는다. 크기를 안 붙이는 것과 같은 이유다.
- 소수 첫째 자리까지. 중심과 같은 자리수다.

### 4.5 채점 — "얼마나 벗어났나"에 "벗어날 줄 알았나"를 더한다

`thesis_outcome`에 칸 하나.

| 컬럼 | 타입 | 뜻 |
| --- | --- | --- |
| `predicted_band_pct` | `Numeric(5, 2)` nullable | 실현 방향에 대응하는 오차 폭 **스냅샷**. `thesis`에서 골라 온 값 |

**적중 여부 칸은 두지 않는다.** `abs(return_error_pct) <= predicted_band_pct`가 답이고, 두 칸이
이미 행에 있다. 파생값을 저장하면 어긋날 자리가 생긴다(`return_error_pct`를 MAE로 저장하지
않은 것과 같은 판단).

`domain.return_error`가 튜플 셋을 돌려주도록 넓힌다 — `(predicted, error, band)`. `band`가
`None`인 경우(옛 행, 모델이 규칙을 어긴 경우)를 그대로 통과시킨다.

CHECK 하나 더:

```
ck_thesis_outcome_band_needs_prediction:
  predicted_band_pct IS NULL OR predicted_return_pct IS NOT NULL
```

### 4.6 무엇을 보나 — ops 브리핑

`briefing/ops.py`의 `THESIS_CALIBRATION` 지평 0 행에 칸 하나가 는다.

```
크기 오차: 평균 +0.42%p (과소, n=37)   밴드 적중: 24/37 (65%)
```

**적중률이 크기 오차와 다른 것을 잰다.** 오차 평균은 "중심이 어디로 치우쳤나"이고 적중률은
"모델이 자기 불확실성을 아는가"다. 잘 보정된 모델은 **적중률이 지나치게 높아도 문제다** —
95%면 폭을 너무 넓게 불러 구간이 아무 것도 말하지 않는다는 뜻이다. 목표대는 `TUNING.md`에
손잡이로 적는다(초안: 60~80%. **실측이 아니라 출발점이고 4주 뒤 분포를 보고 조정한다**).

### 4.7 프롬프트

`## 크기` 절에 오차를 받는 규칙을 더한다.

- `up_return_band_pct`·`down_return_band_pct`는 **중심값의 ± 폭**이지 상한이 아니다. 양수이고
  중심값보다 작아야 한다.
- **출발점은 `typical_move`의 흩어짐이다.** `baseline`의 `p75_abs_pct − p25_abs_pct`의 절반이
  "평소 이만큼 흩어진다"이고, 그날 근거가 특별히 강하면 좁히고 약하면 넓힌다.
- **좁히려면 근거가 있어야 한다.** 근거 없이 좁은 폭을 쓰는 것은 아는 척이다. 반대로 폭이
  중심값에 가깝게 넓으면 그 예측은 아무 것도 말하지 않는다 — 그때는 `prob_flat`을 올리는
  것이 맞다.
- 출력 형식 예시에 두 칸을 더한다.

### 4.8 이 칸들은 언제 실전에 나오나

**리비전은 1절·2절과 같은 배포로 올린다**(칸이 전부 nullable이라 lock 창을 두 번 잡을 이유가
없다). 값은 프롬프트가 오차를 요구하는 판(C)부터 채워진다. 그 사이에 만들어지는 행은 오차가
빈 채로 남고, 렌더가 지금 문구로 떨어질 뿐이다.

---

## 5. 순서와 배포

| 순서 | 무엇 | 제약 |
| --- | --- | --- |
| **A1** | 수기 리비전 — `thesis`에 축 셋 + 오차 둘, `thesis_outcome`에 오차 스냅샷 하나, CHECK 다섯 | 컬럼만 늘고 아무도 안 읽는다. **구 코드와 완전 호환** |
| **A2** | 모델·`insert.sql`·`select_by_run.sql`·`store`·`common`·`forecast`·`intraday`·`review`·`nxt_review`·렌더·API | **A1 필수.** `insert.sql`이 없는 컬럼을 쓰면 첫 저장에서 죽는다 |
| **A3** | `select_pending_grades.sql` COALESCE + 주석 셋 갱신 | A2와 같은 배포도 되고 뒤도 된다 |
| **A4** | 해설 축 줄 + `NARRATIVE_PROMPT_VERSION` 3 | A2 뒤 |
| **B** | `macro_changes` 술어 + 상수 + 툴 설명 + `PROMPT_VERSION` 13 | 스키마 변경 없음. A와 무관 |
| **C** | `move_sizes` + `typical_move` + `## 크기` 절(앵커 + 오차) + 오차 검증·채점·ops 칸 + `PROMPT_VERSION` 14 + `MAX_TOOL_CALLS` 36 | **A1·B 뒤.** 판을 갈라야 어느 쪽 효과인지 읽힌다 |

- **마이그레이션은 언제나 코드보다 먼저.** A1 → A2가 강제 순서이고, C의 오차 저장도 A1을 탄다.
- 리비전은 `ADD COLUMN ... NULL` 여섯이라 메타데이터만 바꾸지만 `ACCESS EXCLUSIVE` lock을
  잡는다. 추론 DAG 넷이 평일만 도므로 **주말이 가장 쉽다**
  ([11-expected-return.md](11-expected-return.md) 7절과 같은 판단).
- **오차 칸을 A1에 함께 넣는 이유**는 lock 창을 두 번 잡지 않기 위해서다. C가 값을 채우기
  전까지 그 칸은 NULL이고 아무 것도 안 깨진다.
- **롤백:** A2를 되돌려도 컬럼은 남고 NULL로 채워진다(불변 테이블이라 문제 없다). B·C는
  코드만이라 커밋 되돌리기로 끝난다.
- **B와 C를 같은 배포에 넣지 않는다.** `TUNING.md` 1절 "한 번에 한 손잡이"다. C 안에서 앵커와
  오차가 한 판에 묶이는 것은 받아들인다 — 오차의 출발점이 앵커의 분위수라 둘을 뗄 수 없다.
- C는 **스크래치패드에서 실제 모델 출력을 먼저 본다.** 새 툴을 붙인 프롬프트로 08-28 장전
  상태를 재현해 크기와 오차가 어떻게 나오는지 보여 준 뒤 구현한다.

## 6. 안 하는 것

- **장중 잔여 구간의 실현 분포.** 분봉이 9거래일뿐이다. 20거래일을 넘기면 `typical_move`에
  슬롯별 창을 더한다.
- **비대칭 오차와 분위수 예측(P10/P50/P90).** 대칭 폭 하나가 지금 모델이 근거를 댈 수 있는
  한계다. 분위수는 채점 방법(pinball loss)까지 새로 정해야 한다
  ([11-expected-return.md](11-expected-return.md) 5절이 같은 이유로 미뤄 둔 것이다).
- **밴드 적중 여부를 컬럼으로 저장하기.** 두 칸에서 유도된다(4.5절).
- **축 종류 enum 칸.** `run_slot`과 `base_at`이 이미 그것을 말한다(1.2절).
- **장전 채점의 축 교체.** 크기 오차 표본이 판 경계에서 갈린다(1.6절). 로그 감시만 둔다.
- **크기·기준가·오차의 되채우기.** `thesis` 행은 불변이다.
- **`PastThesis`·`SameDayThesis`에 기준가나 오차를 새로 싣기.** 자기 과거 숫자에 앵커링한다.
- **`ObservedState`에 변동폭 칸도 baseline 칸도 넣지 않는다.** 사용자 결정이고, `input_state`
  JSONB 모양이 그대로여야 1.6절의 COALESCE 하위 호환이 산다.
  [11-expected-return.md](11-expected-return.md) 2절의 "관측 상태에 변동폭 칸을 새로 만들지
  않는다"는 **여전히 참이다** — 칸이 아니라 툴을 만들었다.
- **`us_market_close`와 `macro_window_start`를 건드리지 않는다.**
- **국내 지수선물·환율을 `macro_changes`에서 빼지 않는다.** 24시간 봉이라 이 왜곡을 안 탄다
  (2.2절 실측).
- **`_change_label`에 창을 적기.** 국내 지수를 빼고 나면 남는 것은 전부 "창 동안"이 맞는
  값이다. 오독이 다시 관측되면 그때 한다.

## 7. 산출물

| 파일 | 무엇 |
| --- | --- |
| `apps/models/analysis/thesis.py` | `Thesis`에 축 셋 + 오차 둘, `ThesisOutcome`에 오차 스냅샷, CHECK 다섯 |
| `migrations/versions/<신규>.py` | 수기 리비전. `f4b19c6ea283_add_thesis_expected_return.py`를 형식 그대로 |
| `airflow/sql/postgres/thesis/insert.sql`·`select_by_run.sql` | 칸 다섯 |
| `airflow/sql/postgres/thesis_outcome/*` | COALESCE, 채점 upsert에 오차 스냅샷, 해설 조회에 축 |
| `airflow/modules/thesis/domain.py` | `ForecastBaseline`, `DOMESTIC_*` 둘, `return_error` 확장, `PROMPT_VERSION` 두 번, `MAX_TOOL_CALLS` |
| `airflow/modules/thesis/generation.py` | `ThesisAnswer`·`ThesisDraft` 오차 두 필드, `normalize_band_pct`, `## 크기` 절 자리표시자 |
| `airflow/modules/thesis/store.py` | `StoredThesis`·`PendingGrade` 갱신, `store_theses(..., baselines=...)` |
| `airflow/modules/thesis/common.py`·`forecast.py`·`intraday.py`·`review.py`·`nxt_review.py` | baselines를 만들어 넘긴다 |
| `airflow/modules/thesis/render.py` | `_verdict_label`의 `±`, `_baseline_line` |
| `airflow/modules/thesis/outcomes.py` | 해설 기준 줄, `NARRATIVE_PROMPT_VERSION` |
| `airflow/modules/briefing/ops.py` | `THESIS_CALIBRATION`에 밴드 적중 칸 |
| `apps/api/schemas/thesis.py`·`service/thesis.py` | 칸 다섯과 축·오차 설명 |
| `airflow/modules/thesis/tools.py` | `MoveWindow`·`TypicalMovePayload` |
| `airflow/modules/thesis/toolbox.py` | `typical_move` 등록·인자·설명, `macro_changes` 호출 dict화와 설명 |
| `airflow/modules/technical/base_rate.py` | `MOVE_SIZE_BARS`·`RECENT_MOVE_BARS`·`move_sizes` |
| `airflow/sql/postgres/quote_bar/select_window_changes.sql` | named 파라미터 + 국내 지수 제외 |
| `airflow/modules/prompts/thesis_generation.yaml` | `## 크기` 절 교체(앵커 + 오차) |
| `docs/analysis/market-thesis/TUNING.md` | 밴드 적중률 손잡이 한 줄 |

## 8. 테스트

**DAG를 돌려 검증하지 않는다.** pytest · ruff · 오프라인 SQL까지다.

| 변경 | 잡는 테스트 |
| --- | --- |
| 칸 여섯·CHECK 다섯·주석 | `tests/migrations/test_thesis_schema.py`(`head_sql` 기반), `tests/models/test_analysis_models.py` |
| `insert.sql` 컬럼 누락 | 기존 대조 테스트가 `Thesis` metadata와 자동으로 맞춘다 |
| baselines 순수 함수 둘 | `tests/modules/test_thesis_common.py`(`return_pct=0`, `close_at(session)`), `tests/modules/test_thesis_pipeline.py`(`bar_at`·`return_pct` 그대로) |
| 오차 검증 경계값 | `tests/modules/test_thesis_pipeline.py` — 0 이하·상한 초과·**중심보다 큰 폭**이 그 칸만 버려지고 중심은 남는다 |
| 저장 경로 | 같은 파일 — 파라미터 끝에 다섯이 실린다 |
| Slack 렌더 | 같은 파일 — 오차가 있으면 `±0.4%p`가 붙고, NULL이면 **글자 그대로 지금과 같은** 블록을 낸다(회귀). 기준 줄도 같은 규칙 |
| 채점 하위 호환과 오차 스냅샷 | `tests/modules/test_thesis_review.py` — 옛 장중 행이 여전히 채점되고, 오차가 없는 행은 `predicted_band_pct`가 NULL |
| 밴드 적중 계산 | `tests/modules/test_briefing_ops.py` — 경계값(`abs(error) == band`)이 적중이다 |
| API | `tests/api/conftest.py`의 픽스처 + `tests/api/test_routes.py` |
| 해설 축 줄과 판 | `tests/modules/test_thesis_review.py`, `tests/modules/test_prompt_versions.py`(`("thesis_narrative", "3")`, 해시는 2와 같다) |
| `macro_changes` 술어 | `tests/modules/test_thesis_pipeline.py` — 국내 **지수**가 빠지고 **환율·국내 지수선물은 남는다** |
| SQL 주석의 퍼센트·플레이스홀더 | `tests/modules/test_sql_placeholders.py`(전 SQL 자동 스캔) |
| 크기 통계 | `tests/modules/test_base_rate.py` — 표본 < 20이면 전부 `None`, 상승·하락 분리, p25·p75·p90 손계산 대조 |
| `typical_move` 툴 | `tests/modules/test_thesis_pipeline.py` — 응답 모양, 대상 목록 밖이면 `ToolLimitExceeded` |
| 판 둘 | `tests/modules/test_prompt_versions.py` — `("thesis_generation", "13")`은 12와 같은 해시, `"14"`는 새 해시 |
| import 무게 | `tests/modules/test_import_weight.py` |
| DagBag | `tests/dags/test_market_thesis_forecast.py`·`test_market_thesis_intraday.py`·`test_market_thesis_review.py` |

전 단계 공통:

```
uv run pytest tests -q
uv run ruff check apps airflow migrations tests
```

**운영 DB 읽기 전용 확인 한 번:** C는 새 SQL 파일이 없지만 새 소비 방식이다.
`select_unconditional_returns.sql`을 `symbols=['KOSPI','KOSDAQ','005930','000660']`,
`horizons=[1]`로 한 번 돌려 250봉 창의 `sample_size`가 20을 넘는지, 상승/하락 분리 개수와
`p75 − p25`가 그럴듯한지 본다(이미 잰 코스피 중앙값 1.53%, p90 5.73%와 대조된다).

**마이그레이션을 운영에 직접 적용하지 않는다** — 반영은 사용자가 한다.
