# KIS 국내·해외 시장 영업일 저장 설계

## 1. 결론

국내와 미국의 일별 시장 상태를 `market_session` 한 테이블에 저장한다. **국내는 KIS
국내휴장일조회가, 미국은 NYSE 공식 캘린더가 개장일 판정의 주 소스다.** KIS 해외결제일자조회는
개장일을 판정하지 않고 미국 행에 결제일만 채운다.

미국 판정을 NYSE에 맡기는 이유는 실측(§4.2) 때문이다. KIS 해외 응답은 **휴장한 나라의 행을
아예 주지 않고, 미래 날짜를 조회하면 0건**이다. 그래서 KIS만으로는 미국 휴장일 행이 영원히
생기지 않고, 오늘 이후를 미리 알 수도 없다. NYSE 페이지는 3년치 휴장일을 미리 고시한다.

한 행 안에 판정과 근거가 함께 있으므로 조회하는 쪽은 `effective_open_day` 하나만 보면 되고,
값이 어디서 왔는지는 `verified_by`와 두 개의 계보 외래키로 추적한다.

## 2. 목표

- KIS 국내휴장일조회 결과를 날짜별 KRX 행으로 저장한다.
- NYSE 공식 캘린더로 미국 현물장(`US_EQUITY`)의 날짜별 개장 여부를 저장한다.
- KIS 해외결제일자조회로 미국 행의 현지·국내 결제일을 채운다. 응답 원본은 계보에 보존한다.
- 장중 수집기는 확정 휴장일의 불필요한 시세 요청을 생략한다.
- 캘린더 조회 실패가 시세 누락으로 이어지지 않도록 알 수 없는 상태에서는 기존 수집을 계속한다.

## 3. 범위 밖

- CME 선물, 원자재, 외환, 암호화폐의 세션 캘린더
- 미국 조기 폐장 시각을 이용한 분 단위 수집 중단
- KRX 외부 자료를 이용한 국내 상태 재검증
- 미국 외 해외 국가(일본·홍콩·중국·영국·베트남)의 개장일 판정 행
- 과거 캘린더 정정 내역을 버전별로 보존하는 별도 감사 테이블
- Airflow 스케줄러가 휴장일에는 DAG run 자체를 만들지 않는 사용자 정의 timetable

이 범위에서는 날짜 단위의 현물장 개장 여부만 다룬다. 조기 폐장일까지 `open`으로 보는 것은
의도된 단순화다. 장 종료 뒤의 빈 봉은 기존 수집기가 정상 상태로 흡수한다.

## 4. 공식 원천과 실측

아래 실측은 2026-08-12에 직접 호출해 확인했다.

### 4.1 국내 KIS 국내휴장일조회

- 예제: `examples_llm/domestic_stock/chk_holiday/chk_holiday.py`
- REST 경로: `/uapi/domestic-stock/v1/quotations/chk-holiday`
- TR ID: `CTCA0903R`
- 요청값: `BASS_DT`, `CTX_AREA_FK`, `CTX_AREA_NK`
- 응답값: `bass_dt`, `wday_dvsn_cd`, `bzdy_yn`, `tr_day_yn`, `opnd_yn`, `sttl_day_yn`

`opnd_yn`이 주문 가능 여부를 판단할 개장일 값이다. KIS 공식 예제는 이 API를 단시간에
반복 호출하지 말고 가급적 하루 한 번만 호출하라고 명시한다.

**실측**

- 응답은 `BASS_DT` 하루가 아니라 **그 날부터 앞으로 하루 1행씩** 온다.
- 한 페이지 24행이다. `BASS_DT=20260812`으로 12페이지를 받으면 288행이고 마지막 날짜가
  2027-05-26인데 **그 시점에도 아직 끝나지 않는다.** 끝까지 받아 본 적은 없다.
