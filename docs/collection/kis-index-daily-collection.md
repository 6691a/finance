# KIS 지수·지수선물 일봉 수집

> 작성 기준: 2026-08-27
>
> 상태: **미구현. 구현 계약. 착수 게이트는 통과했다** — 선물 단축코드·과거 만기물(4.4절)과
> 미국 `COMP` 일봉(6.3절)을 2026-08-27 운영 앱키로 실측했다
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

**세 API의 페이지 동작이 서로 다르다.** 공식 예제(2026-08-27 대조) 기준이다.

| API | 예제의 연속조회 | 실측 `tr_cont` | 실측 행 상한 |
| --- | --- | --- | --- |
| 국내지수 `inquire_daily_indexchartprice` | 구현 | 기존 수집기가 헤더와 창 걷기를 둘 다 다룬다 | 미기재 |
| 국내선물 `inquire_daily_fuopchartprice` | 미구현 | **빈 문자열** | **100행** |
| 해외지수 `inquire_daily_chartprice` | 구현(`M`·`F`면 재귀) | **빈 문자열** | **100행** |

**새 둘은 `tr_cont`를 아예 안 준다**(2026-08-27 실측). 해외는 공식 예제가 연속조회를 구현해 두었는데도
실제 응답 헤더가 비어 있었다. 그러므로 **새 수집기 둘은 창 걷기만 구현한다.** 안 오는 분기를 미리 두면
테스트에서만 도는 죽은 길이 되고, 나중에 KIS가 헤더를 주기 시작해도 창 걷기는 그대로 맞는다.

창 걷기 자체도 실측했다. 종료일을 **가장 오래된 응답 날짜의 전날**로 옮기면 이어진다.

```text
선물 A01609  2026-02-10 ~ 2026-08-27 요청
  1장 end=20260827 -> 100행 20260402..20260827
  2장 end=20260401 ->  33행 20260210..20260401
  3장 end=20260209 ->   0행 (정지)

해외 COMP   같은 구간
  1장 end=20260827 -> 100행 20260406..20260826
  2장 end=20260405 ->  35행 20260212..20260402
  3장 end=20260211 ->   2행 20260210..20260211
```

200달력일(약 135거래일)은 두 장이다. `INDEX_DAILY_MAX_PAGES`(10)에 넉넉히 들어간다.

## 4. 국내 지수선물 일봉

### 4.1 요청

| 항목 | 값 |
| --- | --- |
| Method | `GET` |
| Path | `/uapi/domestic-futureoption/v1/quotations/inquire-daily-fuopchartprice` |
| TR ID | `FHKIF03020100` (실전·모의 동일) |
| `FID_COND_MRKT_DIV_CODE` | `F` — 지수선물 |
| `FID_INPUT_ISCD` | 선물 종목코드. **분봉과 같은 `A0` 형식**(`A01609`). 4.4절 실측 |
| `FID_INPUT_DATE_1` | 시작일 `YYYYMMDD` |
| `FID_INPUT_DATE_2` | 종료일 `YYYYMMDD` |
| `FID_PERIOD_DIV_CODE` | `D` — 일봉 |

**코드 변환은 필요 없다.** 공식 예제가 `101W09`를 예시로 들지만 그 코드는 실제로 0행을 돌려주고,
분봉이 쓰는 `A01609`·`A06609`가 그대로 먹는다(4.4절 실측). `front_contract()`의 형식이 이미 맞다.

남은 문제는 형식이 아니라 **범위**다. `front_contract(future, today)`는 그날 거래되는 최근월물
하나만 주는데 조회 창 200달력일은 만기를 최소 하나 넘는다. 4.4.1절이 그 열거 규칙이다.

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
| **요청한 창의 코드** (응답이 아니다 — 아래) | `contract_code` |

`output2` 한 행이 실제로 가진 칸은 여덟이다(2026-08-27 실측):
`stck_bsop_date`·`futs_oprc`·`futs_hgpr`·`futs_lwpr`·`futs_prpr`·`acml_vol`·`acml_tr_pbmn`·`mod_yn`.
**`futs_shrn_iscd`는 `output2`에 없다** — 요청 코드는 `output1`에만 오고, 그마저 만기물에서는 빠진다
(4.4절). 그래서 `contract_code`의 원본은 응답이 아니라 **요청한 창의 코드**다.

