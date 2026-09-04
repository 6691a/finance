# 미국 현물 지수 마감 분봉(KIS)과 미국장 브리핑 섹션 분리

> 작성 기준: 2026-08-22  
> 상태: 구현 완료(2026-08-22). 수집기는 `KisOverseasIndexCollector` 클래스다(2026-08-23). §13 아시아 장중 분봉·일봉은 구현 완료, 운영 반영 전(2026-09-04)  
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
- 일봉 API(`inquire-daily-chartprice`, `FHKST03030100`)는 `kis_overseas_index_daily`가 따로 쓴다.
  같은 공식 종가를 `index_daily`에 이력으로 쌓는 경로이고, 브리핑이 읽는 마감값의 원천은 여전히
  여기(분봉 → `index_bar`)다. 설계는 [kis-index-daily-collection.md](kis-index-daily-collection.md) 6절이다.
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

## 13. 아시아 지수 장중 1분봉 `kis_asia_index_intraday`와 확정 일봉 `kis_asia_index_daily` (2026-09-04 추가)

> 상태: 구현 완료(2026-09-04). 운영 반영은 리비전 `b7e2d4a91c35` 적용 뒤 DAG 둘을 켜는 것  
> 대상: 니케이225·상해종합·항셍·대만가권 장중 1분봉(§13.1~13.6)과 확정 일봉(§13.7)  
> 산출물: `kis_overseas_index.py`의 `AsiaIndex`·`parse_index_bars`·`fetch_since`, `kis_overseas_index_daily.py`의
> 타입 완화, `airflow/dags/kis_asia_index_intraday.py`·`kis_asia_index_daily.py`, 리비전 `b7e2d4a91c35`,
> `tests/collectors/test_kis_asia_index.py`·`tests/dags/test_kis_asia_index_*.py`, README·operations

### 13.1 왜

2026-09-03 14:00~14:27 KST에 코스피가 3.2% 빠졌다 되돌렸다. 일본·중국도 같이 빠졌다는데 우리
데이터로는 가릴 수 없었다 — `index_bar`의 닛케이가 **하루 7봉**이었다. Yahoo `^N225`는 1분봉을
하루 390개 주지만 **15분 지연**이고, `yahoo_quote_intraday`의 `lookback_minutes`가 15라
정렬된 봉이 전부 `since` 앞으로 떨어져 버려졌다. 살아남은 것은 배열 끝의 초 단위 라이브 봉뿐이다
(`09:25:20`, `14:40:30` 같은 시각이 그 흔적이다).

국내에서 받을 수 있으면 국내를 우선한다. KIS 해외지수 분봉 API가 아시아 지수를 준다(§13.2).
Yahoo와 똑같이 15분 지연이라 지연에서 얻는 것은 없고, 얻는 것은 **정식 API**라는 점이다.

### 13.2 KIS 실측 (2026-09-04, 운영 앱키)

§3과 같은 엔드포인트다. 코드가 다르다.

| 지수 | KIS 코드 | `hts_kor_isnm` | 시각 기준 |
| --- | --- | --- | --- |
| 니케이225 | `JP#NI225` | 일본니케이 225지수 | JST |
| 상해종합 | `SHANG` | 중국상해종합지수 | CST |
| 항셍 | `HK#HS` | 항셍지수 | HKT |
| 대만가권 | `TW#WT` | 대만가권 | 국가표준시(대만) |

- 통상 코드(`N225`·`HSI`·`SHCOMP`·`TWII`)는 **전부 `rt_cd=0`에 0건**이다. 코드의 원본은 KIS 해외지수
  마스터 `https://new.real.download.dws.co.kr/common/master/frgn_code.mst.zip`(cp949, 첫 글자는 시장
  구분이고 그 뒤가 코드)이다. `#`은 URL 인코딩하지 않는다 — `JP%23NI225`는 0건이다.
- 같은 방식으로 되는 것: `HSCE`(홍콩H), `CH#SHA`(상해A), `CZ#399102`(심천 ChiNext), `HK#HSSI`. 이번엔 안 받는다.
- 102봉 최신순, 1분 간격, `stck_bsop_date`는 **현지 거래일**, `stck_cntg_hour`는 **그 시장 벽시계**다.
  §3의 뉴욕 벽시계와 같은 규칙이고 시간대만 다르다.
- **니케이는 15~16분 지연이다.** 10:03:54 KST 조회에 최신 봉이 09:48이었다. Yahoo와 같다.
- 응답에 **어제 봉이 섞여 온다.** 10:03 조회의 오래된 봉이 전날 14:39였다. 장중 폴링은 이것이 정상이다.

### 13.3 흐름