- 그래서 **조회 종료는 우리가 정한다.** 필요한 구간만큼만 받고 페이지 상한에서 멈춘다.
- 288행 JSON은 33,696바이트다.
- 같은 구간에서 `opnd_yn=N`이 95일이었다(주말 포함).

### 4.2 해외 KIS 해외결제일자조회

- 예제: `examples_llm/overseas_stock/countries_holiday/countries_holiday.py`
- REST 경로: `/uapi/overseas-stock/v1/quotations/countries-holiday`
- TR ID: `CTOS5011R`
- 요청값: `TRAD_DT`, `CTX_AREA_FK`, `CTX_AREA_NK`
- 응답값: `prdt_type_cd`, `tr_natn_cd`, `tr_natn_name`, `natn_eng_abrv_cd`, `tr_mket_cd`,
  `tr_mket_name`, `acpl_sttl_dt`, `dmst_sttl_dt`

`tr_natn_name`(국가 한글명)은 공식 문서 목록에 없지만 실제로 온다.

**실측 — 이 절이 설계를 정한다**

| `TRAD_DT` | 결과 |
| --- | --- |
| 2026-08-12 (수, 당일) | 17행 / 6개국 / US 5행 |
| 2026-05-14 (수, 과거) | 17행 / US 5행 |
| 2026-07-03 (미국 독립기념일 대체휴장) | 12행 / **US 0행** / 다른 나라는 옴 |
| 2026-08-15 (토) | 0행, `rt_cd=0`, `msg1='조회할 자료가 없습니다'` |
| 2026-09-01 (3주 뒤) | 0행, 같은 메시지 |
| 2026-11-26 (3개월 뒤) | 0행, 같은 메시지 |

- **휴장한 나라는 행이 오지 않는다.** 개장 여부 컬럼이 없는 대신 행의 존재가 그 신호다.
- **미래는 조회할 수 없다.** 당일까지만 온다. 주말과 미래가 똑같이 0행이라 응답만으로는
  둘을 가를 수 없다.
- 이 둘 때문에 미국 개장 판정을 이 API에 맡길 수 없다. 휴장일에는 갱신할 행이 없고,
  장중 수집기가 필요로 하는 "오늘 이후"를 미리 알 수 없다.
- 페이징은 없었다. 한 장에 다 오고 헤더 `tr_cont='D'`, `ctx_area_nk`는 빈 값이다.
- 미국은 시장별로 5행이다: `01` 나스닥, `02` 뉴욕거래소, `03`·`04` 미국, `05` 아멕스.
  `prdt_type_cd`는 각각 `512`, `513`, `527`, `528`, `529`다.
  **5행의 `acpl_sttl_dt`와 `dmst_sttl_dt`는 모두 같았다.**
- `tr_mket_cd`는 **국가 안에서만 유일하다.** `01`이 나스닥이면서 런던GBP이고 일본이고
  하노이거래소이고 홍콩이다. 시장 식별에 국가를 반드시 함께 건다.
- 2026-08-12(수) 응답의 미국 결제일은 현지 2026-08-13, 국내 2026-08-14였다.
- 한 응답 JSON은 3,504바이트다.

### 4.3 미국 NYSE 공식 캘린더

- `https://www.nyse.com/trade/hours-calendars`
  (`/markets/hours-calendars`로 요청하면 여기로 리다이렉트된다)

**실측**

- scrapling `Fetcher.get(..., stealthy_headers=True)`로 HTTP 200, 108,796바이트.
  표가 정적 HTML에 있다. `DynamicFetcher`나 브라우저는 필요 없다.
- 페이지에 `<table>`이 **하나**뿐이고 그 표가 휴장일 표다.
- 첫 행이 `['Holiday', '2026', '2027', '2028']`이다. **지원 연도는 열 헤더에서 읽는다.**
- 셀 값은 `'Thursday, January 1'`처럼 요일·월 이름·일이고 **연도가 없다.** 연도는 열이 준다.
- 셀에 섞이는 변형이 셋이다.
  - `'—*'` — 그 해에는 그 휴일을 지키지 않는다(2028년 신정이 토요일).
  - `'Friday, July 3 (Independence Day observed)'` — 괄호 주석.
  - `'Thursday, November 26***'` — 조기 폐장 각주 마커(`**`, `***`, `****`).
