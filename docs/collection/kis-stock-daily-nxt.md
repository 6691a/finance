# 국내 종목 일봉의 거래소 축(KRX·NXT)

> **한눈에**
> - 왜: 국내 종목 일봉(`stock_investor_trade_daily`)은 KRX 기준 한 값뿐이고 NXT 마감 값은 분봉으로만 남는다. 시장 기준이 KRX에서 NXT로 넓어지면 일봉·지표·브리핑의 종가가 어느 거래소 값인지 가릴 수 없다. 지금도 가장 최근 거래일 종가는 마지막 체결가(NXT 포함)로 와서 두 거래소 값이 섞여 있다(2026-09-18 실측).
> - 무엇: 지금 DAG를 KRX용과 NXT용 둘로 나눠, 같은 KIS API를 시장 코드(`J`·`NX`)만 바꿔 받는다. NXT 일봉과 NXT만의 투자자별 수급이 따로 쌓인다. 둘을 합친 통합 마감가는 만들지 않는다.
> - 한계: NXT 일봉은 2025-03-24부터만 실제 값이 있다(그 앞은 빈 행). NXT 값은 20:00 이후에 받아야 마감가다. 일봉 거래량은 분봉 합과 3~17% 어긋나고 원인은 못 밝혔다. 어느 거래소 값이 공식 마감가가 될지는 코드가 미리 고르지 않는다.

> 작성 기준: 2026-09-20 / 실측: 2026-09-20 (운영 앱키, 읽기 전용)
> 상태: **구현 완료(코드·테스트) — 미배포.** 마이그레이션 적용과 NXT 백필이 남았다(6절). 저장 위치는 별도 표로
> 정했다(5절). 관련 코드는 `airflow/dags/kis_investor_trade_daily.py`(DAG 둘), `airflow/modules/collectors/market/kis_investor_flow.py`,
> `apps/models/market/investor_flow.py`의 `StockInvestorTradeDaily`·`StockInvestorTradeDailyNxt`,
> `migrations/versions/a5d3f8c21b96_add_stock_investor_trade_daily_nxt.py`,
> `airflow/sql/postgres/stock_investor_trade_daily_nxt/`
> 대상: 삼성전자(`005930`), SK하이닉스(`000660`) 두 종목만

## 1. 지금 KRX는 어떻게 저장되나

- DAG `kis_investor_trade_daily`(KST 평일 18:10)가 KIS `investor-trade-by-stock-daily`(TR `FHPTJ04160001`)를
  시장 코드 `J`(KRX)로 불러 한 응답에 30 거래일씩 받는다.
- 표는 `stock_investor_trade_daily` 하나이고 자연키는 `(provider, stock_code, business_date)`다.
  **거래소 열이 없다.** 시가·고가·저가·종가·거래량·거래대금과 투자자 12분류 수급이 한 행에 든다.
- 저장 전에 같은 거래일의 기존 종가와 대조해 수정주가 소급 조정을 감지하고(`close_conflicts`), 저장된
  가장 최근 행은 잠정값이라 대조에서 뺀다(2026-09-20).

## 2. 실측 (2026-09-20)

세 API가 모두 시장 코드 `NX`를 받는다: 기간별시세 `inquire-daily-itemchartprice`(`FHKST03010100`),
일자별 `inquire-daily-price`(`FHKST01010400`), 투자자 일별 `investor-trade-by-stock-daily`(`FHPTJ04160001`).
공식 예제는 `koreainvestment/open-trading-api`의 `examples_llm/domestic_stock/`이다. **이 DAG가 이미 쓰는 투자자
일별 API를 시장 코드만 바꿔 그대로 쓸 수 있다.**

- **NX 일봉은 NXT 분봉과 일치한다.** 6 거래일 × 2종목에서 시가·고가·저가·종가가 전부 우리 NXT 분봉
  집계와 같다. 거래량은 분봉 합이 0.01% 이하로 적다(005930 09-11: 8,077,800 대 8,077,358).
  **NX 종가는 NXT 마지막 봉 종가, 곧 NXT 마감가다.**
- **NX 응답이 J와 같은 모양이다.** 두 종목 30행씩이 지금 수집기의 항등식 검증(`StockTradeDailyRow`)을
  전부 통과했고, 최근 30 거래일의 날짜 집합도 J와 같다.
- **수급이 거래소별로 다르다.** 005930 09-18 외국인 순매수: J −1,673,323, NX +240,463.
- **가장 최근 거래일 행의 종가는 그 시점 마지막 체결가다.** J로 불러도 005930은 260,000, 000660은
  1,849,000이었다(KRX 15:30 종가는 261,000, 1,857,000). 세 API가 같다. 일요일에 받아도 그대로라 시각을
  미뤄도 안 바뀐다. NX의 최근 행은 NXT의 마지막 체결이라 20:00 이후에는 NXT 마감가와 같다.
