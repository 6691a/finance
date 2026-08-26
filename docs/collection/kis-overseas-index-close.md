# 미국 현물 지수 마감 분봉(KIS)과 미국장 브리핑 섹션 분리

> 작성 기준: 2026-08-22  
> 상태: 구현 완료(2026-08-22). 수집기는 `KisOverseasIndexCollector` 클래스다(2026-08-23)  
> 대상: S&P500(`SPX`)·나스닥 종합(`COMP`) 현물 마감 분봉 수집, `slack_us_market_briefing` 표 분리  
> 의존: `quote_symbol` 마스터, `index_bar`, `market_session`, `modules/briefing/market.py`  
> 산출물: `airflow/modules/collectors/market/kis_overseas_index.py`, `airflow/dags/kis_overseas_index_close.py`,
> `quote_symbol` 시드 리비전, `market.py` 섹션 분리, 테스트

## 1. 문제

미국장 아침 브리핑(`slack_us_market_briefing`, KST 화~토 08:00)의 `미국 지수·선물` 표 하나에
지수·지수선물·국채선물·원자재(구리·금·은·WTI)·크립토(BTC·ETH)·ADR이 전부 들어간다.
`US_KIND_ORDER`가 줄 순서만 종류로 묶을 뿐 섹션 제목이 없어 읽는 사람이 어디서 성격이
바뀌는지 눈으로 찾아야 한다.

또 S&P500·나스닥·다우는 **선물만** 수집한다(Yahoo `ES=F`·`NQ=F`·`YM=F`). 미국 현물 지수는
VIX·SOX·러셀2000(Yahoo)뿐이라 "미국장 마감" 브리핑에 정작 S&P500·나스닥의 마감 현물값이 없다.

## 2. 결정

| 항목 | 결정 | 이유 |
| --- | --- | --- |
| 현물 소스 | **KIS 해외지수 분봉 API** | 국내에서 받을 수 있으면 국내 우선. 프로브로 동작 확인(§3). 무료 — CME 시세료는 선물 API 이야기다 |
| 심볼 | `SP500`(KIS `SPX`), `NASDAQ`(KIS `COMP`) | 나스닥은 **종합지수**로 한다(사용자 결정). 러셀 현물은 이미 Yahoo `RUSSELL2000`. 다우 현물은 요청 밖이고 KIS 분봉이 0건이라 넣지 않는다 |
| 저장 | `index_bar`에 `provider='kis'`, 1분봉 | 브리핑이 `quote_bar`(1분봉 뷰)만 읽는다. 일봉 경로를 새로 열면 브리핑 SQL과 병합 규칙을 함께 고쳐야 해서 더 크다 |
| 수집 시점 | 마감 뒤 하루 한 번, KST 화~토 07:30 | 브리핑이 필요한 건 마감값이다. API가 "최근 102봉"만 주므로 장중 폴링은 나중에 필요해지면 붙인다 |
| 브리핑 | `미국 지수·선물` / `원자재` / `크립토` / `ADR` 네 섹션 | 현물 옆에 그 선물을 놓는다. 빈 섹션은 그리지 않는다 |

## 3. KIS 해외지수 API 실측 (2026-08-22, 운영 앱키)

분봉:

```
GET /uapi/overseas-price/v1/quotations/inquire-time-indexchartprice
tr_id: FHKST03030200
FID_COND_MRKT_DIV_CODE=N   # N = 해외지수
FID_INPUT_ISCD=SPX         # 또는 COMP
FID_HOUR_CLS_CODE=0        # 0 = 정규장. 1은 빈 응답
FID_PW_DATA_INCU_YN=Y
```

- `output1`: `hts_kor_isnm`(`S&P500`, `나스닥 종합`), `ovrs_nmix_prpr`(현재·마감값),
  **`ovrs_nmix_prdy_clpr`(전일 종가 — `previous_close`로 쓴다)**, `prdy_ctrt`, `stck_shrn_iscd`(요청 코드).
- `output2`: **102봉, 최신순.** 칸은 `stck_bsop_date`, `stck_cntg_hour`, `optn_oprc`, `optn_hgpr`,
  `optn_lwpr`, `optn_prpr`, `cntg_vol`. 지수라 `cntg_vol`은 0.
