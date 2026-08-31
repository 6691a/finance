# 18단계 — 애프터마켓을 다음날 장전의 재료로 잇는다 (`post_nxt_close` → `pre_open`)

- 상위: [README.md](README.md)
- 날짜: 2026-08-31 (같은 날 검토 반영 — 장전 관측 상태에 KRX 종가와 NXT 종가를 나란히
  싣는다, 2.6절)
- 상태: **설계. 미구현.** 2절(과거 추론 목록 + 애프터마켓 관측 블록)부터 한다. 3절(프리마켓
  관측)은 2절을 운영에 올리고 트레이스를 본 뒤 결정한다 — 2절만으로 문제가 풀리면 3절은
  안 한다
- 의존: [5-followup.md](5-followup.md)(`past_theses`), [7-nxt-review.md](7-nxt-review.md)
  (`post_nxt_close` 슬롯), [9-intraday.md](9-intraday.md)(슬롯 목록 파라미터화),
  [15-return-basis.md](15-return-basis.md)(축 세 칸 — 이 단계가 **건드리지 않는** 것)
- 산출물: 2절 — `thesis/state.py`의 `PRECEDENT_SLOTS`와 `ObservedState.after_hours`,
  `ThesisStore.past_theses`의 슬롯 인자 교체, `select_past_with_outcomes.sql` 머리말, 애프터마켓
  창·행 모델·SQL 상수를 `nxt_review.py`에서 `common.py`로 옮기고 `ThesisRun.after_hours_state`
  추가(이동 커밋 따로), `forecast.py`가 그 블록을 얹는 것, `thesis_generation.yaml`의
  `pre_open` 지시문 한 문장과 "과거 추론과 결과" 절의 불릿 하나, `PROMPT_VERSION` 15와 해시,
  테스트. 3절 — `select_nxt_pre_market.sql`, `PreMarketObservation`과
  `ObservedState.pre_market`, `pre_open` 지시문 한 문장 더, `PROMPT_VERSION` 한 번 더

## 0. 문제 — 애프터마켓 리뷰가 다음날로 흐르지 않는다

한국 주식의 하루는 KRX 15:30이 아니라 NXT 애프터마켓 20:00에 끝난다. 7단계가 그 구간을
보는 슬롯(`post_nxt_close`, 21:00)을 만들었고 2026-08-25부터 매일 돈다(8건, 2026-08-31 실측).
그런데 **그 리뷰를 읽는 소비자가 Slack뿐이다.** 다음날 08:35 장전 전망은 어젯밤 애프터마켓을
어느 경로로도 못 본다.

- `ThesisStore.past_theses`가 슬롯을 `NARRATED_SLOTS`로 거른다(`thesis/store.py`의
  `cursor.execute(PAST_THESES, (as_of_at, list(NARRATED_SLOTS), ...))`). 그 목록은
  `(*FORECAST_SLOTS, POST_CLOSE)`이고 `POST_NXT_CLOSE`가 없다(`thesis/state.py`). 7단계 3절이
  **해설 루프에서** 이 슬롯을 빼려고 만든 목록인데, 같은 상수가 "장전이 되돌아보는 것"의
  목록도 겸하고 있어 애프터마켓 리뷰가 두 자리에서 함께 빠졌다.
- 장전 관측 상태는 `stock_investor_trade_daily.close_price` — 전 영업일 15:30 확정 종가 —
  뿐이다(`thesis/common.py`의 `observed_state`). 애프터마켓 종가도, 오늘 프리마켓 가격도 없다.
- 매크로 창은 전 영업일 15:30부터 열려 있어(`forecast.py`의 `macro_window_start`) 밤사이
  미국 선물·환율·금리는 본다. **빈칸은 종목 자신의 NXT 가격 하나다.**

사용자 질문(2026-08-31)은 이것이었다 — "정규장 끝나고 이슈가 나오면 NXT에 반영되고, 정규장이
오면 그 반영이 맞는지 조정이 한 번 더 되는데, 다음날 평가에 안 넣어도 되나." 그 "한 번 더
조정"이 얼마나 되는지를 먼저 쟀다.

### 0.1 실측 — 애프터마켓은 절반짜리 신호, 프리마켓은 갭 그 자체