- 조기 폐장 날짜는 표가 아니라 **각주 본문**에 `'Friday, November 27, 2026'`처럼 연도까지
  붙어 있다. 이번 범위는 조기 폐장을 `true`로 보므로 각주를 파싱하지 않는다.
- 월 이름과 요일 이름은 `LC_TIME`을 타므로 `strptime`을 쓰지 않는다. `boe.MONTH_NAMES`처럼
  표를 수집기 안에 직접 둔다.

NYSE가 명시한 완전 휴장일과 토·일요일은 `effective_open_day = false`, 지원 연도 안의 그 밖의
평일은 `true`다. 지원 연도 밖은 아무 행도 만들지 않는다.

### 4.4 남은 미확인

- **KST 07:00 시점에 그날의 해외 응답이 이미 존재하는지.** 실측은 KST 19:50에 했다.
  당일 행이 장 시작 후에 생성된다면 아침 조회가 0행일 수 있다. 배포 후 첫 며칠의
  `source_record.metadata`로 확인한다. 0행이어도 미국 판정은 NYSE가 이미 갖고 있으므로
  결제일만 비고, 다음 날 같은 날짜를 다시 요청할 수 있다.
- 국내 연속조회의 실제 끝. 최소 288일 뒤까지는 계속된다.

## 5. 데이터 모델

`apps/models/market.py`에 `MarketSession`을 둔다. 마스터가 아니라 날짜별 관측값이므로
`reference.py`가 아니라 `market.py`가 소유한다. `table_options(comment=..., database="default")`로
선언한다. 스키마는 지정하지 않고 연결의 `search_path`를 따른다.

자연키는 `(market_code, session_date)`다.

| 컬럼 | 타입 | NULL | 의미 |
| --- | --- | --- | --- |
| `id` | bigint | 아니요 | 공통 기본키 |
| `created_at` | timestamptz | 아니요 | 생성 시각(UTC) |
| `updated_at` | timestamptz | 아니요 | 최종 수정 시각(UTC) |
| `market_code` | varchar(20) | 아니요 | 정규화 시장 식별자. `KRX` 또는 `US_EQUITY` |
| `market_name` | text | 아니요 | 사람이 읽는 시장 이름 |
| `country_code` | text | 아니요 | ISO 3166-1 alpha-2 국가 코드 |
| `session_date` | date | 아니요 | 그 시장의 현지 거래일 |
| `kis_weekday_code` | text | 예 | 국내 KIS `wday_dvsn_cd` |
| `kis_business_day` | boolean | 예 | 국내 KIS `bzdy_yn` |
| `kis_trading_day` | boolean | 예 | 국내 KIS `tr_day_yn` |
| `kis_open_day` | boolean | 예 | 국내 KIS `opnd_yn` |
| `kis_settlement_day` | boolean | 예 | 국내 KIS `sttl_day_yn` |
| `local_settlement_date` | date | 예 | 해외 KIS `acpl_sttl_dt` |
| `domestic_settlement_date` | date | 예 | 해외 KIS `dmst_sttl_dt` |
| `effective_open_day` | boolean | 예 | 소비자가 쓰는 최종 개장일 판정. 모르면 `NULL` |
| `verified_by` | varchar(20) | 예 | 판정을 준 제공처. `kis` 또는 `nyse` |
| `verified_at` | timestamptz | 예 | 판정을 확인한 시각(UTC) |
| `source_record_id` | bigint FK | 아니요 | **이 행을 만든 수집.** `source_record.id`, `ON DELETE RESTRICT` |
| `verification_source_record_id` | bigint FK | 예 | **같은 행을 보강한 다른 출처.** 미국 행의 결제일을 채운 KIS 해외 수집 |

