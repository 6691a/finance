# 7단계 — NXT 애프터마켓 리뷰 슬롯 (`post_nxt_close`)

- 날짜: 2026-08-22
- 상태: 설계. 구현 미착수
- 의존: 1·2·3단계(저장, 에이전트, DAG·Slack). 5단계와는 **분리된다**(채점·해설 대상이 아니다)

## 0. 문제 — 하루의 마지막 4시간 30분이 기록에 없다

한국 주식의 실제 하루는 KRX 15:30이 아니라 NXT 애프터마켓 20:00에 끝난다. Slack 브리핑은
이미 그것을 알고 20:15까지 발송하고 종목 행에 거래소를 밝힌다
(`slack_kr_market_briefing` 모듈 docstring). 그런데 **추론 층은 그 구간을 아예 보지 않는다.**

- 장후 리뷰(`market_thesis_review`)의 `as_of`는 **15:30 마감**이다. 그 이후 정보는 재실행마다
  근거가 달라지는 것을 막으려고 **일부러** 뺀다(`modules/thesis_review.py` docstring).
- 관측 상태와 채점은 `stock_investor_trade_daily.close_price`(18:10 수집 확정 종가)만 읽는다
  (`modules/thesis_common.py`의 `observed_state`). 분봉을 쓰지 않는 것은 2026-08-13 005930의
  마감 동시호가 누락 실측 때문이고 그 판단은 지금도 유효하다.
- 추론 툴 `macro_changes`는 `quote_bar` 뷰를 읽는데 **그 뷰는 NXT를 태우지 않는다**
  (`b91f4e2a6c53_add_sk_hynix_adr.py`, 같은 종목·같은 분에 두 줄이 생기기 때문). 모델이
  애프터마켓을 조회할 경로가 **하나도 없다.**

그래서 "삼성전자가 애프터마켓에서 왜 빠졌나"에 답할 기록이 남지 않는다. 데이터는 이미 있다 —
realtime WebSocket이 장중에 `stock_bar`(`exchange='NXT'`)에 쌓고 20:05
`kis_stock_minute_bars_daily`가 REST로 확정(`is_final=true`)한다.

**기존 리뷰를 늘리지 않고 슬롯을 하나 더 만든다.** 기존 리뷰에 애프터 데이터를 얹으면
그 슬롯의 event-time cutoff(15:30)가 깨진다. 저장소 규칙 "슬롯·모드로 갈리는 DAG는
나눈다"가 이 경우를 그대로 다룬다(2026-08-21 장전·장후 분리와 같은 판단).

## 1. 결정 사항

| 항목 | 값 | 이유 |
| --- | --- | --- |
| 슬롯 값 | `post_nxt_close` | `run_slot` 컬럼이 String(20). 14자라 들어간다 |
| 추론 대상 | **종목 둘만**(005930, 000660) | NXT에 지수가 없다. 지수를 대상으로 두면 모델이 매번 "지수는 안 움직였다"를 쓴다 |
| 기준 시각 `as_of` | 당일 **20:00 KST** | NXT 애프터마켓 마감. 실행 시각(21:00)이 아니다 |
| 매크로 창의 시작 | 당일 **15:30 KST** | KRX 마감. "정규장이 닫힌 뒤 무엇이 움직였나"가 이 창 |
| 스케줄 | KST 평일 **21:00** | 20:05 REST 백필이 `is_final`을 확정한 뒤 + 재시도 여유 |
| Slack | MARKET 채널, 헤더 `🌙 애프터마켓 리뷰` | 기존 두 슬롯과 같은 채널. 헤더로 갈린다 |
| 채점 | **없음** | 리뷰는 예측이 아니다(기존 `post_close`와 같은 이유) |
| 사후 해설 | **없음**(첫 컷) | 3절 참조 |

## 2. 시각 축과 관측 상태

```
15:30 ─────────────────────────────── 20:00        21:00
KRX 마감                          NXT 애프터 마감    DAG 실행
= 애프터마켓 시작                  = as_of
= macro_window_start
```

**`macro_changes` 툴은 그대로 쓴다.** 이 창(15:30~20:00)에 미국 선물·환율·금리가 움직이고
그 값들은 `quote_bar` 뷰에 있다 — 뷰의 NXT 제외는 **stock에만** 걸린다. 애프터마켓 해석의
핵심 외생 변수가 이 창에서 나오므로 새 툴 없이 첫 컷이 선다.