`symbol`은 월물과 무관한 `KOSPI200_FUT` 또는 `KOSDAQ150_FUT`이다. 거래대금(`acml_tr_pbmn`),
수정 여부(`mod_yn`), 이론가, 미결제약정, 베이시스는 이번 일봉 계약에 넣지 않는다. 필요해질 때
별도 분석 원천으로 설계한다.

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

### 4.4 프로브 결과 (2026-08-27 실측)

운영 앱키로 읽기 전용 조회만 했다. 스크립트는 스크래치패드에 있었고 저장소에 없다.

**선물 종목 조회** — `display-board-futures`, TR `FHPIF05030200`,
`FID_COND_MRKT_DIV_CODE=F`, `FID_COND_SCR_DIV_CODE=20503`. 결과는 `output`(하나짜리 배열이 아니라
`output` 키다. `output1`/`output2`가 아니다).

| `FID_COND_MRKT_CLS_CODE` | 돌아온 것 |
| --- | --- |
| 빈 값 또는 `K2I` | **정규 KOSPI200 선물** `A01609`(`F 202609`), `A01612`, `A01703` … 7건 |
| `MKI` | 미니 KOSPI200 선물 `A05609`(`미니F 202609`) … 6건 |
| `KQI` | **코스닥150 선물** `A06609`(`코스닥150F 202609`) … 7건 |
| `KSI`·`MFI`·`KPI`·`MNI` | `rt_cd=0`에 0건 |

**`MKI`는 미니다.** 이름만 보고 고르면 계약 크기가 1/5인 다른 상품을 받는다. 정규는 빈 값이나 `K2I`다.

**일봉 조회** — `inquire-daily-fuopchartprice`, TR `FHKIF03020100`, `F`/`D`,
`FID_INPUT_DATE_1=20260301`, `FID_INPUT_DATE_2=20260827`.

| 요청 코드 | `rt_cd` | 행 | 구간 | `output1.futs_shrn_iscd` |
| --- | --- | --- | --- | --- |
| `A01609` (정규 K200 최근월) | 0 | 100 | 20260402~20260827 | `A01609` |
| `A06609` (코스닥150 최근월) | 0 | 100 | 20260402~20260827 | `A06609` |
| `A01606` (**만기 지난** 6월물) | 0 | 69 | 20260303~20260611 | **없음** |
| `A06606` (만기 지난 6월물) | 0 | 69 | 20260303~20260611 | **없음** |
| `101W09` (공식 예제 형식) | 0 | **0** | — | 없음 |
| `101WC000` | 0 | **0** | — | 없음 |

여기서 나온 사실 여섯.

1. **`A0` 형식이 그대로 먹고 `101W09`는 0행이다.** 코드 변환기를 만들 것이 없다. 공식 예제의
   `101W09`는 우리가 쓰는 시장 구분에서 맞지 않는다.
2. **만기된 계약도 과거 날짜로 조회된다.** `A01606`이 만기일 20260611까지 69행을 줬다. 백필 범위를
   운영 시작일로 제한할 이유가 없다.
3. **`expiry_date()`가 맞다.** 2026년 6월 두 번째 목요일은 6월 11일이고 만기물의 마지막 행이 그 날짜다.
   만기일 봉이 들어 있으므로 4.3절의 "만기일까지 만기월에 귀속"도 실제와 맞는다.
4. **`output1`은 만기물에서 빈 dict(`{}`)다.** 타입은 언제나 dict이지만 칸이 없다. 그래서 요청·응답
   코드 대조는 **"있으면 대조"**여야 한다. 필수로 만들면 만기물 조회가 전부 실패한다.
5. **행 상한은 100이고 `tr_cont` 헤더는 빈 문자열이다.** 창 걷기가 유일한 페이지 수단이다(3절).
6. **`output2`는 최신순(내림차순)이다.** 저장 전에 오름차순으로 뒤집는다.

`A01606`의 가장 오래된 행이 20260303인 것은 잘림이 아니다. 요청 시작일 20260301이 일요일이고
20260302가 삼일절 대체휴일이라 그 앞에 거래일이 없다. 69행은 100 미만이라 상한에도 안 닿았다.

### 4.4.1 과거 월물 열거 규칙

