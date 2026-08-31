# 19단계 — 애프터마켓 리뷰에 사후 해설을 붙인다 (`post_nxt_close` → 해설 루프)

- 상위: [README.md](README.md)
- 날짜: 2026-08-31
- 상태: **구현 완료**(2026-08-31, 브랜치 `worktree-feature-nxt-narration`). 마이그레이션 없음 —
  운영 반영은 배포뿐이다. 검증은 `uv run pytest tests -q`(2,853건),
  `uv run ruff check apps airflow migrations tests`, `uv run pyrefly check`(0 errors).
  **7절의 착수 게이트(18단계 2절의 5영업일 관측)는 기다리지 않았다**(사용자 결정,
  2026-08-31). 되돌릴 자리가 상수 둘이라 관측 결과가 반대로 나와도 값이 싸다 —
  그래도 4주 뒤 `verdict` 분포는 그대로 본다(7절)
- 의존: [5-followup.md](5-followup.md)(해설 루프와 `FollowupNarrator`),
  [7-nxt-review.md](7-nxt-review.md)(`post_nxt_close` 슬롯 — **이 단계가 그 3절을 뒤집는다**),
  [9-intraday.md](9-intraday.md)(슬롯 목록 파라미터화 — 그래서 SQL을 안 고친다),
  [13-llm-ledger.md](13-llm-ledger.md)(비용 실측의 원본),
  [16-narration-model.md](16-narration-model.md)(해설 모델과 대상당 단가),
  [18-nxt-precedent.md](18-nxt-precedent.md)(이 슬롯을 읽는 소비자를 만든 단계)
- 산출물: `thesis/state.py`의 `NARRATED_SLOTS`·`PRECEDENT_SLOTS` 정의 둘, SQL 머리말 셋
  (`select_pending_narratives.sql`·`select_backlog.sql`·`select_past_with_outcomes.sql`),
  낡은 docstring 넷(`thesis/store.py`·`thesis/nxt_review.py`·`market_thesis_nxt_review` DAG·
  `apps/models/analysis/thesis.py`의 `ThesisOutcome`), 테스트 셋과 뒤집힌 단언 둘,
  7·18단계 문서의 포인터. **SQL 본문·프롬프트·리비전은 없다**(2절).

## 0. 문제 — 다음 추론이 읽는 해석 하나에만 검증이 없다

18단계가 애프터마켓 리뷰를 다음날 장전의 재료로 이었다. 그래서 지금 장전 프롬프트의
"과거 추론과 결과" 절에는 되돌아보는 슬롯 셋이 함께 들어온다. 그런데 셋 중 하나만 검증이
붙지 않는다.

| 슬롯 | 채점 | 사후 해설 | 장전 프롬프트에 실리나 | `outcomes` 배열 |
| --- | --- | --- | --- | --- |
| 예측 슬롯(`pre_open`·장중) | O | O | O | 채점 + 해설 |
| `post_close` | X | O | O | 해설만 |
| `post_nxt_close` | X | **X** | O (18단계) | **영영 빈 배열** |

`select_past_with_outcomes.sql`이 `LEFT JOIN thesis_outcome`이라 행은 오고 배열만 빈다
(18단계 2.2절). 그 빈 배열이 "아직 T+1이 안 왔다"가 아니라 **"이 슬롯은 영원히 안 채워진다"**
라는 것이 지금 상태다.

프롬프트는 과거 추론 절에서 "**해설도 사실이 아니라 그때의 해석이다.** 같은 이유로 같은
방향을 고르고 있다면 그 이유가 이번에도 맞는지 따로 확인하라. 과거 문장을 베끼지 마라"고
말한다. `verdict`가 붙은 행에서는 그 경고가 실물을 갖는다 — 모델이 `contradicted`를 눈으로
본다. NXT 행에서는 경고만 있고 실물이 없다.

사용자 질문(2026-08-31)이 이것이었다 — "다음 추론에 사용된다면 NXT 마감도 채점 및 해설을
받아야 할 것 같은데, 채점이 불필요하다면 해설이라도 받아야 하지 않나."

### 0.1 처음 뺀 이유는 값어치 판단이 아니라 구현 결함이었다

연혁이 셋이고, **셋 다 지금은 유효하지 않다.**

