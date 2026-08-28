# KIS 시장 수급·포지션·캘린더 수집

- 기준일: 2026-08-25
- 상태: 구현 완료

가격 수집과 별개로 누가 사고파는지, 누가 들고 있는지, 시장이 실제로 열리는지를 채우는
KIS 수집 계약이다. 수급·포지션의 감시 종목은 삼성전자(`005930`)와 SK하이닉스(`000660`)다.

## DAG와 저장 대상

| DAG | 스케줄(KST) | 저장 대상 |
| --- | --- | --- |
| `market_calendar_daily` | 매일 07:00 | `market_session` |
| `kis_investor_flow_intraday` | 평일 09:00~15:55, 5분마다 | `market_investor_flow_snapshot` |
| `kis_investor_estimate_intraday` | 평일 09:35·10:05·11:25·13:25·14:35 | `stock_investor_estimate_snapshot` |
| `kis_investor_trade_daily` | 평일 18:10 | `stock_investor_trade_daily` |
| `kis_market_positioning_daily` | 화~토 08:10 | `krx_*` 수급·포지션 6개 테이블 |

각 API 호출은 별도 트랜잭션이다. 앞선 호출의 성공분은 뒤 호출이 실패해도 남고, 실패가 하나라도
있으면 루프가 끝난 뒤 태스크를 실패시켜 재시도한다. 자연키 upsert라 재실행해도 행이 늘지 않는다.

## 장중 투자자 수급

### 시장 누적 수급

`kis_investor_flow_intraday`는 첫 집계가 나오는 09:05부터 일곱 시장을 5분마다 받는다.

| 시장 | KIS 코드 |
| --- | --- |
| KOSPI | `KSP/0001` |
| KOSDAQ | `KSQ/1001` |
| 선물 | `K2I/F001` |
| 콜옵션 | `K2I/OC01` |
| 풋옵션 | `K2I/OP01` |
| 주식선물 | `999/S001` |
| ETF | `ETF/T000` |

API는 `inquire-investor-time-by-market`(`FHPTJ04030000`)이다. 외국인·기관·개인의
매도·매수·순매수 수량과 순매수 대금, 기관 세부와 기타 분류의 순매수 수량을
`market_investor_flow_snapshot`에 누적 스냅샷으로 저장한다. 파생 수량은 주가 아니라 계약이며
KIS 표기를 환산하지 않는다. 관측 시각은 응답에 없으므로 수집한 UTC 분으로 기록한다.

잘못된 시장 코드도 전부 0인 정상 응답처럼 오므로 all-zero 응답은 실패시킨다. 다만 첫 집계 전
09:00 실행은 정상적인 0이어서 미리 건너뛴다.

### 종목 추정 수급

`kis_investor_estimate_intraday`가 따로 받는다. API는 `investor-trend-estimate`
(`HHPTJ04160200`)이고 호출 시각은 09:35, 10:05, 11:25, 13:25, 14:35다. KIS가 하루 몇 번만
갱신하므로 매 5분 호출하지 않는다.

**시장 누적과 한 DAG 가 아니다**(2026-08-25에 가름). 전에는 한 DAG 가 벽시계로 "지금이 갱신
시각인가"를 판단해서, 갱신 시각이 아닐 때 UI 의 Trigger 를 누르면 추정이 조용히 빠진 채
태스크가 성공했다. 두 조회는 실패 판정도 반대다 — 시장 누적은 값이 전부 0이면 시장 코드
오류라 실패시키고, 종목 추정은 0행이 갱신 전이라 정상이다. 놓쳤을 때도 다르다. 시장 누적은
5분 뒤 run 이 같은 누적값을 싣지만 종목 추정은 다음 슬롯이 한두 시간 뒤라 재시도를 더 준다.

외국인·기관 추정 순매수를 `stock_investor_estimate_snapshot`에 저장한다. 자연키는
`(provider, stock_code, business_date, source_time_code)`다. 제공처 슬롯 코드가 실제 값의
정체성이므로 수집 시각을 대신 키로 쓰지 않는다. 0행은 갱신 전일 수 있어 정상이다.