- **시각은 America/New_York 벽시계다.** 2026-08-21 응답이 14:40~16:41 봉을 줬다. 16:00 봉이 현물 마감에
  해당하고(`7674.30`), 16:01~16:41은 값이 거의 고정된 **정산 구간** 봉이며 마지막 봉 `7674.37`이 일봉
  API가 주는 공식 종가와 같다.
- 일봉 API(`inquire-daily-chartprice`, `FHKST03030100`)도 되지만 브리핑이 안 읽는 경로라 쓰지 않는다.
- 안 되는 것: `.DJI`(다우) 분봉 0건(일봉은 옴), `RUT`(러셀2000) 일봉·분봉 모두 0건.

프로브 스크립트는 저장소 밖(스크래치패드)에 있고 키는 `config.yaml`에서 읽었다.

## 4. 수집기 — `airflow/modules/collectors/market/kis_overseas_index.py`

`calendar/kis_market_calendar.py`·`market/kis_positioning.py`와 같은 꼴로 `modules/collectors/kis.py`의 `send_get`,
`access_token`, `QuoteBar`, 예외 타입, `_decimal`을 재사용한다. `KisRawBar`·`parse_bars`는 국내
전용(`futs_*`/`bstp_nmix_*` 칸, KST)이라 재사용하지 않는다.

### 4.1 정의

- `OverseasIndex(StrEnum)` — `DomesticIndex`처럼 `(symbol, kis_code, label)`:
  `SP500 = ("SP500", "SPX", "S&P500")`, `NASDAQ = ("NASDAQ", "COMP", "나스닥 종합")`.
  Enum으로 좁히는 이유: KIS는 모르는 코드에도 `rt_cd=0`·0건으로 답한다(`.DJI`, `RUT` 실측).
- 상수: `OVERSEAS_INDEX_CHART_PATH`, `OVERSEAS_INDEX_CHART_TR_ID = "FHKST03030200"`,
  `SOURCE_KEY = "overseas_index_1m"`, `MARKET_DIV_CODE = "N"`, `HOUR_CLS_CODE = "0"`,
  `US_EASTERN = ZoneInfo("America/New_York")`.
- Pydantic(`frozen=True`): `KisOverseasRawBar`, `KisOverseasChartHead`, `KisOverseasChartPayload`,
  결과 `OverseasIndexFetch(index, session_date, bars, started_at, completed_at, status)`.

### 4.2 파싱 규칙

| 상황 | 처리 |
| --- | --- |
| JSON이 스키마와 다름 | `KisPayloadError from error` |
| `rt_cd != "0"` | `KisResultError(msg_cd, msg1)` — 재시도 여부는 DAG가 정한다 |
| `output2` 비어 있음 | `KisPayloadError`. 조용한 0건을 만들지 않는다 |
| `stck_shrn_iscd`가 있고 요청 코드와 다름 | `KisPayloadError`. 요청 안 한 식별자가 섞이면 멈춘다 |
| `ovrs_nmix_prdy_clpr` 없음·0 | `KisPayloadError`. 등락 계산의 분모다 |
| 봉의 `stck_bsop_date`가 기대 세션 날짜와 다름 | **`KisPayloadError`.** 묵은 봉을 오늘 것처럼 저장하지 않는다 |
| 시각 변환 | `strptime(date + hour, "%Y%m%d%H%M%S").replace(tzinfo=US_EASTERN).astimezone(UTC)`. 국내 파서처럼 시프트 없음 |
| 정산 구간 봉(16:01~16:41) | **저장한다.** 브리핑의 `DISTINCT ON … bar_at DESC`가 마지막 봉을 집으므로 공식 종가가 브리핑에 실린다 |

반환은 `bar_at` 오름차순.

### 4.3 저장

`source_record` 1행(`api`, `kis`, `overseas_index_1m`, payload `NULL`, metadata에 `symbol`·`kis_code`·
`session_date`·`bar_count`·`latest_bar_at`) → `index_bar` upsert(`airflow/sql/postgres/index_bar/upsert.sql`,
`kis.store_bars`와 같은 튜플 모양). `previous_close`는 모든 봉에 `output1.ovrs_nmix_prdy_clpr`.