| 날짜 | 무엇을 정했나 | 지금 |
| --- | --- | --- |
| 2026-08-22 ([7-nxt-review.md](7-nxt-review.md) 3절) | 해설 대상에서 뺀다 | **이유가 사라졌다**(아래) |
| 2026-08-23 ([7-nxt-review.md](7-nxt-review.md) 9절 취소선) | 그 이유였던 결함을 고쳤다 | 유효 |
| 2026-08-31 ([18-nxt-precedent.md](18-nxt-precedent.md) 4절) | 이번에도 안 붙인다 | 이 문서가 다시 본다 |

7단계가 뺀 이유는 "애프터마켓 해석은 검증할 값어치가 없다"가 **아니었다.** 당시
`thesis.review.narrate_followups`가 `run_slot=RunSlot.PRE_OPEN`을 하드코딩해 넘기고
`FollowupNarrator.build_messages`가 `"전" if run_slot is RunSlot.PRE_OPEN else "후"`라는 이진
분기였다. 새 슬롯을 해설 대상에 넣으면 **어젯밤 애프터마켓 리뷰가 "장전에 쓴 추론"으로
라벨링돼 프롬프트가 거짓말을 한다.** 그래서 첫 컷에서 SQL에 슬롯 목록을 걸어 뺐고, 9절에
"나중에 붙이려면 `NarrativeTarget`에 `run_slot`을 싣고 슬롯별로 호출을 나눠야 한다"고
조건부로 적었다.

그 결함은 다음 날 풀렸다(2026-08-23). 지금은 `NarrativeTarget`이 `run_slot`을 들고
`narrate_followups`가 (지평, 슬롯)마다 호출을 나누며, `FollowupNarrator.run`은 대상에서 슬롯을
읽고 섞이면 `ThesisError`다. `SLOT_LABELS`에는 `POST_NXT_CLOSE: "애프터마켓 리뷰"`가 이미
있다. 7단계 9절이 그 취소선 항목에 **"이제 이 슬롯을 해설 루프에 넣으려면 두 SQL의 슬롯
목록에 `post_nxt_close`를 더하면 된다"**고 적어 두었다.

18단계가 그때 안 붙인 이유는 값어치 판단이 아니라 **되돌리기 쉬운 것만 골랐기 때문이다**
(2.5절 — "효과가 없으면 되돌리고 끝이다. 리비전도 데이터 이관도 없다"). 리뷰 본문은 해설이
없어도 프롬프트에 실리므로 그 단계의 질문(다음날 장전이 애프터를 보는가)은 해설 없이 답이
났다. 4절에도 "이 단계의 질문과 별개"라고만 적혀 있다.

**막고 있던 것은 지금 없다.** 남은 질문은 하나다 — 해설이 값어치가 있나.

## 1. 판단 — 해설은 붙이고 채점은 계속 안 붙인다

### 붙이는 근거 셋

1. **`post_close`에 해설을 붙인 논리가 글자 그대로 적용된다.** 그 근거는 "장후 리뷰는
   '오늘 이래서 움직였다'는 인과 주장이라 며칠 뒤 보도로 검증할 값어치가 오히려 크다"였다
   (`apps/models/analysis/thesis.py`의 `ThesisOutcome` docstring). NXT 리뷰의 "정규장이 닫힌
   뒤 이 재료가 나와서 애프터마켓이 이렇게 움직였다"도 같은 형태의 인과 주장이다.
2. **애프터마켓이 해석이 틀리기 쉬운 자리라 검증의 값어치가 더 크다.** 18단계 0.1절 실측에서
   애프터 방향이 다음날 정규장 종가로 이어지는 것은 56%(±1% 이상 날 63%)다. 프롬프트가
   그 성격을 라벨로 달아 뒀지만(18단계 2.3절) 라벨은 일반론이고 `verdict`는 그날 그 종목의
   해석에 붙는 판정이다.
3. **7단계 때 없던 소비자가 생겼다.** 그때는 이 리뷰를 읽는 것이 Slack뿐이라 해설을 붙여도
   읽는 코드가 없었다. 18단계 뒤로는 다음날 장전이 읽는다 — 0절의 빈 배열이 그 소비자가
   보는 자리다.

### 채점을 안 붙이는 근거 (7단계 3절 그대로)

리뷰 슬롯도 형식상 확률 셋을 낸다 — 저장 스키마와 프롬프트 스키마가 요구한다. 하지만 그것은
**이미 일어난 애프터마켓 움직임**에 대한 확률이다. 답을 아는 자리에서 낸 확률에 Brier를
매기면 판(版) 비교가 오염된다. `post_close`와 같은 사정이고, `FORECAST_SLOTS`가 그 경계를
지킨다(`select_pending_grades.sql`이 그 목록을 파라미터로 받는다).