운영 DB 읽기 전용 조회(2026-08-31). 대상은 005930·000660, NXT 봉이 있는 2025-08-18부터
2026-08-28까지, 다음 영업일 확정 행이 있는 날 491쌍(종목 둘 합). 분모는 전부 당일 15:30
확정 종가(`stock_investor_trade_daily.close_price`)이고, 다음날 시가·종가는 같은 테이블의
`open_price`·`close_price`다.

| 신호 | 다음날 시가 갭과 상관(Pearson / Spearman) | 다음날 종가 등락과 상관 | 방향 일치(종가) |
| --- | --- | --- | --- |
| 애프터마켓 15:30~20:00 마지막 봉 | 0.36 / 0.25 | 0.33 / 0.21 | 273 / 491 (56%) |
| 위 중 애프터 등락이 ±1% 이상인 날 | — | — | 92 / 145 (63%) |
| 프리마켓 08:00~08:34 마지막 봉 | 0.95 / 0.94 | 0.72 / 0.64 | 424 / 486 (87%, 시가 기준) |

2026-06-01 이후 122쌍만 보면 애프터 0.47, 프리마켓 0.76이다(종가 등락과 Pearson).

읽는 법.

- **애프터마켓 방향이 다음날 정규장 종가로 이어지는 것은 56%다.** 동전보다 조금 낫고,
  크게 움직인 날(±1% 이상)은 63%다. 사용자의 관찰 — 정규장이 한 번 더 조정한다 — 이
  숫자로 맞다. 그래서 애프터 종가를 **라벨 없이** 싣지 않는다. 싣되, 그 값이 "얇은 호가의
  첫 반응이고 정규장이 되돌리는 일이 잦다"는 것을 프롬프트가 함께 말한다(2.6절). 이 숫자가
  그 문장의 근거다.
- **KRX 종가와 NXT 종가를 나란히 두면 그 자체가 정보다**(사용자 제안, 2026-08-31). 정규장에서
  3% 빠진 종목이 애프터에서 0.7% 더 빠진 것과, 오른 종목이 애프터에서 빠진 것은 다른
  이야기다 — 7단계 2절이 리뷰 슬롯에 두 값을 같이 준 이유이고, 장전도 같다. 리뷰 본문에도
  숫자가 있지만 산문 속이라 모델이 안 쓸 수 있다. 칸으로 준다.
- **해석은 따로 넘긴다.** "장 마감 뒤 무슨 재료가 나왔고 시장이 그것을 어느 쪽으로
  읽었나"는 숫자 둘로는 안 나오고, 그 문장을 `post_nxt_close` 리뷰가 이미 쓰고 있다.
  2026-08-28 000660 리뷰의 `up_reasoning`이 그 예다 — 애프터 종가(1,658,000원, +0.3%), 창 중
  나스닥100 선물, 자사주 매입 진행이 한 문단에 있다. **이 문장을 다음날 장전에 보여 주는
  것이 2.1~2.3절이다.**
- **프리마켓은 다른 문제다.** 장전 `as_of`가 08:35라 08:00~08:34 NXT 프리마켓은 event-time
  안에 있고, 그 가격은 그날 시가 갭을 거의 그대로 말한다(0.95). 종가 등락과도 0.72다.
  애프터 종가보다 늦은 가격이라 더 강하지만, 08:35에 잠정 봉이고 확정 DAG와 같은 분에
  경합한다(3.1절). 애프터 종가는 20:05에 확정된 값이라 그 문제가 없다. 그래서 순서가
  애프터 먼저, 프리마켓은 게이트 뒤다. 3절.

한계를 적어 둔다. 종목 둘·1년·한 체제의 상관이고 인과가 아니다. 프리마켓의 0.95는 개장
25분 전 가격이라 당연한 값이고, 값어치는 종가 쪽 0.72다. 2026-07-31 005930 +26.81% 같은
극단값이 있어 Spearman을 함께 적었다. 조회 스크립트는 저장소 밖(스크래치패드)이고 요지는
아래다 — 다시 재려면 [TUNING.md](TUNING.md) 2절의 "손으로 읽는 쿼리" 옆에 둔다.

```sql
-- 애프터: exchange='NXT', KST 15:30~20:00 마지막 close / 당일 close_price
-- 프리:   exchange='NXT', KST 08:00~08:34 마지막 close / 전 영업일 close_price
-- 다음날: lead(open_price), lead(close_price) OVER (PARTITION BY stock_code ORDER BY business_date)
```

## 1. 결정 사항

