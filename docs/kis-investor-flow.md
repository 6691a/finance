# 개발 문서 2 — 종목·시장별 외국인/기관 수급

> 작성 기준: 2026-08-11 (2026-08-14 3차 운영 프로브로 갱신)  
> 상태: 미구현 기능의 실행 계획  
> 대상 종목: 삼성전자, SK하이닉스  
> 대상 시장: 코스피, 코스닥

## 1. 결론

수급은 가격 봉이 아니므로 `quote_bar`에 넣지 않는다. **종목 추정과 시장 누적을 각각 다른
테이블에 저장한다** — `stock_investor_estimate_snapshot`과 `market_investor_flow_snapshot`이다.
값의 성격도 식별자도 달라서 한 테이블에 담으면 어느 쪽과도 조인이 안 된다(§4).

REST 두 API와 KRX·NXT 회원사 WebSocket 계약을 공식 예제와 운영 프로브로 확인했다.

- 종목: 삼성전자·SK하이닉스 외국인/기관 장중 추정 순매수
- 시장: 우선 코스피 외국인/기관 장중 누적 매매동향, 코스닥은 코드 확인 후 추가
- 실시간 보조 신호(2차): 두 종목의 KRX·NXT 외국계 회원사 순매수

일별 확정치와 투자자 세부 분류는 2차다. 먼저 장중 가격과 같은 시간축에서 방향을 확인하는 데
필요한 최소 컬럼만 저장한다. WebSocket 회원사 값은 외국인 투자자 수급과 의미가 다르므로
거래소별 물리 테이블과 별도 라벨로 노출한다.

외국인·기관 추정 REST 값은 KRX/NXT별 수치가 아니라 종목·시장 단위 값이다. 따라서 이를
두 거래소 테이블에 복제하지 않는다. 거래소별로 분리되는 회원사 WebSocket 값은 공식 필드와
라이브 프레임으로 확인했으므로 `krx_foreign_member_flow_snapshot`과
`nxt_foreign_member_flow_snapshot`으로 나눈다.

**1차는 REST 둘까지다.** 회원사 WebSocket은 상주 프로세스(`kis_realtime.py`)가 있어야 도는데
그것이 아직 없다. 테이블만 먼저 만들면 쓰는 코드가 없는 스키마가 남아 "여기 곧 데이터가
온다"는 거짓 신호를 준다. 상주 서비스를 만들 때 테이블·수집·테스트를 함께 넣는다.

## 2. 왜 별도 테이블인가

`quote_bar`는 1분 OHLCV다. 수급은 다음 성질이 다르다.

- 종목 API 값은 실시간 체결 합계가 아니라 특정 시각에 갱신되는 **추정치**다.
- 시장 API 값은 장중 누적 스냅샷이다.
- 가격처럼 `open/high/low/close`가 없다.
- 동일 값이 여러 수집 시각에 반복될 수 있다.
- 실시간 회원사 값은 외국계 증권사 흐름이지 투자자 국적별 확정치가 아니다.

따라서 가격 봉의 빈 컬럼에 억지로 맞추지 않고 출처와 의미를 테이블 이름으로 드러낸다.

## 3. 확인된 API 계약과 검증 대상

### 3.1 종목별 외인·기관 추정