```
평일 09:00~17:55 KST, 5분마다 (cron */5 9-17 * * 1-5)
  KIS inquire-time-indexchartprice ×4  ── 지수마다 최근 102봉, 15분 지연
    → 봉 시각을 그 지수의 시간대로 읽어 UTC로
    → bar_at >= now - lookback_minutes(기본 30) 인 봉만 남김
    → index_bar (provider='kis', symbol=NIKKEI225·SSE_COMP·HSI·TAIEX) upsert
    → source_record 1행 / 지수 / 폴링  (source_key = asia_index_1m)
```

창이 17:55까지인 이유: 항셍이 HKT 16:00(KST 17:00) 마감이고 정산 봉이 16:08까지 온다. 15분 지연을
더하면 17:25 KST에 마지막 봉이 보인다. 도쿄는 15:30, 상해는 KST 16:00, 대만은 KST 14:30 마감이다.

`lookback_minutes` 기본 30: 지연 15분 + 폴링 5분 + 여유. 상한은 102(한 번에 오는 봉 수).

### 13.4 고치는 것

**`collectors/market/kis_overseas_index.py`**

- `AsiaIndex` Enum을 **따로** 둔다. `OverseasIndex`를 늘리지 않는 이유는 `kis_overseas_index_daily`와
  `kis_overseas_index_close`가 그 Enum을 **통째로 순회**하며 미국 달력과 뉴욕 세션 날짜를 쓰기
  때문이다. 거기에 니케이가 들어가면 일봉 DAG가 도쿄 날짜를 뉴욕 날짜로 검사하다 죽는다.
  회원은 `(저장 심볼, KIS 코드, 라벨, 시간대)`다. 저장 심볼은 Yahoo와 같게 둬(`NIKKEI225` 등)
  `quote_symbol`의 라벨·국가를 공유하고 `(provider, symbol)`로 갈린다.
- 파싱을 둘로 가른다. `parse_index_bars(body, index)`가 봉마다 자기 `stck_bsop_date`와 **지수의
  시간대**(`index.timezone` — `OverseasIndex`는 뉴욕, `AsiaIndex`는 현지)로 읽고, 옛
  `parse_overseas_index_bars`는 그것에 `require_session_bars`(모든 봉이 `session_date`의 것인지)를 얹은
  것이다. 마감 DAG는 옛 함수를 그대로 부른다(동작 불변, 기존 테스트 무수정 통과). 장중 폴링
  (`fetch_since`)은 세션 검사 없이 `since`로 자른다 — 어제 봉이 섞여 오는 게 정상이라(§13.2) 그
  검사를 그대로 쓰면 매 폴링이 죽는다.
- `store`는 결과 모델이 `source_key`와 `metadata()`를 갖게 해 둘을 같은 코드로 저장한다
  (`OverseasIndexFetch` → `overseas_index_1m`, `AsiaIndexFetch` → `asia_index_1m`). 아시아 폴링은 0건이
  정상이라 `latest_bar_at`이 `None`일 수 있고 계보에도 그렇게 남는다.

**`airflow/dags/kis_asia_index_intraday.py`** — `kis_quote_intraday`를 베낀다.

- 휴장 달력이 없다. 한국 휴일에 도쿄가 열고 그 반대도 있어 KRX 달력을 걸면 틀린다. 새 봉 0건은
  성공이다(휴장·개장 전·마감 뒤). 응답 자체가 비면(`output2` 0건) 그 지수는 실패다.
- 항목별 실패 수집. **전부 실패했을 때만 죽인다** — 5분 뒤 같은 창을 다시 본다. 실패 목록은
  `이름(사유)` 형태로 `;` 구분.
- HTTP 400/403은 즉시 실패, 401은 토큰 한 번 재발급, 나머지는 재시도. §5와 같다.
- `dag_display_name="🌏 아시아 지수 1분봉 (KIS)"`, tags `kis`·`market`·`intraday`·`asia`.

**마이그레이션** — `quote_symbol`에 `('kis', NIKKEI225|SSE_COMP|HSI|TAIEX, 'index', JP|CN|HK|TW, …)` 넷.
DDL 변경 없음. §6과 같은 꼴이고 수기로 쓴다.

**테스트**

- 도쿄 시간대 파싱: `20260904 094800` → `2026-09-04T00:48:00Z`.
- `since` 절단: 어제 봉이 섞인 응답에서 폴링은 최근 봉만 남고, 마감 경로(`require_session_bars`)는 죽는다.
- 기존 `test_kis_overseas_index.py`가 그대로 통과한다(리팩터 뒤 동작 불변의 증거).
- 카탈로그 테스트: `AsiaIndex` 회원이 `('kis', symbol)`로 시드돼 있는지. `test_us_spot_indexes_are_seeded_under_kis`와 같은 꼴.
- DAG cron 창(9~17시)이 §13.3의 시장 마감 + 지연을 덮는지 상수 하나로 대조.

**문서** — README의 DAG 수(42 → 44, 수집 31 → 33)와 하루 흐름 표 두 줄, `docs/operations.md` DAG 표 두 줄.

### 13.5 입출력 예시

