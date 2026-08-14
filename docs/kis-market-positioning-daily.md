# 개발 문서 4 — KIS 신용·증시자금·공매도·대차 일별 수집

> - 작성 기준: 2026-08-11 (2026-08-13 운영 키 실측으로 갱신)
> - 상태: 미구현 기능의 실행 계획
> - 수집 방식: 일별 REST
> - 주 시장: KRX
> - 보조 시장: NXT

## 1. 결론

다음 5개 데이터를 매일 수집한다.

1. 종목별 신용잔고 일별추이
2. 신용잔고 상위 종목
3. 국내 증시자금 종합
4. 종목별 공매도 일별추이
5. 종목별 일별 대차거래추이

이 값들은 체결 틱이나 호가가 아니라 날짜별 집계다. WebSocket을 추가하지 않고 KST 화~토
08:10에 REST로 최근 7일을 다시 조회해 발표 지연과 정정치를 흡수한다.

거래소는 물리 분리 방식으로 저장한다. **1차 배포는 KRX 다섯 테이블만 만든다.**

| 데이터 | KRX 주 테이블 | NXT 보조 테이블 |
| --- | --- | --- |
| 종목별 신용잔고 일별 | `krx_stock_credit_balance_daily` | 계약 확인 뒤 |
| 신용잔고 상위 | `krx_credit_balance_ranking_daily` | 계약 확인 뒤 |
| 증시자금 종합 | `krx_market_funds_daily` | 계약 확인 뒤 |
| 종목별 공매도 일별 | `krx_stock_short_sale_daily` | 계약 확인 뒤 |
| 종목별 대차거래 일별 | `krx_stock_securities_lending_daily` | 계약 확인 뒤 |

KRX는 필수 주 경로다. NXT는 공식 KIS 계약과 운영 키 응답으로 거래소별 값임이 확인된
API만 보조 경로로 켠다. 현재 공식 계약에는 이 5개 API의 NXT 전용 endpoint, TR ID, 거래장
selector가 없다.

**그래서 NXT 테이블을 미리 만들지 않는다.** 초판은 빈 NXT 테이블 다섯을 먼저 만들어
두려 했는데, 쓰는 코드가 없는 스키마는 읽는 사람에게 "여기 곧 데이터가 온다"는 거짓 신호를
준다. 계약이 확인될 때 마이그레이션을 한 번 더 도는 편이 빈 테이블 다섯을 이고 가는 것보다
싸다. 테이블 이름과 컬럼 구성은 위 표에 남겨 두었으니 그때 그대로 쓴다.

중요하게, 아래 API의 `J`는 **국내주식 상품 구분**이지 KRX 거래장 selector가 아니다.
`krx_` 테이블명은 사용자가 정한 프로젝트의 주 라우팅을 나타내며, 응답이 KRX 체결만으로
계산됐음을 보장하지 않는다. 엄밀한 거래장별 원천이 필요하면 KIS가 별도 계약을 제공해야 한다.

다음 두 방식은 사용하지 않는다.

- KRX 응답을 NXT 테이블로 복사
- 국내시장 전체 값을 KRX와 NXT에 각각 중복 저장

NXT 테이블이 없는 것은 누락이 아니라 `NXT 일별 계약 미확인` 상태다. 나중에 NXT가 실패하거나
비어 있어도 KRX 수집·저장 결과를 되돌리지 않는다.

## 2. 실시간성 및 권장 수집 시각

| 데이터 | 값의 성격 | 가장 이른 실용 수집 시점 | 주의점 |
| --- | --- | --- | --- |
| 종목별 신용잔고 일별 | 결제일 기준 일별 | 다음 영업일 아침 | 거래일과 결제일이 다르며 통상 결제 시차가 있음 |
| 신용잔고 상위 | API가 제시한 기준일의 순위 | 다음 영업일 아침 | 최대 30종목 스냅샷이며 과거 임의 날짜 조회가 아님 |
| 증시자금 종합 | 금융투자협회 계열 시장 자금 | 다음 영업일 이후 | 일부 항목은 다른 항목보다 하루 이상 늦을 수 있음 |
| 공매도 일별 | 영업일 단위 집계 | 장 마감 후 또는 다음 영업일 아침 | 당일 중간값을 확정치로 간주하지 않음 |
| 대차거래 일별 | 영업일 단위 집계 | 장 마감 후 또는 다음 영업일 아침 | 체결 실시간 신호가 아니라 잔고·신규·상환 일계 |

KIS는 이 5개 API의 공식 게시·확정 시각 SLA를 제시하지 않는다. 위 시각은 운영 정책이며
저장 기준일은 항상 요청일이 아니라 응답의 거래일·결제일·기준일·영업일 필드다.

장중 여러 번 호출해도 실시간 판단력이 생기지 않는다. 오히려 미완성 당일 행을 확정값처럼
보일 위험이 있으므로 정기 수집은 다음처럼 한 번만 실행한다.

```text
schedule = 10 8 * * 2-6  # KST 화~토 08:10
lookback_days = 7
```

정정치나 늦게 발표된 값은 자연키 upsert로 갱신한다. 조회 구간 계산은 기존
`modules.period.resolve_observation_period()`를 재사용한다.

**휴장일 판정은 `market_session`이 이미 갖고 있다.** 초판을 쓸 때는 없던 테이블이다.
`kis_quote_intraday`가 쓰는 `modules.market_session.krx_open_day()`를 그대로 불러 확정
휴장일이면 태스크를 skip한다. 화~토 스케줄이 주말은 이미 거르므로 실효는 평일 공휴일이다.

`lookback_days`는 **신용잔고 일별·공매도·대차** 세 API에만 적용한다.

- 신용잔고 상위는 과거 기준일 입력이 없다. run마다 최신 스냅샷을 한 번만 조회한다.
- **증시자금은 한 번 호출에 100영업일이 온다**(§3.3 실측). 하루 한 번 부르면 5개월치를
  매번 덮으므로 되돌아볼 일수를 따로 줄 이유가 없다.

## 3. 공식 API 계약

