# KIS 지수·지수선물 일봉 수집

> 작성 기준: 2026-08-27
>
> 상태: **미구현. 구현 계약.** 국내 선물 단축코드와 과거 만기물 조회 프로브가 착수 게이트다(4.4절)
>
> 대상: `KOSPI200`, `KOSPI200_FUT`, `KOSDAQ150_FUT`, `SP500`, `NASDAQ`
>
> 저장: `index_daily`, `index_future_daily`, 기존 `quote_daily` 뷰
>
> 의존: `DomesticIndex`, `DomesticFuture`, `OverseasIndex`, `market_session`, KIS 공용 인증·전송 계층

## 1. 문제

시세 수집 대상 35개 중 다섯 개는 분봉 또는 마감 분봉이 있지만 확정 일봉 수집 경로가 없다.

| 대상 | 현재 수집 | 빠진 이유 |
| --- | --- | --- |
| `KOSPI200` | KIS 국내지수 1분봉 | `kis_index_daily`가 시장 분포용 `MOVEMENT_INDEXES`만 순회해 제외됨 |
| `KOSPI200_FUT` | KIS 최근월물 1분봉 | 국내선물 일봉 수집기 없음 |
| `KOSDAQ150_FUT` | KIS 최근월물 1분봉 | 국내선물 일봉 수집기 없음 |
| `SP500` | KIS 미국장 마감 부근 1분봉 102개 | 마감 브리핑용 경로만 구현됨 |
| `NASDAQ` | KIS 미국장 마감 부근 1분봉 102개 | 마감 브리핑용 경로만 구현됨 |

다섯 값은 모두 KIS가 일봉 API를 제공한다. 분봉을 다시 집계하지 않고 제공처의 확정 일봉을
받는다. 분봉 집계는 수집 구간이 비거나 정산 봉이 섞이면 공식 OHLC와 달라진다.

## 2. 결정

| 영역 | 결정 | 이유 |
| --- | --- | --- |
| `KOSPI200` | 기존 `kis_index_daily`가 `DomesticIndex` 전체를 순회 | 같은 API·응답·저장 계약이다. 새 수집기가 필요 없다 |
| 국내 지수선물 | `KisFutureDailyCollector`와 `kis_future_daily`를 추가 | 현물 지수와 API·필드·월물 축이 다르다 |
| 미국 현물지수 | `KisOverseasIndexDailyCollector`와 `kis_overseas_index_daily`를 추가 | 기존 마감 분봉 수집기와 날짜 범위·응답·저장 목적이 다르다 |
| 저장 | 기존 `index_daily`, `index_future_daily` 재사용 | 이미 `quote_daily` 뷰와 기술지표 조회가 이 테이블들을 읽는다 |
| 선물 월물 | `index_future_daily.contract_code`를 nullable로 추가 | 논리 심볼과 실제 조회 월물을 함께 보존해야 재현할 수 있다 |
| 계보 | 한 심볼·조회 구간마다 `source_record`, 원문 payload는 `NULL` | 여러 페이지를 한 수집으로 묶고 장기 백필 원문은 저장하지 않는다 |

세 경로를 한 DAG에 합치지 않는다. KRX 현물지수, KRX 파생상품, 미국 현물지수는 거래일과
실패 원인이 다르다. 한 제공처라는 이유로 묶으면 미국 휴장이 국내 일봉을 막는다.

## 3. 공통 KIS 계약

공식 기준은 한국투자증권 Open Trading API 저장소의 `main`이다. 구현할 때 다시 대조한다.