```
요청  FID_INPUT_ISCD=JP#NI225
응답  {"stck_bsop_date":"20260904","stck_cntg_hour":"094800","optn_prpr":"64709.74", ...}
      output1.ovrs_nmix_prdy_clpr = "64214.48"
저장  index_bar ('kis','NIKKEI225', 2026-09-04T00:48:00Z, close 64709.74, previous_close 64214.48)
```

### 13.6 안 하는 것

- **Yahoo 쪽 아시아 심볼은 그대로 둔다.** `index_daily`(일봉 10년치)가 거기서 온다. `lookback` 수정은
  KIS가 대신하므로 안 한다. 그 결과 `index_bar`에 같은 지수가 yahoo(성긴 봉)·kis 두 벌 쌓인다.
- 브리핑 "장중 해외" 표에 같은 지수가 두 줄 뜬다. 게다가 `PROVIDER_VENUES`가 kis를 `KRX`로 적어
  닛케이 행이 `KRX`로 나온다. **후속 작업이다** — 같은 심볼이면 kis를 고르고 해외 지수의 venue를
  제공처 이름으로 적는 것.
- 코스피 전망 요인(`kospi.domain.Factor`)에 닛케이·상해 추가. LLM 흐름이라 따로 설계한다.
- 급변 감지(장중 슬롯 밖 이벤트 트리거). 이 봉이 쌓인 뒤의 이야기다.
- 저장 테이블은 `index_bar` 한 벌이다. 나라별 테이블은 안 만든다 — 컬럼이 같고 지역은
  `quote_symbol.country`가 갖는다. **해외 종목**은 다르다: 나중에 나라별 종목을 받을 때 `stock_bar`에
  섞지 않고 나라(거래소)별로 가르기로 했다(2026-09-04 사용자 결정). 그때 `exchange` 파티션을 먼저 낸다.

### 13.7 확정 일봉 `kis_asia_index_daily`

처음 설계는 일봉을 Yahoo에 두는 것이었다. 실측이 그것을 뒤집었다(2026-09-04, 운영 DB).

**Yahoo 아시아 일봉은 종가만 맞다.** 같은 날 종가는 KIS와 소수점까지 같았다(09-02 상해 3941.39,
대만 46164.72, 항셍 25311.21). 그러나 시가·고가·저가가 틀린다 — 최근 66거래일에서 시가가 고가나
저가와 같은 날이 대만가권 32일, 항셍 12일, 니케이 5일이다(미국 SOX는 2일). 분봉이 하루 7봉이던 것과
같은 뿌리로, Yahoo가 아시아 지수를 성긴 스냅샷으로 만든다. 그리고 **하루 늦다** — 09-04 10:30에
Yahoo 아시아 일봉은 09-02까지, KIS·미국은 09-03까지 있었다.

분봉을 KIS로 받으면서 일봉을 Yahoo로 두면 같은 지수의 두 그레인이 다른 기준을 갖는다. 급변 감지가
"당일 저가 대비"를 볼 때 그 어긋남이 그대로 들어간다. 그래서 일봉도 KIS로 받는다(사용자 결정).

| 항목 | 결정 |
| --- | --- |
| API | §3의 일봉 엔드포인트(`inquire-daily-chartprice`, `FHKST03030100`), 코드는 §13.2와 같다. 한 장 100행, 2016년까지 확인(2000년은 0행) |
| 수집기 | `KisOverseasIndexDailyCollector` 그대로. `fetch`·결과 모델의 `index` 타입만 `OverseasIndex \| AsiaIndex`로 풀었다. 200달력일 창 걷기·멱등 upsert·잘림 판정이 전부 따라온다 |
| DAG | `kis_asia_index_daily`, 평일 18:00 KST(`0 18 * * 1-5`). 항셍(KST 17:00 마감, 정산 17:08, 지연 15분)이 가장 늦다. 장중 폴링(17:55)이 끝난 뒤다 |
| 기준 날짜 | run의 `data_interval_end`의 **KST 날짜**. 네 시장의 거래일이 KST 날짜와 같다. 수동 run은 `run_after` |
| 휴장 | 달력 없음. 쉰 시장은 그 날짜 행이 안 올 뿐이고 창 안의 다른 거래일이 온다. 창 전체가 비면 그 지수는 실패 |
| 실패 판정 | 하루 한 번 도는 확정 수집이라 **하나라도 실패하면 죽인다**(`kis_overseas_index_daily`와 같다) |
| Yahoo 행 | 지우지 않는다. 읽는 쪽이 `(provider, symbol)`로 걸어 kis를 본다. 어느 SQL이 어느 provider를 보는지는 후속(§13.6의 브리핑 항목과 같은 작업) |
| 백필 | `--conf '{"start_date": "2016-08-01", "end_date": "2026-09-04"}'`. 운영 반영 뒤 사용자가 트리거한다 |