- **통합(`UN`)은 어느 거래소 값도 아니다.** 005930 09-15 종가: KRX 248,500, NXT 250,000, UN 250,500.
- **NXT 실제 값은 2025-03-24부터다.** 그 앞은 응답에 행은 오되 시가·종가·거래량이 빈 문자열이고, 지금
  파서는 빈 칸을 0으로 읽어 통과시킨다. 2025-03-24 이후는 두 종목 모두 30행이 전부 실제 값이었다.
- **J 일봉 거래량이 KRX 정규장 분봉 합보다 크다.** 005930 09-18: 17,489,615 대 14,652,390. 09-11까지는
  0.1% 안팎이었고 09-14부터 3~17%다. 가격은 일치한다. 원인은 못 밝혔다(시간외 거래 포함으로 추정할 뿐이다).
- 토큰 발급은 분당 1회 제한이라 3분 안에 세 번 받으면 HTTP 403이 온다. 두 DAG가 같은 캐시를 쓰므로
  스케줄을 겹치지 않게 둔다.

입출력 예시 — 005930, 2026-09-18:

| | 시가 | 고가 | 저가 | 종가 | 거래량 | 외국인 순매수 |
| --- | --- | --- | --- | --- | --- | --- |
| `J`(KRX) | 261,000 | 262,000 | 257,500 | 260,000(※최근 행) | 17,489,615 | −1,673,323 |
| `NX`(NXT) | 259,500 | 262,000 | 257,500 | 260,000 | 6,577,566 | +240,463 |

## 3. 결정 (2026-09-20)

1. **범위는 두 종목**(`InvestorFlowStock`)이다. 다른 종목은 하지 않는다.
2. **소스는 KIS API의 `NX`다.** 분봉 집계로 만들지 않는다. 실측이 위와 같다.
3. **DAG를 KRX용과 NXT용 둘로 나눈다.** 기존 `kis_investor_trade_daily`는 이름을 유지해 KRX가 되고
   (이름을 바꾸면 Airflow 실행 이력이 끊긴다), NXT용 `kis_investor_trade_nxt_daily`를 새로 둔다.
4. **NXT 값은 KRX 표에 섞지 않고 새 표에 쌓는다**(`stock_investor_trade_daily_nxt`). 이유는 5절이다.

## 4. 설계

**흐름.**

```
KRX DAG  18:10  → J  → 수집기(exchange=KRX) → KRX 일봉·수급
NXT DAG  20:10  → NX → 수집기(exchange=NXT) → NXT 일봉·수급
```

- **시장은 수집기 생성자가 받는다.** 한 실행 동안 안 변하는 값이라 규칙에 맞다(`StockExchange`가 이미 `J`·`NX`를
  `division_code`로 갖는다). 그래서 `"J"`를 박아 둔 상수 `DAILY_TRADE_MARKET_DIV`는 없앴다.
- **NXT DAG는 KST 평일 20:10 = UTC 평일 11:10이다.** NXT가 20:00에 끝나므로 그 뒤에 받으면 최근 행도 NXT
  마감가다. 18:10 KRX DAG와 겹치지 않아 토큰 발급도 부딪치지 않는다.
- **DAG 둘은 한 파일에서 `build_dag(exchange, ...)`가 만든다.** 파일 하나가 DAG 하나인 이 저장소의 관례에서
  벗어나지만, 걷기·복구·구멍 검사 로직(`walk_back`·`collect_stocks`)을 복사하지 않으려는 선택이다. 처음에는
  로직을 `modules/`로 옮겨 DAG 파일 둘로 나누려 했다. 그러면 `walk_back`이 던지는 `AirflowFailException`을
  Airflow를 import하지 않는 `modules/` 밖으로 빼야 해서 옮기는 일이 커지고, 그 이동은 회귀 원인을 못 가르는
  큰 변경이다. 시장 코드·스케줄·이름·바닥 날짜만 다르니 공장 함수 하나가 더 작다.
- **테이블 SQL 넷(`upsert`·`count_stored`·`select_missing_open_days`·`select_close_conflicts`)은 표만 바꿔
  `stock_investor_trade_daily_nxt/`에 복제했다.** 수집기가 `TRADE_DAILY_SQL[거래소]`로 고른다. 어느 표에 쓸지는
  응답(`StockTradeDailyFetch.exchange`)이 정한다.
- **원장 조회 이름도 갈랐다**(`investor_trade_by_stock_daily_nxt`). 어느 표를 채운 수집인지 계보만 보고 안다.
- **소급 조정 감지는 NXT에도 그대로 쓴다.** 수정주가는 거래소와 무관하게 과거를 다시 쓴다. 대조는 거래소별로 한다.
- **NXT의 시작일은 `2025-03-24`다**(`NXT_FIRST_TRADE_DATE`, DAG의 `BACKFILL_START_DATES`). 이 날 이후 두
  종목은 빈 행이 없어 지금의 "0행·0값은 정상" 규칙을 건드리지 않아도 된다. KRX의 `IDENTITY_EPOCH`(2018-12-10)는
  KRX 항등식 문제라 따로다.