## 종목별 확정 수급과 완전성 검사

`kis_investor_trade_daily`는 `investor-trade-by-stock-daily`(`FHPTJ04160001`)를 사용한다.
한 호출이 종료일에서 과거 30거래일을 반환하며 OHLCV, 누적 거래대금, 12개 투자자 분류,
외국인·기관·개인 순매수 대금을 저장한다. 수량은 주, 투자자 대금은 백만원,
`accumulated_trade_amount`는 원으로 KIS 단위를 그대로 유지한다.

자연키는 `(provider, stock_code, business_date)`다. 저장 뒤 받은 구간의 KRX 개장일과
DB의 날짜를 대조한다. 빠진 개장일이 하나라도 있으면 태스크를 실패시켜 응답 누락과 저장 누락을
조용히 넘기지 않는다. API 호출 실패가 있으면 그 원인을 먼저 보고하고, 성공적으로 받은 뒤의
구멍만 완전성 오류로 보고한다.

자동 실행은 실행일이 확정 휴장이면 건너뛴다. 수동 백필은 종료일이 휴장일이어도 그 앞 거래일을
받을 수 있으므로 허용한다.

```bash
airflow dags trigger kis_investor_trade_daily \
  --conf '{"end_date":"2026-07-01","pages":6}'
```

`pages`는 30거래일 구간 수다. 다음 종료일은 달력으로 계산하지 않고 응답의 가장 이른 거래일
하루 전으로 이동한다.

## 신용·공매도·대차·증시자금

`kis_market_positioning_daily`가 다음 계약을 한 번에 운영한다.

| 데이터 | API / TR ID | 저장 테이블 | 조회 규칙 |
| --- | --- | --- | --- |
| 종목 신용잔고 | `daily-credit-balance` / `FHPST04760000` | `krx_stock_credit_balance_daily` | 입력이 결제일이라 거래일 구간에 14일 padding 후 필터. **한 번에 30행**(아래) |
| 신용잔고 순위 | `credit-balance` / `FHKST17010000` | `krx_credit_balance_ranking_daily` | 전체(`0000`)·코스닥(`1001`) 최신 스냅샷만 가능 |
| 증시자금 | `mktfunds` / `FHKST649100C0` | `krx_market_funds_daily` | 종료일 한 번으로 약 100영업일 반환 |
| 종목 공매도 | `daily-short-sale` / `FHPST04830000` | `krx_stock_short_sale_daily` | 시작·종료일 적용 |
| 종목 대차 | `daily-loan-trans` / `HHPST074500C0` | `krx_stock_securities_lending_daily` | 구분 `3`, 시작·종료일 적용 |
| 시장 대차 | `daily-loan-trans` / `HHPST074500C0` | `krx_market_securities_lending_daily` | KOSPI `1`, KOSDAQ `2` |


### 신용잔고 조회의 결제일과 30행 상한 (2026-08-28 실측)

같은 조사를 두 번 하지 않으려고 남긴다. 운영 앱키로 조회 전용 실측했다(삼성전자).

| 요청 결제일 | 행수 | `deal_date` 범위 | `tr_cont` |
| --- | --- | --- | --- |
| 20260828 (오늘) | 30 | 20260713~20260825 | 빈 문자열 |
| 20260729 | 30 | 20260615~20260727 | 빈 문자열 |
| 20250828 (1년 전) | 30 | 20250715~20250826 | 빈 문자열 |
| 20240313 (2년 전) | 30 | 20240125~20240311 | 빈 문자열 |

- **`FID_INPUT_DATE_1`은 지켜진다.** 그 결제일 **이하**의 30행을 준다 — "날짜와 무관하게
  최신 N행"이 아니다. 1년 전·2년 전 요청도 그 구간을 그대로 준다.
- **한 번에 30행이고 `tr_cont`는 안 온다.** 공식 예제도 "한 번의 호출에 최대 30건 확인
  가능하며 `fid_input_date_1`을 입력하여 다음 조회가 가능합니다"라고 적는다 —
  연속조회 헤더가 아니라 **날짜를 앞으로 밀어 페이징**한다.