| 항목 | 값 | 이유 |
| --- | --- | --- |
| 채점 축 | **그대로.** 장전은 전일 정규 종가 → 당일 정규 종가 | 15단계가 축을 행에 남긴 직후다. NXT 종가를 섞으면 축이 다시 흔들린다. 평가는 정규장, 재료는 NXT — 이 둘을 가른다 |
| 장전이 되돌아보는 슬롯 | `PRECEDENT_SLOTS = (*NARRATED_SLOTS, RunSlot.POST_NXT_CLOSE)` | 리뷰 본문이 애프터 종가·등락·재료를 이미 담고 있다(0.1절). 새 SQL 없이 목록 하나로 흐른다 |
| 해설 루프 | **여전히 안 넣는다.** `NARRATED_SLOTS` 불변 | 7단계 3절·9절의 판단 그대로. 이 단계 범위 밖 |
| 장전 관측 상태 | `after_hours` 블록을 더한다 — 종목마다 전 영업일 NXT 마감가·정규 종가 대비 등락·마지막 봉 시각·봉 수. `stock`(KRX 종가)과 나란히 | 사용자 제안. SQL·모델이 리뷰 슬롯에 이미 있어 싸고, 칸이 산문보다 확실하다(0.1절) |
| 등락률의 분모 | 전 영업일 **정규** 종가(`stock_investor_trade_daily.close_price`) | `regular`와 같은 원본. 기준가가 둘이 되지 않는다 |
| 프롬프트 | `pre_open` 지시문에 `after_hours`의 성격 한 문장, "과거 추론과 결과" 절에 `post_nxt_close` 불릿 하나. `PROMPT_VERSION` 15 | 라벨 없이 주면 NXT 종가를 기준가로 잡는다. 채점 칸이 빈 것이 정상임을 `post_close`처럼 말해 줘야 빗나간 예측으로 안 읽는다 |
| 판 | 2절 전부가 **판 15 하나**다 | 리뷰 본문과 애프터 종가는 "애프터마켓을 장전에"라는 한 손잡이다(TUNING.md 1절). 따로 재고 싶으면 판 둘로 가른다 — 그 결정은 구현 때 |
| 프리마켓 관측 블록 | **게이트 뒤.** 2절을 올리고 5영업일 트레이스를 본 뒤 | 최소 구현 먼저. 08:35 경합(3.1절)이 있어 따로 판을 나눠 본다 |

## 2. 장전이 어젯밤 애프터마켓을 본다 — 리뷰 본문과 종가 둘

2.1~2.3절이 리뷰 본문(해석)을, 2.6절이 종가(숫자)를 넘긴다. 둘이 한 판이다.

### 2.1 슬롯 목록이 둘이 된다

지금 `NARRATED_SLOTS` 하나가 두 뜻을 진다 — "해설을 받는 슬롯"과 "장전이 되돌아보는 슬롯".
7단계가 전자를 위해 만든 목록이고, 9단계가 `past_theses`를 파라미터화하면서 손에 있던
같은 상수를 넘겼다(`select_past_with_outcomes.sql` 머리말이 "원본은 `NARRATED_SLOTS`"라고
적고 있다). 뜻이 둘이면 이름을 둘로 나눈다.

```python
# thesis/state.py
# 사후 해설을 받는 슬롯. 애프터마켓은 빠져 있다(`7-nxt-review.md` 3절).
NARRATED_SLOTS: tuple[RunSlot, ...] = (*FORECAST_SLOTS, RunSlot.POST_CLOSE)

# 장전·장중이 프롬프트에 되돌아보는 슬롯. 해설 대상보다 애프터마켓 리뷰 하나가 더 있다 —
# 리뷰는 채점도 해설도 없지만 "장 마감 뒤 무슨 재료가 나왔나"를 담고 있어 다음날 아침에
# 볼 값어치가 있다(`18-nxt-precedent.md` 0.1절).
PRECEDENT_SLOTS: tuple[RunSlot, ...] = (*NARRATED_SLOTS, RunSlot.POST_NXT_CLOSE)
```

손대는 자리는 셋이다.