공식 예제: [종목별 외인기관 추정가집계](https://github.com/koreainvestment/open-trading-api/blob/main/examples_llm/domestic_stock/investor_trend_estimate/investor_trend_estimate.py)

| 항목 | 값 |
| --- | --- |
| Path | `/uapi/domestic-stock/v1/quotations/investor-trend-estimate` |
| TR ID | `HHPTJ04160200` |
| 파라미터 | `MKSC_SHRN_ISCD=005930` 또는 `000660` |

저장 필드:

| 의미 | KIS 필드 |
| --- | --- |
| API 기준 시각 코드 | `bsop_hour_gb` |
| 외국인 추정 순매수 수량 | `frgn_fake_ntby_qty` |
| 기관 추정 순매수 수량 | `orgn_fake_ntby_qty` |
| 합계 추정 순매수 수량 | `sum_fake_ntby_qty` |

**실측 (2026-08-14 10:44 KST 장중)**

```text
005930  gb=2  외국인   878,000  기관  -464,000  합   414,000
005930  gb=1  외국인 1,059,000  기관         0  합 1,059,000
000660  gb=2  외국인   323,000  기관   -73,000  합   250,000
000660  gb=1  외국인   299,000  기관         0  합   299,000
```

- 응답 배열은 **`output2`** 하나다. 행 키는 문서 표의 네 필드가 전부다.
- **한 번 호출에 여러 행이 온다.** 시각 슬롯(`bsop_hour_gb`)마다 한 행이고 최신 슬롯이
  먼저 온다. 장이 진행되면 행이 늘어난다(초판 프로브에서는 3행이었다).
- `sum = 외국인 + 기관`이 네 행 모두 일치했다.
- 값은 `000000000000878000`, `-00000000000464000`처럼 부호 뒤에 0이 채워져 온다.
- **없는 종목코드(`999999`)는 오류가 아니라 0행으로 온다.** `rt_cd=0`, 정상 메시지다.
  종목은 Enum으로 막으므로 위험이 작지만, 0행을 정상으로 다루고 계보만 남긴다.

**슬롯이 무엇인가 (2026-08-14 실측)**

`bsop_hour_gb`는 시각이 아니라 **그날 몇 번째 갱신인지를 뜻하는 회차**다.

```text
10:44 KST 조회 → 2행 (슬롯 1,2)
14:43 KST 조회 → 5행 (슬롯 1~5)

삼성전자 외국인:  슬롯1 1,059,000 → 2 878,000 → 3 1,507,000 → 4 2,789,000 → 5 4,405,000
```

- 값은 그 시점까지의 **당일 누적** 추정치다. 슬롯이 커질수록 쌓인다.
- 장이 진행되면 행이 늘어난다. 그래서 재조회가 기존 슬롯을 갱신하고 새 슬롯을 더한다.
- **슬롯 1은 기관이 0이다.** 외국인은 09:30, 기관은 10:00부터 집계한다는 공식 설명과 맞는다.
- 공식 갱신 시각과 견주면 1≈09:30, 2≈10:00, 3≈11:20, 4≈13:20, 5≈14:30이고 회차 수도
  다섯으로 일치한다.

**그래도 이 대응을 저장하지 않는다.** 공식 예제가 시각이 변동될 수 있다고 밝히고 있어, 표를
만들면 제공처가 회차를 늘리거나 시각을 바꾸는 날 조용히 틀린다. 자연키는 슬롯 코드 그대로
두고 위 대응은 관측 기록으로만 남긴다.

공식 예제 설명상 값은 장중에 수동 집계되어 대략 다음 시각에 갱신된다.

- 외국인: 09:30, 11:20, 13:20, 14:30
- 기관: 10:00, 11:20, 13:20, 14:30

시각은 변동될 수 있다. 따라서 이 값을 1분 실시간 수급으로 표시하면 안 된다.

### 3.2 시장별 투자자 매매동향

공식 예제: [시장별 투자자매매동향](https://github.com/koreainvestment/open-trading-api/blob/main/examples_llm/domestic_stock/inquire_investor_time_by_market/inquire_investor_time_by_market.py)

| 항목 | 값 |
| --- | --- |
| Path | `/uapi/domestic-stock/v1/quotations/inquire-investor-time-by-market` |
| TR ID | `FHPTJ04030000` |
| 공식 예제 | `FID_INPUT_ISCD=999`, `FID_INPUT_ISCD_2=S001` |

사용 필드:

- 외국인: `frgn_seln_vol`, `frgn_shnu_vol`, `frgn_ntby_qty`, `frgn_ntby_tr_pbmn`
- 기관: `orgn_seln_vol`, `orgn_shnu_vol`, `orgn_ntby_qty`, `orgn_ntby_tr_pbmn`
- 개인: `prsn_seln_vol`, `prsn_shnu_vol`, `prsn_ntby_qty`, `prsn_ntby_tr_pbmn`

**실측 (2026-08-14)**

응답 배열은 **`output`** 하나이고 1행이다. **필드가 72개다.** 문서가 적은 여덟 개는 그중
일부이고 나머지는 투자자 세부 분류다.

```text
frgn_*  외국인    orgn_*  기관       prsn_*  개인
scrt_*  금융투자  ivtr_*  투신       insu_*  보험
bank_*  은행      fund_*  기타금융   etc_corp_*  기타법인
pe_fund_*  사모   etc_orgt_*  기타단체  mrbn_*  회원사
```

각 분류마다 `_seln_vol`, `_shnu_vol`, `_ntby_qty`, `_seln_tr_pbmn`, `_shnu_tr_pbmn`,
`_ntby_tr_pbmn` 여섯 칸이 있다. **같은 응답에 이미 들어 있으므로 나중에 세부 분류를 넣을 때
API를 다시 부르는 것이 아니라 컬럼만 늘리면 된다.** 그래서 1차에 **개인(`prsn_*`)까지
저장한다.** 외국인·기관만 보면 "누가 팔았나"는 알아도 "누가 받았나"를 모른다. 추가 호출
비용이 0이라 미룰 이유가 없다. 나머지 분류는 필요할 때 컬럼을 더한다.

**잘못된 시장 코드가 오류가 아니라 0으로 온다.** 코스닥 코드를 찾으려 후보를 넣어 봤다.

```text
999/S001  → 외국인 순매수 -32,326  (코스피. 값이 있는 유일한 조합)
999/S002, 999/K001, 999/Q001, 999/S003, 998/S001, 1001/S001
          → 모두 rt_cd=0, 1행, 값이 전부 0
```

ECOS가 없는 항목코드에 `INFO-200`을 주던 것과 같은 함정이다. 응답만으로는 "그 시장이 오늘
0이었다"와 "코드가 틀렸다"를 가를 수 없다. 그래서 두 가지를 함께 건다.

- 시장 코드는 수집기 Enum이 정한 값만 보낸다. 문자열을 조립하지 않는다.
- **모든 값이 0인 시장 응답은 저장하지 않고 실패시킨다.** 장중에 전 투자자 분류가 정확히
  0인 일은 없다.

**코스닥 코드는 여전히 미확인이다.** 포털·HTS 근거가 있는 코드를 얻기 전에는 켜지 않는다.

### 3.2-1 수량·대금 단위 — 두 API가 다르다

시장 응답의 총매도 대금을 총매도 수량으로 나눠 봤다(2026-08-14).

| 투자자 | 수량 | 대금 | 대금/수량 |
| --- | --- | --- | --- |
| 외국인 | 2,105,067 | 6,243,852 | 2.97 |
| 기관 | 1,059,345 | 2,699,047 | 2.55 |
| 개인 | 263,424 | 895,641 | 3.40 |

**주·원 그대로가 아니다.** 그랬다면 평균단가가 3원이 된다. `천주`·`백만원`이라면 평균단가
2,966원이 되어 값이 맞고 수량 규모도 코스피답다. 다만 **확정하지 못했다.**

반면 종목 추정 API의 878,000은 주 단위로 보인다(삼성전자 외국인 87.8만주). **두 API의
배율이 다르다는 것은 확실하다.**

그래서 이렇게 다룬다.

- 저장은 KIS 표기 그대로 두고 환산하지 않는다.
- **화면에서 두 값을 같은 축에 그리지 않는다.** 종목 추정 수량과 시장 수량은 단위가 다르다.
- 단위가 확정되기 전에는 축에 `주`·`억원` 같은 라벨을 붙이지 않는다. 포지션 화면에서
  같은 이유로 이미 그렇게 했다(`docs/kis-market-positioning-daily.md` §5.1).
- HTS [0403]과 대조해 확정하면 그때 컬럼 주석과 화면 라벨을 함께 고친다.

### 3.3 실시간 외국계 회원사 WebSocket

공식 예제:

- [국내주식 실시간회원사 KRX](https://github.com/koreainvestment/open-trading-api/blob/main/examples_llm/domestic_stock/member_krx/member_krx.py)
- [국내주식 실시간회원사 NXT](https://github.com/koreainvestment/open-trading-api/blob/main/examples_llm/domestic_stock/member_nxt/member_nxt.py)

| 항목 | 값 |
| --- | --- |
| KRX TR ID | `H0STMBC0` |
| NXT TR ID | `H0NXMBC0` |
| `tr_key` | `005930`, `000660` |
| 프레임 | 평문 `0|TR_ID|건수|^ 구분 payload`, 레코드당 78필드 |

공식 컬럼 순서와 2026-08-11 운영 프레임이 일치했다. 아래 인덱스는 0부터 센다.

| 인덱스 | KIS 필드 | 의미 |
| --- | --- | --- |
| 0 | `mksc_shrn_iscd` | 종목코드 |
| 61 | `glob_total_seln_qty` | 외국계 총매도 수량 |
| 62 | `glob_total_shnu_qty` | 외국계 총매수 수량 |
| 65 | `glob_ntby_qty` | 외국계 순매수 수량 |

KRX `000660` 실측값은 매도 `244999`, 매수 `175031`, 순매수 `-69968`로
`매수 - 매도 = 순매수`가 일치했다. NXT `005930`도 세 값이 모두 0으로 같은 불변식을
만족했다. 파서는 정확히 78필드를 요구하고 이 불변식이 깨진 프레임을 저장하지 않는다.

주의: `glob_ntby_qty`는 외국인 투자자 전체 순매수의 대체값이 아니다. 외국계 회원사를 통한
국내 투자자 주문과 국내 회원사를 통한 외국인 주문이 있을 수 있다. 화면 이름은 반드시
`외국계 회원사 순매수`로 표시하며 `외국인 순매수`와 합치지 않는다. 기관 투자자 전체를
동등하게 나타내는 실시간 계약을 별도로 확인하기 전에는 기관 실시간 값을 추정 생성하지 않는다.

### 3.4 운영 계약 프로브

프로브는 저장소에 커밋하지 않는다. `notebooks/`는 `.gitignore`에 있고 앱키가 로그에 남기
쉬운 곳이라 결과만 이 문서에 남긴다. 초판이 가리키던 `docs/kis-investor-flow-probe.ipynb`는
저장소에 없다.

키는 셀에 적지 않는다. 환경 변수를 우선 쓰고 없으면 Git에서 제외된 루트 `config.yaml`의
`kis_app_key`·`kis_app_secret`을 읽는다.

프로브가 확인할 것은 다음이다.

- 액세스 토큰과 WebSocket approval key를 한 번씩 발급하되 값은 출력하지 않는다.
- 코스피 `999`/`S001`의 외국인·기관 순매수 대금만 조회해 HTS [0403] 표시 단위와 대조한다.
- 공용 연결 하나에서 KRX·NXT 두 종목을 함께 구독해 네 ACK와 `OPSP0008` 여부만 확인한다.
- 이미 승인된 종목 REST, 개별 WebSocket, 78필드 매핑은 다시 호출하거나 파싱하지 않는다.
- 코스닥 코드는 공식 근거가 생기기 전까지 노트북에도 넣지 않는다.
- 마지막 셀은 비밀값을 제외한 두 잔여 검증 결과만 출력한다.

REST는 `rt_cd == "0"`, 예상 필드 존재, 비어 있지 않은 `output` 또는 `output2`를 모두
만족해야 승인한다.
WebSocket 계약은 공식 예제와 78필드 실측으로 승인했다. 2차 프로브에서 네 개의 종목·거래소
조합 모두 개별 구독 ACK도 확인했다.

운영 프로브 시각:

- 1차: 2026-08-11 11:54:58 KST
- 2차: 2026-08-11 12:15:03 KST
- 3차: 2026-08-14 10:44 KST (장중, 이 문서 갱신)

| 대상 | 상태 | 근거·남은 확인 |
| --- | --- | --- |
| 종목 REST `005930` | 승인 | HTTP 200, `rt_cd=0`, 3행, 필수 4필드 확인 |
| 종목 REST `000660` | 승인 | 2차 HTTP 200, `rt_cd=0`, 3행, 필수 4필드 확인 |
| 시장 REST 코스피 `999`/`S001` | 승인 | 두 번 모두 HTTP 200, `rt_cd=0`, 1행, 필수 8필드 확인 |
| 시장 REST 코스닥 | **미확인** | 후보 6개가 모두 `rt_cd=0`에 값 0이었다(§3.2). 오류로 오지 않으므로 근거 있는 코드를 얻기 전에는 켜지 않는다 |
| 시장 순매수 대금 단위 | **미확정** | 주·원이 아닌 것만 확인(§3.2-1). HTS [0403] 대조 필요 |
| KRX `H0STMBC0` | 승인 | 두 종목 개별 ACK 성공, 1차 `000660` 78필드 수신 |
| NXT `H0NXMBC0` | 승인 | 두 종목 개별 ACK 성공, 두 종목 모두 78필드 수신 |

삼성전자 종목 응답은 `bsop_hour_gb`가 최신 슬롯부터 역순으로 온다(1차 `3`,`2`,`1` / 3차 `2`,`1`). 음수는
`-00000000000079000`처럼 부호 뒤에 0이 채워질 수 있으므로 문자열을 그대로 `int`로 파싱한다.
`sum_fake_ntby_qty = frgn_fake_ntby_qty + orgn_fake_ntby_qty`는 두 종목의 세 행 모두 확인됐다.
2차 코스피 응답도 외국인 `4231900 - 4208544 = 23356`, 기관
`2342873 - 2351599 = -8726`으로 `매수량 - 매도량 = 순매수량`이 일치했다.
2차 WebSocket에서 ACK 뒤 15초 동안 데이터가 없었던 조합은 구독 실패로 보지 않는다. 회원사
값이 바뀌지 않으면 새 프레임이 없을 수 있으므로 활성 상태는 ACK로, 파서 계약은 공식 78필드와
실제 수신 프레임으로 각각 검증한다.

## 4. 데이터 모델

**테이블을 둘로 나눈다.** 초판은 `scope`와 `target` 두 칸으로 종목과 시장을 한 테이블에
담으려 했는데, 그러면 어느 쪽과도 조인이 안 된다. 저장소에 이미 두 어휘가 자리 잡았기
때문이다.

| 대상 | 식별자 | 쓰는 곳 |
| --- | --- | --- |
| 종목 | `stock_code` 6자리 (`005930`) | `disclosure_event`, `earnings_fact`, `krx_stock_*` |
| 시장 | `KrxMarket` (`KOSPI`, `KOSDAQ`) | `market_movement_snapshot`, `krx_market_securities_lending_daily` |

`target` 한 칸에 `SAMSUNG_ELECTRONICS`와 `KOSPI`를 섞으면 셋째 어휘가 생긴다. 이번 주에
포지션 문서에서 같은 이유로 `symbol`을 `stock_code`로 바꿨다.

### 4.1 `stock_investor_estimate_snapshot`

종목별 외국인·기관 **추정** 순매수. 확정치가 아니라는 것이 이름에 드러나야 한다.

| 컬럼 | 타입 | 설명 |
| --- | --- | --- |
| `provider` | text | `kis` |
| `stock_code` | text | `005930`, `000660` |
| `business_date` | date | 그 값이 속한 거래일(KST) |
| `source_time_code` | text | 응답의 `bsop_hour_gb`. 갱신 슬롯이다 |
| `foreign_net_buy_qty` | bigint | 외국인 추정 순매수 |
| `institution_net_buy_qty` | bigint | 기관 추정 순매수 |
| `total_net_buy_qty` | bigint | 합계. 위 둘의 합인지 검증한다 |
| `collected_at` | timestamptz | 이 값을 받은 시각(UTC) |
| `source_record_id` | bigint FK | 수집 계보 |

멱등 키:

```text
(provider, stock_code, business_date, source_time_code)
```

**초판의 자연키는 응답의 대부분을 버렸다.** 키가 `(provider, scope, target, observed_at)`이고
`observed_at`이 수집 시각이었는데, 한 번 호출로 오는 2~4행이 전부 같은 분이라 마지막 한 행만
남는다. 슬롯이 값을 가르는 축이므로 `source_time_code`가 키에 들어가야 한다.

`collected_at`은 키가 아니라 값이다. 같은 슬롯을 다시 받으면 갱신된다. 슬롯 코드를 시각으로
바꾸는 표는 만들지 않는다. 공식 갱신 시각이 변동될 수 있다고 예제가 밝히고 있어, 우리가
시각을 지어내면 그 표가 틀리는 날 조용히 어긋난다.

### 4.2 `market_investor_flow_snapshot`

시장별 투자자 누적 매매동향.

| 컬럼 | 타입 | 설명 |
| --- | --- | --- |
| `provider` | text | `kis` |
| `market_code` | varchar(20) | `KrxMarket`. 1차는 `KOSPI`뿐이다 |
| `observed_at` | timestamptz | 수집 시각(UTC), 분 단위 절삭 |
| `foreign_sell_qty` / `foreign_buy_qty` / `foreign_net_buy_qty` | bigint | 외국인 |
| `foreign_net_buy_amount` | numeric | 외국인 순매수 대금 |
| `institution_*` | 위와 같음 | 기관 |
| `individual_*` | 위와 같음 | 개인(`prsn_*`) |
| `securities_net_buy_qty` | bigint | 금융투자(`scrt_ntby_qty`) |
| `investment_trust_net_buy_qty` | bigint | 투자신탁(`ivtr_ntby_qty`) |
| `private_equity_net_buy_qty` | bigint | 사모펀드(`pe_fund_ntby_vol`) |
| `bank_net_buy_qty` | bigint | 은행(`bank_ntby_qty`) |
| `insurance_net_buy_qty` | bigint | 보험(`insu_ntby_qty`) |
| `merchant_bank_net_buy_qty` | bigint | 종금(`mrbn_ntby_qty`) |
| `pension_fund_net_buy_qty` | bigint | 기금(`fund_ntby_qty`) |
| `other_corporation_net_buy_qty` | bigint | 기타법인(`etc_corp_ntby_vol`) |
| `other_organization_net_buy_qty` | bigint | 기타단체(`etc_orgt_ntby_vol`) |
| `source_record_id` | bigint FK | 수집 계보 |

멱등 키:

```text
(provider, market_code, observed_at)
```

수량과 대금은 음수가 정상이다. 음수 금지 제약을 두지 않는다. **단위는 KIS 표기 그대로 두고
환산하지 않는다**(§3.2-1).

#### 기관을 일곱으로 쪼갠다

같은 응답에 이미 들어 있어 **호출이 늘지 않는다.** 기관계 한 칸으로는 연기금이 사는 장과
투신이 파는 장을 가릴 수 없는데, 그 둘은 다음 날이 다르다.

세부 분류는 순매수 수량만 저장한다. 매도·매수와 대금은 상위 셋에만 둔다. 세부에서 필요한
것은 방향이고, 대금은 배율이 미확정이라(§3.2-1) 지금 넣어도 읽을 수 없다.

두 항등식이 실측으로 정확히 성립했고 파서가 매 응답 검증한다.

```text
기관계 = 금융투자 + 투자신탁 + 사모펀드 + 은행 + 보험 + 종금 + 기금
개인 + 외국인 + 기관계 + 기타법인 + 기타단체 = 0
```

기타법인·기타단체는 기관이 아니지만 저장한다. 이 둘이 없으면 두 번째 항등식이 닫히지 않아
분류를 빠뜨렸는지 검증할 수단이 사라진다.

**접미사가 분류마다 다르다.** 사모펀드·기타법인·기타단체만 `_ntby_vol`이고 나머지는
`_ntby_qty`다. `f"{prefix}_ntby_qty"` 한 벌로 조립하면 그 셋이 오류 없이 0이 된다.

시장 API가 누적값을 주므로 저장 단계에서 델타를 계산하지 않는다. 5분 변화량은 SQL의
`lag()`로 계산한다. 재수집·누락이 있는 환경에서 수집기가 델타를 저장하면 복구가 더 어렵다.

### 4.3 회원사 실시간 (2차)

실시간 보조 신호는 `KrxForeignMemberFlowSnapshot`, `NxtForeignMemberFlowSnapshot`에 각각
저장한다. **1차에서는 만들지 않는다.** 상주 WebSocket 프로세스가 있어야 채워지는 테이블이다.

| 컬럼 | 타입 | 설명 |
| --- | --- | --- |
| `provider` | text | `kis` |
| `stock_code` | text | `005930`, `000660` |
| `observed_at` | timestamptz | 수신 시각 UTC |
| `foreign_member_sell_qty` | bigint | 외국계 회원사 누적 매도 |
| `foreign_member_buy_qty` | bigint | 외국계 회원사 누적 매수 |
| `foreign_member_net_buy_qty` | bigint | 외국계 회원사 순매수 |
| `source_record_id` | bigint FK | WebSocket 세션 계보 |

물리 테이블은 `krx_foreign_member_flow_snapshot`, `nxt_foreign_member_flow_snapshot`이다.
두 테이블 모두 멱등 키는 `(provider, stock_code, observed_at)`이며 `venue` 컬럼은 두지 않는다.
프레임에 거래 시각이 없으므로
`observed_at`은 프로세스 수신 시각을 초 단위로 절삭하고 같은 초에는 마지막 값을 upsert한다.

## 5. 수집기와 DAG

### 5.1 수집기

새 파일 `airflow/modules/collectors/kis_investor_flow.py`에 둔다. 초판은 `kis.py`에 넣으라
했지만 그 뒤로 `kis_market_calendar.py`·`kis_positioning.py` 선례가 생겼다. 분봉 수집과
스케줄·실패 처리가 달라서 한 파일에 두면 어느 상수가 어느 수집의 것인지 읽기 어려워진다.

- `InvestorFlowStock`(종목 Enum, 값은 6자리 코드)
- `InvestorFlowMarket`(시장 Enum, 확인된 코드만)
- `fetch_stock_investor_estimates()`
- `fetch_market_investor_flow()`
- 응답 Pydantic 모델과 숫자 파서
- `store_stock_estimates()`, `store_market_flow()`

인증, **`send_get()`**, 오류 타입, 토큰 캐시는 `kis.py`의 것을 그대로 쓴다. `send_get()`은
초판이 적은 `_get()`의 새 이름이고 `(본문, 상태, 헤더)` 세 값을 돌려준다.

파서 규칙 둘을 반드시 건다.

- **시장 응답의 모든 값이 0이면 실패시킨다.** 잘못된 시장 코드가 오류가 아니라 0으로 오기
  때문이다(§3.2). 장중에 전 투자자 분류가 정확히 0인 일은 없다.
- **종목 응답의 `sum`이 외국인+기관과 다르면 실패시킨다.** 네 행 모두 일치했다.
두 종목 REST는 병렬 호출하지 않고 호출 간격을 둔다. `EGW00201`이면 같은 요청을 한 번만
지연 재시도하며, 다시 실패하면 해당 종목만 실패로 남긴다. 새 endpoint 요청에도 `tr_cont=""`
헤더를 보낸다.

### 5.2 DAG

새 파일 `airflow/dags/kis_investor_flow_intraday.py`를 만든다.

- 스케줄: 평일 KST 09:00~15:59, 5분마다
- 확정 휴장일에는 `modules.market_session.krx_open_day()`로 태스크를 skip한다. 초판을 쓸
  때는 없던 캘린더다
- 매 run: 지원이 확인된 시장 스냅샷 수집. 현재는 코스피만 활성화
- 종목 추정: 공식 갱신 시각 뒤의 지정된 run에서만 호출
- 권장 호출 시각: 09:35, 10:05, 11:25, 13:25, 14:35 KST
- 수동 실행은 시장과 두 종목을 모두 조회

DAG는 5분마다 깨지만 종목 API를 매번 부르지 않는다. 시간 집합 하나로 분기하면 충분하며
별도 Timetable이나 두 번째 DAG를 만들지 않는다.

### 5.3 WebSocket 보조 수집

**이 절은 2차다.** `kis_realtime.py`가 아직 없다. 아래는 그 상주 서비스를 만들 때 함께
넣을 내용이고, 1차 배포에는 회원사 테이블도 SQL도 만들지 않는다.

`airflow/modules/collectors/kis_realtime.py`의 registry에 두 TR ID와 두 종목을 등록한다.
기존 공용 연결에서 먼저 구독하되 모든 ACK를 받은 경우에만 ready로 표시한다. 네 조합의 개별
구독은 모두 성공했으므로 종목 미지원 가능성은 제외한다. 다만 1차 동시 구독의 네 번째 요청에서
`OPSP0008`이 발생했으므로 공용 연결의 총 구독 registry로 운영 용량을 확인하기 전에는
동시 구독 한도를 단정하거나 연결 수를 늘리지 않는다.

- WebSocket 세션 시작 시 `source_record(source_type='websocket')` 1건 생성
- TR ID로 저장 대상을 판별해 KRX/NXT 전용 테이블에 upsert
- 거래소별 외국계 회원사 값이 바뀔 때만 snapshot upsert
- 연결 종료 시 세션 상태와 수신 건수 갱신
- 재연결 뒤 REST 외국인·기관 추정치와 별도로 계속 수집

## 6. 변경 파일

### 작업 0 — 운영 계약 확정

- 실행: `docs/kis-investor-flow-probe.ipynb`
- 수정: 이 문서의 3.4 결과 표
- 완료: 두 종목 REST, KRX/NXT TR ID·78필드 순서·네 조합 개별 ACK
- 완료(3차): 종목 응답이 슬롯마다 한 행이라는 것, 시장 응답 72필드, 잘못된 시장 코드가
  0으로 온다는 것, 대금·수량이 주·원 단위가 아니라는 것
- 남음: **코스닥 코드**, 공용 연결 동시 구독 용량, **시장 대금·수량의 정확한 배율**

모델과 두 종목 WebSocket 파서는 구현할 수 있다. 코스닥 활성화와 공용 연결 배포는 각각 코드와
동시 구독 용량을 확인한 뒤에만 한다.

### 작업 1 — 모델과 migration

- 수정: `apps/models/market.py`
- 추가: 새 Alembic revision (테이블 둘. 회원사 테이블은 2차)
- 수정: `tests/models/test_market_models.py`
- 추가: `tests/migrations/test_investor_flow_schema.py`

검증할 제약:

- 두 테이블의 멱등 UNIQUE. 종목 쪽 키에 `source_time_code`가 들어간다
- `market_code`가 `KrxMarket` Enum과 CHECK
- 종목 쪽 식별자가 `stock_code`이고 `disclosure_event`와 같은 체계
- `source_record_id` FK와 인덱스
- 음수 순매수 허용

### 작업 2 — SQL과 수집기

- 추가: `airflow/sql/postgres/stock_investor_estimate_snapshot/upsert.sql`
- 추가: `airflow/sql/postgres/market_investor_flow_snapshot/upsert.sql`
- 추가: `airflow/sql/postgres/stock_investor_trade_daily/upsert.sql` (§9)
- 추가: `airflow/dags/kis_investor_trade_daily.py` (§9)
- 추가: `airflow/modules/collectors/kis_investor_flow.py`
- 추가: `tests/collectors/test_kis_investor_flow.py`

최소 테스트:

- 종목 추정 응답 두 종목 파싱
- **한 응답의 여러 슬롯이 각각 한 행으로 저장됨**(자연키에 슬롯이 들어간다)
- 시장 외국인/기관/개인 필드 파싱
- **모든 값이 0인 시장 응답은 실패**(잘못된 코드가 0으로 온다)
- 쉼표·공백·음수 숫자 처리
- 부호 뒤에 0이 채워진 값 처리
- 종목 추정 합계가 외국인과 기관 합계인지 검증
- 시장 매수량과 매도량의 차이가 순매수량인지 검증
- API 오류와 빈 output 구분
- `EGW00201`은 API 미지원이 아니라 재시도 가능한 호출 제한으로 분류
- 같은 observed_at 재저장 시 upsert
- 한 대상 실패 시 다른 대상 저장

### 작업 3 — DAG

- 추가: `airflow/dags/kis_investor_flow_intraday.py`
- 추가: `tests/dags/test_kis_investor_flow_intraday.py`

검증:

- 정규장 밖에는 예약 실행되지 않음 (`*/5 9-15 * * 1-5`)
- 확정 휴장일에는 태스크가 skip됨
- 지정 시각 밖에는 종목 추정 API를 호출하지 않음
- 수동 실행이 `include_stock_estimates`로 그 판단을 덮을 수 있음
- 시장 코드를 라이브 프로브 결과와 일치시킴

### 작업 4 — 실시간 회원사 보조 신호 (2차, `kis_realtime.py` 이후)

- 수정: `airflow/modules/collectors/kis_realtime.py`
- 추가: `airflow/sql/postgres/krx_foreign_member_flow_snapshot/upsert.sql`
- 추가: `airflow/sql/postgres/nxt_foreign_member_flow_snapshot/upsert.sql`
- 수정: `tests/collectors/test_kis_realtime.py`

- KRX/NXT 모두 78필드와 인덱스 61·62·65 고정
- `매수 - 매도 = 순매수` 불변식 검증
- ACK 실패 구독은 활성 상태로 표시하지 않음
- `OPSP0008`을 종목 미지원으로 분류하지 않음
- 값이 바뀌지 않은 반복 프레임은 새 행을 만들지 않음
- 외국인·기관 수급 테이블로 잘못 저장되지 않음
- KRX와 NXT의 동일 시각 값이 서로 덮어쓰지 않음

## 7. 조회 예

가격과 같은 5분 구간에서 보는 쿼리:

```sql
SELECT
    business_date,
    source_time_code,
    stock_code,
    foreign_net_buy_qty,
    institution_net_buy_qty
FROM stock_investor_estimate_snapshot
WHERE provider = 'kis'
  AND stock_code IN ('005930', '000660')
  AND business_date >= current_date - 5
ORDER BY business_date, source_time_code, stock_code;
```

시장 누적값의 5분 변화:

```sql
SELECT
    observed_at,
    market_code,
    foreign_net_buy_qty
      - lag(foreign_net_buy_qty) OVER (PARTITION BY market_code ORDER BY observed_at) AS foreign_delta,
    institution_net_buy_qty
      - lag(institution_net_buy_qty) OVER (PARTITION BY market_code ORDER BY observed_at) AS institution_delta,
    individual_net_buy_qty
      - lag(individual_net_buy_qty) OVER (PARTITION BY market_code ORDER BY observed_at) AS individual_delta
FROM market_investor_flow_snapshot
WHERE provider = 'kis';
```

거래소별 외국계 회원사 보조 신호 비교:

```sql
SELECT 'KRX' AS venue, stock_code, observed_at, foreign_member_net_buy_qty
FROM krx_foreign_member_flow_snapshot
WHERE provider = 'kis'
UNION ALL
SELECT 'NXT' AS venue, stock_code, observed_at, foreign_member_net_buy_qty
FROM nxt_foreign_member_flow_snapshot
WHERE provider = 'kis'
ORDER BY observed_at, venue, stock_code;
```

이 조회는 2차다. 회원사 테이블은 상주 WebSocket 서비스와 함께 생긴다.

## 8. 검증과 완료 조건

```bash
uv run pytest tests/collectors/test_kis_investor_flow.py tests/models/test_market_models.py \
  tests/migrations tests/dags/test_kis_investor_flow_intraday.py -q
uv run ruff check apps airflow migrations tests
uv run pyrefly check
```

이 저장소에는 Django가 없다. 초판에 있던 `manage.py check`는 다른 프로젝트의 명령이었다.

완료 조건:

- 두 종목의 외국인·기관 추정 순매수가 **갱신 슬롯마다 한 행씩** 저장된다.
- 시장 수급에 개인이 함께 저장돼 외국인·기관·개인 삼분이 읽힌다.
- 지원이 확인된 각 시장의 외국인·기관 누적 수급이 5분 간격으로 저장된다.
- 종목 식별자가 `005930` 6자리라 공시·포지션과 한 키로 이어진다.
- 화면과 API의 부호가 일치한다.
- 시장 대금·수량 배율을 HTS [0403]과 대조해 확정하기 전에는 화면 축에 단위를 붙이지 않는다.
- 잘못된 시장 코드의 0 응답이 저장되지 않는다.
- 반복 수집은 같은 자연키를 갱신한다.
- 종목 추정값은 UI에서 반드시 `추정`으로 표시한다.
- 회원사 값은 UI에서 반드시 `외국계 회원사`로 표시하고 외국인 값과 합산하지 않는다.

## 8-1. 구현 기록 (2026-08-14)

1차(REST 둘)를 구현했다. 실행 결과는 다음과 같다.

```text
market:KOSPI      1행   외국인 -30,140  기관 62,665  개인 -34,420
estimate:005930   2행   슬롯 2·1
estimate:000660   2행   슬롯 2·1
```

**슬롯 키 수정이 실제로 값을 살렸다.** 종목당 두 슬롯이 각각 행으로 남았다. 초판 자연키였다면
종목당 한 행만 남았을 것이다.

10:55 run 에서는 종목 추정을 건너뛰고 로그에 이유를 남겼다(`not an update slot`).

만든 것:

- 모델 `StockInvestorEstimateSnapshot`, `MarketInvestorFlowSnapshot`
- `airflow/modules/collectors/kis_investor_flow.py`
- `airflow/dags/kis_investor_flow_intraday.py`
- upsert SQL 둘, 테스트 네 파일

대시보드도 함께 만들었다: `compose/local/grafana/dashboards/investor-flow.json`
(uid `investor-flow`, 패널 일곱).

- 투자자별 누적 순매수(stat), 누적 추이, **구간 순매수**, **기관 세부 일곱**,
  지수 대 외국인 누적, 종목 추정 표(갱신 시각별), 거래일별 마지막 슬롯 추이
- **구간 순매수는 거래일로 나눠 뺀다.** 누적값이 장마다 0에서 다시 시작하므로 날짜를 넘어
  `lag()`를 걸면 하루 첫 스냅샷에 전날 마지막값만큼의 가짜 급변이 생긴다. 구현 중 실제로
  그 상태였고 어제 행을 넣어 재현한 뒤 `PARTITION BY`로 고쳤다.
- **슬롯을 가로축으로 쓰지 않는다.** 표는 슬롯을 열로 보여 주고, 추이 패널은 거래일을 축으로
  쓰면서 슬롯은 그날의 마지막 값을 고르는 데만 쓴다.
- **슬롯은 저장만 코드고 화면은 시각이다.** 표가 `1`을 `09:30`으로 보여 준다. 모르는 슬롯은
  `슬롯 N`으로 그대로 둔다. 시각을 지어내면 틀린 시각이 맞는 것처럼 보인다.
- 기관 세부 패널은 일곱을 다 그린다. 하나라도 빼면 합을 기관계와 대조할 수 없다. 기관이 아닌
  기타법인·기타단체는 이 패널에 넣지 않는다.
- 단위가 미확정이라 축 라벨이 `단위 미확정`이다.

이어서 시장 기관 세부 아홉(기관 일곱 + 기타 둘)을 §4.2대로 편입했다. 같은 응답에 이미 있어
호출은 늘지 않았고, 두 항등식 검증이 파서에 들어갔다.

아직 안 한 것: 코스닥, 회원사 WebSocket(2차).

## 9. 종목 확정 일별 (2026-08-14 편입)

장중 추정(§3.1)이 하루 다섯 회차의 **추정치**라면 이쪽은 장 마감 뒤의 **확정값**이다.
`FHPTJ04160001`, `/uapi/domestic-stock/v1/quotations/investor-trade-by-stock-daily`.

**실측 (2026-08-14 17:19 KST, 005930)**

```text
output1  7필드   현재가·전일대비·누적거래량·대표시장명(KOSPI200)
output2 101필드  30행 = 30 거래일 (20260814 .. 20260703)
tr_cont  빈 문자열. 연속조회가 없다
```

값은 이랬다.

```text
외국인  +4,913,432 (등록 +4,922,472 / 미등록 -9,040)
개인    -3,049,224
기관계  -1,830,920  금융투자 -1,390,485  투신 +107,489  사모 -511,711
                    은행 +19,391  보험 -82,201  종금 -746  기금 +27,343
기타       -33,288 (법인 -33,288 / 단체 0)
```

### 9.1 네 항등식

전부 정확히 성립했고 파서가 매 행 검증한다.

```text
외국인 = 외국인등록 + 외국인미등록
기관계 = 금융투자 + 투자신탁 + 사모펀드 + 은행 + 보험 + 종금 + 기금
기타   = 기타법인 + 기타단체          (제공처가 etc_ntby_qty로 따로 준다)
개인 + 외국인 + 기관계 + 기타 = 0
```

셋째는 우리가 더한 값과 제공처 합계를 대조하는 것이라 둘 중 하나가 다른 뜻으로 바뀌면 잡힌다.

### 9.2 단위가 여기서 확정됐다

`frgn_seln_tr_pbmn / frgn_seln_vol × 1e6 = 271,200`원이고 그날 VWAP은 271,093원이었다.
**수량은 주, 투자자별 대금은 백만원이다.**

**같은 응답 안에서 대금 단위가 섞인다.** `acml_tr_pbmn`만 원이다(5,874,118,816,500 ÷
21,668,266 = 271,093). 한 벌로 환산하면 백만 배 어긋난다.

장중 API(§3.2-1)의 배율은 **여전히 미확정이며 이 값과 다르다.** 확정됐다고 그쪽에 옮겨
붙이지 않는다.

### 9.3 요청 날짜는 구간의 끝이다

`FID_INPUT_DATE_1`에 2026-07-01을 넣으면 2026-07-01~2026-05-19가 온다(실측). 그래서
하루치 호출이 이미 지난 달까지 채우고, 매일 도는 것만으로 30 거래일이 겹쳐 들어와 실패한
날이 저절로 메워진다.

백필은 날짜를 뒤로 건다. 다음 끝 날짜는 **응답이 준 가장 이른 거래일의 하루 전**이다.
우리가 거래일을 세면 휴장일에서 어긋난다. 실측에서 2026-04-05를 요청하니 2026-04-03까지
왔다.

응답이 끝 날짜보다 뒤의 거래일을 담으면 실패시킨다. 그 전제가 깨지면 백필이 같은 구간을
조용히 맴돈다.

### 9.4 코스닥 문제가 여기엔 없다

`FID_COND_MRKT_DIV_CODE=J` 하나로 코스피와 코스닥을 함께 받는다(실측: 247540이 `J`로
`KSQ150` 응답). 장중 시장 조회처럼 시장별 코드를 찾을 필요가 없다.

### 9.5 저장

`market.stock_investor_trade_daily`, 멱등 키는 `(provider, stock_code, business_date)`다.
확정값이라 겹쳐 받아도 값이 같다.

순매수 수량은 12분류 전부, 대금은 상위 셋만 저장한다. 매도·매수 총량도 12분류 전부 오지만
저장하지 않는다. 확정값에서 읽는 것은 방향과 규모이고, 회전율이 필요해지면 재호출 없이 컬럼만
늘린다. 종가와 누적 거래량·거래대금은 수급과 가격을 한 화면에서 겹치려고 함께 저장한다.

DAG는 `kis_investor_trade_daily`, KST 평일 18:10이다. `end_date`·`pages` params로 백필한다.
대시보드 패널 8·9가 확정값을 그리며 **추정 패널과 같은 축에 올리지 않는다.**

## 10. 남은 2차 범위

필요가 확인되면 다음을 추가한다.

- 시장별 투자자매매동향 일별: `FHPTJ04040000`
- 확정값의 12분류 매도·매수 총량(회전율이 필요해질 때)

프로그램매매는 의미와 필드가 달라 `kis-program-trading.md`에서 별도로 다룬다.