관측 상태는 세 덩이다.

```json
{
  "session": "2026-08-22",
  "regular":    {"005930": {"close": 71000, "return_pct": 1.2}},
  "after_hours":{"005930": {"close": 71500, "return_pct": 0.70,
                            "last_bar_at": "2026-08-22T10:59:00Z", "bars": 264}},
  "index_regular": {"KOSPI": {"close": 3150.2, "return_pct": 0.8},
                    "KOSDAQ": {"close": 790.1, "return_pct": -0.3}}
}
```

- `regular`와 `index_regular`는 `thesis_common.observed_state`가 이미 만드는 값과 **같은
  원본**(확정 종가, 15:30 `index_bar`)이라 그 함수를 그대로 부른다.
- `index_regular`라는 이름을 쓰는 이유는 **지수가 subject가 아니라 맥락이기 때문이다.**
  키 이름이 그 사실을 밝혀야 모델이 지수에 대한 추론을 쓰지 않는다.
- `after_hours`만 새 SQL이 채운다.

**정규장 등락을 함께 주는 이유**: 애프터 등락만 주면 "왜 애프터에서 더 빠졌나"를 말할 수
없다. 정규장에서 이미 3% 빠진 종목이 애프터에서 0.7% 더 빠진 것과, 정규장에서 오른
종목이 애프터에서 빠진 것은 완전히 다른 이야기다.

## 3. 채점도 해설도 붙이지 않는다

**채점 없음** — 리뷰는 이미 일어난 일의 해석이라 예측이 아니다. 기존 `post_close`와 같다.
`thesis_outcome/select_pending_grades.sql`이 이미 `run_slot = 'pre_open'` 리터럴이라 새 슬롯은
**자동으로** 빠진다. 손댈 것이 없다.

**사후 해설 없음(첫 컷)** — 이쪽은 자동으로 빠지지 않는다. 손대야 한다.

`thesis_outcome/select_pending_narratives.sql`에는 **슬롯 필터가 없다**
(`WHERE thesis.run_date = %s AND outcome.narrative IS NULL`). 그래서 새 슬롯 행이 그대로
T+1·3·5 해설 대상이 된다. 그런데 `thesis_review.narrate_followups`가
`run_slot=RunSlot.PRE_OPEN`을 **하드코딩**해서 넘기고,
`FollowupNarrator.build_messages`는 `"전" if run_slot is RunSlot.PRE_OPEN else "후"`라는
**이진 분기**다. 결과: 새 슬롯 추론이 "장전에 쓴 추론"으로 라벨링돼 프롬프트가 거짓말을 한다.

그래서 두 SQL에 슬롯 목록을 리터럴로 건다.

| 파일 | 넣을 것 |
| --- | --- |
| `thesis_outcome/select_pending_narratives.sql` | `AND thesis.run_slot IN ('pre_open', 'post_close')` |
| `thesis_outcome/select_backlog.sql` | `unnarrated` FILTER에 같은 조건 |

**백로그를 함께 고치는 것이 중요하다.** `select_backlog.sql`의 `unnarrated` FILTER에도
슬롯 조건이 없어서, 해설 대상에서 뺀 슬롯이 거기 남으면 목표일이 지난 뒤 **영구히** 밀림으로
잡힌다. 하루 종목 2 × 지평 3 = 6건씩 누적돼 OPS 브리핑이 거짓 경보를 내고, 그 파일 머리말이
경고한 "매일 같은 숫자가 떠서 아무도 안 보게 된다"가 그대로 일어난다.
**두 SQL이 같은 목록을 봐야 한다** — 어긋나면 한쪽은 해설을 안 만들고 다른 쪽은 그것을
밀림으로 센다. 서로를 주석으로 가리켜 둔다.

파라미터가 아니라 리터럴인 것은 같은 디렉터리의 `select_pending_grades.sql`이 이미
`'pre_open'` 리터럴이기 때문이다. `briefing/ops.py`는 손대지 않는다 — 파라미터 수가 그대로고,
그 모듈이 LLM 층의 상수를 import하지 않는다는 원칙도 지켜진다.