| 자리 | 바꾸는 것 |
| --- | --- |
| `thesis/store.py`의 `past_theses` | `list(NARRATED_SLOTS)` → `list(PRECEDENT_SLOTS)`. docstring의 "예측 슬롯 다섯과 장후 리뷰"에 애프터마켓 리뷰를 더한다 |
| `thesis/sql/select_past_with_outcomes.sql` 머리말 | "원본은 `NARRATED_SLOTS`" → `PRECEDENT_SLOTS`. 애프터 리뷰는 채점·해설 둘 다 없어 `outcomes`가 빈 배열이라는 한 줄 |
| `thesis/toolbox.py`의 `_tool_past_theses` | 손대지 않는다 — `ThesisStore.past_theses`를 부르므로 따라온다 |

**해설 SQL 둘(`select_pending_narratives.sql`·`select_backlog.sql`)은 그대로 `NARRATED_SLOTS`를
받는다.** 그 둘이 같은 목록을 봐야 한다는 7단계 3절의 제약은 이 변경과 무관하게 유지된다.

### 2.2 프롬프트에 무엇이 실리나

- **슬롯당 `PREFETCHED_PAST_THESES = 2`행이라 종목마다 최대 2행이 는다.** 지수는 NXT 행이
  없어 0이다. 프롬프트 길이는 슬롯 수에 비례하고([TUNING.md](TUNING.md) 손잡이 장부), 지금
  실제로 돌고 있는 슬롯은 `pre_open`·`intraday_midday`·`post_close`·`post_nxt_close` 넷이라
  종목 대상은 6행에서 8행이 된다.
- **event-time은 이미 맞다.** SQL이 `thesis.run_date < (as_of_at AT TIME ZONE 'Asia/Seoul')::date`를
  걸어, D일 21:00 리뷰(`run_date = D`)는 D+1 08:35 장전에 보이고 D+1 21:00 리뷰는 안 보인다.
  장전 슬롯을 오후에 재실행해도 그날 저녁 리뷰가 섞이지 않는다.
- **`outcomes`는 빈 배열이다.** `LEFT JOIN thesis_outcome`이라 행은 오고 `FILTER (WHERE
  outcome.id IS NOT NULL)`로 배열만 빈다. `PastThesis.outcomes`가 빈 튜플을 받는 것은
  `post_close`가 해설 전인 날과 같은 경로라 새 코드가 없다.
- **`thesis_precedent` 엣지가 리뷰 행에도 붙는다.** 장전이 무엇을 보고 냈는지를 남기는
  엣지라, "어젯밤 애프터 리뷰를 봤다"가 그래프에 남는 것이 맞다. `PREFETCHED_PAST_THESES = 0`
  으로 끄면 함께 꺼진다(5단계 5절).

### 2.3 프롬프트 문장

두 자리다. 둘 다 판 15에 든다.

**`pre_open` 지시문**에 `after_hours`의 성격을 한 문장으로 적는다(2.6절의 블록).

```yaml
  pre_open: >-
    오늘 한국 장이 열리기 전이다. 밤사이 해외 시장과 전일 국내 세션을 근거로
    **오늘 각 대상이 어느 방향으로 움직일지**를 가설로 적어라.
    관측 상태의 `stock`은 전일 KRX 정규장 종가이고 `after_hours`는 그 뒤 NXT
    애프터마켓(15:30~20:00) 마감가와 정규 종가 대비 등락이다. **기준가는 정규장 종가다** —
    애프터마켓은 호가가 얇아 그 방향을 정규장이 되돌리는 일이 잦으니, 장 마감 뒤 나온
    재료에 시장이 처음 어떻게 반응했는지로 읽어라.
```

**"과거 추론과 결과" 절**, `post_close` 불릿 다음에 하나 더 둔다.

```yaml
  - `post_nxt_close` — 정규장이 닫힌 뒤 NXT 애프터마켓(15:30~20:00)에서 무엇이 움직였나를
    적은 **해석**이다. 예측이 아니라 채점이 없다. 애프터마켓은 호가가 얇아 그 방향을
    정규장이 되돌리는 일이 잦다 — 방향의 근거로 베끼지 말고 **장 마감 뒤 어떤 재료가
    나왔고 시장이 처음에 어느 쪽으로 읽었는지**로 읽어라.
```

- 0.1절의 56%·63%를 문장에 적지 않는다. 상수는 낡는다 — `flat` 기준선을 상수에서 실측으로
  옮긴 이유와 같다(`ObservedState.flat_base_rate`). "되돌리는 일이 잦다"까지만 말한다.