## 5. DAG — `airflow/dags/kis_overseas_index_close.py`

```python
SCHEDULE = "30 7 * * 2-6"  # KST 화~토 07:30 = UTC 월~금 22:30
```

- 미국 마감(KST 05:00/06:00) 뒤, `market_calendar_daily`(07:00)가 `market_session`을 갱신한 뒤,
  `slack_us_market_briefing`(08:00) 앞. 화~토인 이유는 브리핑과 같다 — 월요일 아침엔 직전 미국 세션이 없다.
- 메타: `dag_display_name="🇺🇸 미국 지수 마감 1분봉 (KIS)"`, `description`, `doc_md=__doc__`. `Param` 없음 —
  API에 날짜 커서가 없어 "최근 102봉" 말고는 받을 것이 없다. 묵은 날짜 검사(§4.2)가 가드다.
- 태스크 하나 `collect`:
  1. `session_date = now.astimezone(US_EASTERN).date()` — `briefing/market.us_session_date`와 같은 값이다.
     브리핑 모듈을 import하지 않고 테스트로 대조한다.
  2. `us_equity_open_day(connection, session_date) is False` → `AirflowSkipException`. `None`(캘린더 없음)은 진행.
  3. 자격(`KIS_APP_KEY`/`KIS_APP_SECRET`)·토큰(`access_token(Variable, …)`, 캐시 키 `kis_access_token` 공유)·
     재시도 헬퍼는 `market_calendar_daily.py`의 `_credentials`·`_cached_token`·`_fetch_with_retry`와 같다:
     400/403/404 즉시 실패, 401은 토큰 한 번 재발급, 그 밖의 HTTP·네트워크 오류는 Airflow 재시도.
  4. 두 심볼을 먼저 다 받는다. `KisResultError`·`KisPayloadError`는 즉시 `AirflowFailException`.
     심볼이 둘뿐이라 항목별 실패 수집 대신 **하나라도 실패하면 죽인다.**
  5. `atomic(connection)` 안에서 둘 다 저장. 심볼별 `bar_count`·`latest_bar_at` 로그.
- `default_args={"retries": 2, "retry_delay": timedelta(minutes=5)}`, `max_active_runs=1`, `catchup=False`,
  `start_date=pendulum.datetime(2026, 8, 22, tz=KST_TIMEZONE)`.

## 6. 마스터 시드 — `quote_symbol`

리비전은 **손으로 쓴다**(`makemigrations`는 운영 DB에 붙으므로 쓰지 않는다). `down_revision = "6e09dafae6f8"`.
`b91f4e2a6c53_add_sk_hynix_adr.py`가 틀이다.

| provider | symbol | kind | country | country_name | label |
| --- | --- | --- | --- | --- | --- |
| `kis` | `SP500` | `index` | `US` | `미국` | `S&P500` |
| `kis` | `NASDAQ` | `index` | `US` | `미국` | `나스닥 종합` |

`downgrade`는 두 행 DELETE. `tests/migrations/test_quote_symbol_catalog.py`의 기대 집합과
`collected_symbols()`에 `OverseasIndex`를 넣어 Enum과 시드를 대조한다.

## 7. 브리핑 — `airflow/modules/briefing/market.py`

`US_KIND_ORDER`를 없애고 둘을 둔다.

```python
US_SYMBOL_ORDER = ("SP500", "SP500_FUT", "NASDAQ", "NASDAQ100_FUT", "DOW_FUT",
                   "RUSSELL2000", "RUSSELL2000_FUT", "SOX", "VIX", "US10Y_FUT")
US_SECTIONS = (
    ("미국 지수·선물", frozenset({"index", "index_future", "bond_future"})),
    ("원자재", frozenset({"commodity"})),
    ("크립토", frozenset({"crypto"})),
    ("ADR", frozenset({"equity"})),
)  # kind 합집합은 QUOTED_KINDS와 같아야 한다. 테스트가 대조한다.
```

