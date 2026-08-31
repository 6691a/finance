---
name: table-conventions
description: Use when reading from or writing to one of this repo's core tables — source_record, indicator_observation, indicator_series, the bar tables (index_bar / stock_bar / quote_bar view), instrument, document, or stock_event_*. Covers 자연키, 무엇을 저장하고 무엇을 저장하지 않는가, 외래키를 거는 자리와 안 거는 자리, 0건일 때의 처리. Also use when choosing a series_id, unit, kind, or maturity_months, or when a query joins these tables.
---

# 테이블 규칙

이 저장소의 핵심 테이블마다 "무엇이 자연키이고 무엇을 저장하지 않는가"가 정해져 있다.
**모르고 쓰면 조용히 틀린다.**

## 빠른 참조

| 테이블 | 자연키 | 안 하는 것 |
| --- | --- | --- |
| `source_record` | 수집 1회(응답/문서 버전/배치) | 웹소켓 메시지마다 만들지 않는다 |
| `indicator_observation` | `(provider, series_id, observation_date)` | `indicator_series`로 FK를 걸지 않는다 |
| `indicator_series` | `(provider, series_id)` | 만기 없는 지표에 `maturity_months=0`을 넣지 않는다 |
| `<kind>_bar` / `_daily` | `(provider, symbol, bar_at\|business_date)` | `quote_bar` 뷰에 쓰지 않는다 |
| `stock_bar` | 위 + **`exchange`** | 통합(`UN`) 시세를 받지 않는다 |
| `instrument` | `(ticker, market)` | `source_record_id`를 연결하지 않는다 |
| `document` | `(source_slug, external_id)` | `content_hash`를 키에 넣지 않는다 |
| `stock_event_*` | `(stock_code, event_type, period_key) + metric` | 단위 컬럼을 두지 않는다 |

---

## `source_record`

API·크롤링·웹소켓 수집 결과의 출처와 상태를 가볍게 보존한다. **API는 응답 1회, 크롤링은
문서 버전 1개, 웹소켓은 메시지가 아닌 배치 또는 연결 세션 1개**가 레코드 단위다.

- 수집 방식, 제공처, 원천 식별자, **UTC 수집 구간**, 상태, 생성 레코드 수는 항상 저장한다.
- **작은 JSON 원본만** `payload`에 선택적으로 저장한다. 대용량은 외부 저장소에 두고
  `payload_uri`만 저장한다. **원본이 JSON이 아니면 `payload`에 넣지 않는다**(컬럼이 jsonb다).
- **API 키·인증 헤더·개인정보를 `payload`나 `metadata`에 저장하지 않는다.**
- 정규화 테이블은 `source_record_id` 외래키와 **`ON DELETE RESTRICT`**로 출처를 연결한다.
- **웹소켓 메시지별로 `SourceRecord`를 생성하지 않는다.**

---

## `indicator_observation`

여러 제공처의 지표 관측값을 날짜와 단위와 함께 누적 저장한다. 지금
`fred_treasury_daily`·`fred_macro_daily`·`fred_signal_daily`·`ecos_market_rate_daily`·
`mof_jgb_daily`·`boe_gilt_daily`·`bbk_bund_daily`·`ecb_yield_curve_daily`·
`ecb_convergence_monthly`·`policy_rate_weekly`·`central_bank_assets_weekly`·
`kcs_trade_daily` 열둘이 채운다.

- `provider`는 그 값을 준 제공처(`fred`·`ecos`·`mof`·`boe`·`bbk`·`ecb`·`kcs`)이며
  **같은 수집의 `source_record.source`와 같다.**
- **`series_id`는 제공처 안에서만 고유하다.** 그래서 자연키에 `provider`가 함께 들어가고,
  **조회하는 쪽도 `provider`를 함께 건다.** `series_id` 하나로 거는 쿼리는 제공처가 늘어나면
  조용히 틀린다.