- 두 자리가 같은 말("되돌리는 일이 잦다")을 한다. 일부러다 — 숫자 블록을 읽을 때와 리뷰
  본문을 읽을 때 각각 그 자리에서 경고가 보여야 한다.
- 문장을 고쳤으므로 `PROMPT_VERSION`을 14에서 15로 올리고
  `tests/modules/test_prompt_versions.py`의 해시를 같은 커밋에서 갱신한다. `fragments/`는
  건드리지 않는다.

### 2.4 테스트

| 테스트 | 무엇을 잡나 |
| --- | --- |
| `test_thesis_pipeline.py`의 `past_theses` 파라미터 대조(`(AS_OF, list(NARRATED_SLOTS), "KOSPI", ...)`) | `PRECEDENT_SLOTS`로 바꾼다 |
| 같은 파일의 `RunSlot.POST_NXT_CLOSE not in NARRATED_SLOTS` | **그대로 둔다.** 해설 루프에는 여전히 안 들어간다는 것을 이 줄이 지킨다 |
| 새로: `set(PRECEDENT_SLOTS) == {*NARRATED_SLOTS, RunSlot.POST_NXT_CLOSE}` | 두 목록의 차이가 정확히 슬롯 하나라는 것 |
| 새로: `build_messages`에 `run_slot=POST_NXT_CLOSE`인 `PastThesis`를 넣으면 JSON에 `"run_slot": "post_nxt_close"`가 실린다 | 렌더가 슬롯을 가리지 않는다는 것 |
| `test_prompt_versions.py` | 판 15와 해시 |
| 장전(`forecast.py`) — 가짜 연결에 전 영업일 애프터 봉 행을 넣으면 `observed.after_hours`에 실리고, 0행이면 빈 dict, 잠정 봉만이면 빈 dict와 warning | 2.6절의 가드 셋 |
| 장후(`review.py`)·장중(`intraday.py`)의 관측 상태는 `after_hours`가 비어 있다 | 15:30 cutoff 슬롯에 그 뒤 값이 새지 않는다는 것 |
| `test_market_thesis_nxt_review.py` | 이동 뒤에도 그대로 통과한다 — import 경로만 바뀐다 |
| `ObservedState(...).model_dump(mode="json")`에 `after_hours` 키가 있다 | `input_state` JSONB에 남는다는 것 |

### 2.5 효과를 어떻게 보나

[TUNING.md](TUNING.md) 4절의 판단 캘린더를 따른다. 자동으로 나오는 것은 장전 T+0 Brier
추이뿐이고, 이 변경이 실제로 쓰였는지는 **장전 이유 문장이 어젯밤 애프터 재료·등락을
인용하는지**를 LangSmith 트레이스에서 눈으로 본다. 5영업일 뒤에 3절을 할지 정한다. 효과가
없으면 `PRECEDENT_SLOTS`를 `NARRATED_SLOTS`와 같게 되돌리고 `forecast.py`가 블록을 안 얹는
것으로 끝이다 — 리비전도 데이터 이관도 없다.

### 2.6 장전 관측 상태의 `after_hours` 블록

리뷰 슬롯의 관측 상태(`NxtObservedState`)가 이미 갖는 것을 장전의 `ObservedState`에도 둔다.
**키 이름과 모델을 같게 둔다** — 프롬프트 어휘가 하나여야 모델이 두 슬롯에서 같은 뜻으로
읽는다.

```python
class ObservedState(BaseModel):
    ...
    stock: dict[str, StockObservation] = Field(default_factory=dict)   # KRX 15:30 확정 종가
    # 전 영업일 NXT 애프터마켓(15:30~20:00) 마감가. **장전만 채운다** — 장후·장중은 as_of가
    # 15:30 이전이라 이 값이 미래다. 등락률의 분모는 `stock`과 같은 정규 종가다.
    after_hours: dict[str, AfterHoursObservation] = Field(default_factory=dict)
```

관측 상태 JSON은 이렇게 된다(종목만 — NXT에 지수가 없다).

```json
{
  "session": "2026-08-28",
  "stock":       {"005930": {"close": 257000}},
  "after_hours": {"005930": {"close": 256500, "return_pct": -0.19,
                             "last_bar_at": "2026-08-28T10:59:00Z", "bars": 260}}
}
```