**나중에 붙이려면** `NarrativeTarget`에 `run_slot`을 싣고 슬롯별로 호출을 나눠야 한다
(지평당 최대 3배). 그건 `post_close`의 기존 라벨 결함과 함께 별건으로 푼다(9절).

## 4. 새 모듈 `airflow/modules/thesis_nxt_review.py`

**`thesis_common.py`에는 아무 것도 추가하지 않는다** — 그 모듈의 원칙은 "슬롯을 알 필요가
없는 것만 둔다"이고, 여기 있는 것은 전부 슬롯을 안다.

저장소 규칙 "클래스와 함수를 가르는 기준"을 따른다. **연결과 세션 날짜가 상태다** — 조회
셋(추론 대상, 애프터 봉, 확정 종가)이 전부 그 둘을 쓰므로 함수로 두면 인자에 매번 다시
들어간다. 그 반복이 규칙이 지목하는 신호다. 반대로 기준 시각 계산은 날짜 하나를 받아 시각
하나를 주는 순수 계산이라 **모듈 함수로 남긴다** — 감쌀 상태가 없는 것을 클래스로 만들지
않는다.

```python
SLOT = "post_nxt_close"
AFTER_HOURS_OPEN = time(15, 30)   # KRX 마감. 실측 첫 애프터 봉은 15:40이다(5절)
AFTER_HOURS_CLOSE = time(20, 0)   # NXT 마감

# 순수 계산 — 감쌀 상태가 없다
def as_of(run_date: date) -> datetime
def macro_window_start(run_date: date) -> datetime
def after_hours_window(run_date: date) -> tuple[datetime, datetime]


class AfterHoursBar(BaseModel):
    """`select_nxt_after_hours.sql`이 주는 한 줄. `frozen=True`."""
    stock_code: str
    last_bar_at: AwareDatetime
    last_close: Decimal
    bar_count: int
    all_final: bool
    settled_close: Decimal | None
    return_pct: Decimal | None

    @classmethod
    def from_row(cls, row: tuple) -> "AfterHoursBar"


class NxtAfterHoursReview:
    def __init__(self, connection, *, run_date: date) -> None

    @property
    def targets(self) -> tuple      # lazy. 종목만
    @property
    def watched(self) -> list[str]
    @property
    def bars(self) -> tuple[AfterHoursBar, ...]   # lazy

    def check_ready(self) -> None
    def observed_state(self) -> dict[str, Any]
    def run(self, *, dag_run_id: str) -> int


def build() -> dict[str, Any]      # Airflow 컨텍스트를 읽는 얇은 진입점
```

- **생성자는 이 실행 동안 안 변하는 것만 받는다.** 연결과 세션 날짜뿐이다.
- **`targets`와 `bars`는 처음 한 번만 조회하고 들고 있는다.** `check_ready`와
  `observed_state`가 같은 값을 봐야 하고, 두 번 읽으면 그 사이에 값이 갈릴 수 있다.
- **SQL 한 줄은 `AfterHoursBar`다.** 맨 tuple을 `row[4]`로 인덱싱하면 SQL이 열을 늘릴 때
  값이 조용히 옆 칸으로 밀린다. 컬럼 순서를 아는 자리는 `from_row` 하나뿐이다.
- **대상 필터는 `targets` 안에 있다.** `subjects()`가 주는 지수 둘을 여기서 뺀다.

`run()`은 `thesis_common.build_and_store(...)`를 그대로 부른다 — 그 함수는 이미 슬롯
무관이고 `targets`·`observed`·`as_of_at`만 다르게 받는다. Slack 발송도
`thesis_common.notify_slack`을 그대로 쓴다(`SLOT_HEADERS`에 한 줄 추가하면 통과한다).

### readiness guard — 판정 넷

| 순서 | 조건 | 결과 |
| --- | --- | --- |
| 1 | `krx_open_day(conn, run_date) is False` | `AirflowSkipException` |
| 2 | 확정 종가가 watched 종목 전부에 있는가 | 없으면 `ThesisNotReady` |
| 3 | 애프터 창의 NXT 봉이 0개인가 | `AirflowSkipException` |
| 4 | 봉은 있는데 전부 `is_final = false`인가 | `ThesisNotReady` |