- **`series_id`는 사람이 읽을 수 있어야 한다.** FRED의 `DGS10`처럼 제공처 ID가 이미 읽히면
  그대로 쓰고, ECOS 항목코드(`010210000`)처럼 숫자뿐이면 `KTB10Y` 같은 ID를 만들어 저장한다.
  **DB나 대시보드에서 값만 보고 무슨 시계열인지 알 수 없으면 안 된다.** 제공처의 원본
  좌표는 수집기 Enum이 들고 있다가 요청에 쓰고 `source_record.metadata`에 남긴다.
- **`unit`은 제공처 표기가 아니라 정규화한 표기다.** 연이율은 제공처가 `Percent`든 `연%`든
  `Percent`로 저장한다 — 그래야 두 나라 금리를 한 쿼리로 비교할 수 있다.
  **단위는 계열마다 다르다** — 금리만 있던 때의 모듈 상수 하나로는 물가지수
  (`Index 1982-1984=100`)와 소매판매(`Millions of Dollars`)에 거짓이 실린다.
  **수집기 Enum이 계열별로 들고 있는다.**
- 국가·만기 같은 시계열의 성격은 여기 두지 않고 `indicator_series`에 둔다.
- **관측값이 0건이어도 `source_record`는 남긴다.** 조회했지만 값이 없는 구간과 아직
  조회하지 않은 구간이 구분돼야 한다.

---

## `indicator_series`

관측값이 어느 나라 무슨 값인지 설명하는 마스터다. `(provider, series_id)`가 자연키이고
조회가 이 키로 관측값을 조인한다.

**이 테이블이 있는 이유는 나라를 추가할 때 조회 쪽을 안 고치기 위해서다.** 일본을 붙일 때
바뀐 건 만기 변수 쿼리 한 줄뿐이었고, 영국과 유로 지역을 붙일 때는 그 한 줄조차 안 고쳤다.

### `kind` — 금리 전용 테이블이 아니다

아홉을 가른다: `government_bond`·`money_market`·`policy_rate`·`tips_rate`·`credit_spread`·
`price_index`·`activity`·`balance_sheet`·`balance_sheet_item`.

- **정책금리는 `money_market`이 아니다** — CD 91일은 시장이 만드는 값이고 정책금리는
  중앙은행이 정하는 값이라, 한 축에 섞으면 시장금리 패널이 정책금리 계단을 함께 그린다.
- **총자산(`balance_sheet`)과 그 항목(`balance_sheet_item`)도 가른다** — 영란은행은 총자산을
  분기로만 고시하고 주간으로는 준비금잔액 같은 항목만 준다. 한 종류로 두면 "중앙은행 총자산
  전부"를 묻는 쿼리가 영국의 준비금을 총자산으로 읽어 영국만 값이 작게 나온다.
- **조회하는 쪽은 `kind`를 반드시 건다.** 단위가 다른 값이 한 축에 섞이면 화면이 조용히
  거짓말을 한다.

### 나머지 칸

- `country`는 **ISO 3166-1 alpha-2**. 유로 지역처럼 나라가 아닌 통화권은 `XM`이고
  `country_name`에 `유로 지역`을 넣는다. 저장 식별자는 제공처가 부르는 이름을 따라도 된다 —
  `series_id`가 `EA10Y`인 것과 `country`가 `XM`인 것은 쓰임이 달라 안 어긋난다.
- **`maturity_months`는 만기 개념이 없는 지표(물가지수·소매판매·정책금리·대차대조표 잔액)면
  `NULL`이다.** 0으로 채우면 만기별 비교 쿼리가 그 시계열을 "0개월물"로 그린다.
  91일물은 3으로 둔다.
- **월간 계열은 저장 식별자를 `M`으로 끝낸다**(`CPI_M`·`FR10YM`). 한 테이블에 일별과 월간이
  섞여 있어 표시가 없으면 조회하는 쪽이 주기를 구분할 수 없다.