공식 구현과 fixture는 [한국투자증권 Open Trading API 저장소](https://github.com/koreainvestment/open-trading-api)의
각 API 예제를 기준으로 한다. 포털과 예제가 다르면 배포 시점 포털 계약과 운영 키 응답을 우선하고,
차이는 테스트에 명시한다.

### 3.1 종목별 신용잔고 일별추이

공식 예제: [국내주식 신용잔고 일별추이](https://github.com/koreainvestment/open-trading-api/blob/main/examples_llm/domestic_stock/daily_credit_balance/daily_credit_balance.py)

| 항목 | 값 |
| --- | --- |
| Method | `GET` |
| Path | `/uapi/domestic-stock/v1/quotations/daily-credit-balance` |
| TR ID | `FHPST04760000` |
| 상품 구분 | `FID_COND_MRKT_DIV_CODE=J` — 국내주식, KRX selector 아님 |
| 화면 구분 | `FID_COND_SCR_DIV_CODE=20476` |
| 종목 | `FID_INPUT_ISCD=005930` 또는 `000660` |
| 날짜 | `FID_INPUT_DATE_1=YYYYMMDD` — 결제일 기준 |
| 한 번의 응답 | 최대 30건 |

저장 대상 KIS 필드:

| 구분 | 필드 |
| --- | --- |
| 날짜 | `deal_date`, `stlm_date` |
| 가격·거래량 문맥 | `stck_prpr`, `acml_vol` |
| 융자 신규·상환·잔고 수량 | `whol_loan_new_stcn`, `whol_loan_rdmp_stcn`, `whol_loan_rmnd_stcn` |
| 융자 신규·상환·잔고 금액 | `whol_loan_new_amt`, `whol_loan_rdmp_amt`, `whol_loan_rmnd_amt` |
| 융자 비율 | `whol_loan_rmnd_rate`, `whol_loan_gvrt` |
| 신용대주 신규·상환·잔고 수량 | `whol_stln_new_stcn`, `whol_stln_rdmp_stcn`, `whol_stln_rmnd_stcn` |
| 신용대주 신규·상환·잔고 금액 | `whol_stln_new_amt`, `whol_stln_rdmp_amt`, `whol_stln_rmnd_amt` |
| 신용대주 비율 | `whol_stln_rmnd_rate`, `whol_stln_gvrt` |

`deal_date`를 관측일, `stlm_date`를 결제일로 각각 보존한다. 결제일 하나만 저장하면 사용자가
보고 싶은 실제 거래일 추이를 잘못 해석할 수 있다.

**실측 (2026-08-13, 삼성전자 `FID_INPUT_DATE_1=20260812`)**

- `output` 30건. 문서 표대로다.
- `deal_date` 20260810~20260629, `stlm_date` 20260812~20260701. **결제 시차가 2영업일**이다.
  요청 날짜는 `stlm_date`의 최신값이 된다. 14일 padding 규칙이 이 시차를 덮는다.
- 문서 표에 없는 필드가 더 온다: `stck_oprc`, `stck_hgpr`, `stck_lwpr`, `prdy_vrss`,
  `prdy_ctrt`, `prdy_vrss_sign`. 시·고·저가는 이미 `quote_bar`가 갖는 성격의 값이라
  이 테이블에 넣지 않는다.

공통 `observation_start..observation_end`는 저장할 **거래일 범위**다. 이 API만 입력이
결제일이므로 요청 끝은 `observation_end + 14일`과 실제 run 날짜 중 이른 날까지 넓히고,
반환된 행은 `deal_date`가 원래 거래일 범위 안인 것만 저장한다. 14일은 결제 시차와 연휴를
거래소 캘린더 없이 흡수하는 운영 padding이며 공식 결제 SLA를 뜻하지 않는다.

포털 계약이 다음 조회를 보장하지 않으므로 `tr_cont` 헤더에 의존하지 않는다. 30건보다 긴
백필은 받은 최저 `stlm_date`의 전날을 다음 `FID_INPUT_DATE_1`로 사용해 뒤로 이동하고,
거래일 시작일보다 앞선 행까지 확보하면 멈춘다. 같은 최저 결제일이 반복되면 무한 반복 대신
실패시킨다.

### 3.2 신용잔고 상위

공식 예제: [국내주식 신용잔고 상위](https://github.com/koreainvestment/open-trading-api/blob/main/examples_llm/domestic_stock/credit_balance/credit_balance.py)

| 항목 | 값 |
| --- | --- |
| Method | `GET` |
| Path | `/uapi/domestic-stock/v1/ranking/credit-balance` |
| TR ID | `FHKST17010000` |
| 화면 구분 | `FID_COND_SCR_DIV_CODE=11701` |
| 상품 구분 | `FID_COND_MRKT_DIV_CODE=J` — 국내주식, KRX selector 아님 |
| 대상 | `FID_INPUT_ISCD=0000` — 전체 |
| 비교 기간 | `FID_OPTION=5` — 5일 고정 |
| 정렬 | `FID_RANK_SORT_CLS_CODE=2` — 융자잔고금액 상위 |
| 한 번의 응답 | **100건**, 다음 조회 없음 (실측) |

1차는 사용 목적에 맞게 `융자잔고금액 상위` 한 종류만 저장한다. 잔고율, 증가율, 신용대주
순위를 모두 미리 수집하지 않는다. 다른 순위가 실제 화면에 필요해질 때 정렬 코드와 자연키를
확장한다.

**실측 (2026-08-13)**

응답이 두 부분이다. 초판 문서에는 이 구조가 없었다.

```text
output1  1건   bstp_cls_code=1001, hts_kor_isnm='종합',
               stnd_date1=20260806, stnd_date2=20260812
output2  100건 1위 삼성전자(융자잔고 506,846,396) … 100위 대우건설(4,028,182)
```

- **30건이 아니라 100건이다.** 자연키의 `rank`와 정정 규칙이 100까지 간다.
- **`stnd_date1`이 과거이고 `stnd_date2`가 최신이다.** 초판은 date1을 기준일, date2를
  비교일이라 적었는데 반대다. `stnd_date2`가 기준일이고 `stnd_date1`이 5영업일 전 비교일이다.
  그대로 구현하면 스냅샷이 5일 전 날짜로 쌓인다.
- 독립 순번 필드가 **없다**(확인). 배열 순서로 `rank=1..응답건수`를 만든다. 같은 응답에
  종목 중복이 없는 것도 확인했다.
- **종목코드가 숫자만은 아니다.** 실측 응답에 `0126Z0`이 있었다. 신주인수권증서·ETN 같은
  종목은 영문자가 섞인 단축코드를 쓴다. 여섯 자리 숫자로 검증하면 그 응답 전체가 실패한다.
  여섯 자리 **영숫자**로 본다.
- `output1.bstp_cls_code`가 `1001`로 오는데 이 조회는 전체(`FID_INPUT_ISCD=0000`)이고 1위가
  코스피 종목이다. 코스닥 지수 코드와 값이 같을 뿐 시장 구분이 아니므로 저장하지 않는다.
- 초판이 "5일 변화량"이라 부른 값의 실제 필드는 `nday_vrss_loan_rmnd_inrt`,
  `nday_vrss_stln_rmnd_inrt`이고 **변화량이 아니라 증가율**이다. 컬럼 이름과 주석을 그렇게 쓴다.

저장 대상은 기준일 `stnd_date2`, 비교일 `stnd_date1`, 종목코드·종목명, 현재가·거래량,
융자와 신용대주의 잔고 수량·금액·비율, 그리고 위 두 증가율이다.

이 API에는 과거 기준일 입력값이 없다. 따라서 배포 전 과거 순위를 임의로 백필할 수 없고,
운영 시작일부터 매일 스냅샷을 쌓는다. 기준일(`stnd_date2`)이 전날과 같으면 같은 순위 슬롯을
갱신할 뿐 새 날짜로 만들지 않는다.

### 3.3 국내 증시자금 종합

공식 예제: [국내 증시자금 종합](https://github.com/koreainvestment/open-trading-api/blob/main/examples_llm/domestic_stock/mktfunds/mktfunds.py)

| 항목 | 값 |
| --- | --- |
| Method | `GET` |
| Path | `/uapi/domestic-stock/v1/quotations/mktfunds` |
| TR ID | `FHKST649100C0` |
| 날짜 | `FID_INPUT_DATE_1=YYYYMMDD` — **종료일**이며 그 전날부터 과거로 준다 |
| 한 번의 응답 | **100영업일** (실측) |

저장 대상 KIS 필드:

| 의미 | 필드 |
| --- | --- |
| 영업일 | `bsop_date` |
| 시장지수·전일대비·등락률 | `bstp_nmix_prpr`, `bstp_nmix_prdy_vrss`, `prdy_vrss_sign`, `prdy_ctrt` |
| 시가총액 | `hts_avls` |
| 고객예탁금·전일대비 | `cust_dpmn_amt`, `cust_dpmn_amt_prdy_vrss` |
| 금액회전율·미수금 | `amt_tnrt`, `uncl_amt` |
| 신용융자잔고 | `crdt_loan_rmnd` |
| 선물 관련 자금 | `futs_tfam_amt` |
| 주식형·혼합형·채권형·MMF | `sttp_amt`, `mxtp_amt`, `bntp_amt`, `mmf_amt` |
| 대차 금액 | `secu_lend_amt` |

**실측 (2026-08-13)**

```text
FID_INPUT_DATE_1=20260812 → bsop_date 20260811 ~ 20260318 (100건)
FID_INPUT_DATE_1=20250101 → bsop_date 20241230 ~ 20240731 (100건)
```

- **날짜 하나가 아니라 100영업일이 온다.** 요청일은 포함하지 않고 그 전날부터 과거로 채운다.
  그래서 하루 한 번 호출이 5개월치를 매번 덮는다. `lookback_days`를 이 API에 적용하지 않는다.
- 과거를 더 받으려면 받은 최소 `bsop_date`를 다음 요청의 `FID_INPUT_DATE_1`로 준다.
- **`prdy_ctrt` 값이 등락률이 아니다.** 실측에서 지수 6345.53, 전일대비 45.87인데
  `prdy_ctrt=100.73`이었다. 실제 등락률은 0.73%다. 100을 더한 형태로 보이지만 확정하지
  못했으므로 **이 필드를 등락률로 저장하지 않는다.** 등락률이 필요하면
  `bstp_nmix_prdy_vrss / (bstp_nmix_prpr - bstp_nmix_prdy_vrss)`로 조회에서 계산한다.
  의미가 확인되면 그때 컬럼을 추가한다.

KIS 설명상 이 데이터는 금융투자협회 자료를 사용하므로 제공 지연이나 항목별 기준일 차이가
생길 수 있다. 각 행은 반드시 응답의 `bsop_date`로 저장하고 요청일이나 수집일을 대신 넣지 않는다.
단위는 한 가지가 아니다. 포털 기준 `hts_avls`는 백만원이고 주요 예탁금·미수금·신용융자·
펀드·MMF·대차 금액은 억원이다. 모든 금액을 일괄 억원으로 해석하지 않고 컬럼별 원단위를
DB 주석과 fixture에 고정한다.

이 API에는 거래소 구분 파라미터가 없다. 응답은 주 경로인 `krx_market_funds_daily`에만
저장한다. 여기서 `krx_`는 수집 운영의 주 경로를 뜻하며, 모든 금액이 KRX 체결만으로
만들어졌다는 의미는 아니다.

### 3.4 종목별 공매도 일별추이

공식 예제: [국내주식 공매도 일별추이](https://github.com/koreainvestment/open-trading-api/blob/main/examples_llm/domestic_stock/daily_short_sale/daily_short_sale.py)

| 항목 | 값 |
| --- | --- |
| Method | `GET` |
| Path | `/uapi/domestic-stock/v1/quotations/daily-short-sale` |
| TR ID | `FHPST04830000` |
| 상품 구분 | `FID_COND_MRKT_DIV_CODE=J` — 국내주식, KRX selector 아님 |
| 종목 | `FID_INPUT_ISCD=005930` 또는 `000660` |
| 날짜 | 시작일·종료일 `YYYYMMDD` |

저장 대상은 `output2`의 `stck_bsop_date`, 종가·거래량, 공매도 체결수량·거래량 비중,
누적 공매도 수량·비중, 공매도 거래대금·전체 거래대금·대금 비중, 공매도 평균가다.
`output1`의 현재 시세는 응답 검증과 `source_record.metadata`에만 쓰고 일별 테이블에 중복
저장하지 않는다.

**실측 (2026-08-13, 삼성전자)**

- 7/1~8/12 요청에 30건, 5/1~8/12 요청에 **69건**이 왔다. **30건 상한이 아니다.** 앞의 30은
  그 구간의 영업일 수였을 뿐이다. 실제 상한은 최소 69이고 아직 모른다.
- `output2` 필드가 문서 표보다 많다. `stck_oprc`, `stck_hgpr`, `stck_lwpr`가 더 온다.
  시·고·저가는 이 테이블에 넣지 않는다.

**당일 행은 0으로 온다**(실측 2026-08-13 장중: 당일 공매도 수량 0, 비중 0, 누적은 전날 값
그대로). 값이 없다는 뜻이지 그날 공매도가 없었다는 뜻이 아니다. 저장은 그대로 하고 다음
영업일 재조회가 같은 자연키를 덮는다. **화면에서는 마지막 행이 0일 때 "미발표"로 표시하고
0으로 그리지 않는다.**

정기 실행은 다음 영업일 아침에 최근 7일을 다시 받아 영업일별 행을 upsert한다. 대량 백필은 상한을 모르므로 60영업일 구간으로
쪼개 호출하고, 받은 행 수가 요청 구간의 영업일 수보다 적으면 잘린 것으로 보고 최소 날짜부터
이어 받는다.

### 3.5 종목별 일별 대차거래추이

공식 예제: [종목별 일별 대차거래추이](https://github.com/koreainvestment/open-trading-api/blob/main/examples_llm/domestic_stock/daily_loan_trans/daily_loan_trans.py)

| 항목 | 값 |
| --- | --- |
| Method | `GET` |
| Path | `/uapi/domestic-stock/v1/quotations/daily-loan-trans` |
| TR ID | `HHPST074500C0` |
| 조회 분류 | `MRKT_DIV_CLS_CODE=3` — **종목 조회** (실측 확정) |
| 종목 | `MKSC_SHRN_ISCD=005930` 또는 `000660` |
| 날짜 | `START_DATE`, `END_DATE` (`YYYYMMDD`) |
| 연속값 | 첫 호출 `CTS=` |
| 한 번의 응답 | 최소 69건 확인, 상한 미확인 |

저장 대상 KIS 필드:

| 의미 | 필드 |
| --- | --- |
| 영업일 | `bsop_date` |
| 종가·전일대비·거래량 | `stck_prpr`, `prdy_vrss`, `acml_vol` |
| 대차 신규·상환 | `new_stcn`, `rdmp_stcn` |
| 전일대비 잔고 | `prdy_rmnd_vrss` |
| 대차 잔고 수량·금액 | `rmnd_stcn`, `rmnd_amt` |

이 API의 `MRKT_DIV_CLS_CODE`는 조회 분류이지 KRX/NXT 거래장 구분이 아니다.

**실측으로 `3`과 `1`을 갈랐다 (2026-08-13, 같은 요청에 코드만 바꿈).**

```text
MRKT_DIV_CLS_CODE=3  stck_prpr 255,500    acml_vol 27,102,479   → 삼성전자
MRKT_DIV_CLS_CODE=1  stck_prpr 6,579.04   acml_vol 360,651,200  → 코스피 지수·시장 전체
```

**`3`이 종목이고 `1`은 시장 전체다.** 공식 예제가 `1`을 쓴 것은 시장 대차를 조회한 것이지
삼성전자를 조회한 것이 아니다. `1`로 구현했다면 종목 코드를 보냈는데도 코스피 전체 대차
숫자가 삼성전자 행에 조용히 들어갔을 것이다. **종목별 대차거래는 반드시 `3`이다.**

덧붙여 `1`이 시장 전체 대차 잔고를 준다는 사실은 이번 범위 밖이지만 기록해 둔다. 시장
대차 지표가 필요해지면 같은 endpoint에 코드만 바꿔 얻을 수 있다.

응답은 5/1~8/12 요청에 69건이었고 헤더 `tr_cont='E'`였다. 백필은 60영업일 구간으로 잘라
호출하고 `CTS`는 비운다. 받은 행 수가 구간의 영업일 수보다 적으면 최소 날짜부터 이어 받는다.

## 3.6 시장 단위 값 — 어디까지 갈리는가

같은 다섯 API에서 종목이 아닌 **시장 전체** 값도 나온다. 어디까지 코스피·코스닥으로 갈 수
있는지 운영 키로 확인했다(2026-08-13).

| 값 | 시장 분리 | 방법 |
| --- | --- | --- |
| 대차 잔고 | **된다** | `MRKT_DIV_CLS_CODE` `1`=코스피, `2`=코스닥 |
| 신용잔고 상위 | **된다** | `FID_INPUT_ISCD` `0000`=전체, `1001`=코스닥 |
| 신용융자 잔고(금액) | **안 된다** | 증시자금 응답에 한 줄뿐이고 파라미터가 먹지 않는다 |
| 고객예탁금·펀드·미수금 | **안 된다** | 같은 이유 |

**대차 조회 분류 실측**

```text
code=1  종가 6,579.04   거래량 360,651,200   잔고 1,619,264,288  → 코스피
code=2  종가   858.91   거래량 561,958,300   잔고 1,444,553,429  → 코스닥
code=3  종가 255,500    거래량  27,102,479   잔고    89,405,665  → 삼성전자
code=5  종가  53,992    거래량           0   잔고 3,063,817,717  → 코스피+코스닥 합
code=0, 4  빈 응답
```

**`5`는 저장하지 않는다.** 5영업일 내내 `1`과 `2`의 정확한 합이었다. 유도되는 값을 한 벌 더
두면 둘이 어긋날 때 어느 쪽이 맞는지 알 수 없다. 필요하면 조회에서 더한다.

시장 대차는 종목 대차와 같은 응답 모양이지만 **`stck_prpr`이 주가가 아니라 지수**다. 그래서
`krx_market_securities_lending_daily`는 그 칸을 `index_close`로 부른다.

**증시자금에 시장 구분을 넣어 봤지만 먹지 않았다.** `FID_COND_MRKT_DIV_CODE=J`,
`FID_INPUT_ISCD=1001`을 각각 넣어도 지수·예탁금·신용융자가 모두 같은 값이었다. 그러므로
시장 신용융자 잔고는 코스피/코스닥으로 나눌 수 없고, 한 줄로만 저장한다.

**신용잔고 상위의 모집단**은 `0000`(전체, 1위 삼성전자)과 `1001`(코스닥, 1위 알테오젠)이다.
`0001`은 전체와 같은 응답을 준다. 응답 헤더 `bstp_cls_code`는 요청값과 다른 체계라
(`0000` → `1001/종합`, `1001` → `2001/KOSDAQ`) 읽지 않는다. 저장하는 `universe_code`는
우리가 보낸 값이다. 자연키에 이미 들어 있어 두 모집단이 한 테이블에 공존한다.

## 4. KRX/NXT 라우팅 규칙

### 4.1 배포 초기 상태

| API | KRX | NXT 초기 상태 | 이유 |
| --- | --- | --- | --- |
| 신용잔고 일별 | 활성 | 비활성 | `J`는 국내주식 구분이고 NXT selector 없음 |
| 신용잔고 상위 | 활성 | 비활성 | `J`는 국내주식 구분이고 NXT selector 없음 |
| 증시자금 종합 | 활성 | 비활성 | 거래소 구분 파라미터 없음 |
| 공매도 일별 | 활성 | 비활성 | `J`는 국내주식 구분이고 NXT selector 없음 |
| 대차거래 일별 | 활성 | 비활성 | 조회 분류만 있고 거래장 구분 없음 |

NXT를 켜는 조건은 다음 세 가지를 모두 만족하는 경우뿐이다.

1. 공식 포털 또는 공식 예제에 NXT endpoint, TR ID 또는 selector가 명시되어 있음
2. 운영 키 호출이 성공하고 응답이 KRX와 구별되는 의미를 가짐
3. 날짜·단위·빈 응답을 포함한 fixture 테스트가 추가됨

문서에 없는 `FID_COND_MRKT_DIV_CODE=NX`를 임의로 보내 정기 수집 경로를 만들지 않는다.
향후 공식 selector가 생겨 HTTP 200을 반환하더라도 같은 날짜의 주 경로 응답과 비교하고,
응답이 어느 거래장을 뜻하는지 확인해야 한다.

### 4.2 저장 라우팅

수집 요청마다 내부 라우팅 값은 `KRX` 또는 `NXT` 하나다. 저장 함수는 이 값으로 허용된 SQL
상수 중 하나를 고르고 외부 입력으로 테이블 이름을 조합하지 않는다.

```text
KRX response -> krx_*_daily only
NXT response -> nxt_*_daily only
unsupported NXT -> no request, no row
```

각 물리 테이블에는 `venue` 컬럼을 두지 않는다. 테이블 이름이 거래장을 결정하고, 종목
식별자는 두 거래장에서 같은 값을 쓴다(§5.1).

## 5. 데이터 모델

모델 정의의 원본은 기존 `apps/models/market.py`다. 새 스키마나 범용 지표 테이블을 만들지
않는다. 다섯 데이터는 날짜·자연키·단위가 달라 `indicator_observation` 한 행에 억지로 넣지 않는다.

### 5.1 공통 컬럼

모든 테이블은 다음 컬럼을 가진다.

| 컬럼 | 타입 | 설명 |
| --- | --- | --- |
| `provider` | text | 항상 `kis` |
| `source_record_id` | bigint FK | `source_record.id`, `ON DELETE RESTRICT` |
| `created_at`, `updated_at` | timestamptz | `EntityBase` 공통 컬럼 |

**금액 단위는 API마다 다르고 일부는 아직 모른다.** 실측으로 잰 것만 적는다.

| 컬럼 | 단위 | 근거 |
| --- | --- | --- |
| 대차 `rmnd_amt` | **백만원** | 잔고수량 × 종가의 정확히 1/1,000,000 |
| 공매도 `ssts_tr_pbmn` | **원** | 공매도수량 × 종가와 거의 같다(평균가 차이만) |
| 신용잔고 `whol_*_amt` | **미확정** | 수량 × 종가와 배수가 10의 거듭제곱이 아니다 |
| 증시자금 금액들 | **미확정** | 포털 표기는 시가총액 백만원·나머지 억원이나 대조하지 못했다 |

미확정인 값은 **화면에 단위를 붙이지 않는다.** 대신 수량과 비율을 앞세운다. 그 둘은 뜻이
분명하다. 단위를 확정하면 그때 화면에 붙인다.

종목 테이블에는 **`stock_code`**(6자리, `005930`·`000660`)를 추가한다.

초판은 `SAMSUNG_ELECTRONICS` 같은 내부 이름을 쓰려 했는데 **저장소에 이미 같은 회사를
가리키는 식별자가 있다.** `disclosure_event.stock_code`와 `earnings_fact.stock_code`가
6자리 코드를 쓰고 `reference.instrument.ticker`도 같다. 내부 이름을 새로 만들면 "삼성전자의
공시"와 "삼성전자의 신용잔고"를 잇는 조회가 대응표를 들고 다녀야 한다. 표시 이름은 조회에서
붙인다.

`quote_bar.symbol`의 `KOSPI200_FUT` 같은 값과는 다른 층이다. 저쪽은 티커가 없는 지수·선물의
시계열 이름이고 이쪽은 상장 종목이다. 상장 종목에는 이미 공식 코드가 있으니 그것을 쓴다.

금액·비율은 KIS 원단위를 그대로 `numeric`으로 저장한다. 수집기에서 억원이나 퍼센트 소수로
바꾸지 않고, 각 컬럼의 DB 주석에 공식 단위를 적는다. 수량은 `bigint`, 날짜는 `date`를 쓴다.

### 5.2 종목별 신용잔고 일별

대상 클래스(NXT는 계약 확인 뒤 같은 모양으로 추가):

- `KrxStockCreditBalanceDaily`

주요 컬럼:

- `stock_code`, `trade_date`, `settlement_date`
- `close_price`, `accumulated_volume`
- 융자 신규·상환·잔고 수량과 금액, 잔고율·지급률
- 신용대주 신규·상환·잔고 수량과 금액, 잔고율·지급률

자연키:

```text
(provider, stock_code, trade_date)
```

### 5.3 신용잔고 상위

대상 클래스(NXT는 계약 확인 뒤 같은 모양으로 추가):

- `KrxCreditBalanceRankingDaily`

주요 컬럼:

- `standard_date`(`stnd_date2`), `comparison_date`(`stnd_date1`)
- `universe_code`, `sort_code`, `period_days`
- `rank`, `stock_code`, `stock_name`
- 가격·거래량 문맥
- 융자와 신용대주의 잔고 수량·금액·비율, 그리고 기간 대비 **증가율**(`nday_vrss_*_inrt`)

자연키:

```text
(provider, standard_date, universe_code, sort_code, period_days, rank)
```

파서는 `rank=1..응답건수`가 연속이고 종목코드가 중복되지 않는지 검증한다(실측 100건, 중복
없음). 같은 스냅샷을 다시 받으면 순위 슬롯별로 upsert하고, 이전 응답보다 짧아졌다면 받은
마지막 순위보다 큰 기존 슬롯을 같은 트랜잭션에서 삭제한다. 이렇게 해야 순위 구성 종목이
정정되어도 탈락 종목이 유령 행으로 남지 않는다. **응답 건수를 상수로 박지 않는다.** 실측이
100이었을 뿐 제공처가 바꿀 수 있다.

### 5.4 증시자금 종합

대상 클래스(NXT는 계약 확인 뒤 같은 모양으로 추가):

- `KrxMarketFundsDaily`

주요 컬럼은 3.3의 저장 대상 전체다.

자연키:

```text
(provider, business_date)
```

### 5.5 종목별 공매도 일별

대상 클래스(NXT는 계약 확인 뒤 같은 모양으로 추가):

- `KrxStockShortSaleDaily`

주요 컬럼:

- `stock_code`, `business_date`, `close_price`, `accumulated_volume`
- 공매도 일별·누적 수량과 거래량 비중
- 공매도 일별·누적 대금과 대금 비중
- `short_sale_average_price`

자연키:

```text
(provider, stock_code, business_date)
```

### 5.6-1 시장 대차거래 일별

대상 클래스: `KrxMarketSecuritiesLendingDaily`

주요 컬럼은 종목 대차와 같되 `close_price` 자리가 `index_close`(지수)다.

자연키:

```text
(provider, market_code, business_date)
```

`market_code`는 `KrxMarket`(`KOSPI`, `KOSDAQ`)이고 `market_movement_snapshot.symbol`과 같은
값 집합이다. 시장 단위 값이 둘 이상 생겨서 Enum을 하나로 합쳤다.

### 5.6 종목별 대차거래 일별

대상 클래스(NXT는 계약 확인 뒤 같은 모양으로 추가):

- `KrxStockSecuritiesLendingDaily`

주요 컬럼:

- `stock_code`, `business_date`, `close_price`, `price_change`, `accumulated_volume`
- `new_quantity`, `repayment_quantity`, `balance_change_quantity`
- `balance_quantity`, `balance_amount`

자연키:

```text
(provider, stock_code, business_date)
```

## 6. 수집기와 DAG

### 6.1 REST 수집기

기존 `airflow/modules/collectors/kis.py`를 확장한다. 토큰 캐시, **`send_get()`**, KIS 오류 타입,
Pydantic 검증, `source_record` 저장 패턴을 그대로 재사용한다. 이 기능만을 위한 HTTP 클라이언트,
repository base class, 새 의존성은 만들지 않는다.

추가할 최소 공개 함수:

- `fetch_daily_credit_balance()`
- `fetch_credit_balance_ranking()`
- `fetch_market_funds()`
- `fetch_daily_short_sale()`
- `fetch_daily_loan_trans()`
- 각 응답 parser와 해당 테이블 upsert 함수

`send_get()`은 초판이 적은 `_get()`의 새 이름이고 `(본문, 상태, 헤더)` 세 값을 돌려준다.
헤더는 연속조회에만 필요하다.

외부 입력과 응답은 기존 규칙대로 `ConfigDict(frozen=True)` Pydantic 모델로 검증한다.

파서 공통 규칙:

- `rt_cd != "0"`은 `KisResultError`
- 공백·쉼표가 있는 숫자는 정규화하되 숫자가 아닌 값은 실패
- 날짜는 정확히 `YYYYMMDD`만 허용
- 종목별 API는 응답에 종목코드가 있으면 요청 종목과 일치하는지 검증
- **대차거래는 `MRKT_DIV_CLS_CODE=3`을 쓴다.** `1`은 시장 전체라 종목 행에 시장 숫자가 들어간다
- 순위 API는 여러 종목이 정상이며 각 종목코드 형식과 중복만 검증한다. 형식은 여섯 자리
  영숫자다. 숫자만으로 좁히면 `0126Z0` 같은 코드에서 응답 전체가 실패한다(실측)
- 요청 종료일보다 미래인 행은 실패; 시작일보다 과거인 정상 창 행은 저장 대상에서 제외
- 순매수·증감 수량과 금액의 음수는 정상값으로 허용
- 빈 배열 자체는 `source_record`에 0건으로 남기고 아래 endpoint별 완전성 규칙으로 판정
- 같은 자연키는 `ON CONFLICT ... DO UPDATE`

빈 응답 완전성 규칙:

- 신용잔고 상위는 최신 완전 스냅샷 API이므로 빈 배열이면 즉시 실패
- 나머지 4개는 휴장일·미발표일의 단일 호출 0건을 허용
- 기본 7일 run에서 데이터셋 전체가 0건이면 휴장으로 보기 어렵기 때문에 task 실패
- 수동 1일 조회는 휴장일일 수 있으므로 0건 성공을 허용

원문 JSON은 매일 중복 저장하지 않는다. 기존 KIS 봉 수집처럼 `source_record.payload`는 `NULL`로
두고 endpoint, TR ID, 프로젝트 라우팅 라벨, 조회 구간, HTTP 상태, 응답 건수와 오류를
`metadata`에 남긴다. API 호출 시도 하나마다 `source_record` 하나를 만들며, 0건·HTTP 실패·
본문 실패도 정규화 행 없이 계보만 남긴다.

### 6.2 일별 DAG

새 파일 `airflow/dags/kis_market_positioning_daily.py` 하나에 일별 DAG를 둔다.

```text
dag_id = kis_market_positioning_daily
schedule = KST 화~토 08:10
lookback_days = 7
retries = 2
retry_delay = 1시간
max_active_runs = 1
```

대상 종목은 DART 수집기가 이미 쓰는 것과 같다. `modules.collectors.dart.DartCompany`가
종목코드·회사명을 들고 있으므로 그 값을 재사용하고 새 목록을 만들지 않는다.

| `stock_code` | 회사 |
| --- | --- |
| `005930` | 삼성전자 |
| `000660` | SK하이닉스 |

확정 휴장일에는 `modules.market_session.krx_open_day()`로 태스크를 skip한다. 캘린더 행이
없거나 아직 판정하지 않았으면 수집을 계속한다.

한 run은 KRX 5종을 모두 시도하고 성공한 응답은 즉시 upsert한다. 일부 API가 실패하면 다른
성공값은 유지한 채 task를 실패시켜 재시도한다. 재시도는 자연키 upsert이므로 중복 행을 만들지
않는다. 이를 위해 호출 하나의 `source_record`와 정규화 행만 한 트랜잭션으로 커밋하고 다음
호출로 넘어간다. 전체 루프가 끝난 뒤 실패가 하나라도 있으면 집계 예외를 올린다. 모든 KRX
API가 성공해야 KRX task가 성공이다.

초기 버전에는 NXT task도 NXT 테이블도 만들지 않는다. 공식 NXT 계약이 확인되면 그때
테이블과 task를 함께 추가하고 KRX 저장 뒤 실행한다. 그 task가 실패해도 이미 커밋된 KRX
행을 롤백하거나 삭제하지 않는다.

수동 실행 파라미터는 기존 일별 DAG와 같다.

| 이름 | 기본값 | 뜻 |
| --- | --- | --- |
| `observation_start` | `null` | 직접 지정한 조회 시작일 |
| `observation_end` | `null` | 직접 지정한 조회 종료일 |
| `lookback_days` | `7` | 날짜를 직접 주지 않았을 때 재조회할 일수 |

## 7. 변경 파일

### 작업 1 — 운영 키 응답 프로브

- ~~삼성전자 기준으로 5개 API를 각 1회 호출~~ — 2026-08-13 완료
- ~~응답의 키, 배열 방향, 날짜, 최대 건수 확인~~ — §3 각 절에 실측으로 기록
- ~~대차 `MRKT_DIV_CLS_CODE` 3과 1 비교~~ — 3=종목, 1=시장 전체(§3.5)
- **남은 것**: 공매도·대차의 실제 응답 상한(최소 69 확인), 증시자금 `prdy_ctrt`의 의미,
  금액 컬럼별 원단위(포털 표기와 실제 값 대조)
- 공식 문서에 NXT 계약이 추가된 API만 별도로 1회 호출해 주 경로 응답과 비교
- 앱키·토큰·원문 운영 응답은 저장소에 커밋하지 않음

### 작업 1.5 — 추적 종목 마스터 시드

- 추가: 새 Alembic revision (`instrument` 시드)
- 추가: `tests/migrations/test_instrument_catalog.py`

`reference.instrument`가 비어 있었다. 화면이 종목코드를 그대로 보여 주지 않으려면 이름의
출처가 한 곳이어야 하고, 이 테이블이 바로 그 용도다(`indicator_series`·`quote_symbol`이
지표·심볼에 하는 역할). 삼성전자·SK하이닉스 두 행을 마이그레이션이 넣는다.

관측 테이블에서 이 마스터로 외래키를 걸지 않는다. 걸면 시드를 빠뜨린 순간 수집이 죽는다.
대신 카탈로그 테스트가 수집기 Enum과 시드를 대조한다.

### 작업 2 — 모델과 migration

- 수정: `apps/models/market.py`
- 수정: `apps/models/__init__.py`
- 추가: 새 Alembic revision 1개
- 수정: `tests/models/test_market_models.py`
- 추가: `tests/migrations/test_kis_market_positioning_schema.py`

검증:

- KRX 5개 물리 테이블이 생성됨. **NXT 테이블은 만들지 않는다**
- 모든 테이블에 독립된 `source_record_id` FK와 인덱스가 있음
- 종목 테이블의 식별자가 `stock_code`이고 DART 테이블과 같은 6자리 값을 쓴다

### 작업 3 — SQL과 수집기

- 추가: `airflow/sql/postgres/krx_<데이터셋>_daily/upsert.sql` — KRX 5개
- 수정: `airflow/modules/collectors/kis.py`
- 수정: `tests/collectors/test_kis.py`

SQL 문자열은 Python에 넣지 않는다. 각 upsert의 컬럼과 `ON CONFLICT` 키를 SQLAlchemy 모델
metadata와 대조한다. NXT upsert와 NXT 테이블은 공식 원천을 켤 때 함께 만든다.

### 작업 3.5 — 대시보드

- 추가: `compose/local/grafana/dashboards/market-positioning.json` (uid `market-positioning`)
- 추가: `tests/dashboards/test_market_positioning_dashboard.py`

패널은 일곱이다. 종목별 융자 잔고·공매도 비중(stat 둘), 신용융자 잔고 추이, 대차 잔고 추이,
공매도 비중 추이, 시장 자금, 신용잔고 상위 표다.

- **수량과 비율을 앞세운다.** 신용잔고 금액과 증시자금 금액은 단위가 미확정이라 화면에
  단위를 붙이지 않는다. 축 라벨이 `단위 미확정`이다.
- **미발표 행을 0으로 그리지 않는다.** 조건은 "0이면 숨김"이 아니라 "마지막 영업일이면서
  0이면 숨김"이다. 0을 통째로 거르면 공매도 금지 같은 제도 변화가 화면에서 사라진다.
- 신용잔고는 `trade_date`로 그린다. 결제일로 그리면 추이가 2영업일씩 밀린다.
- 순위 표는 최신 기준일 한 벌만 본다.
- **화면에는 이름이 나오고 조회에는 종목코드가 나간다.** 저장은 `005930` 그대로 두고 이름은
  화면 층에서만 붙인다. 이름의 출처는 `reference.instrument` 마스터 하나이고, 대시보드마다
  코드→이름 대응을 복사하지 않는다. 종목 드롭다운은 Grafana 의 `__text`/`__value` 두 칼럼을
  쓴다.
- 순위 표의 종목명만 응답이 준 값을 그대로 쓴다. 마스터에 없는 종목(신주인수권증서 등)이
  섞이기 때문이다.

### 작업 4 — 일별 DAG

- 추가: `airflow/dags/kis_market_positioning_daily.py`
- 추가: `tests/dags/test_kis_market_positioning_daily.py`

새 컨테이너, 새 서비스, 새 Python 패키지, WebSocket 프로세스는 필요 없다.

## 8. 테스트

최소 자동 테스트:

- 5개 공식 응답 fixture를 정규화된 모델로 변환
- `deal_date`와 `stlm_date`를 바꾸어 저장하지 않음
- 신용잔고 거래일 구간에 14일 결제 padding을 적용하고 저장은 원래 거래일 범위로 제한
- 신용순위 배열 순서가 `rank=1..응답건수`가 되고 건수를 상수로 박지 않음
- 신용순위 기준일은 `stnd_date2`, 비교일은 `stnd_date1`
- 같은 기준일의 신용순위가 짧아지면 마지막 순위보다 큰 슬롯이 제거됨
- 증시자금은 응답의 `bsop_date`를 사용하고 한 응답의 100행을 모두 저장
- 증시자금 `prdy_ctrt`를 등락률 컬럼에 넣지 않음
- 대차거래 요청이 `MRKT_DIV_CLS_CODE=3`이며 `1`을 쓰지 않음
- 음수 증감량과 0 잔고를 허용
- 잘못된 종목, 날짜, 숫자, `rt_cd`를 거부
- 미래 날짜 행을 거부하고 조회 시작일보다 오래된 창 행은 저장하지 않음
- 신용잔고 일별의 30건 날짜 커서가 앞으로 진행하지 않으면 실패
- 순위 종목코드가 여섯 자리 영숫자면 통과(`0126Z0`), 길이나 기호가 다르면 실패
- 신용순위 빈 응답과 기본 7일 데이터셋 전체 0건은 실패, 수동 휴장일 0건은 성공
- KRX SQL의 컬럼·자연키가 대응 SQLAlchemy 모델과 같음
- NXT 미지원 상태에서 NXT 네트워크 호출과 SQL 실행이 전혀 없음
- 확정 휴장일에는 태스크가 요청을 보내지 않음
- 최근 7일 재수집과 같은 자연키 upsert가 멱등임

실행:

```bash
uv run pytest \
  tests/collectors/test_kis.py \
  tests/models/test_market_models.py \
  tests/migrations/test_kis_market_positioning_schema.py \
  tests/dags/test_kis_market_positioning_daily.py -q
uv run ruff check apps airflow migrations tests
uv run pyrefly check
```

이 저장소에는 Django가 없다. 초판에 있던 `manage.py check`는 다른 프로젝트의 명령이었다.

## 9. 운영 조회 예시

```sql
SELECT
    'KRX' AS venue,
    stock_code,
    business_date,
    short_sale_quantity,
    short_sale_ratio
FROM krx_stock_short_sale_daily
WHERE provider = 'kis'
  AND stock_code IN ('005930', '000660')
ORDER BY business_date DESC, stock_code;
```

NXT를 켜면 같은 모양의 `nxt_*` 테이블을 literal 거래장 컬럼과 함께 `UNION ALL`로 붙인다.
화면은 NXT 부분이 비어 있을 때 `0`으로 채우지 말고 `NXT 미수집`으로 표시한다. 0은 실제
잔고나 거래량이 0이라는 뜻이므로 결측과 같지 않다.

공시와 잇는 조회는 `stock_code`로 바로 조인된다. 같은 6자리 코드를 `disclosure_event`와
`earnings_fact`가 쓰기 때문이다.

## 10. 완료 조건

1차 배포 완료:

- KRX 5종 데이터가 삼성전자·SK하이닉스 또는 시장 기준일로 저장됨
- KRX 최근 7일 재수집이 중복 없이 정정치를 반영함
- KRX 5개 물리 테이블이 존재하고 NXT 테이블은 아직 없음
- NXT 미지원 API는 호출도 테이블도 없음
- 신용순위는 운영 시작일부터 매일 응답 건수만큼 쌓임(실측 100종목)
- 각 행이 성공한 API 호출의 `source_record_id`와 연결됨
- KIS 실패가 데이터 없음으로 조용히 저장되지 않음

NXT 보조 수집 완료는 별도 조건이다.

- 공식 계약 또는 운영 프로브로 거래장별 값임을 확인
- 해당 API의 NXT fixture, `nxt_*` upsert SQL과 라우팅 테스트 추가
- NXT 테이블에만 첫 실데이터 적재
- NXT 실패가 기존 KRX 행을 변경하지 않음

## 11. 제외 범위

다음은 이번 구현에 넣지 않는다.

- WebSocket 또는 장중 폴링
- KRX 데이터를 이용한 NXT 추정치
- 신용잔고 상위의 10개 정렬 코드를 모두 수집
- 신용순위의 존재하지 않는 과거 날짜 백필
- 전 종목 공매도·대차 전체시장 백필
- 별도 상태 테이블이나 범용 수집 프레임워크

필요가 확인되면 종목 목록이나 순위 종류를 늘리면 된다. 현재 목적에는 삼성전자·SK하이닉스와
융자잔고금액 상위 30종목이면 충분하다.