- **2는 등락률의 분모다.** 당일 15:30 확정 종가가 없으면 애프터 등락을 계산할 수 없다.
  `kis_investor_trade_daily`가 18:10에 채우므로 21:00이면 있어야 한다.
- **3이 skip인 이유**: 애프터마켓 체결이 진짜 0인 날이 있을 수 있고(휴장 전날, 연휴),
  수집 실패와 응답만으로 가를 수 없다. `kis_stock_minute_bars_daily`도 0봉을 INFO로
  넘긴다. 죽여 봐야 매일 같은 빨간 실행이 남을 뿐이다.
- **4가 재시도인 이유**: 21:00에 잠정 봉만 있다는 것은 20:05 REST 백필이 돌지 않았거나
  실패한 것이다. 그대로 추론하면 **첫 성공본 불변** 규칙 때문에 나중에 REST가 값을
  바로잡아도 추론은 잘못된 값 위에 영원히 남는다. 저장소 규칙 "조용한 성공을 만들지
  않는다"가 이것이다. 재시도 3회 × 10분이 21:30까지 기다린다.

**3과 4를 가르는 축은 "재시도로 풀리는가"다.** 봉이 없는 것은 기다려도 안 생기고,
잠정만 있는 것은 백필이 늦게 돌면 확정된다.

## 5. 새 SQL `airflow/sql/postgres/stock_bar/select_nxt_after_hours.sql`

**관측 상태 전용이다. 툴로 열지 않는다** — YAGNI. 모델이 애프터 분봉 흐름을 직접 뒤질
필요가 생기면(예: "몇 시에 빠졌나"를 이유에 쓰기 시작하면) 그때 `nxt_intraday_series`를
**별도 파일로** 연다(저장소 규칙: 툴을 늘릴 때 조회 SQL은 새 파일로 만든다).

파라미터 넷: `(window_start_utc, window_end_utc, business_date, stock_codes[])`.
KST 경계를 UTC로 만드는 일은 파이썬이 한다(`index_bar/select_session_return.sql` 계보).

반환: `stock_code, last_bar_at, last_close, settled_close, return_pct, bar_count, all_final`

주석에 남길 판단 셋:

- **애프터마켓만 걸러야 한다.** NXT는 프리(08:00~08:50)·주간(09:00~15:20)도 체결하므로
  `exchange = 'NXT'`만으로는 하루 전체가 섞인다. 창의 양 끝을 파라미터로 받는다.
  **실측(2026-08-21 운영 DB)**: 하루 690봉 중 애프터는 260봉이고 15:40~19:59가 정확히
  260분이라 구멍이 없다. 창의 하한은 KRX 마감 15:30으로 두되 첫 봉은 15:40이다 — 그
  10분에 봉이 없어 결과가 같고, NXT가 나중에 15:30부터 열면 코드를 안 고쳐도 잡힌다.
- **분모가 `stock_bar.previous_close`가 아니다.** 그 칸은 **전일** KRX 확정 종가라
  (스키마 주석) 정규장 등락률의 분모다. 애프터 등락은 **당일 15:30 확정 종가** 대비라
  `stock_investor_trade_daily.close_price`(`stck_clpr`)를 조인한다. 여기를 틀리면 애프터에서
  0.7% 움직인 것이 하루 등락 1.9%로 조용히 부풀려진다.
- **`bar_count`와 `all_final`을 함께 준다.** guard 판정 3·4가 이 두 칸을 읽는다.

창 상한은 `bar_at <= window_end`다. `quote_bar/select_window_changes.sql`의
`bar_at + interval '1 minute' <= as_of_at` 규칙을 쓰지 않는다 — 그 규칙은 창 **도중**을
자를 때 경계 봉이 담은 미래 1분을 빼는 것이고, 여기 20:00 봉은 NXT 마감 체결이라 세션의
일부다. 배제하면 세션의 끝을 못 본다. 실행이 21:00이라 미래 정보 문제도 없다.

## 6. 슬롯 어휘와 스키마

### `airflow/modules/thesis.py`