- 국가 비교 패널의 만기 목록은 **두 나라 이상이 가진 만기로 좁힌다**
  (`HAVING count(DISTINCT country) > 1`). 일본 40년이나 유로 지역 6개월처럼 한 나라만
  고시하는 만기는 골라도 비교할 대상이 없다.

### 대차대조표 잔액은 통화별 단위를 그대로 둔다

금리는 전부 `Percent`로 정규화하지만 잔액은 `Millions of Dollars`·`Hundred Millions of Yen`·
`Billions of Won`처럼 갈린다. **한 통화로 환산하면 환율 변동이 자산 증감으로 위장한다** —
2022년 엔 약세 구간에서 달러 환산 BOJ 자산은 줄고 엔화 잔액은 늘었다.

**나라 사이 비교는 잔액이 아니라 증가율(YoY)로 하고 그 계산은 조회하는 쪽이 한다.**
증가율을 저장하지 않는 이유는 주기가 주간·월간·분기로 갈려 "무엇 대비"가 값에서 사라지기
때문이다.

### 발표가 밀린 계열

영란은행 총자산은 분기 고시에 17개월, 한국은행 총자산은 두 달 지연이다(2026-08-28 실측).
좁은 창은 BoE에서 HTML 오류 페이지로, ECOS에서 **조용한 0건**으로 나타난다. 제공처마다 창을
다르게 두는 대신 **하나를 넓게 잡고**(`central_bank_assets_weekly`가 800일),
**그러고도 0건이면 실패시킨다.**

### 마스터로 FK를 걸지 않는다

걸면 마스터 행이 없는 시계열을 수집기가 저장하지 못해, **수집기 Enum에만 추가하고 마스터
시드를 빠뜨린 순간 DAG가 죽는다.** 대신 `tests/migrations/test_indicator_series_catalog.py`가
수집기 Enum과 시드를 대조한다.

**시계열을 늘릴 때는 수집기 Enum과 마스터 시드를 같은 커밋에서 함께 늘린다.**
시드는 마이그레이션이 넣고 **리비전 파일에서 앱 코드를 import하지 않는다.**

---

## 봉 테이블 (`<kind>_bar` / `<kind>_daily` / `stock_bar`)

시세 봉은 kind별 물리 테이블에 쌓는다(2026-08-18 분리): `index_bar`·`index_future_bar`·
`fx_bar`·`rate_bar`·`bond_future_bar`·`commodity_bar`·`crypto_bar`와 각각의 `_daily`,
그리고 개별 종목의 `stock_bar`/`stock_daily`다. 심볼의 성격(라벨·국가·kind)은 `quote_symbol`
마스터가 갖는다.

- **`quote_bar`/`quote_daily`는 이들을 UNION ALL 한 읽기 전용 뷰다.** 조회는 뷰를 써도
  되지만 **쓰기는 반드시 물리 테이블로 간다.** 수집기가 kind별 upsert 파일
  (`airflow/sql/postgres/<table>/upsert.sql`)을 쓴다.
- 매크로 테이블의 자연키는 `(provider, symbol, bar_at|business_date)`다. `contract_code`는
  `index_future_bar`에만 있다.
- **`stock_bar`는 거래소(`exchange`: KRX/NXT/NYSE)가 자연키의 한 축이다.** 같은 종목이 KRX와
  NXT에서 따로 체결되므로 거래소 없이 시각만 키로 쓰면 서로를 덮어쓴다. **통합(`UN`) 시세는
  받지 않는다.** 뷰에는 KRX·NYSE만 태워 심볼이 겹치지 않게 하고, **NXT는 물리 테이블을 직접
  조회한다.**
- **국내 종목 일봉은 `stock_daily`가 아니라 `stock_investor_trade_daily`가 갖는다**(수급과
  함께). `stock_daily`는 해외 상장 종목(TSMC ADR)용이다.

---