**공유하는 것은 `common.py`로 옮긴다.** 창 상수(`AFTER_HOURS_OPEN`·`AFTER_HOURS_CLOSE`),
`after_hours_window`, 행 모델 `AfterHoursBar`, SQL 상수 `AFTER_HOURS_STATE`가 지금
`nxt_review.py`에 있고, 그 모듈 docstring이 "다른 슬롯과 공유하지 않는다"고 못박고 있다.
그 규칙이 막으려는 것은 `if slot ==` 분기인데, NXT 애프터마켓이 15:30~20:00이라는 것은
**세션 사실**이지 슬롯 사실이 아니다 — `observed_state(session, targets)`가 세션을 인자로
받는 것과 같은 자리다. 옮기고 나면 `nxt_review.py`에 남는 것은 `as_of`·`macro_window_start`·
`SLOT`·클래스뿐이고 그것들은 전부 슬롯을 안다.

```python
class ThesisRun:  # thesis/common.py
    def after_hours_state(self, session: date, stock_codes: Sequence[str]) -> dict[str, AfterHoursObservation]:
        """그 세션의 NXT 애프터마켓 마감가. 어느 세션인지는 부르는 쪽이 정한다."""
```

- `NxtAfterHoursReview.bars`·`_after_hours_entry`가 이것을 부르도록 바꾼다. 리뷰의 readiness
  guard(확정 종가·0봉·잠정 봉 판정)는 리뷰 것이라 그 모듈에 남는다.
- **이동 커밋을 따로 둔다.** 저장소 규칙 "이동과 파일 분리를 같은 커밋에 두지 않는다" —
  어느 쪽이 회귀를 만들었는지 가르기 위해서다. 이동만 한 커밋에서 `test_market_thesis_nxt_review.py`가
  그대로 통과해야 한다.

**채우는 곳은 `PreOpenForecast.run`이다.** `common.observed_state`는 슬롯을 모르는 것만 두는
자리라 거기 넣지 않는다 — 장후 리뷰가 같은 함수를 부르는데 그쪽은 이 값을 보면 안 된다.

```python
observed = self._run.observed_state(session, targets)
observed = observed.model_copy(update={"after_hours": self._run.after_hours_state(session, stock_codes)})
```

가드 셋. 리뷰 슬롯(7단계 4절)과 다르게 **어느 것도 재시도하지 않는다** — 장전은 08:35에
묶여 있고 애프터 종가는 선택 입력이라 없어도 전망이 성립한다.

| 상태 | 처리 | 이유 |
| --- | --- | --- |
| 전 영업일 애프터 봉 0개 | 빈 dict, INFO | 무거래일과 수집 실패를 응답만으로 못 가른다(7단계 guard 3) |
| 봉은 있는데 전부 잠정(`all_final = false`) | 빈 dict, **WARNING** | 20:05 백필이 12시간 전에 끝났어야 한다. 잠정 봉 위에 쓰면 첫 성공본 불변 때문에 못 고친다. 다만 비우고 경고하는 것이지 죽이지 않는다 — 경고가 쌓이면 수집 쪽 문제다 |
| 확정 종가 없음(`settled_close IS NULL`) | 그 종목만 뺀다, WARNING | 등락률의 분모가 없다. `observed.stock`도 그 종목이 비어 있을 것이라 같은 종목이 두 곳에서 빠진다 |

**`input_state`에 남는다.** `ObservedState`가 JSONB로 통째로 저장되므로 장전 행에
`after_hours` 키가 생긴다. 채점 SQL은 장전 축을 `stock_investor_trade_daily`에서 읽지
`input_state`를 캐내지 않으므로(15단계 0.1절 — 캐내는 것은 장중의 `price`뿐) 영향이 없다.
API의 `input_state: dict[str, Any]`는 그대로 통과한다.

**장중 슬롯은 이번에 안 채운다.** `intraday_midday`의 as_of는 전 영업일 애프터가 과거라
넣을 수는 있지만, 장중은 이미 그날 봉을 보고 있어 어젯밤 값의 값어치가 작다. 2.5절
관측에서 장전에 효과가 보이면 그때 같은 한 줄을 `intraday.py`에도 얹는다.

## 3. (게이트 뒤) 프리마켓 관측 블록