**채점 축도 그대로다.** 18단계 1절이 정한 대로 채점의 분모·분자는 정규장이고, NXT 종가로
채점하지 않는다.

## 2. 바꾸는 자리 — 상수 둘이다

7단계 3절은 "두 SQL에 슬롯 목록을 **리터럴**로 건다"고 적었지만, **9단계가 그 둘을 파라미터로
바꿨다**(장중 슬롯이 예측이라 목록이 코드에서 자라야 했다). 그래서 지금 원본은
`thesis/state.py`의 상수 하나이고, 부르는 쪽 둘이 그것을 넘긴다.

- `ThesisStore.pending_narratives` — `cursor.execute(PENDING_NARRATIVES, (horizon_days, run_date, list(NARRATED_SLOTS)))`
- `briefing/ops.py` — `cursor.execute(THESIS_BACKLOG, (..., list(FORECAST_SLOTS), list(NARRATED_SLOTS), today))`

**두 SQL이 같은 목록을 봐야 한다**는 7단계 3절의 제약은 상수가 하나이므로 자동으로 지켜진다.
어긋나면 한쪽은 해설을 안 만들고 다른 쪽은 그것을 밀림으로 세서 ops 브리핑이 매일 거짓
경보를 낸다.

| 자리 | 바꾸는 것 |
| --- | --- |
| `thesis/state.py`의 `NARRATED_SLOTS` | `RunSlot.POST_NXT_CLOSE`를 더한다. 주석의 "애프터마켓은 빠져 있다(`7-nxt-review.md` 3절)"를 이 문서로 바꾼다 |
| `thesis/state.py`의 `PRECEDENT_SLOTS` | 파생 정의(`(*NARRATED_SLOTS, POST_NXT_CLOSE)`)가 중복이 되므로 **리터럴로 적는다.** 두 목록이 지금 같다는 것과 왜 이름을 남기는지를 주석에 남긴다 |
| `thesis_outcome/select_pending_narratives.sql` 머리말 | "**post_nxt_close는 뺀다**(2026-08-22)" 문단을 교체한다. 파일이 슬롯 목록을 파라미터로 받는 것은 그대로다 |
| `thesis_outcome/select_backlog.sql` 머리말 | `unnarrated` FILTER가 같은 목록을 본다는 주석 유지. 슬롯 하나가 늘었다는 것만 반영 |
| `thesis/select_past_with_outcomes.sql` 머리말 | "`post_nxt_close`는 채점도 해설도 없어 `outcomes`가 빈 배열" → 해설은 찬다. 두 상수의 값이 같아졌다는 것도 여기 적는다 |
| 낡아진 docstring 넷 | `ThesisStore.past_theses`, `thesis/nxt_review.py` 모듈, `market_thesis_nxt_review` DAG, `apps/models/analysis/thesis.py`의 `ThesisOutcome`. 넷 다 "채점도 해설도 없다"를 사실로 적고 있었다 |
| `7-nxt-review.md` 3·9절, `18-nxt-precedent.md` 4절 | 이 문서를 가리킨다 |

**DAG의 태스크는 늘지 않는다.** 해설은 `market_thesis_nxt_review`(21:00)가 아니라 며칠 뒤
`market_thesis_review`의 `narrate_followups`가 붙인다 — 판정에 필요한 보도가 그때 쌓인다.

**동작 코드 변경은 없다.** `narrate_followups`는 이미 `for run_slot in RunSlot`으로 순회하며
그 슬롯의 대상만 묶어 호출한다. `FollowupNarrator`도 슬롯을 대상에서 읽는다.
**리비전도 없다** — `thesis_outcome`은 슬롯을 모른다(슬롯은 `thesis`에 있다).

### 2.1 두 목록이 값으로 같아진다

18단계 2.1절이 목록을 둘로 나눈 이유는 값이 달라서가 아니라 **뜻이 둘이어서**였다 —
"해설을 받는 슬롯"과 "장전이 되돌아보는 슬롯". 이 단계 뒤에는 값이 같아진다.

그래도 **상수는 둘로 남긴다.** 하나로 합치면 다음에 어느 한쪽만 늘려야 할 때(예: 해설은
붙이지 않되 되돌아보게만 하고 싶은 슬롯이 또 생길 때) 18단계가 푼 문제를 다시 푼다. 대신
파생 정의는 지운다 — `(*NARRATED_SLOTS, POST_NXT_CLOSE)`는 중복 항목을 만든다.