| 자리 | 추가 |
| --- | --- |
| `RunSlot` | `POST_NXT_CLOSE = "post_nxt_close"` |
| `SLOT_INSTRUCTION` | 아래 문구 |
| `SLOT_HEADERS` | `"🌙 애프터마켓 리뷰"` |
| `SLOT_LABELS` | `"애프터마켓 리뷰"` |

세 dict 모두 `dict[RunSlot, str]` 조회라 빠뜨리면 `KeyError`다.

`SLOT_INSTRUCTION` 문구:

> 한국 정규장(KRX)이 15:30에 닫히고 NXT 애프터마켓이 20:00에 닫혔다. 관측 상태에 정규장
> 등락(`regular`)과 애프터마켓 등락(`after_hours`)이 따로 있다. **정규장이 닫힌 뒤 무엇이
> 애프터마켓을 움직였는지**를 가설로 적어라. 지수(`index_regular`)는 정규장 마감값이라
> 애프터마켓 움직임을 담지 않는다 — 맥락으로만 읽어라.

### `apps/models/analysis.py` + 수기 리비전

- `RunSlot`에 값 추가
- `ck_thesis_run_slot` CHECK 문자열
- `run_slot` 컬럼 주석
- `as_of_at` 컬럼 주석(슬롯별 시각을 나열하고 있다)

리비전은 `down_revision = "6e09dafae6f8"`(현재 head)이고
`b91f4e2a6c53_add_sk_hynix_adr.py`의 헬퍼 형태를 따른다 — CHECK를 drop 후 recreate 하고
**값 집합을 나열하는 컬럼 주석도 함께 다시 찍는다.**

```python
def _set_slot_axis(slots: tuple[str, ...], comment: str) -> None:
    check = "run_slot IN ({})".format(", ".join(f"'{value}'" for value in slots))
    op.drop_constraint("ck_thesis_run_slot", "thesis", type_="check")
    op.create_check_constraint("ck_thesis_run_slot", "thesis", check)
    op.execute(f"COMMENT ON COLUMN thesis.run_slot IS '{comment}'")
```

`downgrade_default()`는 값 집합을 되돌린다. **`post_nxt_close` 행이 남아 있으면 CHECK 생성이
실패하고 그게 맞다** — 데이터를 조용히 지우지 않는다. 리비전 docstring에 적는다.

모델과 리비전의 CHECK 문자열·컬럼 주석은 **글자 그대로** 같아야 한다.

## 7. DAG `airflow/dags/market_thesis_nxt_review.py`

```python
dag_id="market_thesis_nxt_review"
dag_display_name="🧠 시장 추론 · 애프터마켓 리뷰 (LLM)"
schedule="0 21 * * 1-5"   # KST 평일 21:00 = UTC 월~금 12:00
```

태스크 둘: `build_thesis >> notify_slack`. 채점도 해설도 없다(3절).

실패 판정은 기존 두 DAG와 같다.

- `LlmError`·`ThesisError` → `AirflowFailException`(재시도해도 같다)
- `RetryableLlmError`·`ConnectionError`·`ThesisNotReady` → 그대로 올려 Airflow가 재시도
- `SlackError` → `AirflowFailException`. 발송은 at-least-once다

## 8. 테스트

| 파일 | 무엇 |
| --- | --- |
| `tests/dags/test_market_thesis_nxt_review.py` (신규) | 태스크 그래프, 스케줄, `SLOT`, 표시 메타데이터, `as_of`·`macro_window_start`·`after_hours_window`, `AfterHoursBar.from_row`와 frozen, `NxtAfterHoursReview`의 대상 필터·조회 1회·창 파라미터·guard 판정 넷·관측 상태 |
| `tests/modules/test_thesis.py` | 새 SQL 텍스트(주석에 `%` 없음, `now()` 없음, 분모가 확정 종가), `_statement_key` 분기, 해설·백로그 SQL의 슬롯 목록 |
| `tests/migrations/test_thesis_schema.py` | 전체 dump에서 새 CHECK 문자열 |

**자동으로 검사 대상이 되는 것**: `tests/models/test_analysis_models.py`의
`test_analysis_check_constraints_repeat_the_enum_values`와 `tests/modules/test_thesis.py`의
`test_the_airflow_enums_match_the_backend_vocabulary`는 enum을 순회하므로 값을 넣는 순간
CHECK 문자열과 두 enum의 일치를 강제한다.