## `instrument`

**우리가 이름을 아는 종목의 마스터다.** 문서에서 그 종목을 알아보고, 리서치 리포트를 받고,
시세를 받을지를 여기서 정한다. **관측값이 아니라 기준 정보이므로 `source_record_id`로 수집
계보를 연결하지 않는다.**

- `(ticker, market)`이 자연키. `id`는 다른 테이블이 참조할 대리키다.
- **`source_symbol`은 수집 소스 심볼이 티커와 다를 때만 채운다.** 같으면 `NULL`이다.
- **행이 있다는 것과 `is_watched`는 다른 뜻이다.** 행이 있으면 "이름을 안다"이고,
  `is_watched`가 참이면 "시세까지 받는다"이다. 읽는 쪽이 어느 쪽을 물어야 하는지 SQL 둘이
  가른다.

  | 읽는 SQL | 무엇을 묻나 | 소비자 |
  | --- | --- | --- |
  | `instrument/select_taggable.sql` | 이 종목 이름을 아는가 | 문서 평가의 종목 후보, 네이버 기업 리포트 필터 |
  | `instrument/select_watched.sql` | 이 종목 시세를 받는가 | 투자의견 수집, 추론 subject, 기술지표 조회, 주간 인과 그래프 대상 |

  **시세가 없어도 성립하는 조회만 `select_taggable.sql`을 읽는다.** `is_watched`가 참인
  종목은 수집기 Enum 다섯(`kis.DomesticStock`, `InvestorFlowStock`, `PositioningStock`,
  `DartCompany`, `apps.realtime.service.DomesticStock`)과 **정확히 같아야 하고**
  `tests/migrations/test_instrument_catalog.py`가 그것을 대조한다. 시세 없는 종목을 참으로
  두면 기술지표 조회는 조인에서 빠지고 추론 baseline 셋은 NULL로 들어간다 —
  `ck_thesis_base_all_or_none`이 그 조합을 허용해서 **오류 없이 빈 값이 쌓인다.**
- `is_watched`는 상장폐지·거래정지 같은 생애주기 상태를 뜻하지 않는다. 그것이 필요해지면
  별도 `status` enum 컬럼으로 분리한다.
- 한 종목을 여러 소스에서 수집하게 되면 `source_symbol` 한 칸으로 못 버틴다. 그때는
  `instrument_source(instrument_id, source, symbol)` 자식 테이블로 옮긴다.

---

## `document` 계열

수집한 문서 한 건과 그 문서에 붙은 태그다. `document_ingestion_hourly`가 넣고
`document_assessment_hourly`가 평가를 채운다.

- 자연키는 `(source_slug, external_id)`다. **`content_hash`를 키에 넣지 않는다** — 넣으면
  본문이 조금만 달라져도 새 행이 생겨 같은 기사가 매시간 쌓인다. 본문이 바뀌면 같은 행을
  갱신하고, 다시 평가할지는 `assessed_content_hash`와 현재 `content_hash`의 비교가 정한다.
- **승인·보류 같은 상태 머신을 두지 않는다.** 소비자가 사람이 아니라 LLM이라 전부 저장하고
  점수(`value_score`)만 남긴다. 상태로 버리면 나중에 기준을 바꿀 때 되돌릴 수 없다.
- **평가에 실패한 문서는 `assessed_at`을 `NULL`로 남긴다.** 삭제하거나 다른 상태로 바꾸지
  않는다 — 다음 정시 실행이 다시 집는다.
- **`document_instrument`와 `document_indicator`는 마스터로 외래키를 걸지 않는다.** 마스터에
  없는 태그가 오면 태깅 전체가 죽는 대신 **그 태그만 빠져야 한다.** 후보 목록은 프롬프트로
  주고 목록 밖의 값은 저장 전에 버린다.
