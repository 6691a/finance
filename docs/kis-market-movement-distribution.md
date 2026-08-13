# 개발 문서 6 — KIS 장중 상승·보합·하락 종목 분포

> 작성 기준: 2026-08-11 (2026-08-13 실측으로 갱신)  
> 상태: 미구현 기능의 실행 계획  
> 대상 시장: 코스피, 코스닥

## 1. 결론

코스피·코스닥 전 종목을 각각 조회해 계산하지 않는다. KIS 지수 API가 이미 제공하는
상한가·상승·보합·하락·하한가 종목 수를 직접 수집한다.

- 실시간 주 경로: KIS 지수 WebSocket `H0UPCNT0`
- 저장 간격: WebSocket의 마지막 값을 1분 스냅샷으로 저장
- 누락 확인: 지수 현재가 REST `FHPUP02100000`을 기존 KIS DAG의 별도 task에서 5분마다 조회
- 저장 테이블: `market_movement_snapshot`
- **다섯 값이 모두 0이면 저장하지 않는다.** 장 시작 전과 마감 후에는 종목 수가 0으로
  리셋된다(실측). 장중에는 상승·보합·하락의 합이 전 종목이라 all-zero가 될 수 없다
- REST 대상: 코스피(`0001`), 코스닥(`1001`)
- WebSocket 대상: 코스피 `0001`, 코스닥 코드는 운영 확인 후 고정
- 제외: 종목 전수 조회, NXT 전용 분포, 250일 일봉

이 데이터는 “지수가 올랐지만 소수 대형주만 오른 장인지, 시장 전반이 함께 오른 장인지”를
판단하는 보조 신호다. 저장·화면 이름은 영어 용어 대신 `상승·보합·하락 종목 분포`를 쓴다.

## 2. 실제 수집 가능 시점

| 방식 | 수집 시점 | 해상도 | 용도 |
| --- | --- | --- | --- |
| WebSocket | 장중 실시간 수신 | 원본 초 단위, 저장 1분 | 주 수집 경로 |
| REST | 장중 요청 시점 | 요청 시점 스냅샷 | 연결 누락 확인 |
| REST | 장 마감 뒤 15:35·15:40 | 요청 시점 스냅샷 | **리셋 시점 확인 뒤 결정**(§3.1) |

WebSocket은 구독 뒤 프레임이 오는 방식이라 실시간 수집이 맞다. 다만 DB에는 매 틱을 모두
저장하지 않고 삼성전자·SK하이닉스 분봉과 맞추기 위해 분마다 마지막 프레임만 보존한다.

REST 응답에는 WebSocket처럼 명확한 원천 체결 시각이나 확정 여부가 없으므로 요청 수신
시각을 분 단위로 절삭해 저장한다. 따라서 REST 행은 5분 간격의 보조 스냅샷이지 과거 분포를
복구하는 백필 데이터가 아니다. 수집기를 며칠 늦게 배포하면 지나간 장중 분포는 복구할 수
없다.

## 3. 공식 API 계약

### 3.1 REST 지수 현재가