**해설 SQL 테스트를 반드시 보강한다.** `test_pending_narratives_covers_both_slots`의 기존
`assert "run_slot = 'pre_open'" not in query`는 우리 문구(`IN (...)`)로도 그대로 통과한다.
**"둘만"을 고정하는 assertion을 더한다** — 그러지 않으면 나중에 슬롯이 또 늘 때 조용히
해설 루프에 들어간다.

**운영 DB 확인**: 새 SQL은 읽기 전용으로 한 번 돌려 보고 넣는다. 테스트는 가짜 연결을
쓰므로 컬럼 이름과 조인 조건이 틀려도 통과한다. 2026-08-21에 같은 확인이 결함 둘을 잡았다.

### 2026-08-21 세션으로 한 실측

이 슬롯이 실제로 설 수 있는지를 하루치로 확인했다. **그날 애프터마켓에서 005930이 -4.09%,
000660이 +1.79%로 방향이 갈렸다** — 두 종목이 같은 창에서 반대로 움직인 날이라 확인 대상이
좋았다.

- **`previous_close`를 분모로 안 쓴 판단이 맞았다.** 그 칸은 두 종목 모두 전일 확정 종가와
  글자 그대로 같았다(005930 271,000 / 000660 1,691,000). 그것으로 나눴다면 -4.09%가
  **-0.37%**로 나와 하락을 통째로 놓친다. 당일 확정 종가(281,500)가 옳은 분모다.
- **마지막 봉은 대표성이 있다.** 19:59 봉이 005930 179,761주다. 하락은 17:00~17:30 구간에서
  거래량 7,596,317주(그날 애프터 평균의 14배)와 함께 일어났고 그 뒤로는 옆으로 갔다.
  마지막 1분의 단발 체결이 아니라 세션의 결론이라 `last_close`를 그대로 쓴다.
- **NXT는 15:19까지 찍히다 15:40에 재개한다.** 15:20~15:39가 정규장 마감 처리 구간이다.
  창 하한 15:30은 그 빈 구간에 걸쳐 결과에 영향이 없다(5절).
- **근거가 창 안에 있다.** 자사주 취득 결정 공시가 17:20 접수, 그것을 해석한 기사가
  17:58 발행 → 18:06 감지 → 18:28 평가(`value_score` 8, `direction` negative)다. 셋 다
  `as_of` 20:00 안이라 모델이 툴로 집어 온다. **공시 제목만으로는 방향을 못 읽는다** —
  "자기주식취득결정"은 보통 호재로 읽히고, 그날 하락의 이유(매입만 있고 소각이 없어 이미
  반영된 기대가 되돌려졌다)는 기사 본문에만 있다. 이 슬롯이 `recent_documents`와
  `recent_disclosures`를 **둘 다** 여는 이유가 그것이다.
- **문서 수집에 저녁 사각지대가 없다.** `document_ingestion_hourly`의 `5 * * * *`는 시간
  제한이 없어 24시간 돈다(그날 16~21시 각 시간대에 4~16건이 들어왔고 전부 평가됐다).
  시간이 제한된 것은 `dart_disclosure_intraday`(`*/2 7-20`)뿐이고 그것도 20시까지라
  애프터마켓 전체를 덮는다.

## 9. 이 단계가 하지 않는 것

- **애프터마켓 분봉 툴** — 첫 컷은 관측 상태만. 5절.
- **채점** — 리뷰는 예측이 아니다. 3절.
- **사후 해설** — 3절. 붙이려면 `NarrativeTarget`에 `run_slot`을 싣고 슬롯별로 호출을
  나눠야 한다.
- **NXT 휴장 캘린더** — `market_session`에 NXT market_code가 없다. NXT 달력이 KRX와 다른
  날이 실제로 생기면 그때 만든다. 지금은 `krx_open_day` 하나를 본다.
- **`thesis_review.narrate_followups`의 `RunSlot.PRE_OPEN` 하드코딩** — 이미 `post_close`
  추론의 해설 프롬프트를 "장전에 쓴 추론"으로 라벨링하고 있는 **기존 결함**이다. 이 단계는
  새 슬롯이 그 경로에 들어가지 않게만 막는다. 별건으로 푼다.