`front_contract(future, today)`는 **그날 거래되는 최근월물 하나**만 준다. 조회 창이 200달력일이라
만기를 최소 하나 넘고 백필은 더 넘는다. 그 함수로 구간 전체를 요청하면 과거 구간까지 현재 월물
코드로 물어보게 되고, 4.4절 실측대로 그 구간은 조용히 빈다.

그래서 **구간을 덮는 월물들을 열거하는 함수를 새로 만든다.** `CONTRACT_MONTHS`와 `expiry_date`를
재사용해 만기일(포함)까지를 그 월물의 창으로 끊고 다음 날부터 차기월물 창을 연다. 4.3절의 롤
규칙과 같은 규칙을 쓰므로 두 곳에 판단이 생기지 않는다. 실측이 그 규칙을 뒷받침한다 — `A01606`의
마지막 행이 만기일 20260611이고, `A01609`가 그 뒤를 잇는다.

**코드 형식은 분봉과 같아서 `front_contract`의 문자열 조립을 그대로 쓴다.** 다만 그 형식은 연도가
한 자리다(`A0{자릿수}{year % 10}{MM}`). 10년을 넘기면 `A01609`가 2016년 9월물과 겹친다. 지금
백필 계획(2025년~)에서는 문제가 아니고, **열거 함수가 연·월을 인자로 받게 두면** 그때 형식만
바꾸면 된다.

### 4.5 저장 스키마

`index_future_daily`에만 nullable `contract_code TEXT`를 추가한다. Yahoo의 `ES=F` 같은 연속
심볼은 실제 월물이 없으므로 `NULL`, KIS 국내선물은 실제 단축코드를 저장한다. 자연키는 기존
`(provider, symbol, business_date)`를 유지한다. 선택된 최근월물은 날짜마다 하나뿐이다.

마이그레이션은 손으로 작성하고 다음을 함께 바꾼다.

- `apps/models/market/series.py`의 `IndexFutureDaily`
- `airflow/sql/postgres/index_future_daily/upsert.sql`
- squash 리비전은 고치지 않고 새 리비전에서 `ADD COLUMN`
- `quote_daily` 뷰는 공통 읽기 모양을 유지하므로 `contract_code`를 노출하지 않는다
- `airflow/modules/collectors/market/yahoo.py`의 `_daily_upserts()`. 지금은 매크로 kind 전부가
  9칸 행 하나를 공유하는데 `index_future_daily`만 10칸이 된다 — 그 kind에 분기가 하나 는다

리비전 파일에 대한 것 셋.

- **`upgrade(engine_name)` 디스패치 형태여야 한다.** `migrations/env.py`가 별칭 이름을 넘긴다.
  `upgrade()`/`downgrade()`로 쓰면 호출에서 죽는다. `b6d02f5a91c7_add_cached_prompt_tokens.py`가 형태다.
- **`down_revision`은 리비전 파일을 읽어 그때의 head로 잡는다.** 이 문서를 쓴 2026-08-27 시점은
  `b6d02f5a91c7`다.
- **`alembic` CLI를 직접 부르지 않는다.** 별칭 목록이 `config.yaml`에서 `migrations/cli.py`를 거쳐
  들어가므로 진입점은 `just migrate <args>`다. 오프라인 SQL 검증은 이미 `tests/helpers.head_sql()`이
  in-process로 하고 있어 셸 단계를 따로 둘 이유도 없다. autogenerate는 `config.yaml`이 운영 DB를
  가리켜 어차피 못 돌린다.

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
**부를 대상은 상수로 뽑지 않고 `DomesticIndex`를 그대로 순회한다.** `DAILY_INDEXES = tuple(DomesticIndex)`
같은 상수를 두면 그것을 검증하는 테스트가 `tuple(DomesticIndex)`와 대조하는 동어반복이 된다. 실제
회귀는 "부분집합을 다시 import한다"이므로 그것을 막는 테스트를 둔다.