- `market_code`와 `verified_by`는 값 집합이 닫혀 있으므로 프로젝트 규칙대로 `StrEnum` +
  `SqlEnum(native_enum=False, length=20, values_callable=...)` + `CHECK`로 선언한다.
- **미국은 시장을 나누지 않고 `US_EQUITY` 한 행이다.** 나스닥·뉴욕거래소·아멕스는 휴장일이
  같고 실측에서 결제일도 5행 모두 같았다. NYSE도 "All NYSE markets"로 한 벌만 고시한다.
  KIS 해외 5행은 결제일이 서로 같은지 검증한 뒤 한 행으로 접는다. 다르면 실패시킨다.
  그래서 `kis_product_type_code`·`kis_country_code`·`kis_market_code` 컬럼은 두지 않는다.
  접은 뒤에는 어느 시장의 값인지가 의미를 잃고, 원본 5행은 계보 `payload`에 남는다.
- `kis_*` 컬럼은 국내 전용이고 미국 행에서는 `NULL`이다.
- 기존 `reference.Market` StrEnum(`kospi`/`kosdaq`/`nyse`/`nasdaq`)과는 **값 체계가 다르다.**
  저쪽은 종목이 상장된 거래소이고 이쪽은 휴장 캘린더를 공유하는 시장 묶음이다. 이름이 비슷해도
  조인 키가 아니다. 나중에 이어야 하면 `US_EQUITY` → (`nyse`, `nasdaq`) 대응표를 그때 만든다.
- 인덱스는 자연키 고유 제약과 `session_date`, 두 계보 외래키에 둔다.

## 6. 갱신 규칙

세 수집 경로가 같은 테이블을 서로 다른 권한으로 만진다.

### 6.1 국내 KIS — KRX 행의 주인

`(market_code='KRX', session_date)`로 upsert한다.

- KIS 원본 컬럼과 `source_record_id`를 최신 응답으로 갱신한다.
- `effective_open_day = kis_open_day`, `verified_by = 'kis'`, `verified_at`을 함께 갱신한다.

### 6.2 NYSE — `US_EQUITY` 행의 주인

**NYSE 수집이 미국 행을 만든다.** 지원 연도(현재 2026·2027·2028)의 **모든 날짜**에 대해
`(market_code='US_EQUITY', session_date)`로 upsert한다.

- `effective_open_day`: 완전 휴장일과 토·일요일은 `false`, 나머지는 `true`
- `verified_by = 'nyse'`, `verified_at`, `source_record_id`
- **결제일 두 컬럼과 `verification_source_record_id`는 건드리지 않는다.** KIS 해외가 채운
  값이 매일 NYSE 태스크 때문에 사라지면 안 된다.
- 지원 연도 밖은 아무 것도 하지 않는다.
- 파싱이나 저장이 실패하면 트랜잭션을 롤백한다. 이전 판정이 그대로 남는다.

연도가 세 개면 1,096행 남짓이고 매일 같은 값으로 덮어써도 부담이 없다. 대신 페이지가
새 연도로 갱신되는 순간 그 연도가 저절로 채워진다.

### 6.3 해외 KIS — 결제일만 채운다

`TRAD_DT` 하루치 응답에서 미국 행만 꺼내 `(market_code='US_EQUITY', session_date=TRAD_DT)`
행을 갱신한다.

- 갱신 대상은 `local_settlement_date`, `domestic_settlement_date`,
  `verification_source_record_id` 셋뿐이다.
- **`effective_open_day`와 `verified_by`를 건드리지 않는다.**
- **행이 없으면 만들지 않는다.** NYSE가 지원 연도 전체를 이미 만들어 두었으므로, 행이 없다는
  것은 지원 연도 밖이라는 뜻이다.