- `body`는 `content_level`이 `metadata_only`면 `NULL`이고 CHECK가 그것을 강제한다.
- **출처 고유 값(증권사·목표가)은 컬럼을 더하지 않고 제목·`summary`에 넣는다.**
  **제목 말머리에 대괄호를 쓰지 않는다** — `dedup`이 15자 이하 대괄호 말머리를 벗기고
  비교해서, 같은 날 두 증권사의 같은 제목이 중복으로 묶인다. 증권사는 제목 끝에 낱말로
  붙인다(`… - 대신증권`). 구조화된 숫자는 `stock_analyst_opinion`처럼 별도 테이블이 갖는다.

---

## `stock_event_*` 계열

같은 사건에 대한 **기대와 실제를 잇는** 테이블 셋이다. `event_expectation_hourly`가 문서에서
주장을 뽑아 `stock_event_claim`에 쌓고, 실제값이 생기면 `stock_event_outcome`에 판정을 남긴다.

- 잇는 키는 `(stock_code, event_type, period_key) + metric`이다. **`period_key`는 세 형식
  (`2026`·`2026Q2`·`2026H1`)만 허용하고 DB CHECK가 강제한다.** 느슨하게 받으면 기대와 실제가
  다른 표기로 저장돼 조용히 매칭이 깨진다.
- **단위 컬럼을 두지 않는다.** 단위는 `metric`이 정하고 전부 원(KRW)이다. 원문 표기(조·억)는
  수집 단계에서 정규화하고 **모르는 표기는 그 주장을 버린다.**
- 실적 지표(`revenue`·`operating_profit`·`net_income`)는 `earnings_fact.metric`과 **글자 그대로
  같다** — 판정이 대응표 없이 조인하기 위해서다. 테스트가 두 Enum을 대조한다.
- **실적의 실제값은 `earnings_fact`가 원본이다.** 기사 산문에서 다시 뽑지 않는다 — DART
  파서가 원문 표에서 읽는 값과 어긋나면 어느 쪽이 맞는지 고를 수 없다. 추출은
  `earnings`+`actual` 조합을 저장 전에 버린다.
- **판정은 첫 성공본 불변이다**(`INSERT ... ON CONFLICT DO NOTHING`). 발표 뒤 기대 행이 늦게
  추출돼도 다시 내지 않는다 — 덮어쓰면 Slack으로 이미 나간 판정과 DB가 어긋난다.
- **발표 전 기대만 판정에 쓴다**(`stated_at < announced_at`). 발표 뒤 "기대치는 X였다"라고
  회고한 기사가 기대로 섞이면 판정이 오염된다.
- **실제값 주장이 갈리면 판정하지 않는다.** "총 환원 8조"와 "배당+자사주 8.5조"처럼 집계
  범위가 다른 숫자가 온다. 조용히 한쪽을 고르는 대신 보류하고 다음 실행이 다시 본다.
- **숫자 비교에 LLM을 쓰지 않는다.** 대표 기대치 집계와 beat/meet/miss 분류는 순수 함수다
  (DB 없이 경계값을 테스트하기 위해서다). LLM은 산문에서 숫자를 꺼내는 추출 단계에만 있다.
- **주장 0건 문서도 `stock_event_extraction` 원장에 남긴다.** "뽑았는데 없었다"와 "아직 안
  뽑았다"가 구분돼야 매시간 같은 문서를 다시 뽑지 않는다.

---

## 반복되는 원리 셋

1. **0건과 미조회를 구분한다.** `source_record`가 0건에도 남고 `stock_event_extraction`
   원장이 주장 0건에도 남는 것이 같은 이유다.
2. **마스터로 FK를 걸지 않는다.** `indicator_observation`도 `document_instrument`도 같다 —
   마스터에 없는 값 하나가 전체를 죽이는 대신 그 값만 빠져야 한다. 대조는 테스트가 한다.
3. **읽기용 뷰에 쓰지 않는다.** `quote_bar`/`quote_daily`는 UNION ALL 뷰다.