- [국내주식업종기간별시세](https://github.com/koreainvestment/open-trading-api/blob/main/examples_llm/domestic_stock/inquire_daily_indexchartprice/inquire_daily_indexchartprice.py)
- [국내주식업종기간별시세 응답 필드](https://github.com/koreainvestment/open-trading-api/blob/main/examples_llm/domestic_stock/inquire_daily_indexchartprice/chk_inquire_daily_indexchartprice.py)
- [선물옵션기간별시세](https://github.com/koreainvestment/open-trading-api/blob/main/examples_llm/domestic_futureoption/inquire_daily_fuopchartprice/inquire_daily_fuopchartprice.py)
- [선물옵션기간별시세 응답 필드](https://github.com/koreainvestment/open-trading-api/blob/main/examples_llm/domestic_futureoption/inquire_daily_fuopchartprice/chk_inquire_daily_fuopchartprice.py)
- [해외주식 종목·지수·환율 기간별시세](https://github.com/koreainvestment/open-trading-api/blob/main/examples_llm/overseas_stock/inquire_daily_chartprice/inquire_daily_chartprice.py)
- [해외 기간별시세 응답 필드](https://github.com/koreainvestment/open-trading-api/blob/main/examples_llm/overseas_stock/inquire_daily_chartprice/chk_inquire_daily_chartprice.py)

모든 요청은 기존 `modules.collectors.kis.send_get()`을 쓴다.

```text
content-type: application/json; charset=utf-8
authorization: Bearer <access token>
appkey: <KIS_APP_KEY>
appsecret: <KIS_APP_SECRET>
tr_id: <API별 TR ID>
custtype: P
tr_cont: ""                 # 첫 요청, 다음 장은 N
```

실전과 모의는 공식 예제상 세 API 모두 같은 TR ID를 쓴다. base URL과 토큰 발급 환경만 다르다.
운영 프로젝트는 현재 실전 base URL을 쓰므로 별도 환경 분기는 추가하지 않는다.

응답 본문의 `rt_cd`가 `0`이 아니면 `result_error()`로 `KisResultError` 또는
`KisTimeWindowError`로 바꾼다. 응답 헤더 `tr_cont`가 `M` 또는 `F`면 다음 요청에 `N`을 보낸다.
단, 공식 예제가 연속조회를 구현하지 않은 선물 API도 있으므로 행 상한과 헤더는 프로브로 먼저
확정한다.

## 4. 국내 지수선물 일봉

### 4.1 요청

| 항목 | 값 |
| --- | --- |
| Method | `GET` |
| Path | `/uapi/domestic-futureoption/v1/quotations/inquire-daily-fuopchartprice` |
| TR ID | `FHKIF03020100` (실전·모의 동일) |
| `FID_COND_MRKT_DIV_CODE` | `F` — 지수선물 |
| `FID_INPUT_ISCD` | 실제 선물 단축코드. 공식 예: `101W09` |
| `FID_INPUT_DATE_1` | 시작일 `YYYYMMDD` |
| `FID_INPUT_DATE_2` | 종료일 `YYYYMMDD` |
| `FID_PERIOD_DIV_CODE` | `D` — 일봉 |

현재 분봉 수집의 `front_contract()`는 `A01609`·`A06609` 꼴 코드를 만든다. 일봉 공식 예제는
`101W09` 꼴의 **선물 단축코드**를 요구한다. 둘이 같다고 가정하거나 문자열 치환 규칙을 만들지
않는다. KIS 종목 마스터 또는 선물 종목 조회에서 현재 계약의 단축코드를 얻고, 응답
`futs_shrn_iscd`가 요청 코드와 같은지 검증한다.

### 4.2 응답 매핑

`output2`에서 다음 값만 저장한다.

| KIS 필드 | 저장 컬럼 |
| --- | --- |
| `stck_bsop_date` | `business_date` |
| `futs_oprc` | `open` |
| `futs_hgpr` | `high` |
| `futs_lwpr` | `low` |
| `futs_prpr` | `close` |
| `acml_vol` | `volume` |
| 요청·응답의 실제 단축코드 | `contract_code` |

`symbol`은 월물과 무관한 `KOSPI200_FUT` 또는 `KOSDAQ150_FUT`이다. 거래대금, 이론가,
미결제약정, 베이시스는 이번 일봉 계약에 넣지 않는다. 필요해질 때 별도 분석 원천으로 설계한다.

### 4.3 연속 시계열과 롤오버

저장 시계열은 **최근월물 비조정 연결선물**이다.

1. 만기월은 기존 `CONTRACT_MONTHS=(3, 6, 9, 12)`와 `expiry_date()`를 재사용한다.
2. 만기일 일봉까지 만기월에 귀속한다.
3. 만기 다음 거래일부터 차기월물 일봉을 같은 논리 `symbol` 아래 저장한다.
4. 각 행의 `contract_code`로 실제 월물을 남긴다.
5. 가격 차이를 소급 조정하지 않는다. 롤오버 수익률이 실제 기초자산 수익률이 아니라는 사실은
   조회·분석 계층이 `contract_code` 변경일로 판단한다.

같은 거래일에 두 월물이 모두 있어도 선택 규칙은 하나다. `front_contract()`와 같은 만기 선택
규칙이 가리키는 계약만 논리 시계열에 들어간다. 실제 요청 코드는 4.4절 프로브로 확정한 별도
단축코드 해석기가 돌려준다. 거래량 우위로 임의 롤하지 않는다. 현재 분봉과 일봉이 같은 만기
선택 규칙을 써야 베이시스 비교 날짜가 어긋나지 않는다.

### 4.4 구현 착수 전 프로브

운영 앱키로 다음을 확인하고 결과를 이 절에 날짜와 함께 기록한다. 하나라도 확인되지 않으면
선물 구현은 시작하지 않는다.

1. KOSPI200·KOSDAQ150 최근월물에 필요한 단축코드와 현재 분봉 코드의 대응.
2. 두 상품 모두 `FID_COND_MRKT_DIV_CODE=F`, `FID_PERIOD_DIV_CODE=D`로 OHLCV가 오는지.
3. 만기일 전후 계약 두 개를 각각 조회할 수 있는지.
4. 만기된 계약도 과거 날짜로 조회 가능한지. 불가능하면 백필 범위를 운영 시작일 이후로 제한한다.
5. 한 응답의 최대 행 수와 응답 헤더 `tr_cont` 값.
6. `output2`의 정렬 방향과 `futs_shrn_iscd` 제공 위치.

### 4.5 저장 스키마

`index_future_daily`에만 nullable `contract_code TEXT`를 추가한다. Yahoo의 `ES=F` 같은 연속
심볼은 실제 월물이 없으므로 `NULL`, KIS 국내선물은 실제 단축코드를 저장한다. 자연키는 기존
`(provider, symbol, business_date)`를 유지한다. 선택된 최근월물은 날짜마다 하나뿐이다.

마이그레이션은 손으로 작성하고 다음을 함께 바꾼다.

- `apps/models/market/series.py`의 `IndexFutureDaily`
- `airflow/sql/postgres/index_future_daily/upsert.sql`
- squash 리비전은 고치지 않고 새 리비전에서 `ADD COLUMN`
- `quote_daily` 뷰는 공통 읽기 모양을 유지하므로 `contract_code`를 노출하지 않는다

## 5. 국내 `KOSPI200` 일봉

새 API나 저장 모델을 만들지 않는다. 기존 `KisIndexDailyCollector`는 `DomesticIndex`를 받으며
`KOSPI200(index_code="2001")`도 이미 같은 타입에 있다.

`kis_index_daily.collect()`의 순회만 다음 의미로 고친다.

```text
현재: MOVEMENT_INDEXES = (KOSPI, KOSDAQ)
변경: 모든 DomesticIndex = (KOSPI, KOSPI200, KOSDAQ)
```

`MOVEMENT_INDEXES`는 상승·보합·하락 종목 분포를 조회하기 위한 부분집합이다. KOSPI200이 시장
전체 분포가 아니라는 이유는 가격 일봉 수집과 무관하므로 일봉 DAG에서 재사용하지 않는다.

요청 계약은 기존과 같다.

| 항목 | 값 |
| --- | --- |
| Path | `/uapi/domestic-stock/v1/quotations/inquire-daily-indexchartprice` |
| TR ID | `FHKUP03500100` |
| `FID_COND_MRKT_DIV_CODE` | `U` |
| `FID_INPUT_ISCD` | `2001` |
| `FID_INPUT_DATE_1`, `FID_INPUT_DATE_2` | 조회 구간 |
| `FID_PERIOD_DIV_CODE` | `D` |

파싱, 날짜 창 걷기, `index_daily` upsert, 휴장 판정, 실패 정책은 기존 구현을 그대로 쓴다.

## 6. 미국 현물지수 일봉

### 6.1 요청

| 항목 | 값 |
| --- | --- |
| Method | `GET` |
| Path | `/uapi/overseas-price/v1/quotations/inquire-daily-chartprice` |
| TR ID | `FHKST03030100` (실전·모의 동일) |
| `FID_COND_MRKT_DIV_CODE` | `N` — 해외지수 |
| `FID_INPUT_ISCD` | `SPX` 또는 `COMP` |
| `FID_INPUT_DATE_1` | 시작일 `YYYYMMDD` |
| `FID_INPUT_DATE_2` | 종료일 `YYYYMMDD` |
| `FID_PERIOD_DIV_CODE` | `D` |

`SPX`와 `COMP`는 기존 `OverseasIndex`가 소유한다. 새 심볼 Enum이나 `quote_symbol` 시드는
만들지 않는다. 기존 실측 문서에서 두 코드의 일봉 동작과 공식 종가를 확인했다.

### 6.2 응답 매핑

| KIS 필드 | 저장 컬럼 |
| --- | --- |
| `stck_bsop_date` | `business_date` |
| `ovrs_nmix_oprc` | `open` |
| `ovrs_nmix_hgpr` | `high` |
| `ovrs_nmix_lwpr` | `low` |
| `ovrs_nmix_prpr` | `close` |
| `acml_vol` | `volume` |

응답의 `stck_shrn_iscd`가 있으면 요청한 `SPX`·`COMP`와 대조한다. 현물 지수 거래량은 0이어도
허용한다. `business_date`는 KIS가 주는 뉴욕 거래일을 그대로 쓴다. 타임스탬프를 UTC 날짜로
다시 계산하지 않는다.

한 응답의 행 상한과 `tr_cont`는 구현 전 프로브로 확인한다. 연속조회가 없다면 기존 국내지수
일봉처럼 가장 오래된 응답 날짜의 전날로 종료일을 옮겨 창을 걷는다. 빈 응답, 요청 시작일 도달,
최대 10장 중 하나에서 멈춘다. 열 번째 장에도 더 과거 값이 있으면 부분 성공으로 끝내지 않고
해당 심볼을 실패시킨다.

## 7. 수집기와 DAG

### 7.1 파일 경계

| 역할 | 파일 |
| --- | --- |
| 국내지수 기존 수집기 | `airflow/modules/collectors/market/kis_index_daily.py` |
| 국내지수 기존 DAG | `airflow/dags/kis_index_daily.py` |
| 국내선물 일봉 수집기 | `airflow/modules/collectors/market/kis_future_daily.py` |
| 국내선물 일봉 DAG | `airflow/dags/kis_future_daily.py` |
| 미국지수 일봉 수집기 | `airflow/modules/collectors/market/kis_overseas_index_daily.py` |
| 미국지수 일봉 DAG | `airflow/dags/kis_overseas_index_daily.py` |

새 수집기는 자격 증명이나 DB 연결을 소유하지 않는다. `send_get`, `_decimal`, `result_error`,
`Kis*Error`와 `source_record` SQL은 공용 계층에서 재사용한다. 각 수집기는 요청·Pydantic 응답
모델·파싱·저장만 갖는다.

### 7.2 일정

| DAG | KST 일정 | 앞뒤 관계 |
| --- | --- | --- |
| `kis_index_daily` | 기존 평일 18:20 | 종목 일봉 18:10 뒤, 그대로 유지 |
| `kis_future_daily` | 평일 18:30 | 현물지수 일봉 뒤, `technical_signal_daily` 18:40 앞 |
| `kis_overseas_index_daily` | 화~토 07:35 | 캘린더 07:00과 마감 분봉 07:30 뒤, 미국 브리핑 08:00 앞 |

국내 DAG는 `krx_open_day`, 미국 DAG는 `us_equity_open_day`가 명시적으로 `False`일 때만 skip한다.
캘린더가 아직 없어 `None`이면 진행하고 응답 날짜 검증이 묵은 값을 막는다. 세 DAG 모두
`max_active_runs=1`, `catchup=False`, 재시도 2회다.

### 7.3 파라미터와 백필

세 DAG는 `end_date`, `start_date`를 `YYYY-MM-DD`로 받는다. 비우면 실행일을 끝으로 최근
200달력일을 요청한다. 자동 실행만 휴장일을 건너뛰며 수동 백필은 끝 날짜가 휴장이어도 구간
안의 거래일을 받을 수 있으므로 진행한다.

```bash
airflow dags trigger kis_future_daily \
  --conf '{"start_date":"2025-01-01","end_date":"2026-08-27"}'

airflow dags trigger kis_overseas_index_daily \
  --conf '{"start_date":"2016-08-15","end_date":"2026-08-27"}'
```

선물 백필은 4.4절의 만기물 과거 조회가 확인된 범위까지만 허용한다. 사용자가 계약 코드를
직접 넣는 파라미터는 만들지 않는다. 논리 시계열의 롤 규칙을 우회할 통로가 된다.

## 8. 검증·오류·트랜잭션

각 응답은 저장 전에 다음을 모두 검증한다.

- JSON 본문과 `output2` 배열 존재
- `rt_cd == "0"`
- 요청·응답 심볼 또는 계약코드 일치
- 날짜가 정확한 `YYYYMMDD`, 요청 범위 안, 중복 없음
- OHLC가 유한한 양수
- `high >= max(open, close, low)`, `low <= min(open, close, high)`
- 거래량이 0 이상
- 페이지 사이 날짜가 과거 방향으로 전진하며 같은 마지막 날짜가 반복되지 않음
- 자동 실행이면 최신 행의 날짜가 기대 시장 세션과 일치

한 심볼의 모든 페이지를 먼저 파싱한 뒤 한 트랜잭션으로 저장한다. 그 심볼에는 부분 구간을
남기지 않는다. 다른 심볼이 먼저 성공해 커밋됐다면 보존하고, 루프 종료 뒤 실패 목록을 모아
DAG를 실패시킨다. 자연키 upsert라 Airflow 재시도는 안전하다.

- HTTP 400·403·404: 설정·주소 오류로 즉시 `AirflowFailException`
- HTTP 401: 공유 토큰을 `force=True`로 한 번 재발급하고 해당 요청 한 번 재시도
- 그 밖의 HTTP·연결 오류: Airflow 재시도
- `KisTimeWindowError`: 재시도해도 같은 시각 제한이면 즉시 실패하고 허용 시각을 메시지에 표시
- 응답 계약 위반·페이지 상한 초과: 해당 심볼 저장 없이 즉시 실패
- 정상 휴장: DAG 시작에서 skip. 수동 백필 중 빈 페이지는 걷기 종료

`source_record.metadata`에는 `symbol`, KIS 코드, `contract_code`, 요청 시작·종료일, 페이지 수,
행 수, 가장 이른·늦은 거래일, 실패 사유를 남긴다. 앱키·토큰은 남기지 않는다.

## 9. 테스트 계약

### 9.1 국내지수

- `tests/dags/test_kis_index_daily.py`: 수집 대상이 `DomesticIndex` 전체인지
- 기존 KOSPI·KOSDAQ 스케줄·휴장·백필 회귀
- `tests/collectors/test_kis_index_daily_collector.py`: `KOSPI200/2001` 요청과 저장 심볼 대조

### 9.2 국내선물

- 요청 path, TR ID, `F`, `D`, 날짜, 단축코드, `tr_cont`
- `output2` 역순을 거래일 오름차순으로 정규화
- OHLCV 필드와 실제 `contract_code` 저장
- 만기일까지 구월물, 다음 거래일부터 차기월물
- 두 월물이 겹치는 날짜에 하나만 선택
- 잘못된 코드·빈 응답·비수치·OHLC 불일치·날짜 반복·페이지 상한 실패
- `index_future_daily` SQL 컬럼을 ORM metadata와 대조
- Yahoo 일봉의 `contract_code IS NULL` 회귀

### 9.3 미국지수

- `SPX`, `COMP` 두 요청과 `N`, `D`, 날짜, `tr_cont`
- `stck_bsop_date`를 뉴욕 거래일로 그대로 저장
- 거래량 0 허용, 심볼 불일치·묵은 날짜·빈 차트 실패
- `source_record`와 `index_daily` upsert 컬럼 계약
- 미국 휴장만 skip하고 국내 DAG에는 영향 없음

### 9.4 통합

- `tests/migrations/test_quote_split_revision.py`: 새 컬럼과 기존 `quote_daily` 뷰 호환
- `tests/migrations/test_quote_symbol_catalog.py`: 다섯 심볼의 기존 provider·kind 불변
- `tests/dags/test_quote_intraday.py`: DAG 일정 충돌 없음
- `technical/select_history.sql`, `technical/select_symbols.sql`이 새 행을 별도 수정 없이 읽는지

## 10. 구현 순서

1. 선물 단축코드·과거 만기물·페이지 동작을 프로브하고 4.4절을 실측값으로 갱신한다.
2. `KOSPI200`을 기존 국내지수 일봉 순회에 포함한다.
3. `index_future_daily.contract_code` 마이그레이션·모델·upsert를 함께 바꾼다.
4. 국내선물 일봉 수집기와 DAG를 테스트부터 추가한다.
5. 미국지수 일봉 수집기와 DAG를 테스트부터 추가한다.
6. 전체 테스트·정적 검사·오프라인 마이그레이션 SQL을 확인한다.
7. 운영은 국내지수 하루치 → 미국지수 하루치 → 선물 하루치 순으로 시험하고, 마지막에 백필한다.
8. 코드 변경 뒤 `graphify update .`로 지식을 갱신한다.

## 11. 운영 검증

```sql
SELECT provider, symbol, min(business_date), max(business_date), count(*)
FROM quote_daily
WHERE (provider, symbol) IN (
    ('kis', 'KOSPI200'),
    ('kis', 'KOSPI200_FUT'),
    ('kis', 'KOSDAQ150_FUT'),
    ('kis', 'SP500'),
    ('kis', 'NASDAQ')
)
GROUP BY provider, symbol
ORDER BY symbol;
```

선물은 물리 테이블에서 월물 경계를 별도로 본다.

```sql
SELECT symbol, contract_code, min(business_date), max(business_date), count(*)
FROM index_future_daily
WHERE provider = 'kis'
GROUP BY symbol, contract_code
ORDER BY symbol, min(business_date);
```

확인 순서는 다음과 같다.

1. 최신 거래일 OHLC가 KIS 응답과 일치.
2. `KOSPI200`과 `KOSPI200_FUT` 거래일이 같은 KRX 개장일을 덮음.
3. 선물 만기일과 다음 거래일 사이 `contract_code`가 정확히 한 번 바뀜.
4. 미국 최신 거래일이 뉴욕 세션 날짜이고 KST 날짜로 하루 밀리지 않음.
5. `daily_history`와 기술지표 조회에서 다섯 심볼이 보임.

## 12. 범위 밖

- 선물 가격의 역조정·비율조정 연속선물. 원천값을 바꾸는 별도 분석 설계가 필요하다.
- 차근월물·원월물 전체 저장과 기간구조 분석.
- 선물 미결제약정·베이시스·이론가 저장.
- 다우·러셀2000 KIS 수집 확대. 현재 대상 다섯 개와 무관하다.
- Yahoo와 KIS 사이 자동 대체. 제공처가 바뀌면 값의 정의와 계보도 바뀐다.
- 분봉에서 일봉을 재계산하는 보조 경로.