- `_us_quote_sections(summary)`: 지금 `_us_quotes`의 선별(`QUOTED_KINDS` ∧ (`country == "US"` ∨ crypto ∨ ADR))을
  그대로 쓰고 `US_SECTIONS`마다 kind로 걸러 `_ordered(…, lambda q: q.symbol, US_SYMBOL_ORDER)`로 정렬한다.
  모르는 심볼은 `_ordered`가 뒤로 보낸다.
- `_us_quotes`는 섹션들을 평탄화한 것으로 바꾼다. `_scope_quotes`를 거치는 `render_text`(텍스트 폴백)와
  `_as_of`(기준 시각 footer)가 같은 순서를 따라간다. 시장 LLM 요약 입력은 2026-08-19에 제거돼 손댈 것이 없다.
- `render_blocks` US 분기는 `_quote_section` 하나 대신 섹션 루프. `_quote_section`이 빈 튜플에 `[]`를 주므로
  빈 섹션은 저절로 빠진다.
- 한국장 리포트는 구조상 영향이 없다. `_intraday_overseas`는 `country == "US"`이면서 `index_future`만 넣고
  `_korea_quotes`는 `KR`만 넣는다. KIS `SP500`(`index`, `US`)은 어느 쪽에도 안 걸린다. 회귀 테스트만 둔다.

렌더 결과(예):

```
🌙 미국장 마감 · 08/21(현지) · 08/22(토) 08:00 KST
미국 지수·선물   S&P500 / S&P500 선물 / 나스닥 종합 / 나스닥100 선물 / 다우 선물 / 러셀2000 / 러셀2000 선물 / SOX / VIX / 미 10년 국채선물
원자재           금 / 은 / 구리 / WTI 원유
크립토           비트코인 / 이더리움
ADR              TSMC ADR / SK하이닉스 ADR
주요국 10년 금리 …
```

## 8. 테스트

- `tests/collectors/test_kis_overseas_index.py`: 뉴욕 벽시계→UTC(여름 `20260821 164100`→`20:41Z`, 겨울
  `20260115 160000`→`21:00Z`), 오름차순·정산봉 보존·마지막 봉 = 종가, `previous_close`는 `output1`에서,
  묵은 날짜·다른 코드·빈 차트·`rt_cd`·비수치 실패, `fetch`가 §3의 쿼리를 보내는지, `store`의 `source_record`
  1행(payload `NULL`)과 `index_bar` 컬럼 계약(`IndexBar.__table__` 대조), `OverseasIndex` 값이
  `yahoo.QuoteSymbol`·`kis.DomesticIndex`와 안 겹침.
- `tests/dags/test_kis_overseas_index_close.py`: 스케줄이 `"30 7 * * 2-6"`이고 브리핑보다 앞, 태스크 하나·
  `max_active_runs == 1`, 메타 채움, `False`만 skip, 세션 날짜 = 뉴욕 날짜(`market.us_session_date`와 같음).
- `tests/migrations/test_quote_symbol_catalog.py`: `EXPECTED_INDEXES`에 `SP500`·`NASDAQ`, 시드가
  `('kis', 'SP500', 'index', 'US', '미국'` 형태.
- `tests/modules/test_briefing_market.py`: 픽스처에 `("kis", "SP500", …)`·`("yahoo", "GOLD", "금", "commodity", …)`
  추가. 섹션 제목 순서, 첫 표가 `S&P500 → S&P500 선물 → …`, 금은 원자재·비트코인은 크립토, 빈 섹션 미출력,
  `US_SECTIONS` kind 합집합 == `QUOTED_KINDS`, `render_text(US)`가 `S&P500`으로 시작, 한국장 두 리포트 표에
  정확히 `S&P500` 셀 없음(`S&P500 선물`은 있어야 하므로 부분일치로 검사하지 않는다).

## 9. 검증과 배포

- 로컬: `uv run pytest tests -q`, `uv run ruff check apps core dags migrations tests airflow`. 오프라인 리비전
  SQL(`upgrade head --sql`)에서 시드 두 행 확인.