- **미국 행이 없는 것을 휴장 판정에 쓰지 않는다.** NYSE가 이미 그 날짜를 판정했다.
  다만 응답 전체가 1행 이상인데 미국만 없고 NYSE는 그날을 `true`로 본다면 **경고를 남긴다.**
  둘 중 하나가 틀렸다는 뜻이고, 사람이 봐야 한다.
- 응답이 0행이면 아무 것도 갱신하지 않고 `source_record`만 남긴다. 주말·미래·장애가
  같은 응답이라 구분할 수 없기 때문이다.

## 7. 수집 계보

기존 `source_record` 규칙을 그대로 쓴다.

- 국내 KIS: `source_type='api'`, `source='kis'`, `source_key='domestic_holiday'`
- 해외 KIS: `source_type='api'`, `source='kis'`, `source_key='overseas_settlement'`
- NYSE 페이지: `source_type='crawl'`, `source='nyse'`, `source_key='hours_calendars'`

- 해외 KIS 응답은 3.5KB라 `payload`에 그대로 넣는다. 미국 외 나라의 행도 여기 남으므로
  나중에 일본이나 홍콩을 행으로 승격할 때 과거를 재구성할 수 있다.
- **국내 KIS 응답은 `payload`에 넣지 않는다.** 한 번에 수백 행이라 매일 쌓으면 계보 테이블이
  캘린더보다 빨리 커진다. 조회 구간과 행 수, 페이지 수를 `metadata`에 남긴다.
- NYSE HTML은 jsonb가 아니므로 `payload`에 넣지 않는다. URL, 지원 연도, 파싱한 휴장일 수를
  `metadata`에 남긴다.
- 인증 헤더와 앱키는 어디에도 저장하지 않는다.
- 관측이 0건이어도 `source_record`는 남긴다.
- 정규화와 계보 저장은 원천 응답 하나당 한 트랜잭션이다.

## 8. 수집기와 DAG

### 8.1 기존 자산 재사용

새로 만들기 전에 이미 있는 것을 쓴다.

- **토큰**: `modules.collectors.kis.access_token(store, app_key, app_secret, force=...)`와
  `TOKEN_CACHE_KEY`, `TOKEN_REFRESH_MARGIN`을 그대로 쓴다. 새 DAG도 Airflow `Variable`을
  저장소로 물리며 `kis_quote_intraday`와 **같은 캐시 키를 공유한다.** 발급 횟수 제한이 있어
  DAG마다 따로 받지 않는다. 401은 `kis_quote_intraday._fetch`처럼 한 번만 재발급하고 재시도한다.
- **HTTP**: `kis._get`은 지금 `(body, status)`만 돌려주므로 **연속조회에 필요한 헤더
  `tr_cont`를 읽을 수 없다.** `_get`을 `(body, status, headers)`로 넓혀 공유한다.
  호출부 `fetch_bars`·`fetch_index_bars`와 `tests/collectors/test_kis.py`가 함께 바뀐다.
  수집기마다 요청 함수를 복사하지 않는다.
- **저장**: `modules.sql.read_sql`, `modules.upsert.execute_upserts`,
  `airflow/sql/postgres/source_record/insert.sql`을 그대로 쓴다. SQL에 스키마 접두어를 붙이지
  않는 관행도 같다.
- **예외**: `KisHTTPError`, `KisResultError`, `KisPayloadError`를 재사용한다. 새 계층을 만들지 않는다.
- **HTML**: scrapling `Fetcher`. `hana.py`가 기준이다.
- 조회 구간 계산이 필요하면 `modules/period.py`에 둔다. DAG에 복사하지 않는다.

### 8.2 새 모듈