```python
# 사후 해설을 받는 슬롯. 애프터마켓 리뷰는 19단계에서 들어왔다.
NARRATED_SLOTS: tuple[RunSlot, ...] = (*FORECAST_SLOTS, RunSlot.POST_CLOSE, RunSlot.POST_NXT_CLOSE)

# 장전·장중이 프롬프트에 되돌아보는 슬롯. **지금 위와 값이 같다.** 이름을 남기는 이유는
# 뜻이 둘이기 때문이다(`18-nxt-precedent.md` 2.1절) — 해설을 안 받되 되돌아보기만 할
# 슬롯이 생기면 여기만 는다.
PRECEDENT_SLOTS: tuple[RunSlot, ...] = (*FORECAST_SLOTS, RunSlot.POST_CLOSE, RunSlot.POST_NXT_CLOSE)
```

## 3. 해설이 실제로 무엇을 받게 되나

- **대상은 종목뿐이다.** NXT에 지수가 없어 리뷰 대상 자체가 종목 둘이다(7단계). 하루에
  느는 것은 지평 3 × 종목 2 = **6행**이고 LLM 호출은 **3**(지평마다 하나, 슬롯이 하나라
  슬롯 분리로는 안 는다).
- **채점 값이 없어 `informed` 변형이 실을 것이 없다.** `_render_target`이 "실제 결과"·"크기
  예측" 줄을 생략한다. `post_close`가 이미 같은 처지라 **새 코드가 없다.**
- **그래서 이 슬롯 표본을 변형 비교에 섞지 않는다.** `informed`/`blind`가 이 슬롯에서는 같은
  프롬프트다. 판은 `4/informed`로 그대로 저장되지만, 5단계 12절의 변형 비교는 채점이 붙는
  슬롯만 본다.
- **프롬프트 판을 올리지 않는다.** `thesis_narrative.yaml`의 문장이 안 바뀐다 —
  `$slot_label`이 `SLOT_LABELS`에서 "애프터마켓 리뷰"를 받고, 시스템 프롬프트의 판정 규칙은
  슬롯을 모른다. `NARRATIVE_PROMPT_VERSION`은 4 그대로이고
  `tests/modules/test_prompt_versions.py`의 해시도 그대로다.
- **ops 브리핑의 판정 분포가 이 슬롯을 포함하게 된다.** `select_calibration.sql`은 지평으로만
  묶고 슬롯을 안 보므로 `narrated`·`supported`·`contradicted`·`unresolved` 넷이 함께 는다.
  `mean_brier`는 `evaluated_at IS NOT NULL`만 세니 그대로다 — **판정 분포와 Brier의 표본이
  달라진다는 것을 읽는 사람이 알아야 한다.** 슬롯별로 갈라 보고 싶어지면 그때 조회에 축을
  더한다(지금 더하면 아무도 안 보는 칸이 는다).
- **해설의 기준 시각은 T+N일 리뷰 실행의 15:30이다.** 원 추론의 `as_of`는 20:00이라 해설의
  창이 원 추론보다 이른 시각에서 끝나지만, 지평이 1 이상이라 실제 창은 며칠 넓다. 지평 0
  해설은 원래 없다(`ck_thesis_outcome_zero_horizon_has_no_narrative`).

## 4. 비용 — 추정이고 실측은 붙인 뒤에 한다

16단계 실측이 대상당 $0.0076이다(`gpt-5.6-luna`, effort=high, 48대상 $0.365). 하루 6대상이
느는 것이므로 **$0.05/일, 월 $1.5 안팎**이 된다. 해설 호출은 하루 6~7에서 9~10이 된다.

**이 값은 추정이지 실측이 아니다.** NXT 대상이 둘뿐이라 호출당 대상 수가 절반이고, 그러면
호출 고정비(시스템 프롬프트·조사 왕복)가 대상당 단가를 끌어올린다. 조사 규칙이 대상마다
`past_theses`·`recent_documents`·`daily_history`·수급을 요구하므로 툴 호출 수는 대상 수만큼
줄지 않는다. 붙인 뒤 `thesis_llm_run`으로 실측한다(13단계).

## 5. 테스트