**이 일봉이 기술 신호를 만들지는 않는다.** `modules/technical/signals.py`의
`SIGNAL_INDEXES = ("KOSPI", "KOSDAQ")`가 화이트리스트라, KOSPI200 봉이 쌓여도
`technical_signal_daily`는 그것을 보지 않는다. 새 봉을 읽는 곳은 `quote_daily` 조회와 추론 툴
`daily_history`다. **KOSPI200과 선물 둘을 신호 대상으로 삼을지는 이 문서 밖의 결정이고, 정하면
`SIGNAL_INDEXES`를 늘리는 것이 전부다.** 1절이 이유로 든 "기술지표의 원천"은 자동으로 따라오지 않는다.

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
만들지 않는다 — 다섯 심볼의 `quote_symbol` 행은 `2029012bafaa`와 `a3f9c1d27e64`가 이미 넣었고
provider도 전부 `kis`다(2026-08-27 확인).

**두 코드 모두 일봉에서 된다.** 6.3절이 2026-08-27 실측이다.

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

공식 예제는 `tr_cont`가 `M`·`F`면 같은 인자로 재귀하는 연속조회를 구현한다(3절 표). 행 상한은
안 적혀 있다. 헤더가 없는 응답에는 기존 국내지수 일봉처럼 가장 오래된 응답 날짜의 전날로 종료일을
옮겨 창을 걷는다 — **두 행태를 다 다룬다.** 빈 응답, 요청 시작일 도달, 최대 10장 중 하나에서
멈춘다. 열 번째 장에도 더 과거 값이 있으면 부분 성공으로 끝내지 않고 해당 심볼을 실패시킨다.

### 6.3 프로브 결과 (2026-08-27 실측)

공식 예제의 `FID_INPUT_ISCD` 설명이 **"미국주식 (다우30, 나스닥100, S&P500만 가능)"**이라 나스닥
**종합**인 `COMP`가 일봉에서 되는지가 확실하지 않았다. 2026-08-22 실측은 분봉
(`inquire-time-indexchartprice`) 기준이었고 그 문서의 일봉 줄은 `.DJI`만 다룬다.

`inquire-daily-chartprice`, TR `FHKST03030100`, `N`/`D`, `20260210`~`20260827` 요청.

| 코드 | `rt_cd` | 행 | 구간 | `output1.stck_shrn_iscd` | `hts_kor_isnm` | `acml_vol` |
| --- | --- | --- | --- | --- | --- | --- |
| `SPX` | 0 | 100 | 20260406~20260826 | `SPX` | S&P500 | `0` |
| `COMP` | 0 | 100 | 20260406~20260826 | `COMP` | 나스닥 종합 | `7421658300` |
| `NDX` | 0 | 100 | 20260406~20260826 | `NDX` | 나스닥 100 | `0` |
| `.DJI` | 0 | 100 | 20260406~20260826 | **없음** | **없음** | `426130740` |

사실 다섯.

1. **`COMP`가 일봉에서 된다.** 공식 예제의 설명이 좁게 적혀 있을 뿐이다. 대체 코드를 찾을 필요가 없다.
2. `output2` 칸은 `stck_bsop_date`·`ovrs_nmix_oprc`·`ovrs_nmix_hgpr`·`ovrs_nmix_lwpr`·
   `ovrs_nmix_prpr`·`acml_vol`·`mod_yn` 일곱이다. 6.2절 매핑이 맞고 `mod_yn`은 안 쓴다.
3. **거래량은 계열마다 0이거나 아니거나다.** `SPX`·`NDX`는 0, `COMP`·`.DJI`는 실제 값이 온다.
   `volume >= 0`으로 받는다. 0을 결측으로 읽지 않는다.
4. **`output1.stck_shrn_iscd`는 `.DJI`에서 아예 없다.** 국내선물 만기물과 같은 형태다. 대조는
   **"있으면 대조"**다.
5. **행 상한 100, `tr_cont`는 빈 문자열.** 공식 예제가 연속조회를 구현해 두었는데도 실제 헤더가
   비어 있다. 창 걷기만 구현한다(3절).

`.DJI`의 일봉이 온다는 것은 2026-08-22 실측(분봉 0건)과 어긋나지 않는다. 이번 계약에 넣지 않는 것은
12절 그대로다.

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

**정규화 봉 모델은 `kis_index_daily.DailyIndexBar` 하나를 쓴다.** 양수·유한 OHLC validator,
고저 일관성 model validator, `volume: int = Field(ge=0)`이 이미 세 경로가 요구하는 것과 같다
(지수 거래량 0도 이 조건에 들어간다). 해외는 그대로 쓰고, 선물은 `contract_code` 한 칸을 더한
**하위 클래스**로 둔다. 같은 검증을 세 번 적으면 언젠가 한 벌만 고쳐진다.