- `airflow/modules/collectors/kis_market_calendar.py`
  - 국내·해외 요청과 응답 Pydantic 모델(`frozen=True`)
  - 연속조회: 헤더 `tr_cont`가 `M`/`F`면 `ctx_area_fk`/`ctx_area_nk`를 되먹여 다음 장을 받는다.
    **총 건수 필드가 없다.** 국내 조회는 미래를 끝없이 주므로 페이지 상한이 정지 조건이고
    상한 도달은 오류가 아니다. `ctx_area_nk`가 직전과 같은데 계속 이어지면 커서가 멈춘
    것이므로 실패시킨다. 페이지 사이에 고정 지연을 둔다.
  - `Y`/`N`의 엄격한 boolean 변환. 그 밖의 값은 실패
  - 미국 5행 결제일 일치 검증과 한 행 접기
- `airflow/modules/collectors/nyse_calendar.py`
  - 페이지 요청, 표 하나 찾기, 열 헤더에서 지원 연도 읽기
  - 셀에서 각주 마커와 괄호 주석을 벗기고 `요일, 월 일`을 파싱. `—`는 그 해 없음
  - 월 이름·요일 이름 표를 직접 둔다
  - 지원 연도의 모든 날짜에 대한 개장 여부 생성
- `airflow/modules/market_session.py`
  - `market_open_day(connection, market_code, session_date) -> bool | None`
  - `us_equity_open_day(connection, session_date) -> bool | None`

SQL은 `airflow/sql/postgres/market_session/`에 둔다. Airflow는 `apps/`를 보지 못하므로
수집기에서 SQLAlchemy 모델을 import하지 않는다.

### 8.3 DAG

`airflow/dags/market_calendar_daily.py` 하나를 추가한다. 다른 DAG와 같은 규칙을 따른다:
`dag_display_name`, `description`, `doc_md=__doc__`, `tags`, `catchup=False`,
`max_active_runs=1`, `default_args={"retries": ..., "retry_delay": ...}`,
`start_date=pendulum.datetime(..., tz=KST_TIMEZONE)`, `CONNECTION_ID = "news"`(PostgresHook).

- 스케줄: `schedule="0 7 * * *"  # KST 매일 07:00 = UTC 전날 22:00`
- 태스크
  - `domestic_holiday` — 독립 실행
  - `nyse_calendar` → `overseas_settlement` — NYSE가 미국 행을 먼저 만들어야 결제일이 붙는다
- 국내와 미국 경로는 서로 롤백하지 않는다.
- `params`: `bass_dt`, `trad_dt`(둘 다 `YYYY-MM-DD`, 기본 `null`). 해외는 미래를 못 주고
  당일치만 쌓이므로 **과거를 채우려면 백필 수단이 필요하다.** 과거 날짜는 실측으로 조회된다.
- KIS의 하루 한 번 권고 때문에 정규 DAG 외의 반복 폴링에서는 휴장 API를 호출하지 않는다.
  수동 재실행은 운영자가 공급자 제한을 확인한 뒤 수행한다.

앱키는 `config.yaml`의 `kis_app_key`/`kis_app_secret`과 `compose/local/airflow/.env`의
`KIS_APP_KEY`/`KIS_APP_SECRET`이 같아야 한다. 어긋나면 토큰 발급이 HTTP 403 `EGW00103`
("유효하지 않은 AppKey")으로 떨어진다. 2026-08-12에 실제로 그 상태였고
`kis_quote_intraday`가 종일 실패했다.

## 9. 장중 수집기 적용

### 9.1 국내 KIS 분봉

`kis_quote_intraday`는 KST 오늘의 `KRX` 행을 조회한다.

- `effective_open_day is false`: `AirflowSkipException`으로 태스크를 skip하고 분봉 API를
  호출하지 않는다.
- `true` / 행 없음 / `NULL`: 현재 동작대로 수집한다.

이 DAG는 이미 `*/5 8-16 * * 1-5`라 주말에는 돌지 않는다. **따라서 실효는 평일 공휴일에만
있다.** 대신 폴링마다 DB 조회가 한 번 늘어난다. 이 교환을 받아들인다. 캐시는 두지 않는다.