공식 예제: [국내업종 현재지수](https://github.com/koreainvestment/open-trading-api/blob/main/examples_llm/domestic_stock/inquire_index_price/inquire_index_price.py)

| 항목 | 값 |
| --- | --- |
| Method | `GET` |
| Path | `/uapi/domestic-stock/v1/quotations/inquire-index-price` |
| TR ID | `FHPUP02100000` |
| 시장 구분 | `FID_COND_MRKT_DIV_CODE=U` |
| 코스피 | `FID_INPUT_ISCD=0001` |
| 코스닥 | `FID_INPUT_ISCD=1001` |

저장 필드:

| 의미 | KIS 필드 |
| --- | --- |
| 상한가 종목 수 | `uplm_issu_cnt` |
| 상승 종목 수 | `ascn_issu_cnt` |
| 보합 종목 수 | `stnr_issu_cnt` |
| 하락 종목 수 | `down_issu_cnt` |
| 하한가 종목 수 | `lslm_issu_cnt` |

`bstp_nmix_prpr`, `acml_vol`, `acml_tr_pbmn`은 응답 검증에만 사용한다. 지수 가격과 거래량은
이미 `quote_bar`가 저장하므로 이 테이블에 중복 저장하지 않는다.

**실측 (2026-08-13 00:37 KST, 장 마감 한참 뒤)**

| 지수 | 지수 가격 | 다섯 종목 수 | 누적 거래량 |
| --- | --- | --- | --- |
| 코스피 `0001` | 6579.04 | 전부 `0` | `0` |
| 코스닥 `1001` | 858.91 | 전부 `0` | `0` |
| 코스피200 `2001` | 1029.43 | 전부 `0` | `0` |

- `rt_cd=0`, 출력 필드 36개. 다섯 종목 수의 이름은 문서 표와 같았다.
- **장 밖에서는 종목 수와 거래량이 0으로 리셋된다.** 지수 가격만 전일 종가로 남는다.
  그러므로 "전일 값을 당일 값으로 오인"할 위험은 우리가 저장하지 않는 가격 쪽에만 있고,
  종목 수 쪽 위험은 **빈 값(0)을 실제 분포로 저장하는 것**이다.
- 그래서 저장 규칙은 하나다. **다섯 값이 모두 0이면 저장하지 않는다.** 장중에는
  상승·보합·하락의 합이 전 종목이라 all-zero가 나올 수 없다.
- 코스피200(`2001`)도 같은 필드를 준다. 다만 분포 대상은 코스피·코스닥 둘로 제한한다.

**아직 못 잰 것**: 리셋이 15:30 장 마감 직후 일어나는지, 그날 밤 어느 시점인지. 이 답이
§5.2의 15:35·15:40 스냅샷이 값을 담을지 0을 담을지를 정한다. 장중에 한 번 재고 이 절을
갱신한다.

**포함 관계 실측 (2026-08-13 장중, 코스닥 20초 간격 14샘플)**

상한가 수가 바뀌는 순간을 잡아 갈랐다.

```
t=180s  상한 3  상승 908  보합 82  하락 728   셋합 1718  다섯합 1721
t=200s  상한 4  상승 892  보합 84  하락 742   셋합 1718  다섯합 1722
```

**상한가가 하나 늘었는데 상승+보합+하락 합이 그대로다.** 배타적 집합이었다면 그 종목이
상승에서 빠져 셋합이 1 줄었어야 한다. 따라서 **상한가는 상승 안에 있다.**

- **전체 종목 수 = 상승 + 보합 + 하락**이다. 다섯 값을 더하면 상·하한가가 이중 계산된다.
- 같은 구간 코스피의 셋합은 899로 **6샘플 내내 고정**이었다. 개별 값은 484~498로 흔들렸다.
  셋이 서로 배타적이고 그 합이 그날 거래 종목 수라는 뜻이다.
- 코스닥 셋합은 1715→1719로 서서히 늘었다. 장 초반에 거래를 시작하는 종목이 더해진 것이다.
- **하한가가 하락에 포함되는지는 직접 못 봤다.** 관측 내내 하한가가 0이었다. 대칭으로
  추정할 뿐이므로 확정 표기하지 않는다.

그래서 저장은 다섯 값을 날것으로 두고 합계 제약을 걸지 않는다. 화면의 3분류는 상승·보합·
하락 셋이고 상·하한가는 그 안의 강조 표시다.

### 3.2 실시간 지수 WebSocket

공식 예제: [국내지수 실시간체결](https://github.com/koreainvestment/open-trading-api/blob/main/examples_llm/domestic_stock/index_ccnl/index_ccnl.py)

| 항목 | 값 |
| --- | --- |
| approval key | `POST /oauth2/Approval` |
| TR ID | `H0UPCNT0` |
| 코스피 구독 | `0001` |
| 코스닥 구독 | 운영 확인 전 `TBD` |

approval key는 REST access token과 별도다. 공용 WebSocket 연결 시작 때 한 번 발급하고 인증
거절 뒤 재연결할 때 다시 발급한다.

사용 필드는 영업 시각 `bsop_hour`와 REST의 다섯 종목 수다. WebSocket 데이터 프레임은
30필드이며 공식 예제의 컬럼 순서를 그대로 고정하고 프레임 필드 수를 먼저 검증한다.

REST의 코스닥 코드는 `1001`이지만 WebSocket 공식 실행 예제는 `0001`, `0128`을 구독한다.
따라서 REST 코드 `1001`을 WebSocket에 그대로 쓰지 않는다. 운영 키로 `0128` 프레임의
`bstp_cls_code`와 실제 코스닥 값을 확인한 뒤 코스닥 구독 코드를 fixture에 고정한다.

WebSocket에는 동시호가용 `qtqt_ascn_issu_cnt`, `qtqt_down_issu_cnt`도 있다. 1차 테이블은
일반 다섯 종목 수만 저장한다. 운영 확인에서 동시호가 중 일반 필드의 의미가 다르면 해당
구간 프레임은 저장하지 않고 장 마감 뒤 REST 최신값만 남긴다.

## 4. 데이터 모델

`apps/models/market.py`에 `MarketMovementSnapshot`을 추가한다.

| 컬럼 | 타입 | 설명 |
| --- | --- | --- |
| `provider` | text | `kis` |
| `symbol` | varchar(20) | `KOSPI` 또는 `KOSDAQ` |
| `observed_at` | timestamptz | 1분 시작 시각 UTC |
| `upper_limit_count` | integer | 상한가 종목 수 |
| `rising_count` | integer | 상승 종목 수 |
| `unchanged_count` | integer | 보합 종목 수 |
| `falling_count` | integer | 하락 종목 수 |
| `lower_limit_count` | integer | 하한가 종목 수 |
| `source_record_id` | bigint FK | 근거가 되는 REST 또는 WebSocket 수집 레코드 |

멱등 키:

```text
(provider, symbol, observed_at)
```

**컬럼 이름을 `target`이 아니라 `symbol`로 둔다.** 값이 `quote_bar.symbol`(`KOSPI`, `KOSDAQ`)과
글자 그대로 같기 때문이다. 같은 값을 다른 이름으로 부르면 "코스피 지수 봉"과 "코스피 종목
분포"를 잇는 조회가 매번 대응표를 들고 다녀야 한다. 같은 이름이면 `reference.quote_symbol`
마스터에 그대로 조인된다.

지금 저장소에 시장을 가리키는 어휘가 이미 셋이라 넷째를 만들지 않는 것이 이 결정의 핵심이다.

| 곳 | 값 | 뜻 |
| --- | --- | --- |
| `reference.Market` | `kospi`, `kosdaq` | 종목이 상장된 거래소 |
| `market_session.market_code` | `KRX`, `US_EQUITY` | 휴장 캘린더를 공유하는 시장 묶음 |
| `quote_bar.symbol` | `KOSPI`, `KOSDAQ` | 시세 시계열 식별자 |

이 테이블이 쓰는 것은 셋째다.

다섯 종목 수는 음수가 될 수 없도록 CHECK를 둔다. 다섯 값의 합계 고정 제약은 두지 않는다.
`venue` 컬럼은 두지 않는다. 이 지수 채널은 KRX 코스피·코스닥 기준이고 NXT 분포인 것처럼
저장하지 않는다.

별도 비율 컬럼은 만들지 않는다. 필요한 비율은 조회에서 계산한다.

**상한가는 상승에 포함된다**(§3.1 실측). 그래서 3분류는 상승·보합·하락 셋을 그대로 쓰고,
전체 종목 수는 그 셋의 합이다. 다섯 값을 더하면 상·하한가가 이중 계산된다. 하한가와 하락의
관계는 아직 못 봤으므로 하한가를 하락에서 빼는 계산은 하지 않는다.

`symbol`은 `StrEnum`과 비원시 SQLAlchemy Enum으로 선언하고 `KOSPI`, `KOSDAQ`만 허용하는
CHECK를 둔다. `quote_bar.symbol`이 열린 `Text`인 것과 다른데, 저쪽은 제공처마다 값 집합이
달라지는 열린 식별자이고 이쪽은 분포를 주는 지수 둘로 닫혀 있기 때문이다. 공급자 코드
(`0001`, `1001`)는 수집기 상수(`DomesticIndex`)에만 두고 DB 값에 섞지 않는다.

## 5. 수집 흐름

### 5.1 WebSocket 주 경로

문서 1에서 계획한 `airflow/modules/collectors/kis_realtime.py`의 공용 연결에 코스피·코스닥
`H0UPCNT0` 구독을 추가한다. 분포만을 위한 새 프로세스나 컨테이너는 만들지 않는다.

```text
H0UPCNT0 프레임
  → TR ID·필드 수 검증
  → bsop_hour를 KST 영업일과 결합
  → 시장별 현재 분의 마지막 값만 보관
  → 다음 분 첫 프레임에서 직전 분 upsert
  → 장 종료 때 남은 분 flush
```

같은 분에 수백 프레임이 와도 DB에는 시장당 한 행만 남긴다. 중간 프레임 전체를 보존할
요구가 생기기 전에는 틱 테이블을 만들지 않는다.

연결이 끊기면 문서 1의 재연결·재구독 로직을 그대로 쓴다. 재연결 동안의 과거 초 단위 값은
복구할 수 없지만, 5분 REST 스냅샷이 긴 공백과 마감 후 최신값을 보완한다.

### 5.2 REST 보조 경로

새 DAG를 만들지 않고 기존 `airflow/dags/kis_quote_intraday.py` 안에 분포용 task를 하나
추가해 지수 현재가 REST를 호출한다. 가격 봉 task와 분리해 분포 실패가 기존 분봉 저장을
막지 않게 한다. 인증, 토큰 캐시, 401 재발급, 부분 실패 처리는 기존 KIS 수집기 규칙을
재사용한다.

- 기존 스케줄: 평일 08:00~16:59 KST, 5분마다
- 장중: 요청 시각 분에 REST 스냅샷 upsert
- **다섯 값이 모두 0이면 저장하지 않는다.** 개장 전과 마감 후의 리셋 상태다(§3.1 실측).
  이 규칙 하나가 "빈 값을 분포로 저장"과 "전일 값 오인"을 함께 막는다
- 15:35와 15:40: 리셋 시점을 잰 뒤 결정한다. 그 시각에 이미 0이면 저장할 것이 없다
- 코스피 실패가 코스닥과 기존 지수·선물 분봉 저장을 막지 않음

**휴장일 판정은 이 DAG가 이미 갖고 있다.** `market_session`의 KRX 행을 보고 확정 휴장일이면
태스크를 skip한다. 문서 초판에는 그 캘린더가 없어서 "오늘 지수 봉이 저장됐는지"로 영업일을
추론하려 했는데, 이제 그럴 필요가 없다. 다만 현재 skip 판정(`_closed_today`)이 `collect`
태스크 **안에** 있으므로, 분포 태스크를 더할 때 두 태스크가 함께 쓰도록 밖으로 빼낸다.
빼지 않으면 분포 태스크만 휴장일에 그대로 돈다.

KIS에는 `final` 필드가 없다. 두 REST 값이 같아도 거래소 확정값임을 증명하지 못한다.
WebSocket의 마지막 프레임이나 REST 값을 확정값으로 승격하지 않고, 일별 조회에서는 15:30
이후 마지막 정상 REST 행을 `마감 후 최신값`으로 표시한다. 전일 값이나 마지막 WebSocket
프레임을 복사해 빈 마감 구간을 만들지 않는다.

WebSocket 행과 REST 행이 같은 자연키이면 마지막 정상값이 upsert된다. `source_record_id`는
그 행을 실제로 갱신한 최신 원천을 가리킨다. REST에 원천 시각이 없으므로 수신 시각보다 과거인
것처럼 시간을 만들어 넣지 않는다.

### 5.3 수집기 확장

`airflow/modules/collectors/kis.py`에 다음을 추가한다.

- 지수 현재가 REST 상수와 `fetch_index_price()`
- 다섯 종목 수 파서와 지수·누적값 검증
- `store_market_movement()`

기존 **`send_get()`**, `DomesticIndex`, 숫자 파서, `source_record`, 토큰 캐시를 재사용한다.
`send_get()`은 초판 문서가 적은 `_get()`의 새 이름이고 `(본문, 상태, 헤더)` 세 값을 돌려준다.
헤더는 연속조회에만 필요하므로 이 조회는 앞 둘만 쓴다.

`KOSPI200`은 분포 대상이 아니므로 호출 대상은 `KOSPI`, `KOSDAQ` 두 값으로 제한한다.
`DomesticIndex`에 셋이 다 있으니 순회하지 말고 명시적으로 고른다.

REST run은 `source_type=api`, `source_key=inquire_index_price`인 `source_record` 한 건을 만들고
시장별 응답 상태를 metadata에 남긴다. WebSocket은 공용 연결 세션의 기존
`source_type=websocket`, `source_key=kis_realtime` 레코드를 공유하며 반복 프레임 원문은
`payload`에 쌓지 않는다.

## 6. 변경 파일

### 작업 1 — 운영 응답 확인

- ~~REST `0001`, `1001` 응답의 필드·단위 확인~~ — 2026-08-13 완료(§3.1)
- ~~장 밖 응답의 종목 수 상태 확인~~ — 전부 0으로 리셋됨(§3.1)
- ~~장중 REST 응답 확인 — 다섯 값이 실제로 차는지~~ — 2026-08-13 완료(§3.1)
- ~~상승이 상한가를 포함하는지~~ — 포함된다(§3.1). 전체 = 상승+보합+하락
- **하락이 하한가를 포함하는지** — 관측 내내 하한가가 0이라 아직 못 봤다
- **리셋 시점 확인** — 15:30 마감 직후인지 그날 밤인지. §5.2의 15:35·15:40 규칙이 여기 달렸다
- WebSocket `H0UPCNT0`의 두 지수 구독 키와 필드 순서 확인
- 동시호가 일반·`qtqt_*` 필드, 15:30 이후 프레임 의미 확인
- 같은 시각의 REST와 WebSocket 값 비교
- 실전·모의 환경의 REST/WebSocket 지원 여부 확인; 모의 지원을 가정하지 않음
- 키·토큰·원문 응답은 커밋하지 않고 축약 fixture만 저장

### 작업 2 — 모델과 SQL

- 수정: `apps/models/market.py`
- 추가: 새 Alembic revision
- 추가: `airflow/sql/postgres/market_movement_snapshot/upsert.sql`
- 수정: `tests/models/test_market_models.py`
- 추가: `tests/migrations/test_market_movement_schema.py`

### 작업 3 — REST 수집기와 기존 DAG

- 수정: `airflow/modules/collectors/kis.py`
- 수정: `airflow/dags/kis_quote_intraday.py`
- 수정: `tests/collectors/test_kis.py`
- 필요 시 수정: `tests/dags/test_quote_intraday.py`

### 작업 3.5 — 대시보드

- 추가: `compose/local/grafana/dashboards/market-movement.json` (uid `market-movement`)
- 추가: `tests/dashboards/test_market_movement_dashboard.py`

패널은 다섯이다. 시장별 상승 비율(stat), 상승 비율 추이, **지수 변동률 대 상승 비율**,
종목 수 누적 막대, 최근 스냅샷 표다.

- 핵심은 세 번째다. 지수는 올랐는데 상승 비율이 50% 아래인 구간이 소수 대형주가 지수를
  끌고 간 장이다. 지수는 `quote_bar`에서 오고, 두 테이블의 `symbol` 값이 같아 대응표 없이
  조인된다. `target`이 아니라 `symbol`로 이름 지은 값이 여기서 나온다.
- 상승 비율의 분모는 **상승+보합+하락**이다. 다섯 값을 더하면 상·하한가가 이중 계산된다.
  `tests/dashboards/test_market_movement_dashboard.py`가 이 규칙을 고정한다.
- 정규장 띠(annotation)는 요일이 아니라 `market_session`의 KRX 거래일로 그린다. 요일로
  그리면 공휴일에 띠가 서서 화면이 거짓말을 한다.
- 시장 목록은 손으로 적지 않고 저장된 값에서 읽는다.

### 작업 4 — 공용 WebSocket 수집기

- 수정: `airflow/modules/collectors/kis_realtime.py`
- 수정: `tests/collectors/test_kis_realtime.py`

문서 1의 공용 WebSocket 수집기가 아직 없으면 작업 3의 REST 5분 수집을 먼저 배포할 수 있다.
이후 공용 수집기를 한 번 만들 때 분봉·회원사·프로그램매매·시장 종목 분포 채널을 함께 붙인다.

## 7. 최소 테스트

- 코스피·코스닥 REST 응답의 다섯 종목 수 파싱
- 쉼표·공백 처리와 음수 거부
- **다섯 값이 모두 0인 응답은 행을 만들지 않고 계보만 남김**(개장 전·마감 후 리셋)
- 일부만 0인 응답은 정상 저장(보합 0은 장중에 있을 수 있다)
- WebSocket 필드 수·순서가 다르면 저장하지 않음
- `bsop_hour` KST→UTC 변환과 1분 절삭
- 같은 분에 여러 프레임이 오면 마지막 값 한 행만 저장
- 시장별 자연키 분리와 같은 자연키 upsert
- WebSocket 종료 시 마지막 분 flush
- 재연결 뒤 두 지수 자동 재구독
- REST 한 시장 실패 시 다른 시장과 기존 분봉 저장
- 장 마감 뒤 같은 값을 새 시계열처럼 반복 저장하지 않음
- 마감 후 최신값을 공급자 확정값으로 표시하지 않음

검증 명령:

```bash
uv run pytest tests/collectors/test_kis.py tests/models/test_market_models.py \
  tests/migrations/test_market_movement_schema.py tests/dags/test_quote_intraday.py -q
uv run ruff check apps airflow migrations tests
uv run pyrefly check
```

이 저장소에는 Django가 없다. 초판에 있던 `manage.py check`는 다른 프로젝트의 명령이었다.
`tests/collectors/test_kis_realtime.py`는 공용 WebSocket 수집기와 함께 생긴다.

실데이터 확인:

```sql
SELECT
    symbol,
    observed_at,
    upper_limit_count,
    rising_count,
    unchanged_count,
    falling_count,
    lower_limit_count
FROM market_movement_snapshot
WHERE provider = 'kis'
  AND observed_at >= now() - interval '1 day'
ORDER BY observed_at, symbol;
```

## 8. 완료 조건

- 코스피·코스닥의 상승·보합·하락 종목 수가 장중 1분 간격으로 저장된다.
- 상·하한가 종목 수가 별도 필드로 보존된다.
- WebSocket 연결이 없을 때도 기존 DAG가 5분 스냅샷을 남긴다.
- **개장 전과 마감 후의 0 응답이 분포 행으로 저장되지 않는다.**
- 확정 휴장일에는 분포 태스크가 요청을 보내지 않는다.
- 같은 분을 재수집해도 중복 행이 생기지 않는다.
- 분포 시각을 삼성전자·SK하이닉스 1분봉·수급과 UTC 기준으로 결합할 수 있다.
- 화면과 API에서 영어 용어 대신 `상승·보합·하락 종목 분포`로 표시한다.

## 9. 이번 범위 아님

- 250일 일봉
- 코스피·코스닥 전 종목 시세를 순회해 분포 재계산
- NXT 전용 또는 KRX+NXT 통합 종목 분포
- 과거 장중 분포 백필
- 틱 단위 원본 영구 보관
- 분포 기반 자동 매매·매수 추천