`source_key`는 엔드포인트 이름이다. 기존 `INDEX_DAILY_SOURCE_KEY`가
`"inquire_daily_indexchartprice"`이므로 새 둘은 `"inquire_daily_fuopchartprice"`,
`"inquire_daily_chartprice"`다.

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

**파라미터 오류는 `AirflowFailException`이다.** `ValueError`가 아니다. 기존
`kis_index_daily._calendar_day()`가 모양을 정규식으로 먼저 보고 그 예외를 올린다 — 되돌릴 수 없는
설정 오류라 재시도해도 같은 답이기 때문이다. 새 DAG 둘도 그 함수를 그대로 따른다.

**창 크기는 "200일 간격"이지 "최대 200일"이 아니다.** 기존 `fetch_windows()`는 커서를 200일씩
밀고 창의 끝을 포함으로 잡아 한 창이 201달력일을 덮는다. 테스트 계약을 "at most 200"으로 적으면
같은 코드를 복사한 새 DAG에서 그 테스트가 깨진다.

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
- **`output1`에 코드 칸이 있으면** 요청과 일치. 국내선물 만기물과 해외 `.DJI`에는 그 칸이 아예
  없다(4.4·6.3절 실측). 필수로 만들면 만기물 백필이 통째로 실패한다
- 날짜가 정확한 `YYYYMMDD`, 요청 범위 안, 중복 없음
- OHLC가 유한한 양수
- `high >= max(open, close, low)`, `low <= min(open, close, high)`
- 거래량이 0 이상
- 페이지 사이 날짜가 과거 방향으로 전진하며 같은 마지막 날짜가 반복되지 않음
- **`output2`는 최신순으로 오므로 저장 전에 오름차순으로 뒤집는다**
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

`source_record.metadata`에는 `symbol`, KIS 코드, `contract_code`(선물은 창마다 다르므로 목록),
요청 시작·종료일, 페이지 수, 행 수, 가장 이른·늦은 거래일, 실패 사유를 남긴다.
앱키·토큰은 남기지 않는다.

`source_key`는 엔드포인트 이름이다(7.1절). 기존 `"inquire_daily_indexchartprice"`와 같은 규칙으로
`"inquire_daily_fuopchartprice"`, `"inquire_daily_chartprice"`다.

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
- 만기를 넘는 구간이 월물 창 둘로 갈리고, 각 창이 자기 코드로만 요청되는지
  (`front_contract`로 구간 전체를 요청하면 과거 창이 조용히 빈다)
- `index_future_daily` SQL 컬럼을 ORM metadata와 대조
- Yahoo 일봉의 `contract_code IS NULL` 회귀

### 9.3 미국지수

- `SPX`, `COMP` 두 요청과 `N`, `D`, 날짜, `tr_cont`
- `stck_bsop_date`를 뉴욕 거래일로 그대로 저장
- 거래량 0 허용, 심볼 불일치·묵은 날짜·빈 차트 실패
- **`stck_shrn_iscd`가 아예 없는 응답도 통과.** `.DJI`에 그 칸이 없었다. 불일치만 테스트하면
  그 칸을 필수로 만든 구현이 통과하고, 칸이 빠진 첫 응답에서 죽는다
- `source_record`와 `index_daily` upsert 컬럼 계약
- 미국 휴장만 skip하고 국내 DAG에는 영향 없음

### 9.4 통합

- `tests/migrations/test_quote_split_revision.py`: 새 컬럼과 기존 `quote_daily` 뷰 호환
- `tests/migrations/test_quote_symbol_catalog.py`: 다섯 심볼의 기존 provider·kind 불변
- `tests/dags/test_quote_intraday.py`: DAG 일정 충돌 없음
- `technical/select_history.sql`, `technical/select_symbols.sql`이 새 행을 별도 수정 없이 읽는지

## 10. 구현 순서

1. `KOSPI200`을 기존 국내지수 일봉 순회에 포함한다. **게이트 밖이라 먼저 한다** — 한 줄이고
   프로브 결과를 기다릴 이유가 없다.
2. ~~프로브~~ **2026-08-27에 끝났다.** 결과는 4.4·6.3절이다.
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
