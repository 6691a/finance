# 개발 문서 1 — 삼성전자·SK하이닉스 KIS 1분봉

> 작성 기준: 2026-08-11  
> 상태: 미구현 기능의 확정 실행 계획  
> 대상: 삼성전자(`005930`), SK하이닉스(`000660`)의 KRX·NXT 체결

> **2026-08-18 갱신**: 저장 스키마는 이 문서와 다르게 확정됐다. `krx_equity_bar`/`nxt_equity_bar`
> 물리 테이블 분리 대신 단일 `stock_bar`에 `exchange`(KRX/NXT) 컬럼을 자연키 축으로 둔다
> (`apps/models/market.py`, 커밋 `e6cf001`). 마감 후 REST 확정본은 `kis_stock_minute_bars_daily`
> DAG로 구현됐다. `kis_quote_intraday`의 5분 REST 조정, `kis_equity_backfill` 백필 DAG는
> 여전히 미구현이다. 이후 절의 테이블 이름과 SQL 경로는 구현 시 `stock_bar` 기준으로 바꿔 읽는다.

> **2026-08-18 갱신 2 — 7장 WebSocket 구현됨** (2026-08-19 위치 확정): 상주 수집기는
> **`apps/realtime/`**(백엔드 트리)다. Airflow가 실행하지 않는 코드는 `airflow/`에 두지
> 않는 규칙이 새로 확정돼, 문서 7.1·11.1의 `airflow/modules/collectors/kis_realtime.py`·
> Airflow 이미지 공유 배포는 대체됐다. 실행은 `python -m apps.realtime`, 배포는 별도
> 스택 `compose/prod/`(개발은 `compose/local/realtime/`), 설정은 FastAPI와 같은
> `config.yaml`(`apps.core.config`), 저장은 `apps.models` ORM
> (`apps/realtime/repository.py`)이다. `stock_bar`에 `ingest_method`/`is_final` 컬럼이
> 붙었고(리비전 `d41f7c9b3a12`), WS는 `provisional_upsert`(ORM)로 `is_final=false` 행만
> 갱신하며 REST(`airflow/sql/postgres/stock_bar/upsert.sql`)는 무조건 덮고
> `is_final=true`로 확정한다. 문서와 다른 구현 결정: ① `previous_close`는 시작 시 REST가
> 아니라 일별 DAG처럼 `stock_investor_trade_daily`에서 읽는다 — REST access token이
> 필요 없고 approval key만 쓴다. ② 세션 필터는 4.2의 NXT 3분할 창(애프터 15:40~) 대신
> REST 수집과 같은 단일 창(KRX 09:00~15:30, NXT 08:00~20:00)이다 — 실측(`kis.py`: 애프터
> 15:30~20:00)이 문서와 어긋났고, REST 저장 범위와 같아야 WS에만 구멍이 생기지 않는다.
> ③ Airflow 수집기와 겹치는 종목·세션 상수는 의도적 중복이고
> `tests/realtime/test_kis_realtime.py`가 대조한다. 인증 거절 코드가 픽스처로 확정되기
> 전까지 "구독 전건 거절 = 인증 문제"로 판정해 approval key를 1회 재발급한다.

## 1. 결론

삼성전자와 SK하이닉스의 KRX·NXT 체결을 거래소별 1분 OHLCV로 저장한다. 실시간 주 경로는
WebSocket이고, REST는 **완료된 과거 분봉만** 다시 받아 누락과 불완전 봉을 확정한다.

- 저장 테이블: `krx_equity_bar`, `nxt_equity_bar`
- 저장 심볼: 두 테이블 모두 `SAMSUNG_ELECTRONICS`, `SK_HYNIX`
- 거래소: KRX(`J`)와 NXT(`NX`)를 물리 테이블로 분리
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
- `apps/models/market.py`: OHLCV·계보·멱등 키 패턴인 `QuoteBar`
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
| `previous_close` | `output1.stck_prdy_clpr` |