2절을 올린 뒤에도 장전이 "오늘 아침 가격"을 못 본다는 문제는 남는다. 0.1절이 그 값어치를
보여 줬다(종가 등락과 0.72 — 애프터 종가의 0.33보다 훨씬 크다). 다만 **2절과 성격이
다르다** — 2.6절의 애프터 종가는 20:05에 확정된 값이고 이것은 08:35에 잠정인 값이라(3.1절),
판을 나눠 효과를 따로 본다.

### 3.1 데이터는 이미 있다

`stock_bar`의 `exchange = 'NXT'`, KST 08:00~08:34 봉이다. 2026-08-24~31 실측(평일 여섯 날):

- 종목당 35봉(08-27 000660만 34). `apps/realtime` WebSocket이 매분 `:03`에 쓰고
  (`created_at` 08:01:03~08:35:03), `kis_equity_bar_reconcile`(`5,35 8-19`)이 08:35에 REST로
  덮어 `ingest_method = 'rest'`·`is_final = true`가 된다.
- **장전 DAG(08:35)가 도는 시각에 이 봉들은 잠정(WebSocket)이다.** 확정은 같은 분에 도는
  reconcile의 몫이라 순서를 보장할 수 없다. 7단계 guard 4("잠정 봉이면 재시도")를 여기
  가져오지 않는다 — 08:35에 잠정인 것이 **정상 상태**이고, 잠정 봉의 가격은 같은 체결에서
  나온 값이라 틀린 값이 아니다. WebSocket이 끊겨 봉이 비면 `bar_at`이 그것을 말한다.
- 08:34 봉은 08:35:03에 들어와 DAG 시작과 경합한다. 상한을 08:34로 두되 **마지막 봉이
  08:33이어도 그대로 쓴다.** 장중 슬롯이 `bar_at`을 싣고 "어느 봉을 봤는지"를 모델에게
  맡기는 것과 같은 형태다(`IntradayObservation`).

### 3.2 모양

```python
class PreMarketObservation(BaseModel):
    """오늘 NXT 프리마켓(08:00~08:34) 마지막 봉. 전 영업일 정규 종가 대비다."""
    model_config = ConfigDict(frozen=True)
    price: float
    return_pct: float
    bar_at: AwareDatetime
    bars: int

class ObservedState(BaseModel):
    ...
    pre_market: dict[str, PreMarketObservation] = Field(default_factory=dict)
```

- **종목만이다.** NXT에 지수가 없다(7단계).
- **분모는 `stock_investor_trade_daily.close_price`(전 영업일)다.** `stock_bar.previous_close`도
  같은 값이지만(스키마 주석 "전일 KRX 확정 종가") 채점과 같은 원본을 보는 쪽을 택한다 —
  `select_nxt_after_hours.sql`이 같은 이유로 조인한다.
- 봉이 0개면 블록을 비우고 로그를 남긴다. skip도 실패도 아니다 — 프리마켓 무거래와 수집
  실패를 응답만으로 가를 수 없고(7단계 guard 3과 같은 이유), 장전 전망은 프리마켓 없이도
  성립한다.
- 채우는 곳은 `PreOpenForecast`다. `common.observed_state`는 슬롯을 모르는 것만 두는
  모듈이라 거기 넣지 않는다 — 장전만 프리마켓을 보므로 `forecast.py`가 `observed_state`를
  받아 `model_copy(update={"pre_market": ...})`로 얹는다. 장중·장후는 빈 dict 그대로다.

### 3.3 SQL `stock_bar/select_nxt_pre_market.sql`

새 파일이다. `select_nxt_after_hours.sql`과 같은 모양(창 양 끝과 분모 날짜를 파라미터로,
종목당 마지막 봉 하나, `bar_count`)에서 `all_final`을 뺀다(3.1절 — 여기서는 판정에 안 쓴다).
새 툴 SQL 규칙대로 운영 DB에 읽기 전용으로 한 번 돌려 보고 넣는다.

### 3.4 프롬프트와 판

`pre_open` 지시문에 한 문장을 더한다.

```yaml
  pre_open: >-
    오늘 한국 장이 열리기 전이다. 밤사이 해외 시장과 전일 국내 세션을 근거로
    **오늘 각 대상이 어느 방향으로 움직일지**를 가설로 적어라.
    관측 상태의 `pre_market`은 오늘 NXT 프리마켓(08:00~08:34) 마지막 가격이다. **기준가는
    여전히 전일 정규 종가다** — 프리마켓이 이미 움직인 만큼은 네 예측의 일부이지 제외가
    아니다(장중 슬롯과 반대다).
```