- 그래서 **30거래일보다 긴 백필 창은 앞부분이 조용히 빈다.** 구간을 잘라 여러 번 돌린다.
  수집기가 그때 경고를 남긴다(`kis_positioning.fetch_credit_balance`).
- 0행의 뜻이 둘로 갈린다 — 반환 구간이 창과 **겹치는데** 0행이면 거르기가 깨진 것이라
  실패시키고, 아예 **안 겹치면**(창 전체가 아직 결제 전이거나 30행 밖) 경고만 남긴다.
  30행은 거래일이 이어져 있어 겹치면 반드시 한 행은 남는다는 것이 그 판정의 근거다.

기본 조회 구간은 최근 7일이며 `observation_start`, `observation_end`, `lookback_days`로
백필할 수 있다. 신용 순위는 과거 조회가 불가능하고, 증시자금은 응답 자체가 긴 구간을 주므로
이 범위를 직접 사용하지 않는다. 시장 신용융자 잔고는 증시자금 응답의 전체 시장 값이고
KOSPI·KOSDAQ으로 나눌 수 없다.

```bash
airflow dags trigger kis_market_positioning_daily \
  --conf '{"observation_start":"2026-06-01","observation_end":"2026-08-12"}'
```

## 시장 캘린더

`market_calendar_daily`는 수집 DAG와 Slack 휴장 판정의 기준인 `market_session`을 채운다.

| 시장 | 개장 판정 | 결제일 |
| --- | --- | --- |
| KRX | KIS 국내휴장일조회 `chk-holiday` / `CTCA0903R` | 같은 응답 |
| US_EQUITY | NYSE 공식 캘린더 | KIS 해외결제일자조회 `countries-holiday` / `CTOS5011R` |

국내 경로는 오늘부터 앞으로의 날짜를 받고, KIS 권고에 따라 이 DAG에서 하루 한 번만 호출한다.
미국은 KIS 응답이 휴장·미래 날짜에 0행이어서 개장 판정을 맡길 수 없다. NYSE가 먼저 공식
개장일을 저장한 뒤 KIS 결제일을 보완한다. 해외 0행은 NYSE 판정을 지우지 않는 정상 결과다.

태스크는 `domestic_holiday`와 `nyse_calendar >> overseas_settlement` 두 경로다. 국내와 미국
경로는 서로 롤백하지 않는다. 401은 공유 토큰을 한 번 재발급해 다시 호출한다.

```bash
airflow dags trigger market_calendar_daily \
  --conf '{"trade_date":"2026-05-14"}'
```

해외 과거 결제일은 `trade_date`로 하루씩 채운다. `base_date`는 국내 조회 기준일이다.

## 공통 오류·계보 계약

- `KIS_APP_KEY`, `KIS_APP_SECRET`, `CONNECTION_ID`가 필요하다.
- 모든 KIS DAG가 Airflow Variable의 동일한 액세스 토큰 캐시를 공유한다.
- HTTP 400·403·404는 설정 오류로 즉시 실패한다. 그 밖의 일시 오류는 DAG 재시도 대상으로 둔다.
- 외부 응답 형식·항등식·시장 코드 계약이 깨지면 저장하지 않는다.
- 각 조회는 `source_record`에 제공처, source key, 구간, 건수와 메타데이터를 남긴다.

## 구현과 검증 위치

| 영역 | 구현 | 테스트 |
| --- | --- | --- |
| 장중·확정 수급 | `airflow/modules/collectors/market/kis_investor_flow.py` | `tests/collectors/test_kis_investor_flow.py` |
| 포지션 | `airflow/modules/collectors/market/kis_positioning.py` | `tests/collectors/test_kis_positioning.py` |
| 캘린더 | `airflow/modules/collectors/calendar/kis_market_calendar.py`, `nyse_calendar.py` | `tests/collectors/test_kis_market_calendar.py` |
| DAG | `airflow/dags/kis_investor_*.py`, `kis_market_positioning_daily.py`, `market_calendar_daily.py` | `tests/dags/test_kis_investor_*.py`, `test_market_calendar.py` |