새 주식 테이블에는 선물 월물 개념이 없으므로 `contract_code` 컬럼을 만들지 않는다.

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

공식 helper의 한 연결 구독 상한은 40개다. 이번 범위는 4개지만 다른 KIS 개발 문서가 같은 공용
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
    KRX = ("J", "H0STCNT0", "krx_equity_bar")
    NXT = ("NX", "H0NXCNT0", "nxt_equity_bar")
```

코드 집합은 Enum으로 고정하고, 당일·과거 요청값은 Pydantic `BaseModel`로 묶어 날짜·시각·시장
코드 조합을 호출 전에 검증한다. 테이블 이름은 Enum에서 SQL 문자열로 직접 조립하지 않고,
허용된 SQL 상수로 라우팅한다.

### 4.2 거래 세션

| 거래소 | 세션 | 저장 대상 체결 시각(KST) |
| --- | --- | --- |
| KRX | 정규장 | `09:00:00 <= t < 15:30:00` |
| NXT | 프리마켓 | `08:00:00 <= t < 08:50:00` |
| NXT | 메인마켓 | `09:00:30 <= t < 15:20:00` |
| NXT | 애프터마켓 | `15:40:00 <= t < 20:00:00` |

NXT 거래시간 출처: [넥스트레이드 거래제도](https://www.nextrade.co.kr/menu/transactionSys.do)

`bar_at`은 체결 시각을 1분 아래로 절삭한 값이다. 따라서 NXT 메인마켓 첫 `09:00:30` 체결은
`09:00` 봉에 들어간다. 휴지 구간에는 봉을 만들지 않고, 체결이 전혀 없는 분에도 0거래량 봉을
합성하지 않는다.

평일 조건은 불필요한 연결을 줄이는 1차 필터다. 휴장일 캘린더를 이 기능에서 새로 만들지 않으며,
평일 휴장일에는 구독 ACK 이후 데이터가 없는 상태를 정상 idle로 기록한다.

## 5. 데이터 모델과 쓰기 우선순위

### 5.1 거래소별 테이블

`apps/models/market.py`에 같은 컬럼의 `KrxEquityBar`, `NxtEquityBar`를 선언한다.

| 컬럼 | 타입 | 설명 |
| --- | --- | --- |
| `provider` | text | 항상 `kis` |
| `symbol` | text | `SAMSUNG_ELECTRONICS`, `SK_HYNIX` |
| `bar_at` | timestamptz | 1분 시작 시각 UTC |
| `open`, `high`, `low`, `close` | numeric | 1분 OHLC |
| `volume` | bigint | 해당 거래소의 1분 체결량 |
| `previous_close` | numeric | KIS `stck_prdy_clpr` |
| `ingest_method` | text | `websocket` 또는 `rest` |
| `is_final` | boolean | REST가 완료 봉을 확인했는지 여부 |
| `source_record_id` | bigint FK | 이 행을 마지막으로 갱신한 원천 |

자연키는 각 테이블의 `(provider, symbol, bar_at)`이다. `venue`와 `contract_code`는 두지 않는다.
테이블 이름이 거래소를 고정하고 주식에는 선물 월물이 없기 때문이다.

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
- `stck_prdy_clpr`가 없거나 0 이하이면 실패
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

`airflow/modules/collectors/kis_realtime.py`가 공용 연결 하나를 관리한다. Airflow task를 장시간
점유하지 않고 compose의 `kis-realtime` 서비스로 실행한다.

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

`compose/local/airflow/requirements.txt`에 백엔드와 같은 호환 범위의 `websockets`를 추가한다.
루트 `pyproject.toml`의 의존성은 Airflow 이미지에 자동 설치되지 않으므로 두 파일을 테스트로
맞춘다.

`kis-realtime` 서비스:

```text
command: python -m modules.collectors.kis_realtime
restart: unless-stopped
공유: Airflow 이미지, modules, sql, logs 볼륨
환경: AIRFLOW_CONN_FINANCE, KIS_ENV=prod, KIS_APP_KEY, KIS_APP_SECRET,
      KIS_REST_DOMAIN, KIS_WEBSOCKET_DOMAIN,
      KIS_ENABLE_NXT_REST, KIS_ENABLE_NXT_WEBSOCKET