### 9.2 Yahoo 해외 분봉

Yahoo DAG 전체를 중단하지 않는다. 현재 대상에는 미국 현물 외에 선물, 아시아 지수, 외환,
원자재와 암호화폐가 함께 있기 때문이다.

실시간 폴링에서만 미국 현물 심볼(`VIX`, `SOX`, `US10Y`, `RUSSELL2000`, `TSMC_ADR`)을 거른다.

- **판정에 쓰는 날짜는 폴링 시각을 `America/New_York`으로 변환한 날짜다.** 이 DAG는
  `*/5 * * * *`로 24시간 돌고 미국 현물 거래 시간은 KST로 전날 22:30~당일 05:00이라,
  KST 날짜로 조회하면 세션의 절반이 엉뚱한 날을 본다.
- `US_EQUITY` 행이 `false`: 위 심볼만 요청 목록에서 제외한다.
- `true` / 행 없음 / `NULL`: 위 심볼을 수집한다.
- 수동 백필은 과거 요청 구간 자체가 대상이므로 오늘의 캘린더로 막지 않는다.

## 10. 오류 처리

- HTTP 상태, 연결 실패, KIS 본문 오류(`rt_cd`)와 응답 형식 오류를 별도 예외로 둔다.
- 연속조회는 총 건수를 대조할 수 없다. 페이지 상한은 정지 조건이라 오류가 아니고,
  `ctx_area_nk`가 멈춘 채 계속 이어지는 것만 실패로 만든다.
- 모르는 KIS `Y`/`N` 값은 조용히 저장하지 않고 실패시킨다.
- 미국 5행의 결제일이 서로 다르면 실패시킨다. 한 행으로 접을 수 없다는 뜻이다.
- 해외 응답 0행은 실패가 아니다. 아무 것도 갱신하지 않고 계보만 남긴다.
- NYSE 표의 연도 헤더, 셀 형식, 월 이름이 예상과 다르면 기존 판정을 유지하고 태스크를 실패시킨다.
- KIS 해외는 미국 행의 판정 컬럼을, NYSE는 결제일 컬럼을 각각 만질 수 없다.
- 캘린더 조회 결과가 없거나 불확실하면 시세 수집을 계속한다.

## 11. 변경 파일

| 파일 | 역할 |
| --- | --- |
| `apps/models/market.py` | `MarketSession`, `MarketCode`·`VerifiedBy` StrEnum |
| `apps/models/__init__.py` | `__all__`에 추가 |
| `migrations/versions/<rev>_*.py` | `upgrade_default()`에 테이블·CHECK·인덱스·주석 |
| `airflow/sql/postgres/market_session/upsert_domestic.sql` | KRX 행 upsert |
| `airflow/sql/postgres/market_session/upsert_us_session.sql` | NYSE 판정 upsert |
| `airflow/sql/postgres/market_session/update_settlement.sql` | 미국 행 결제일 갱신 |
| `airflow/sql/postgres/market_session/select_open_day.sql` | 개장 여부 조회 |
| `airflow/modules/collectors/kis_market_calendar.py` | 새 수집기 |
| `airflow/modules/collectors/nyse_calendar.py` | 새 수집기 |
| `airflow/modules/collectors/kis.py` | `_get`이 헤더를 함께 반환하도록 확장 |
| `airflow/modules/market_session.py` | 개장 여부 조회 헬퍼 |
| `airflow/dags/market_calendar_daily.py` | 새 DAG |
| `airflow/dags/kis_quote_intraday.py` | KRX 휴장일 skip |
| `airflow/dags/yahoo_quote_intraday.py` | 미국 현물 심볼 필터 |
| `tests/models/test_market_models.py` | 모델 컬럼·제약 |
| `tests/migrations/` | 마이그레이션 SQL 사실 검증 |
| `tests/collectors/test_kis_market_calendar.py` | 파싱·연속조회·접기 |
| `tests/collectors/test_nyse_calendar.py` | 표 파싱·판정 |
| `tests/collectors/test_kis.py` | `_get` 반환 변경 반영 |