- **백필은 종목당 13장쯤이다**(2025-03-24부터 2026-09-18까지 약 375 거래일 ÷ 30). 기존 `end_date`·`pages`
  파라미터로 돌린다.
- **읽는 쪽은 만들지 않는다.** NXT 소비자가 아직 없다. 쌓기만 한다.

**KRX 쪽에서 안 바뀌는 것.** 최근 행 종가가 KRX 종가가 아니라 마지막 체결가인 문제는 이 설계의 범위 밖이다.
KRX 15:30 분봉 종가로 보정하는 것은 따로 정할 일이다.

## 5. 저장 위치 — 표를 따로 둔다 (결정 2026-09-20)

**같은 표에 `exchange` 열을 더할지, NXT용 표를 따로 둘지.**

- **축으로 더한다** — `stock_bar`가 2026-08-18에 그렇게 했다(한 표에 두 거래소, 한 쿼리로 읽는다).
  자연키가 `(provider, stock_code, exchange, business_date)`가 되고 기존 행은 `KRX`로 채운다.
  **블로커는 읽는 쪽이다.** 이 표를 `FROM`·`JOIN`으로 읽는 SQL이 열둘이다(2026-09-20 실측). 자기 폴더에 아홉
  (`count_stored`·`select_close_conflicts`·`select_horizon_return`·`select_intraday_horizon_return`·
  `select_latest`·`select_missing_open_days`·`select_previous_close`·`select_session_return`·
  `select_thesis_flows`)과 밖에 셋(`technical/select_history`·`kospi_tools/select_factor_stock`·
  `stock_bar/select_nxt_after_hours`)이다. 하나라도 `exchange = 'KRX'`를 빠뜨리면 같은 날짜가 두 행으로 나와
  종가·SMA가 조용히 틀린다. 오류로 안 보인다.
- **표를 따로 둔다**(`stock_investor_trade_daily_nxt`). 기존 읽는 쪽이 하나도 안 바뀌어 위험이 없다. 대신 컬럼이
  같은 표 둘이 생겨 유지비가 는다. NXT가 기준이 될 때 읽는 쪽이 표를 고르면 된다.

**표를 따로 두기로 했다.** 소비자가 아직 없어 얻는 것이 적고, 잃는 것이 조용한 오류의 위험이다.
NXT가 실제로 기준이 되는 날 축으로 합치는 마이그레이션이 가능하고 되돌리기도 쉽다.
두 표가 어긋나지 않는지는 `tests/models/test_market_models.py`(컬럼 이름·타입·널 여부)와
`tests/migrations/test_investor_flow_schema.py`(실제 DDL)가 본다.

## 6. 구현과 배포 순서

**구현(끝남, 2026-09-20).** 전체 테스트 2,391개와 ruff가 통과했고, 실제 `DagBag`이 DAG 49개를 오류 없이
읽었다.

1. 모델 `StockInvestorTradeDailyNxt`와 리비전 `a5d3f8c21b96` — 리비전은 빈 sqlite에서 autogenerate로
   뽑아 손으로 옮겼다(운영 DB에 접속하지 않는다). 테이블·컬럼 주석이 모델과 같다.
2. 수집기가 거래소를 생성자로 받고, SQL 넷과 원장 조회 이름을 거래소로 고른다.
3. `build_dag`가 DAG 둘을 만들고 `collect_stocks`가 걷기·복구·구멍 검사를 한 벌로 돈다.
4. README의 DAG 개수(48→49, 수집 35→36)와 하루 흐름 표, `docs/operations.md`의 DAG 목록.

**배포(남음, 사용자).** 순서가 중요하다 — 표가 없는 채로 DAG가 돌면 없는 표에 INSERT하다 죽는다.

1. `just migrate upgrade head` — 표를 만든다. 운영 DB 반영은 사용자가 한다.
2. NAS에서 코드를 받는다(`airflow/`가 컨테이너에 마운트돼 있으면 DAG·SQL이 함께 바뀐다).
3. Airflow에서 `kis_investor_trade_nxt_daily`를 켠다(새 DAG는 pause 상태일 수 있다).
4. NXT 백필 — 종목당 13장쯤이라 `pages: 20`이면 시작일에서 멈춘다:

       airflow dags trigger kis_investor_trade_nxt_daily \
         --conf '{"end_date": "2026-09-18", "pages": 20}'

5. 대조 — `source_record`의 `investor_trade_by_stock_daily_nxt` 건수(종목당 13장쯤)와, NXT 표의 종가가
   `stock_bar`의 NXT 마지막 봉 종가와 같은지 본다. API 응답으로는 2026-09-11~18 여섯 거래일 × 두 종목이
   이미 그렇게 일치했다.

**하지 않은 것.** NXT를 읽는 쪽(브리핑·지표·툴)은 만들지 않았다. 소비자가 생길 때 그 쪽이 표를 고른다.
KRX 일봉의 가장 최근 행 종가가 KRX 종가가 아닌 문제도 이 설계의 범위 밖이다.