연결 시간: 평일 07:50~20:10 KST
그 밖 시간: 프로세스는 살아 있고 연결하지 않은 채 대기
```

환경 변수 이름은 애플리케이션 YAML의 `kis_websocket_domain`과 별개임을 README와 compose
샘플에 명시한다. WebSocket 설정값에 `/tryitout`이 이미 있으면 중복해 붙이지 않는다.
NXT 두 feature flag는 운영 계약 확인 전에는 `false`, 확인 뒤에는 독립적으로 `true`로 바꾼다.

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

- 수정: `apps/models/market.py`
- 추가: 새 Alembic revision
- 추가: `airflow/sql/postgres/krx_equity_bar/upsert_rest.sql`
- 추가: `airflow/sql/postgres/krx_equity_bar/upsert_websocket.sql`
- 추가: `airflow/sql/postgres/nxt_equity_bar/upsert_rest.sql`
- 추가: `airflow/sql/postgres/nxt_equity_bar/upsert_websocket.sql`
- 추가: `airflow/sql/postgres/source_record/update.sql`
- 수정: `tests/models/test_market_models.py`
- 추가: `tests/migrations/test_equity_bar_schema.py`

### 작업 2 — REST 수집기와 파서

- 수정: `airflow/modules/collectors/kis.py`
- 수정: `tests/collectors/test_kis.py`
- 구현: `DomesticEquity`, `EquityVenue`, 요청 모델, 당일·과거 fetch, 주식 파서
- 구현: 완료 봉 cutoff, 세션 필터, REST 확정 저장, 시계열별 savepoint

### 작업 3 — WebSocket 수집기

- 추가: `airflow/modules/collectors/kis_realtime.py`
- 추가: `tests/collectors/test_kis_realtime.py`
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
  tests/collectors/test_kis_realtime.py \
  tests/dags/test_kis_quote_intraday.py \
  tests/dags/test_kis_equity_backfill.py \
  tests/models/test_market_models.py \
  tests/migrations/test_equity_bar_schema.py -q

DJANGO_SETTINGS_MODULE=config.settings.test uv run python manage.py check
docker compose -f compose/local/airflow/docker-compose.yaml config --quiet
```

## 14. 운영 검증

거래소별 범위와 확정 상태:

```sql
SELECT
    'KRX' AS venue,
    symbol,
    min(bar_at) AS first_bar_at,
    max(bar_at) AS last_bar_at,
    count(*) AS bar_count,
    count(*) FILTER (WHERE is_final) AS final_count
FROM krx_equity_bar
WHERE provider = 'kis'
GROUP BY symbol
UNION ALL
SELECT
    'NXT' AS venue,
    symbol,
    min(bar_at),
    max(bar_at),
    count(*),
    count(*) FILTER (WHERE is_final)
FROM nxt_equity_bar
WHERE provider = 'kis'
GROUP BY symbol;
```

장중 잠정 봉 정체:

```sql
SELECT 'KRX' AS venue, symbol, max(bar_at) AS latest_bar_at
FROM krx_equity_bar
WHERE provider = 'kis'
GROUP BY symbol
UNION ALL
SELECT 'NXT', symbol, max(bar_at)
FROM nxt_equity_bar
WHERE provider = 'kis'
GROUP BY symbol;
```

운영 확인 항목:

- 같은 분의 WebSocket 잠정 OHLCV와 이후 REST 확정 OHLCV 차이
- 활성 세션의 예상 거래 분 대비 누락 분과 실제 무체결 분 구분
- 마지막 WebSocket 프레임과 REST 확정 봉의 지연
- NXT 프리·메인·애프터 세션 경계와 휴지 구간 무봉 확인
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