- 축이 안 바뀐다는 것을 문장이 말해야 한다. 장중 슬롯은 "지금 가격 대비"라 이미 움직인
  만큼을 **빼는데**, 장전은 전일 종가 대비라 **넣는다.** 이 차이를 안 적으면 모델이 장중
  규칙을 가져다 쓴다.
- `PROMPT_VERSION`을 한 번 더 올린다(16). **이 판 전후의 Brier를 한 판으로 섞지 않는다** —
  갭을 미리 보는 것이라 점수가 기계적으로 좋아지고, 그것은 모델이 나아진 것이 아니다.
  TUNING.md 판단 캘린더에 그 사실을 적는다.

### 3.5 테스트

- `forecast.py`가 `pre_market`을 얹는다 — 가짜 연결에 프리마켓 봉을 넣고 관측 상태에 그
  블록이 실리는지, 0봉이면 빈 dict인지.
- `select_nxt_pre_market.sql`의 파라미터 순서(창 시작·끝·분모 날짜·종목 목록)를 대조한다.
- 장중·장후 관측 상태의 `pre_market`이 비어 있다.

## 4. 이 단계가 하지 않는 것

- **채점 축 변경.** 1절. NXT 종가로 채점하지 않는다.
- **애프터마켓 해설.** `NARRATED_SLOTS`는 그대로다. 붙이려면 7단계 9절이 적은 대로 두
  SQL의 슬롯 목록을 늘리면 되지만, 이 단계의 질문(다음날 장전이 애프터를 보는가)과 별개다.
- **장후 리뷰(`post_close`)·장중 슬롯에 `after_hours` 채우기.** 장후는 as_of가 15:30이라
  미래고, 장중은 2.6절 끝 — 장전 효과를 본 뒤.
- **장후 리뷰(`post_close`)의 `as_of` 15:30 확장.** 7단계 0절의 이유 그대로 — 재실행마다
  근거가 달라지는 것을 막는 cutoff다.
- **프리마켓·애프터마켓 분봉 툴.** 관측 상태로 충분하고, 툴은 모델이 안 부르면 없는 것과
  같다(`common.observed_state`가 기술적 관측을 툴이 아니라 관측 상태에 싣는 이유와 같다).
- **NXT 휴장 캘린더.** 7단계 9절.

## 5. 남은 확인(spike)

- **2절 뒤 프롬프트 길이 실측.** 종목 대상 6행 → 8행과 `after_hours` 블록의 토큰 차이를
  LangSmith에서 읽는다. TUNING.md 손잡이 장부의 `past_theses` 1,370~1,586자(대상당)가 갱신
  대상이다.
- **`select_pending_grades.sql`이 장전 행의 `input_state`를 정말 안 읽는지** 구현 때 파일에서
  확인한다. 2.6절은 15단계 0.1절의 서술("캐내는 것은 장중의 `price`")에 기대고 있다.
- **2절 뒤 인용 여부.** 장전 이유 문장이 애프터 재료를 실제로 가져다 쓰는지 5영업일 트레이스.
  안 쓰면 3절보다 프롬프트 문장을 먼저 의심한다.
- **3절의 08:35 경합.** 운영 DAG 로그에서 장전 시작 시각과 reconcile 시작 시각을 며칠 대조한다.
  잠정 봉 위에 추론을 쓰는 것이 정상 상태라는 3.1절의 판단은 여기서 뒤집힐 수 있다 —
  WebSocket이 자주 끊겨 08:20 봉이 마지막인 날이 잦으면 reconcile을 08:34로 당기는 쪽이 낫다.
- **0.1절의 상관은 두 종목·1년이다.** 추적 종목이 늘면 다시 잰다.

## 6. 순서

1. 이동 커밋 — 애프터마켓 창·행 모델·SQL 상수를 `nxt_review.py`에서 `common.py`로.
   동작 변화 없음, 리뷰 테스트 그대로 통과.
2. 2절 — `PRECEDENT_SLOTS`, `ObservedState.after_hours`, `ThesisRun.after_hours_state`,
   `forecast.py`의 한 줄, SQL 주석, 프롬프트 두 자리, 판 15, 테스트. 리비전 없음.
3. 5영업일 관측(2.5절).
4. 3절 여부 결정. 한다면 판 16과 TUNING.md 판단 캘린더 갱신까지가 한 벌이다.