- 운영(사용자가 한다): `just migrate upgrade head` → 평일 아침 `kis_overseas_index_close` 1회 →
  `SELECT symbol, max(bar_at), count(*) FROM index_bar WHERE provider = 'kis' GROUP BY 1`의 마지막 봉 종가가
  공식 종가와 같은지 → 브리핑 미리보기는 `slack_channel_test`로 `🧪 테스트 발송` 머리표를 달아서.

## 10. 남은 확인 (spike)

- 월요일 미국 휴장 뒤 화요일 07:30 응답이 금요일 봉을 주는지, 빈 102봉을 주는지. 어느 쪽이든 §4.2의
  날짜 검사가 잡지만, 휴장일엔 `us_equity_open_day`가 먼저 skip하므로 실제로는 부딪히지 않을 것이다.
- `stck_shrn_iscd`가 항상 오는지(`.DJI` 응답에는 없었다). 없으면 코드 대조를 건너뛰고 있으면 대조한다.
- 정산 구간 봉의 끝 시각이 날마다 같은지(실측 1회: SPX 16:41, COMP 16:15). 끝 시각은 저장 로직에 영향이 없다.

## 11. 범위 밖

- 다우 현물. KIS 분봉이 없어 Yahoo `^DJI`로만 가능하다.
- 미국 장중 KIS 폴링(1분봉 전체). 지금은 마감 뒤 102봉(14:40~16:41)만 쌓인다.
- Sentry metrics 후보: run당 `bar_count`, `now - latest_bar_at` 지연.

## 12. 추론 툴 `us_market_close()` (2026-08-22 추가)

장전 추론(`market_thesis_forecast`)이 밤사이 미국장 마감을 보게 한다. 기존 `macro_changes()`는
분석 창 `[전 개장일 15:30, 08:35]`의 **첫 봉 대비 마지막 봉**이라 KIS 현물처럼 마감 전 두 시간만
쌓이는 심볼은 변화가 거의 0으로 보인다(SPX 14:40 봉 7671.73 → 16:41 봉 7674.37). 마감 툴은
**전일 정규장 종가(`previous_close`) 대비**를 준다 — 브리핑 표와 같은 수치다.

| 항목 | 결정 |
| --- | --- |
| 이름·인자 | `us_market_close()`, 인자 없음. 창은 슬롯이 정한다 |
| SQL | `airflow/sql/postgres/quote_bar/select_thesis_us_close.sql`(신규). 심볼마다 `bar_at >= macro_window_start AND bar_at + 1 minute <= as_of_at`인 마지막 봉 하나. `country = 'US'`이고 `kind`가 `MACRO_KINDS` 안인 것 |
| 반환 | `symbol`, `label`, `kind`, `close`, `previous_close`, `change_pct`(금리는 `change_bp`), `closed_at`(KST 표기) |
| 근거 | **만든다.** 종류 `macro_change`, ref `macro_change:<symbol>@close`. `macro_changes()`의 `macro_change:<symbol>`과 겹치지 않게 접미를 둔다 — 같은 심볼의 창 변화와 마감 등락은 다른 숫자다. ref는 `<kind>:<id>` 2단이어야 해서(`thesis_evidence.evidence_ref` 주석) 콜론이 아니라 `@`다 |
| 장후 슬롯 | 창이 `[당일 09:00, as_of]`라 미국 봉이 없어 빈 배열이다. 그게 맞다 — 장후 추론은 밤사이가 아니라 당일 세션을 본다 |
| `macro_changes()` | 건드리지 않는다. 설명에 "현물 마감은 `us_market_close`로 본다"를 덧붙인다 |
| 상한 | `MAX_TOOL_RESULTS`(20)로 자른다. 미국 심볼이 지금 14개다 |

툴 수는 11 → 12. `docs/analysis/market-thesis/2-agent.md`의 근거 툴 표(셋 → 넷)와 `TUNING.md`의 수를
고친다. **툴 수가 tool call 상한(`MAX_TOOL_CALLS = 12`)과 같아진다** — 상한에 붙는 실행이
보이면 그 값을 올린다. 새 SQL은 2026-08-22에 운영 DB에 읽기 전용으로 돌려 14행을 확인했다
(SOX -0.51pct가 KIS 응답의 `prdy_ctrt`와 같았다).