## 12. 테스트와 완료 조건

### 모델·마이그레이션

- 모델과 실제 마이그레이션 SQL의 컬럼, 주석, 자연키, CHECK, 두 외래키가 일치한다.
- 두 외래키는 `ON DELETE RESTRICT`다.
- 모델은 `default` 데이터베이스로 라우팅된다.

### KIS 수집기

- 국내 `Y`/`N`과 날짜가 정확히 변환된다.
- 연속조회가 여러 장을 합치고, 페이지 상한에서 멈추며, `ctx_area_nk` 정지를 실패시킨다.
  가짜 응답에 헤더 `tr_cont`를 실어 검증한다.
- 미국 5행의 결제일이 같으면 한 행으로 접고, 다르면 실패한다.
- 해외 갱신이 `effective_open_day`와 `verified_by`를 바꾸지 않는다.
- 해외 응답 0행이 아무 행도 바꾸지 않는다.
- 미국 행이 없는데 NYSE가 개장으로 본 날에는 경고가 남는다.
- INSERT·UPDATE 문의 컬럼과 `ON CONFLICT` 키를 `MarketSession` metadata와 대조한다.

### NYSE 수집기

- 완전 휴장일은 `false`, 주말은 `false`, 그 밖의 평일과 조기 폐장일은 `true`다.
- `—*` 셀은 휴장일을 만들지 않는다.
- 각주 마커(`***`)와 괄호 주석이 붙은 셀도 같은 날짜로 파싱된다.
- 지원 연도 밖에는 행을 만들지 않는다.
- 결제일 컬럼과 `verification_source_record_id`를 바꾸지 않는다.
- 파싱 실패 시 기존 판정이 남는다.

### 장중 수집기

- KRX 확정 휴장일에는 국내 분봉 요청이 없다.
- 캘린더 행이 없거나 `NULL`이면 국내 수집을 계속한다.
- 미국 확정 휴장일에는 미국 현물 심볼만 Yahoo 요청에서 빠지고, 판정 날짜는
  `America/New_York` 기준이다.
- 같은 날에도 선물, 외환, 원자재, 아시아 지수, 암호화폐는 유지된다.
- Yahoo 백필은 오늘의 개장 여부와 무관하게 실행된다.

완료 시 전체 pytest, Ruff, Pyrefly를 실행하고 `graphify update .`로 지식 그래프를 갱신한다.

## 13. 선택한 단순화

- **한 행 안에 판정과 근거를 함께 둔다.** 출처별 관측 테이블과 resolved view로 나누는 구조는
  값의 과거 버전을 모두 보존해야 할 때 만든다. 지금은 최신 판정과 최신 결제일의 근거가 각각
  `source_record_id`로 남으므로 충분하다.
- **미국 외 해외 국가는 행으로 만들지 않는다.** 지금 그 판정을 쓰는 수집기가 없다.
  응답 원본은 `payload`에 통째로 남으므로 필요해지면 과거까지 재구성할 수 있다.
  그때는 그 나라의 공식 캘린더를 NYSE 자리에 하나 더 붙인다. KIS 응답만으로는
  §4.2의 이유로 부족하다.
- **`market_name`과 `country_code`를 관측 행에 그대로 둔다.** `indicator_series`와
  `quote_symbol`은 마스터로 뺐지만 여기는 시장이 둘뿐이고 조회가 항상 `market_code`로
  들어온다. 마스터를 하나 더 두면 조인만 늘고 얻는 게 없다. 시장이 늘고 국가에 붙는 속성이
  생기면 그때 뺀다.
- **조기 폐장을 개장으로 본다.** 장 종료 뒤의 빈 봉은 기존 수집기가 정상으로 흡수한다.
