# 삼성전자·SK하이닉스 KIS 1분봉

> 작성 기준: 2026-08-11 / 본문 구현 반영: 2026-08-26
> 상태: **대부분 구현 완료. 남은 것은 9절 `kis_equity_backfill` 백필 DAG 하나다.**
> 대상: 삼성전자(`005930`), SK하이닉스(`000660`)의 KRX·NXT 체결

**무엇이 있고 무엇이 없나**

| 절 | 무엇 | 상태 |
| --- | --- | --- |
| 3 | KIS REST·WebSocket API 계약 | 유효 — 지금 코드가 이 계약을 지킨다 |
| 5 | 저장 모델과 우선순위 | 구현 완료(`apps/models/market/series.py`의 `StockBar`) |
| 7 | WebSocket 상주 수집기 | 구현 완료 — **위치는 `apps/realtime/`이다**(아래) |
| 8 | 마감 후 REST 확정 | 구현 완료(`kis_stock_minute_bars_daily`, 30분 백업은 `kis_equity_bar_reconcile`) |
| 9 | 백필 DAG | **미구현.** 이 문서에서 아직 안 만든 유일한 부분이다 |
| 11 | 배포 | 구현 완료 — 별도 스택 `compose/prod/`다(아래) |

**작성 당시 계획과 실제가 갈린 곳 셋.** 아래 본문은 전부 실제 기준으로 고쳐 뒀고, 왜 갈렸는지만
여기 남긴다.

1. **저장은 거래소별 물리 테이블이 아니라 단일 `stock_bar` + `exchange` 자연키 축이다**
   (커밋 `e6cf001`, 2026-08-18). 컬럼이 같은 테이블 둘을 두는 것보다 축 하나가 싸고, 브리핑과
   추론이 두 거래소를 한 쿼리로 읽는다.
2. **상주 수집기는 `airflow/modules/collectors/kis_realtime.py`가 아니라 `apps/realtime/`이다**
   (2026-08-19). Airflow가 실행하지 않는 코드는 `airflow/` 아래 두지 않는다는 규칙이 그 뒤에
   확정됐다. 실행은 `python -m apps.realtime.main`, 배포는 별도 스택 `compose/prod/`
   (개발은 `compose/local/realtime/`), 설정은 FastAPI와 같은 `config.yaml`(`apps.core.config`),
   저장은 `apps.models` ORM(`apps/realtime/repository.py`)이다.
3. **WS 세션 필터는 4.2가 적었던 NXT 3분할 창이 아니라 단일 창이다**(KRX 09:00~15:30,
   NXT 08:00~20:00). 실측에서 애프터마켓이 15:40이 아니라 15:30부터였고, **REST 저장 범위와
   같아야 WS에만 구멍이 생기지 않는다.** `apps/realtime/aggregator.py`의 `SESSION_WINDOWS`가
   원본이고 `tests/realtime/test_kis_realtime.py`가 Airflow 수집기 상수와 대조한다.

## 1. 결론

삼성전자와 SK하이닉스의 KRX·NXT 체결을 거래소별 1분 OHLCV로 저장한다. 실시간 주 경로는
WebSocket이고, REST는 **완료된 과거 분봉만** 다시 받아 누락과 불완전 봉을 확정한다.

- 저장 테이블: `stock_bar` 하나. 거래소는 `exchange`(KRX/NXT) 컬럼이고 자연키의 한 축이다
- 저장 코드: `stock_code`에 6자리 종목코드(`005930`, `000660`). `instrument.ticker`·수급·공시와 같은 체계라 한 화면에서 조인된다
- 거래소: KRX(`J`)와 NXT(`NX`)를 `exchange` 값으로 분리. 통합 `UN`은 받지 않는다
- 장중 수집: 공용 `kis-realtime` 서비스가 WebSocket 체결을 1분 OHLCV로 집계
- 누락 복구: 기존 `kis_quote_intraday` DAG가 5분마다 REST **완료 봉**을 확정 upsert
- 과거 백필: 정규 수집과 격리된 `kis_equity_backfill` 수동 DAG가 담당
- 데이터 우선순위: REST 확정 봉 > WebSocket 잠정 봉
- 제외: KRX+NXT 통합(`UN`) 시세, KRX 시간외 체결, 체결 틱 원본 영구 보관

핵심 불변식은 다음과 같다.

1. 진행 중인 현재 분은 REST 테이블에 저장하지 않는다.
2. REST가 확정한 행을 늦게 도착한 WebSocket 봉이 다시 덮을 수 없다.
3. 장중 프로세스가 분 중간에 시작하거나 끊기면 그 분의 WebSocket 봉은 저장하지 않는다.
4. 한 종목·거래소의 실패가 다른 종목·거래소·기존 지수·선물 저장을 막지 않는다.
5. 장기 백필이 정규 5분 수집의 DAG run을 점유하지 않는다.

## 2. 현재 구현과 이번에 추가할 경계

이미 있는 것:

- `airflow/modules/collectors/kis.py`: REST access token 캐시, 공통 GET, KIS 오류 처리,
  KST→UTC 변환, `source_record` 저장, 분봉 upsert 패턴
- `airflow/dags/kis_quote_intraday.py`: 평일 08:00~16:59 KST, 5분 폴링,
  종목별 HTTP 실패 기록과 401 토큰 재발급
- `apps/models/market/series.py`: OHLCV·계보·멱등 키 패턴인 매크로 봉 테이블들
- `apps/models/raw.py`: `source_type=websocket`, `status=running|succeeded|failed|quarantined`
- `apps/models/reference.py`: `QuoteSymbolKind.EQUITY`
- `websockets` 백엔드 의존성과 `kis_websocket_domain` 애플리케이션 설정

이번 구현에서 새로 만드는 경계:

- 국내 주식·거래소 Enum과 REST 요청 모델
- `stck_*` REST 응답 모델과 당일·과거 분봉 함수
- KRX·NXT별 46필드 WebSocket 프레임 모델
- 타이머 기반 1분봉 집계기와 공용 상주 서비스
- REST/WS 우선순위를 보장하는 거래소별 upsert
- 정규 조정 task와 별도 백필 DAG
- WebSocket 연결 세션과 REST run의 `source_record` 생명주기
- 재연결·지연 틱·부분 실패·운영 정체를 검출하는 테스트와 조회

## 3. 공식 API 계약

### 3.1 실행 환경과 공통 REST 헤더

1차 배포는 **실전투자 환경만** 지원한다. 현재 수집기도 운영 REST 도메인을 사용하고,
과거 분봉 공식 예제도 실전계좌의 최대 응답 수만 명시한다. 모의투자 키나 도메인이 들어오면
조용히 운영 TR ID를 쓰지 않고 시작 단계에서 거부한다.

공통 REST 요청 헤더:

```text
content-type: application/json; charset=utf-8
authorization: Bearer <access_token>
appkey: <app_key>
appsecret: <app_secret>
tr_id: <endpoint TR ID>
custtype: P
tr_cont: ""
```

`tr_cont`는 공식 공통 helper와 맞춰 빈 문자열로 보낸다. 두 분봉 API는 응답 헤더의 연속 조회
커서가 아니라 `FID_INPUT_HOUR_1`을 뒤로 이동해 페이지를 진행한다.

### 3.2 당일 분봉