| 테스트 | 무엇을 잡나 |
| --- | --- |
| `test_thesis_pipeline.py`의 `RunSlot.POST_NXT_CLOSE not in NARRATED_SLOTS` | **뒤집는다.** 그 줄이 7단계 3절을 지키던 줄이다 — 그냥 지우지 않고 `in`으로 바꿔 이 단계의 결정을 지키게 한다 |
| 같은 파일의 `set(PRECEDENT_SLOTS) == {*NARRATED_SLOTS, RunSlot.POST_NXT_CLOSE}` | 두 집합이 같아지므로 `set(PRECEDENT_SLOTS) == set(NARRATED_SLOTS)`로 바꾸고, **중복 항목이 없다**(`len(set(x)) == len(x)`)를 더한다 |
| 새로: `test_pending_narratives_ask_for_every_narrated_slot` | 조회에 리터럴이 없다는 것과 **부르는 쪽이 실제로 무엇을 넘기는지**는 다르다. 목록에서 빠진 슬롯은 조용히 영영 미해설로 남고, 조회에 날짜 상한이 없어 오류로도 안 드러난다 |
| 새로: `test_a_narration_call_names_the_after_hours_review` | 프롬프트 첫 줄이 "애프터마켓 리뷰"다. **7단계가 이 슬롯을 뺀 이유가 정확히 여기였다** — 그때는 "장전에 쓴 추론"으로 실렸다 |
| 새로: `test_briefing_ops.py`의 백로그 파라미터 대조 | `briefing/ops.py`가 넘기는 목록이 `pending_narratives`와 같다 — 7단계 3절이 경고한 거짓 경보 |
| 기존 `test_a_narration_call_refuses_mixed_slots` | 그대로 둔다. 슬롯이 셋으로 늘어도 한 호출은 한 슬롯이라는 규칙이 이 단계의 전제다 |
| `test_prompt_versions.py` | **그대로 통과해야 한다.** 통과하지 않으면 프롬프트를 건드린 것이다(3절) |

## 6. 이 단계가 하지 않는 것

- **채점.** 1절. 리뷰는 예측이 아니다. 채점 축도 정규장 그대로다(18단계 1절).
- **프롬프트 문장과 판.** 3절.
- **NXT 지수 대상.** 7단계 그대로 — 지수는 정규장 마감값이라 매번 "움직이지 않았다"를 쓰게 된다.
- **애프터마켓·프리마켓 분봉 툴.** 7단계 5절, 18단계 4절.
- **`post_nxt_close`의 `as_of` 변경.** 20:00 그대로다.
- **18단계 3절(프리마켓 관측).** 별개 게이트다.

## 7. 남은 확인

- **착수 게이트는 18단계 2.5절의 5영업일 관측이다.** 장전 이유 문장이 어젯밤 애프터 재료를
  실제로 인용하는지를 LangSmith 트레이스로 본다. **안 쓰고 있으면 이 단계를 하지 않는다** —
  아무도 안 읽는 본문에 판정을 붙이는 값이 되고, 그때 의심할 것은 프롬프트 문장이 먼저다
  (18단계 5절).
- **붙인 뒤 4주: `verdict` 분포.** ops 브리핑이 지평별로 이미 낸다. `unresolved`가 거의
  전부면 — 애프터 재료가 며칠 뒤 보도로 다뤄지지 않는다는 뜻이면 — 되돌린다.
  **되돌릴 자리는 상수 둘이고 데이터는 남는다.**
- **비용 실측.** 4절.
- **`post_nxt_close` 행에 `base_price`가 채워지는지.** 해설 렌더가 축 줄을 붙이는 조건이다.
  채점이 없어 값이 틀려도 조용히 넘어가는 자리라, 붙이기 전에 한 번 확인한다.

## 8. 순서

**2·3·4는 끝났다**(2026-08-31). 1은 사용자 결정으로 건너뛰었다.

1. ~~18단계 2절의 5영업일 관측 결과 확인.~~ 기다리지 않았다(상태 줄). 관측 자체는 그대로
   돌고, 결과가 반대면 7절대로 되돌린다.
2. 상수 둘과 주석, SQL 머리말 셋, 낡은 docstring 넷.
3. 테스트(5절).
4. 문서 포인터 셋 — 7단계 3·9절, 18단계 4절, 두 README.
5. 배포. 리비전이 없어 코드 배포뿐이다. 첫 실행에서 `thesis_llm_run`에 `narration` 대화가
   하나 더 열렸는지, `thesis_outcome`에 `post_nxt_close` 추론의 해설 행이 붙었는지 확인한다.
   **T+1 해설이라 배포 다음 영업일의 20:30 리뷰가 첫 실행이다.**
6. 4주 뒤 `verdict` 분포와 비용으로 유지/되돌림 판단.