공식 예제: [주식당일분봉조회](https://github.com/koreainvestment/open-trading-api/blob/main/examples_llm/domestic_stock/inquire_time_itemchartprice/inquire_time_itemchartprice.py)

| 항목 | 값 |
| --- | --- |
| Method | `GET` |
| Path | `/uapi/domestic-stock/v1/quotations/inquire-time-itemchartprice` |
| TR ID | `FHKST03010200` |
| 실전·모의 | 같은 TR ID; 이 프로젝트 1차 배포는 실전만 사용 |
| 최대 응답 | 30봉 |
| 범위 | 당일 |
| 시장 코드 | `J` KRX, `NX` NXT, `UN` 통합 |

요청값:

```text
FID_COND_MRKT_DIV_CODE=J 또는 NX
FID_INPUT_ISCD=005930 또는 000660
FID_INPUT_HOUR_1=HHMMSS
FID_PW_DATA_INCU_YN=Y
FID_ETC_CLS_CODE=
```

공식 예제는 `output2` 첫 행의 `cntg_vol`이 현재 분 첫 체결 전까지 이전 분 체결량으로 보일 수
있다고 경고한다. 따라서 REST 조정은 응답의 첫 행을 무조건 저장하지 않고 다음 확정 경계를
적용한다.

```text
completed_before = floor(request_completed_at in KST, 1 minute)
저장 조건 = bar_at < completed_before
```

예를 들어 10:05:12에 응답을 받으면 `10:05` 봉은 버리고 `10:04`까지 저장한다. 현재 분은 다음
5분 조정에서 이미 완료된 봉으로 다시 들어온다. 서버·네트워크 시각 오차를 줄이기 위해
`request_completed_at`은 응답을 실제로 받은 UTC 시각을 사용하고 KST로 변환한다.

### 3.3 과거 분봉

공식 예제: [주식일별분봉조회](https://github.com/koreainvestment/open-trading-api/blob/main/examples_llm/domestic_stock/inquire_time_dailychartprice/inquire_time_dailychartprice.py)

| 항목 | 값 |
| --- | --- |
| Method | `GET` |
| Path | `/uapi/domestic-stock/v1/quotations/inquire-time-dailychartprice` |
| TR ID | `FHKST03010230` |
| 최대 응답 | 실전계좌 120봉 |
| 보존 범위 | 공식 예제 기준 최대 1년, 실제 보관분만 반환 |
| 시장 코드 | `J` KRX, `NX` NXT, `UN` 통합 |
| 환경 | 공식 예제는 실전계좌 기준; 모의투자 지원을 가정하지 않음 |

요청값:

```text
FID_COND_MRKT_DIV_CODE=J 또는 NX
FID_INPUT_ISCD=005930 또는 000660
FID_INPUT_DATE_1=YYYYMMDD
FID_INPUT_HOUR_1=HHMMSS
FID_PW_DATA_INCU_YN=Y
FID_FAKE_TICK_INCU_YN=N
```

공식 계약에는 `NX`가 포함된다. 구현 전 실전 키로 확인할 것은 “지원 여부”가 아니라 권한,
실제 NXT 세션별 반환 범위, `previous_close`의 거래소별 의미다. 확인이 실패하면 NXT 백필만
feature flag로 끄고 KRX·정규 수집은 계속 배포한다.

두 REST API의 저장 필드:

| 저장 컬럼 | KIS 필드 |
| --- | --- |
| `bar_at` | `stck_bsop_date` + `stck_cntg_hour`를 KST로 해석 후 UTC 변환 |
| `open` | `stck_oprc` |
| `high` | `stck_hgpr` |
| `low` | `stck_lwpr` |
| `close` | `stck_prpr` |
| `volume` | `cntg_vol` |
| `previous_close` | **REST 응답이 아니라 `stock_investor_trade_daily`의 직전 거래일 종가** |

`stock_bar`에는 선물 월물 개념이 없으므로 `contract_code` 컬럼을 만들지 않는다.

**`previous_close`를 KIS 응답(`output1.stck_prdy_clpr`)에서 읽지 않는다.** REST 경로도
WebSocket 경로도 일별 DAG처럼 `stock_investor_trade_daily`에서 읽는다. 그래야 상주 수집기가
REST access token 없이 approval key만으로 돌고, 두 경로의 분모가 어긋나지 않는다.

### 3.4 WebSocket approval key와 구독

공식 인증 구현: [KIS WebSocket 인증·프레임 처리](https://github.com/koreainvestment/open-trading-api/blob/main/examples_llm/kis_auth.py)

approval key 요청:

```text
POST <KIS_REST_DOMAIN>/oauth2/Approval
content-type: application/json; charset=utf-8

{
  "grant_type": "client_credentials",
  "appkey": "<app_key>",
  "secretkey": "<app_secret>"
}
```

REST access token과 WebSocket approval key는 서로 대체할 수 없다. approval key는 프로세스
메모리에 보관하고 인증 거절 또는 24시간 만료 전에 다시 발급한다. 단순 네트워크 재연결마다
새로 발급하지 않는다.

운영 WebSocket URL은 설정값에 경로가 없으면 다음처럼 구성한다.

```text
<KIS_WEBSOCKET_DOMAIN>/tryitout
```

구독 JSON:

```json
{
  "header": {
    "content-type": "utf-8",
    "approval_key": "<approval_key>",
    "custtype": "P",
    "tr_type": "1"
  },
  "body": {
    "input": {
      "tr_id": "H0STCNT0 또는 H0NXCNT0",
      "tr_key": "005930 또는 000660"
    }
  }
}
```

| 거래소 | 공식 예제 | TR ID | 구독 수 |
| --- | --- | --- | --- |
| KRX | [국내주식 실시간체결가 KRX](https://github.com/koreainvestment/open-trading-api/blob/main/examples_llm/domestic_stock/ccnl_krx/ccnl_krx.py) | `H0STCNT0` | 2종목 |
| NXT | [국내주식 실시간체결가 NXT](https://github.com/koreainvestment/open-trading-api/blob/main/examples_llm/domestic_stock/ccnl_nxt/ccnl_nxt.py) | `H0NXCNT0` | 2종목 |

공식 helper의 한 연결 구독 상한은 40개다. 이번 범위는 4개지만 다른 KIS 수집이 같은 공용
연결에 채널을 추가하므로 구독 registry가 시작 전에 합계를 검증한다.

### 3.5 WebSocket 프레임 계약

데이터 프레임의 논리 구조:

```text
0 또는 1 | TR_ID | RECORD_COUNT | FIELD_1 ^ ... ^ FIELD_46 [반복]
```

KRX와 NXT 체결은 모두 46필드지만 완전히 같은 스키마는 아니다. KRX의 `CCLD_DVSN` 위치에
NXT는 `CNTG_CLS_CODE`를 둔다. 따라서 TR ID별 필드 목록을 별도로 고정하고 다음을 검증한다.

- 지원 TR ID인지
- `RECORD_COUNT >= 1`인지
- 레코드 수와 파싱한 행 수가 같은지
- 각 레코드가 정확히 46필드인지
- `MKSC_SHRN_ISCD`가 구독 대상인지
- `BSOP_DATE`, `STCK_CNTG_HOUR`, `STCK_PRPR`, `CNTG_VOL`이 유효한지

봉 집계에 쓰는 공통 필드:

```text
MKSC_SHRN_ISCD
BSOP_DATE
STCK_CNTG_HOUR
STCK_PRPR
CNTG_VOL
```

JSON 제어 프레임은 구독 ACK/NACK와 `PINGPONG`을 구분한다. 네 구독 모두 `rt_cd=0` ACK를
받기 전에는 서비스가 ready 상태가 아니다. 인증 거절은 approval key를 한 번 갱신해 재연결하고,
종목별 거절은 해당 시계열만 비활성화해 나머지 구독을 유지한다. `PINGPONG`에는 공식 helper와
같이 즉시 pong을 보낸다.

`encrypt=Y` 데이터는 평문으로 파싱하지 않는다. ACK가 준 key/iv로 복호화할 수 있는 채널만
처리하고, 키가 없거나 복호화가 실패하면 프레임을 `quarantined`로 집계한 뒤 재연결한다.

## 4. 수집 대상과 거래 세션

### 4.1 Enum과 요청 모델

`airflow/modules/collectors/kis.py`에 대상과 라우팅을 분리한다.

```python
class DomesticEquity(StrEnum):
    SAMSUNG_ELECTRONICS = ("SAMSUNG_ELECTRONICS", "005930", "삼성전자")
    SK_HYNIX = ("SK_HYNIX", "000660", "SK하이닉스")

class EquityVenue(StrEnum):
    KRX = ("J", "H0STCNT0")
    NXT = ("NX", "H0NXCNT0")
```

코드 집합은 Enum으로 고정하고, 당일·과거 요청값은 Pydantic `BaseModel`로 묶어 날짜·시각·시장
코드 조합을 호출 전에 검증한다. 테이블 이름은 Enum에서 SQL 문자열로 직접 조립하지 않고,
허용된 SQL 상수로 라우팅한다.

### 4.2 거래 세션

| 거래소 | 저장 대상 체결 시각(KST, 분 기준 양끝 포함) |
| --- | --- |
| KRX | `09:00` ~ `15:30` |
| NXT | `08:00` ~ `20:00` |

**거래소마다 창 하나다.** 이 문서 초안은 NXT를 프리(08:00~08:50)·메인(09:00:30~15:20)·
애프터(15:40~20:00) 셋으로 나눴는데 실측에서 애프터가 15:30부터였고, 무엇보다 **REST 저장
범위와 창이 같아야 WebSocket 쪽에만 구멍이 생기지 않는다.** 세션 사이 공백에는 체결이
안 오므로 창을 좁힐 이유가 없다.

원본은 `apps/realtime/aggregator.py`의 `SESSION_WINDOWS`이고, Airflow REST 수집기의 같은
상수와 어긋나지 않는지 `tests/realtime/test_kis_realtime.py`가 대조한다. 이 중복은 의도된
것이다 — `apps/`와 `airflow/`는 서로를 import하지 않는다(저장소 규칙).

NXT 거래시간 출처: [넥스트레이드 거래제도](https://www.nextrade.co.kr/menu/transactionSys.do)

`bar_at`은 체결 시각을 1분 아래로 절삭한 값이다. 휴지 구간에는 봉을 만들지 않고, 체결이
전혀 없는 분에도 0거래량 봉을 합성하지 않는다.

평일 조건은 불필요한 연결을 줄이는 1차 필터다. 휴장일 캘린더를 이 기능에서 새로 만들지 않으며,
평일 휴장일에는 구독 ACK 이후 데이터가 없는 상태를 정상 idle로 기록한다.

## 5. 데이터 모델과 쓰기 우선순위

### 5.1 저장 테이블 — `stock_bar`

`apps/models/market/series.py`의 `StockBar`다.

| 컬럼 | 타입 | 설명 |
| --- | --- | --- |
| `provider` | text | `kis` 또는 `yahoo` |
| `stock_code` | text | 6자리 종목코드(`005930`, `000660`). 해외 상장은 저장 심볼(`TSMC_ADR`) |
| `exchange` | enum | `KRX`·`NXT`·`NYSE`·`NASDAQ`. **자연키의 한 축이다** |
| `bar_at` | timestamptz | 1분 시작 시각 UTC |
| `open`, `high`, `low`, `close` | numeric | 1분 OHLC |
| `volume` | bigint nullable | 해당 거래소의 1분 체결량 |
| `previous_close` | numeric | 직전 거래일 확정 종가. 변동률의 분모다 |
| `ingest_method` | text | `websocket` 또는 `rest` |
| `is_final` | boolean | REST가 완료 봉을 확정했는지 |
| `source_record_id` | bigint FK | 이 행을 마지막으로 갱신한 원천 |

자연키는 **`(provider, stock_code, exchange, bar_at)`** 이다. 거래소를 키에서 빼면 같은 종목의
KRX·NXT 체결이 같은 분에 서로를 덮어쓴다. `contract_code`는 두지 않는다 — 주식에는 선물
월물이 없다.

**`previous_close`는 NXT 봉도 KRX 확정 종가를 쓴다.** 전일 기준가가 거래소마다 따로 있지 않다.
값은 시작 시 REST를 부르지 않고 `stock_investor_trade_daily`에서 읽는다 — REST access token이
필요 없고 approval key만으로 상주 수집기가 돈다.

제약:

- `provider = 'kis'`
- `ingest_method IN ('websocket', 'rest')`
- `volume >= 0`
- `previous_close > 0`
- `low <= open`, `low <= close`, `high >= open`, `high >= close`, `low <= high`
- 자연키 UNIQUE와 `source_record_id` 인덱스·RESTRICT FK

### 5.2 확정 상태와 upsert 규칙

WebSocket이 분 경계에서 저장한 봉은 `ingest_method=websocket`, `is_final=false`다. 당일·과거
REST가 완료 봉을 저장하면 `ingest_method=rest`, `is_final=true`가 된다.

두 SQL 파일의 충돌 규칙:

```text
REST upsert
  - 신규 행 삽입 허용
  - 기존 websocket/rest 행 모두 갱신 허용
  - is_final=true로 전환

WebSocket upsert
  - 신규 행 삽입 허용
  - 기존 is_final=false 행만 갱신 허용
  - 기존 is_final=true 행은 절대 갱신하지 않음
```

이 규칙으로 `WebSocket 잠정 → REST 확정 → 늦은 WebSocket` 순서에서도 REST 값이 유지된다.
`source_record_id`는 행을 마지막으로 실제 갱신한 원천을 가리킨다. REST 확정 뒤에는 REST
`source_record`로 바뀌고, 이전 WebSocket 계보는 해당 연결 세션 레코드의 metadata와 로그에
남는다. 봉의 모든 수정 이력을 별도 테이블로 보존하는 것은 이번 범위가 아니다.

## 6. REST 파서와 검증

기존 `KisRawBar`, `KisChartHead`, `KisChartPayload`에 주식 필드를 추가하되, 선물·지수·주식
중 두 종류가 동시에 채워진 응답은 계약 오류로 거부한다.

필수 검증:

- `rt_cd != "0"`이면 `KisResultError`
- `output2` 자체가 비었으면 호출 목적에 따라 휴장 또는 payload 오류로 분류
- 직전 거래일 종가(`stock_investor_trade_daily`)를 못 찾거나 0 이하이면 실패
- OHLC가 유한한 숫자인지와 가격 관계가 유효한지
- `cntg_vol`이 정수이며 0 이상인지
- 요청한 종목·거래소 라우팅과 응답 날짜·세션이 일치하는지
- 현재 분과 세션 밖 봉을 저장하지 않는지
- 결과를 `bar_at` 오름차순으로 정렬하고 중복 시각을 제거하는지

당일 폴링은 유효한 응답에 완료 봉이 0건이면 정상이다. 과거 백필은 휴장일의 빈 응답을 정상으로
끝내지만, 거래일로 보이는 날짜에서 반복적으로 빈 응답이 나오는 경우 metadata와 경고 로그를
남긴다.

## 7. WebSocket 실시간 수집기

### 7.1 프로세스와 인증

`apps/realtime/`가 공용 연결 하나를 관리한다. Airflow task를 장시간 점유하지 않고
`python -m apps.realtime.main`으로 도는 상주 서비스다 — 운영 스택은 `compose/prod/`,
개발은 `compose/local/realtime/`다.

**`airflow/` 아래가 아니다.** Airflow가 실행하지 않는 코드는 그 트리에 두지 않는다는 규칙이
2026-08-19에 확정됐다. 모듈은 넷이다 — `frames.py`(TR ID별 46필드 계약과 파싱),
`aggregator.py`(1분봉 집계와 세션 창), `repository.py`(ORM 잠정 upsert),
`service.py`·`main.py`(연결·구독·재연결).

시작 순서:

1. 환경 변수와 실전 환경 여부를 검증한다.
2. 기존 `access_token()`에 Airflow `Variable` 저장소를 연결해 REST token을 재사용한다.
3. 네 시계열의 당일 REST를 호출해 `previous_close`를 캐시한다.
4. approval key를 발급하고 WebSocket에 연결한다.
5. KRX 2개, NXT 2개를 구독하고 ACK를 확인한다.
6. 모든 활성 구독 ACK 뒤 ready heartbeat를 기록한다.

초기 REST가 실패한 시계열은 전일종가를 추정하지 않고 WebSocket 저장만 비활성화한다. 다른
시계열은 계속 수집하며 다음 5분 REST 조정이 해당 시계열을 채운다. 재연결에서는 캐시된
previous close와 approval key를 재사용하되 인증 거절 시에만 approval key를 갱신한다.

### 7.2 분봉 집계

키는 `(venue, symbol, bar_at)`이다.

```text
open   = 이벤트 시각이 가장 이른 체결의 STCK_PRPR
high   = max(STCK_PRPR)
low    = min(STCK_PRPR)
close  = 이벤트 시각이 가장 늦은 체결의 STCK_PRPR
volume = sum(CNTG_VOL)
```

같은 이벤트 시각의 여러 체결은 프레임 수신 순서로 open/close를 결정한다. 현재 열린 분보다 과거
분의 늦은 틱은 이미 저장한 봉에 더하지 않고 `late_tick_count`만 증가시킨다. KIS 체결 프레임에는
재연결 전후 중복을 확실히 제거할 전역 체결 ID가 없으므로, 분 중간 연결·재연결 뒤 첫 불완전
분은 저장하지 않고 REST 조정에 맡긴다.

### 7.3 타이머 기반 flush

다음 분 첫 체결을 기다리지 않는다. 독립 타이머가 매 분 경계 후
`WS_FINALIZATION_DELAY_SECONDS`만큼 기다린 뒤 직전 분을 flush한다. 초기값은 3초이며 설정으로
조정할 수 있다.

- 지연 시간 안에 도착한 직전 분 틱은 집계에 포함
- flush 이후 도착한 과거 틱은 무시하고 지표만 증가
- 체결이 없던 분은 행을 만들지 않음
- 정상 장 종료에서는 열린 마지막 분을 지연 시간 뒤 flush
- SIGTERM·비정상 종료가 분 중간이면 현재 분을 flush하지 않음
- 계획된 종료 시에도 아직 분 경계를 지나지 않은 봉은 REST에 맡김

이 규칙으로 “분 종료 후 수초 안에 저장”과 “불완전 봉을 완성 봉처럼 저장하지 않음”을 함께
지킨다.

### 7.4 재연결과 구독 상태

- 네트워크 종료: jitter를 포함한 exponential backoff, 상한 60초
- 재연결: 활성 registry 전체 재구독, ACK 재확인
- 인증 거절: approval key 한 번 갱신 후 재연결
- 종목별 NACK: 해당 시계열 비활성화, 다른 구독 유지
- 알 수 없는 TR ID·필드 수 오류: 프레임 격리와 경고, 임계치 초과 시 재연결
- `PINGPONG`: 즉시 pong
- 서비스 종료: 마지막 연결 세션 `source_record`를 종료하고 DB 연결 정리

## 8. REST 조정 DAG

기존 `kis_quote_intraday`의 schedule을 평일 08:00~20:00 KST, 5분 간격으로 넓힌다. 하나의
거대한 collect 함수에 모든 상품을 넣지 않고 최소 두 task로 분리한다.

```text
collect_existing_bars
  - 기존 지수·선물
  - 기존 거래시간 밖에서는 정상 skip

reconcile_equity_bars
  - KRX·NXT 두 종목
  - 거래소별 활성 세션과 마감 직후에만 호출
```

정규 조정 규칙:

1. 종목×거래소를 독립 outcome으로 호출한다.
2. `request_completed_at`의 현재 분을 제외하고 완료 봉만 파싱한다.
3. 거래소별 SQL과 savepoint를 사용해 한 저장 실패가 다른 시계열을 rollback하지 않게 한다.
4. REST upsert는 성공한 봉을 `is_final=true`로 만든다.
5. HTTP 401은 access token을 한 번 재발급한다.
6. 한 종목의 400/403/404와 본문 오류는 해당 outcome 실패로 기록한다.
7. 자격증명 누락·DB 연결 실패·모든 시계열 실패만 task 전체 실패로 처리한다.
8. 응답은 정상이나 완료 봉이 0건이면 성공으로 기록한다.

마감 봉을 확정할 수 있도록 각 세션 종료 뒤 첫 스케줄에서도 해당 거래소를 한 번 더 호출한다.
NXT 휴지 구간에는 새 봉을 만들지 않지만 직전 세션의 완료 봉 조정은 허용한다.

### 구현 (2026-08-25)

조정을 `kis_quote_intraday`의 태스크가 아니라 **별도 DAG `kis_equity_bar_reconcile`로**
넣었다. 위 규칙에서 실제 코드와 다른 점을 여기 남긴다.

- **DAG를 나눴고 `kis_quote_intraday`는 손대지 않았다.** 지수·선물은 5분마다 새 봉을 만드는
  수집이고 조정은 이미 있는 봉을 확정하는 백업이라 앞단과 실패 성격이 다르다. 주기가 다르면
  한 DAG에 둘 수 없고, 한 DAG에 두고 실행 분으로 갈라 돌리면 모드가 시계에서 나온다.
- **주기는 5분이 아니라 30분이다.** WebSocket이 이미 상시 도는 원천이라 REST는 백업이다.
  한 호출이 최근 120봉(두 시간)을 덮으므로 30분이면 구멍은 다음 실행이 반드시 메운다.
  5분이면 같은 창을 24번 겹쳐 돌고 호출만 여섯 배다(하루 ~570 대 ~96).
- **틱은 매시 05·35분이다**(`5,35 8-19 * * 1-5`). 정각에 돌면 KRX 마감(15:30) 봉이 아직
  완결되지 않아 그날 마지막 봉만 잠정으로 남는다. 20시대는 열지 않는다 — NXT 마지막
  봉(20:00)은 20:05에 도는 확정 DAG의 몫이다.
- **한 번에 한 호출만 한다**(`fetch_stock_bars(max_calls=1)`). 두 시간보다 오래된 구멍은
  어차피 확정 DAG가 메운다.
- **진행 중인 분은 저장하지 않는다**(`fetch_stock_bars(until=...)`). REST upsert가
  `is_final=true`로 굳히기 때문에 그 분을 넣으면 부분 봉이 확정으로 남는다. 커서도 마감이
  아니라 그 분에서 시작한다 — 미래 시각을 물었을 때 KIS가 무엇을 돌려주는지는 계약에 없다.

규칙 3(거래소별 savepoint)은 거래소마다 트랜잭션 하나(`atomic`)로 구현했다. 규칙 7(전부
실패했을 때만 태스크 실패)은 그대로다 — 30분 뒤 같은 태스크가 다시 보므로 한 번의 실패로
죽이면 경보만 늘고 고쳐지는 것은 없다.

**아직 실측하지 않은 것**: 장중에 일자별 조회(`FHKST03010230`)로 오늘을 물었을 때 오는
봉의 범위. 커서를 현재 분으로 두므로 정상 응답이면 최근 120봉이 와야 한다.

## 9. 별도 백필 DAG

백필은 `schedule=None`인 `kis_equity_backfill` DAG로 분리한다. 기존
`kis_quote_intraday.max_active_runs=1`과 경쟁하지 않으므로 장기 백필 중에도 정규 5분 run이
계속 돈다.

파라미터:

| 이름 | 규칙 |
| --- | --- |
| `backfill_start` | 필수, `YYYY-MM-DD` |
| `backfill_end` | 필수, `YYYY-MM-DD`, 시작일 이상 |
| 최대 범위 | 한 run에 31일 |
| 보존 한계 | 실행일 기준 1년보다 오래된 시작일 거부 |
| 대상 | 두 주식×KRX·NXT, 필요 시 venue/symbol 선택 파라미터 추가 가능 |

페이지 진행 규칙:

1. 종료일부터 시작일까지 날짜별로 처리한다.
2. 첫 기준 시각은 KRX `15:30:00`, NXT `20:00:00`이다.
3. 응답 중 요청 날짜·거래소 세션·전체 요청 범위에 든 봉만 보관한다.
4. 가장 이른 유효 봉의 1분 전을 다음 `FID_INPUT_HOUR_1`로 사용한다.
5. KRX `09:00`, NXT `08:00` 이전, 빈 응답, 중복 페이지, 커서 무진행이면 날짜를 끝낸다.
6. 페이지마다 최대 120봉과 중복 시각 제거를 검증한다.
7. REST 확정 upsert가 중복 실행을 흡수한다.

백필은 별도 `kis_api` Airflow pool을 사용한다. 호출 간 최소 간격은 실전 제한을 측정해 설정으로
고정하고, `EGW00201`·429·일시적 5xx에는 지수 backoff와 jitter를 적용한다. 종목·거래소·날짜
단위로 `source_record`를 완료하므로 실패한 날짜만 같은 파라미터로 재실행할 수 있다.

수동 실행 예:

```bash
airflow dags trigger kis_equity_backfill \
  --conf '{"backfill_start":"2026-07-01","backfill_end":"2026-07-31"}'
```

31일보다 넓은 범위는 여러 run으로 명시적으로 나눈다. 백필 DAG 자체는 `max_active_runs=1`로
두어 동일 테이블에 여러 장기 run이 동시에 쓰지 않게 한다.

### 검토 (2026-08-27) — 이 절의 절반은 이미 다른 DAG에 있다

이 절을 착수하려고 코드와 운영 DB를 다시 읽었더니 **`kis_equity_backfill`이라는 이름의 DAG는
없지만 그 일의 대부분을 `kis_stock_minute_bars_daily`가 이미 한다.** 새 DAG를 짓기 전에
무엇이 진짜로 없는지부터 가른다.

**이미 있는 것**

| 이 절이 말한 것 | 어디에 있나 |
| --- | --- |
| 페이지 커서 일곱 단계 | `KisQuoteCollector.fetch_stock_bars` — 커서는 그 거래일에 속한 가장 이른 봉의 1분 전, 날짜·세션 밖 봉 폐기, 중복 시각 제거, 거래소별 호출 상한 |
| 첫 기준 시각 KRX 15:30 · NXT 20:00 | `StockExchange.last_bar` |
| 날짜 역방향 순회 | `kis_stock_minute_bars_daily`의 `business_date` + `days` (`target = business_date - offset`) |
| 한 run 31일 상한 | 같은 DAG의 `MAX_DAYS = 31`. `Param.maximum`과 `requested_days()` 둘 다 막는다 |
| 종목·거래소·날짜 단위 트랜잭션 | 같은 DAG의 저장 루프. 날짜 하나가 커밋 하나다 |
| REST 확정 upsert의 중복 흡수 | `stock_bar/upsert.sql`, `is_final` 규칙(5.2절) |
| 실패한 날짜만 재실행 | 실패를 모아 판정하고 upsert가 멱등이라 같은 파라미터로 다시 돌린다 |

**운영 실측 (2026-08-27, 읽기 전용)**

```
stock_bar   005930/000660 × KRX·NXT   2026-08-18 ~ 08-27  (8거래일, KRX 381봉/일)
stock_investor_trade_daily            2018-12-10 ~ 08-26  (1,892행)
```

봉은 여드레치뿐이지만 **전일종가는 막히지 않는다.** `last_settled_close`가 읽는
`stock_investor_trade_daily`가 2018년까지 있어, 과거 어느 날짜를 백필해도 분모가 있다.
"확정 일별 수급이 먼저 돌아야 한다"는 제약은 과거 구간에서는 이미 충족돼 있다.

**그래서 남은 값어치는 셋뿐이다**

1. **정규 확정 run 점유.** 지금은 백필과 그날 확정이 같은 DAG이고 `max_active_runs=1`이라,
   31일 run 하나가 20:05 확정을 밀어낸다. 그 DAG의 docstring도 그것을 인정하고 있다.
   별도 DAG(또는 별도 pool)로 나누면 사라진다.
2. **보존 한계 거부.** 이 절은 "실행일 기준 1년보다 오래된 시작일 거부"를 말하지만 코드에
   없다. **그 1년이라는 값 자체가 미측정이다** — 아래 열린 질문.
3. **backoff와 jitter.** `EGW00201`·429·일시적 5xx에 지수 backoff를 적용하자고 했는데
   지금은 재시도가 Airflow 태스크 단위뿐이다. 31일 run은 종목 2 × 거래소 2 × 하루 약 4콜
   = 하루 16콜, 31일이면 500콜에 가까워 정규 수집보다 호출 밀도가 훨씬 높다.

**보존 한계 실측 (2026-08-27, 005930 KRX, 조회 전용)**

```
2026-08-26 (어제)      120봉      2025-08-27 (정확히 1년 전)  120봉
2026-07-28 (1개월)     120봉      2025-08-12 (12.5개월)         0봉
2026-05-28 (3개월)     120봉      2025-07-28 (13개월)           0봉
2026-02-26 (6개월)     120봉      2025-05-27 (15개월)           0봉
                                  2024-08-27 (2년)              0봉
```

**롤링 1년이다.** 이 절이 추측으로 적어 둔 값이 맞았다. 경계 밖도 `rt_cd=0 정상처리`에
빈 `output2`로 답한다 — 애널리스트 의견의 100건 잘림과 같은 모양이라, **조회 실패와 휴장일과
보존 밖이 응답만으로는 구분되지 않는다.** 그래서 시작일 거부를 코드가 해야 한다.

그러면 백필은 실제로 큰 일이다. 약 250거래일 × (종목 2 × 거래소 2 × 하루 4콜) ≈ **4,000콜**,
31일 상한을 지키면 **run 열두 번**이다. 위 세 항목 중 ①과 ②는 값어치가 있다.

### 그래서 무엇을 만드나 (2026-08-27 결정) — 코드는 안 만들고, 데이터는 지금 받는다

두 사실이 반대 방향을 가리킨다.

**① 지금은 아무도 과거 분봉을 안 읽는다.**

| `stock_bar`를 읽는 곳 | 무엇을 보나 |
| --- | --- |
| `briefing/market_data.py` | 오늘 브리핑의 최신 봉과 당일 분봉 차트 |
| `thesis/intraday.py` | 기준 시각 **직전** 봉 하나 (당일) |
| `thesis/nxt_review.py` | 그날 NXT 애프터마켓 구간 |

채점의 호라이즌 등락률(`select_horizon_return.sql`·`select_intraday_horizon_return.sql`·
`select_session_return.sql`)은 전부 `stock_investor_trade_daily`, 즉 **일봉**에서 온다.
기술적 신호와 기저율도 일봉이다.

**② 그런데 분봉은 상한다.** 보존이 롤링 1년이라 오늘 안 받은 날짜는 내일 영영 사라진다.
일봉은 이 문제가 없다 — `index_daily`가 2016-08부터 있고 제공처가 계속 준다.

앞으로 만들 것이 **차트 서사**다(2026-08-27 사용자). "여기서 아래로 꺾였고 이 값을 지지선
삼아 횡보했다"를 시장·경제 사건과 잇고, 매물대로 "왜 저 값이 지지가 되는지 / 왜 저 값을
못 뚫는지"를 설명하는 것이다. 그 기능에서 해상도는 두 층으로 갈린다.

- **다년치 구조**(코로나 하락 같은 몇 년짜리 이야기) → **일봉이면 된다.** 이미 10년치가 있다.
- **정밀 매물대와 분 단위 지지·저항** → **분봉이 필요하고 최근 1년만 구할 수 있다.**

그래서 결론은 "만들지 않는다"가 아니라 **"코드는 안 만들고 데이터만 지금 확보한다"**이다.

**지금 하는 것 — 코드 0줄**

`kis_stock_minute_bars_daily`에 `business_date`와 `days`(≤31)가 이미 있다. 31일씩 **열두 번**
트리거하면 `2025-08-10 ~ 2026-08-16`이 찬다. 그 뒤 구간은 정규 수집이 이미 채웠다.

**오래된 구간부터 돌린다.** 보존 경계가 하루에 하루씩 앞으로 밀리므로 가장 먼저 사라질
것을 먼저 잡는다.

```bash
airflow dags trigger kis_stock_minute_bars_daily --conf '{"business_date": "2025-09-09", "days": 31}'
airflow dags trigger kis_stock_minute_bars_daily --conf '{"business_date": "2025-10-10", "days": 31}'
airflow dags trigger kis_stock_minute_bars_daily --conf '{"business_date": "2025-11-10", "days": 31}'
airflow dags trigger kis_stock_minute_bars_daily --conf '{"business_date": "2025-12-11", "days": 31}'
airflow dags trigger kis_stock_minute_bars_daily --conf '{"business_date": "2026-01-11", "days": 31}'
airflow dags trigger kis_stock_minute_bars_daily --conf '{"business_date": "2026-02-11", "days": 31}'
airflow dags trigger kis_stock_minute_bars_daily --conf '{"business_date": "2026-03-14", "days": 31}'
airflow dags trigger kis_stock_minute_bars_daily --conf '{"business_date": "2026-04-14", "days": 31}'
airflow dags trigger kis_stock_minute_bars_daily --conf '{"business_date": "2026-05-15", "days": 31}'
airflow dags trigger kis_stock_minute_bars_daily --conf '{"business_date": "2026-06-15", "days": 31}'
airflow dags trigger kis_stock_minute_bars_daily --conf '{"business_date": "2026-07-16", "days": 31}'
airflow dags trigger kis_stock_minute_bars_daily --conf '{"business_date": "2026-08-16", "days": 31}'
```

`max_active_runs=1`이라 열둘이 순서대로 돈다. 한 run이 약 500콜에 3~5분이니 전체가 한 시간
안쪽이고, **그날 20:05 확정 run과 겹치지 않게 마감·확정 수집이 끝난 뒤에 건다.** 실패한
구간은 같은 파라미터로 다시 돌리면 된다 — upsert가 멱등이다. 저장량은 250거래일 × 약
380봉 × 종목 2 × 거래소 2로 100만 행 안쪽이다.

**첫 run(2025-09-09)의 앞부분은 0봉으로 온다.** 보존 경계 밖이라 그렇고, 휴장일과 같은
모양이라 DAG가 건너뛴다. 정상이다.

#### 실행 결과 (2026-08-27)

열두 run을 전부 돌렸다. **52만 6천 봉, 1년치가 구멍 없이 찼다.**

```
005930 KRX    95,104봉   2025-08-18 ~ 2026-08-27   251거래일
005930 NXT   172,267봉                             251일
000660 KRX    94,704봉                             250일
000660 NXT   164,646봉                             240일
```

- **구멍 0일.** 일봉(`stock_investor_trade_daily`)이 있는 날 중 분봉이 없는 날이 없다.
- **251일 중 234일이 정확히 381봉**(정규장 전체)이다. 351·352봉인 날 여덟은 반차 거래일,
  380·379봉은 그 분에 체결이 없던 것이다.
- **미확정 봉은 24개뿐**이고 전부 그날 장중 것이다. 백필분은 전부 REST 확정본(`is_final`)이다.
- 실제 보존 경계는 **2025-08-18 언저리**였다. 프로브로 잡은 구간(08-12 0봉 / 08-27 120봉)
  안이고 롤링 1년이라는 결론은 그대로다.

**NXT가 KRX의 1.8배다.** 창이 08:00~20:00으로 길어서다(정규장 381봉 대 690봉 안팎).
000660 NXT만 240일로 열한 날 적은데 그 종목이 NXT에서 안 거래된 날이다. **매물대를 계산할
때 두 거래소를 합치면 안 되는 이유가 이 숫자에 있다** — 같은 종목이라도 체결이 갈리고
날짜 집합부터 다르다.

**안 만드는 것 — 백필 전용 DAG**

위에서 "만들 것"으로 적었던 셋(별도 DAG, 보존 거부, 루프 추출)은 백필을 **자주** 돌 때
값어치가 생긴다. 1년치를 한 번 채우고 나면 그 뒤로는 정규 수집이 매일 이어 붙이므로 다시
돌 일이 없다. 그때가 오면(예: 종목이 늘어 다시 1년을 채워야 할 때) 이 절의 실측값
—롤링 1년, 하루 16콜, 31일 run에 약 500콜—이 그대로 쓰인다.

**같은 논리가 다른 분봉에도 걸린다.** `index_bar`·`index_future_bar`·`fx_bar`·
`commodity_bar`도 8월 중순부터뿐이고, 제공처마다 보존이 다르다. 특히 Yahoo 1분봉은
공개적으로 최근 30일만 준다 — 국내 종목보다 창이 훨씬 좁다. 그쪽까지 확보할지는 차트
서사가 어느 심볼을 다루는지가 정해진 뒤에 판단한다. **분봉 백필 설계 문서가 따로 생기면
그 문서가 이 절을 대체한다.**

## 10. `source_record` 생명주기

### 10.1 WebSocket

물리 연결 하나를 세션 하나로 본다.

```text
연결 시도 시작
  → source_type=websocket
  → source=kis
  → source_key=kis_realtime
  → status=running

정상 종료 또는 연결 끊김
  → completed_at 설정
  → record_count=실제로 upsert한 잠정 봉 수
  → status=succeeded 또는 failed
  → metadata에 session_id, ACK, reconnect 사유, 프레임/지연틱/격리 수 저장
```

재연결은 새 물리 연결이므로 새 `source_record`를 만든다. 인증 실패나 네트워크 실패로 봉을 하나도
쓰지 못해도 실패 세션 레코드를 남긴다. 반복 프레임 원문과 비밀값은 payload에 저장하지 않는다.

### 10.2 정규 REST 조정

5분 task run 하나가 `source_type=api`, `source_key=inquire_time_itemchartprice` 레코드 하나를
만든다. metadata에 종목×거래소별 HTTP 상태, 봉 수, 최신 봉, 오류를 남긴다.

- 하나 이상 정상 파싱: `succeeded`
- 전부 실패: `failed`
- 완료 봉 0건인 정상 응답: `succeeded`, `record_count=0`
- 데이터 계약 위반을 격리하고 나머지를 저장한 경우: 전체 상태는 `succeeded`, outcome은
  `quarantined`로 metadata에 기록

### 10.3 백필

종목×거래소×조회일을 한 수집 단위로 두고 `source_key=inquire_time_dailychartprice`를 쓴다.
페이지 수, 최초·최종 봉, 중복 제거 수, 중단 사유를 metadata에 기록한다.

기존 insert SQL에 더해 running 세션을 종료할 `source_record/update.sql`을 추가한다. WebSocket은
세션 레코드를 먼저 commit하고 봉마다 짧은 트랜잭션을 사용한 뒤 종료 상태를 별도 commit한다.
REST 조정과 백필은 한정된 수집 단위의 source row·봉 upsert·완료 상태를 같은 트랜잭션에서
commit한다. 연결 전체를 감싸는 장기 DB 트랜잭션은 만들지 않는다.

## 11. 배포와 운영

> **2026-08-19 갱신 — 이 절의 배포 서술은 대체됐다.** 실서비스는 `compose/prod/`의 별도
> 스택으로 돌고, 배포는 NAS clone(`/volume1/docker/finance`)에 `just deploy`가 pull·up 한다.
> 현행 절차는 README의 `배포` 절이 기준이다. 아래 11.1은 대체 전 설계 기록이다.

### 11.1 compose 서비스

**Airflow 이미지를 공유하지 않는다.** 상주 수집기는 별도 스택이다 — 운영은 `compose/prod/`,
개발은 `compose/local/realtime/`. `websockets`는 루트 `pyproject.toml`이 갖고 Airflow
requirements와 무관하다.

`kis-realtime` 서비스:

```text
command: python -m apps.realtime.main
restart: unless-stopped
설정: config.yaml (apps.core.config) — FastAPI와 같은 파일
저장: apps.models ORM (apps/realtime/repository.py)
손잡이: KIS_ENABLE_NXT_REST, KIS_ENABLE_NXT_WEBSOCKET
연결 시간: 평일 07:50~20:10 KST
그 밖 시간: 프로세스는 살아 있고 연결하지 않은 채 대기
```

**설정이 Airflow 환경변수가 아니라 `config.yaml`이다.** 상주 서비스는 `apps/` 트리라
백엔드 규칙을 따른다(저장소 규칙 — 위치는 배포가 가르고 규칙은 트리가 가른다). WebSocket 설정값에 `/tryitout`이 이미 있으면 중복해 붙이지 않는다.
NXT 두 feature flag는 운영 계약 확인 전에는 `false`, 확인 뒤에는 독립적으로 `true`로 바꾼다.

### 구현 (2026-08-25)

`KIS_ENABLE_NXT_REST`는 `modules.collectors.kis.rest_exchanges()`가 읽고, 종목 봉 REST 수집이
그 결과를 그대로 돈다. 위 계획과 다른 점 둘이다.

- **두 손잡이 다 기본이 `true`다.** 계획은 확인 전 `false`였지만 NXT는 REST·WebSocket 양쪽
  다 이미 상시 수집 중이라, 기본을 `false`로 두면 손잡이를 넣는 변경만으로 수집이 조용히
  멈춘다. `KIS_ENABLE_NXT_WEBSOCKET`도 같은 날 기본을 `true`로 맞췄다 — 두 손잡이가 다르게
  동작하면 한쪽을 끈 사람이 다른 쪽도 껐다고 믿는다.
- **모르는 값은 즉시 실패한다.** `fasle` 같은 오타가 조용히 켜짐으로 읽히면 손잡이를 당겼다고
  믿는 사람과 실제 동작이 갈린다. `ValueError`를 DAG가 `AirflowFailException`으로 바꾼다.

KRX는 끌 수 없다 — 그건 수집을 통째로 멈추는 것이고 그때는 DAG를 pause 한다.

판정은 트리가 갈려 두 벌이다(백엔드는 airflow 트리를 import하지 않는다).
`tests/realtime/test_kis_realtime.py`가 두 손잡이의 기본값과 허용 값을 대조한다.

서비스는 heartbeat 파일 또는 `--healthcheck` 명령을 제공한다. 다음 상태를 구분한다.

- `idle`: 연결 대상 시간이 아님 또는 평일 휴장
- `connecting`: approval/구독 진행 중
- `ready`: 모든 활성 구독 ACK 완료
- `degraded`: 일부 시계열 비활성 또는 마지막 프레임 지연
- `failed`: 프로세스·DB·인증 오류

### 11.2 운영 지표

구조화 로그와 `source_record.metadata`에 최소 다음 값을 남긴다.

- 연결·재연결·approval 갱신 횟수
- TR ID별 ACK/NACK와 마지막 프레임 수신 시각
- 거래소·종목별 마지막 잠정 봉·마지막 REST 확정 봉 시각
- 파싱 실패, 격리 프레임, 늦은 틱, 분 중간 재연결 수
- REST가 WebSocket OHLCV를 수정한 봉 수
- 백필 페이지 수, throttle 재시도, 커서 무진행 수

알림 기준은 초기 운영 일주일의 정상 분포를 본 뒤 고정하되, 장중 10분 이상 확정 봉이 없고
동시에 REST도 실패한 경우는 즉시 경고한다.

## 12. 변경 파일과 작업 순서

### 작업 0 — 운영 계약 확인

- 실전 키로 당일·과거 API의 `J`, `NX` 응답 확인
- KRX·NXT `previous_close` 의미와 세션별 봉 시각 확인
- WebSocket 네 구독 ACK, 46필드, 다중 레코드 프레임 fixture 확보
- 비밀값·토큰·원문 전체를 커밋하지 않고 축약·익명 fixture만 저장
- 확인 실패 시 NXT REST/WS를 독립 feature flag로 끌 수 있게 결과 기록

### 작업 1 — 모델·마이그레이션·SQL

- 수정: `apps/models/market/series.py`
- 추가: 새 Alembic revision
- 추가: `airflow/sql/postgres/stock_bar/upsert.sql` (REST 확정)
- WebSocket 잠정 upsert는 SQL 파일이 아니라 ORM이다 — `apps/realtime/repository.py`의
  `provisional_upsert`. `apps/`는 `airflow/sql/`을 보지 못한다
- 추가: `airflow/sql/postgres/source_record/update.sql`
- 수정: `tests/models/test_market_models.py`
- 추가: `tests/migrations/test_equity_bar_schema.py`

### 작업 2 — REST 수집기와 파서

- 수정: `airflow/modules/collectors/kis.py`
- 수정: `tests/collectors/test_kis.py`
- 구현: `DomesticEquity`, `EquityVenue`, 요청 모델, 당일·과거 fetch, 주식 파서
- 구현: 완료 봉 cutoff, 세션 필터, REST 확정 저장, 시계열별 savepoint

### 작업 3 — WebSocket 수집기

- 추가: `apps/realtime/`(`frames.py`·`aggregator.py`·`repository.py`·`service.py`·`main.py`)
- 추가: `tests/realtime/test_kis_realtime.py`
- 구현: approval, 구독 ACK, TR ID별 46필드 파서, 다중 레코드 처리
- 구현: 타이머 집계, 재연결, 잠정 upsert, source session, graceful shutdown

### 작업 4 — REST 조정 DAG

- 수정: `airflow/dags/kis_quote_intraday.py`
- 추가: `tests/dags/test_kis_quote_intraday.py`
- 구현: 기존 수집과 주식 조정 task 분리, 08:00~20:00 schedule, 세션 self-gate

### 작업 5 — 백필 DAG

- 추가: `airflow/dags/kis_equity_backfill.py`
- 추가: `tests/dags/test_kis_equity_backfill.py`
- 구현: 31일 제한, 날짜·시간 역방향 페이지, API pool, 재시도, 날짜별 계보

### 작업 6 — 배포·운영

- 수정: `compose/local/airflow/requirements.txt`
- 수정: `compose/local/airflow/docker-compose.yaml`
- 수정: 배포 환경 변수 문서 또는 README
- 추가: compose config·서비스 healthcheck 테스트

## 13. 테스트 계획

### 13.1 REST 계약

- 당일 30봉·과거 120봉 응답 파싱
- `J`와 `NX`, 두 종목 라우팅
- 응답 당시 현재 분 제외와 직전 완료 분 포함
- 현재 분 첫 체결 전 `cntg_vol` 오염 행 제외
- KST→UTC와 NXT `09:00:30`의 `09:00` 봉 절삭
- 숫자 공백·쉼표·0, NaN/Infinity, 음수 거래량, OHLC 관계 위반
- 휴장일 빈 응답, 요청 날짜 밖 행, 중복 시각 제거
- 백필 커서 무진행·중복 페이지·세션 경계·31일 초과 거부

### 13.2 WebSocket 계약과 집계

- KRX·NXT 46필드 차이와 알 수 없는 TR ID
- 한 프레임의 1건·다건 레코드와 record count 불일치
- ACK/NACK, 중복 구독, PINGPONG, `encrypt=Y` 처리
- 같은 분 여러 체결의 OHLCV와 이벤트 시각 기준 open/close
- 다음 분 체결 없이 타이머만으로 직전 봉 flush
- 체결 없는 분은 행을 만들지 않음
- 지연 틱, 역순 틱, 분 중간 시작·재연결·SIGTERM
- 장 종료 마지막 봉과 NXT 두 휴지 구간
- approval 만료, 재구독, 일부 종목 NACK

### 13.3 DB와 동시성

- KRX 행이 NXT 행을 갱신할 수 없음
- 같은 자연키 재실행은 행 수를 늘리지 않음
- WebSocket 잠정 행을 REST가 확정
- REST 확정 행을 늦은 WebSocket이 갱신하지 못함
- 한 시계열 SQL 오류가 다른 시계열 commit을 막지 않음
- `source_record` running→완료 상태와 최신 계보 FK
- migration upgrade·downgrade와 모델 metadata·SQL 컬럼 일치

### 13.4 DAG와 배포

- 정규 schedule과 거래소별 self-gate
- 기존 지수·선물 task와 주식 task의 장애 격리
- 백필 DAG가 정규 DAG run을 점유하지 않음
- 잘못된 날짜·실전/모의 설정·누락 환경 변수 거부
- compose config 렌더링, 의존성 import, healthcheck 상태

자동 검사:

```bash
uv run pytest \
  tests/collectors/test_kis.py \
  tests/realtime/test_kis_realtime.py \
  tests/dags/test_kis_quote_intraday.py \
  tests/models/test_market_models.py -q

# 백필 DAG(9절)은 아직 없다. 만들면 tests/dags/test_kis_equity_backfill.py가 붙는다.

DJANGO_SETTINGS_MODULE=config.settings.test uv run python manage.py check
docker compose -f compose/local/airflow/docker-compose.yaml config --quiet
```

## 14. 운영 검증

거래소별 범위와 확정 상태:

테이블이 하나라 UNION이 필요 없다. `exchange`로 묶는다.

```sql
SELECT
    exchange,
    stock_code,
    min(bar_at) AS first_bar_at,
    max(bar_at) AS last_bar_at,
    count(*) AS bar_count,
    count(*) FILTER (WHERE is_final) AS final_count
FROM stock_bar
WHERE provider = 'kis'
  AND exchange IN ('KRX', 'NXT')
GROUP BY exchange, stock_code
ORDER BY exchange, stock_code;
```

장중 잠정 봉 정체:

```sql
SELECT exchange, stock_code, max(bar_at) AS latest_bar_at
FROM stock_bar
WHERE provider = 'kis'
  AND exchange IN ('KRX', 'NXT')
  AND NOT is_final
GROUP BY exchange, stock_code
ORDER BY exchange, stock_code;
```

운영 확인 항목:

- 같은 분의 WebSocket 잠정 OHLCV와 이후 REST 확정 OHLCV 차이
- 활성 세션의 예상 거래 분 대비 누락 분과 실제 무체결 분 구분
- 마지막 WebSocket 프레임과 REST 확정 봉의 지연
- NXT 세션 창(08:00~20:00) 안의 휴지 구간이 무봉으로 남는지 확인
- 재연결 세션별 source record 종료 상태와 실패 원인

## 15. 단계별 배포

1. **계약 확인**: 실전 fixture와 NXT 권한·세션 의미를 확인한다.
2. **테이블+REST 조정**: WebSocket 없이도 두 거래소의 완료 봉을 5분마다 저장한다.
3. **별도 백필**: 짧은 범위로 KRX를 먼저 검증한 뒤 NXT를 켠다.
4. **KRX WebSocket**: 잠정→REST 확정 전환과 쓰기 우선순위를 검증한다.
5. **NXT WebSocket**: 세 세션과 두 휴지 구간을 검증한다.
6. **공용 연결 확장**: 다른 KIS 문서의 지수·수급 채널을 registry에 추가한다.

각 단계는 앞 단계의 수집을 중단하지 않고 독립 feature flag로 되돌릴 수 있어야 한다.

## 16. 완료 조건

- 두 종목의 KRX·NXT 1분 OHLCV가 서로 다른 물리 테이블에 저장된다.
- 진행 중 REST 봉과 세션 밖 봉이 저장되지 않는다.
- 같은 구간을 반복 수집해도 행 수가 늘지 않는다.
- REST 확정 뒤 늦은 WebSocket 봉이 값을 바꾸지 못한다.
- 분 중간 재연결·종료에서 불완전 WebSocket 봉이 완성 봉으로 저장되지 않는다.
- 정상 연결에서는 완성된 WebSocket 봉이 분 종료 후 설정 지연 이내 저장된다.
- REST 조정 뒤 OHLCV가 공식 완료 분봉과 일치한다.
- 저장 시각은 UTC이고 KST 변환 시 실제 거래소 체결 분과 일치한다.
- 한 종목·거래소 실패가 다른 주식·지수·선물 저장을 막지 않는다.
- 장기 백필 중에도 정규 5분 DAG가 예정대로 실행된다.
- WebSocket과 REST `source_record`가 종료 상태·레코드 수·부분 실패 원인을 남긴다.
- compose healthcheck와 운영 조회로 수신 정체·확정 지연·재연결을 식별할 수 있다.

## 17. 이번 범위 아님

- KRX+NXT 통합(`UN`) 체결
- KRX 시간외 체결
- 호가와 체결 틱 원본의 영구 보관
- 모든 봉 변경 이력을 보존하는 revision 테이블
- 1년을 넘는 분봉 복구
- 새 범용 KIS 클라이언트 계층
- 자동 매매·매수 추천
