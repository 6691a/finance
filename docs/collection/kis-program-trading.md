# 개발 문서 3 — KIS 프로그램매매 수집

> 작성 기준: 2026-08-11  
> 상태: 미구현 기능의 실행 계획  
> 대상 종목: 삼성전자, SK하이닉스  
> 대상 시장: 코스피, 코스닥

## 1. 결론

프로그램매매는 일반 외국인·기관 수급과 분리하고 거래소별 물리 테이블인
`krx_program_trade_snapshot`, `nxt_program_trade_snapshot`에 저장한다.

1차 구현:

- 삼성전자·SK하이닉스 KRX·NXT 프로그램매매 WebSocket 실시간 체결
- KRX·NXT 코스피·코스닥 프로그램매매 종합 시간 추이
- 거래소별 REST 종목 체결 추이를 이용한 연결 누락 조정

2차 구현:

- 종목·시장 일별 백필
- 프로그램매매 투자자별 당일 동향

종목별 프로그램매매는 공식 WebSocket 채널이 있으므로 실시간 경로를 우선한다. REST는 시장
집계, 누락 조정, 일별 백필에 남긴다.

## 2. API 선택

공식 구현은 [한국투자증권 Open Trading API 국내주식 예제](https://github.com/koreainvestment/open-trading-api/blob/main/examples_user/domestic_stock/domestic_stock_functions.py)를 기준으로 한다.

| 용도 | Path | TR ID | 1차 |
| --- | --- | --- | --- |
| 종목별 실시간 체결 KRX | WebSocket | `H0STPGM0` | 예, 주 경로 |
| 종목별 실시간 체결 NXT | WebSocket | `H0NXPGM0` | 예, 주 경로 |
| 종목별 체결 추이 | `/uapi/domestic-stock/v1/quotations/program-trade-by-stock` | `FHPPG04650101` | 예 |
| 시장 종합 시간 추이 | `/uapi/domestic-stock/v1/quotations/comp-program-trade-today` | `FHPPG04600101` | 예 |
| 종목별 일별 추이 | `/uapi/domestic-stock/v1/quotations/program-trade-by-stock-daily` | `FHPPG04650201` | 2차 |
| 시장 종합 일별 | `/uapi/domestic-stock/v1/quotations/comp-program-trade-daily` | `FHPPG04600001` | 2차 |
| 투자자별 당일 | `/uapi/domestic-stock/v1/quotations/investor-program-trade-today` | `HHPPG046600C1` | 2차 |

### 2.1 종목별 체결 추이

공식 예제: [종목별 프로그램매매추이](https://github.com/koreainvestment/open-trading-api/blob/main/examples_llm/domestic_stock/program_trade_by_stock/program_trade_by_stock.py)

```text
FID_COND_MRKT_DIV_CODE=J
FID_INPUT_ISCD=005930 또는 000660
```

KRX는 시장 구분 `J`, NXT는 `NX`로 각각 호출한다. 통합 `UN`은 사용하지 않는다.

사용 필드:

| 의미 | KIS 필드 |
| --- | --- |
| 체결 시각 | `bsop_hour` |
| 현재가 | `stck_prpr` |
| 누적 거래량 | `acml_vol` |
| 프로그램 매도 수량 | `whol_smtn_seln_vol` |
| 프로그램 매수 수량 | `whol_smtn_shnu_vol` |
| 프로그램 순매수 수량 | `whol_smtn_ntby_qty` |
| 프로그램 매도 대금 | `whol_smtn_seln_tr_pbmn` |
| 프로그램 매수 대금 | `whol_smtn_shnu_tr_pbmn` |
| 프로그램 순매수 대금 | `whol_smtn_ntby_tr_pbmn` |

### 2.2 종목별 실시간 프로그램매매

공식 예제: [국내주식 실시간프로그램매매](https://github.com/koreainvestment/open-trading-api/blob/main/examples_user/domestic_stock/domestic_stock_functions_ws.py)

```text
KRX TR ID=H0STPGM0
NXT TR ID=H0NXPGM0
tr_key=005930 또는 000660
```

| 의미 | WebSocket 필드 |
| --- | --- |
| 종목코드 | `mksc_shrn_iscd` |
| 체결 시각 | `stck_cntg_hour` |
| 프로그램 매도 체결량·대금 | `seln_cnqn`, `seln_tr_pbmn` |
| 프로그램 매수 체결량·대금 | `shnu_cnqn`, `shnu_tr_pbmn` |
| 프로그램 순매수 체결량·대금 | `ntby_cnqn`, `ntby_tr_pbmn` |
| 매도·매수 호가잔량 | `seln_rsqn`, `shnu_rsqn` |
| 전체 순매수 호가잔량 | `whol_ntby_qty` |

두 채널은 같은 11필드 구조다. 수신 TR ID가 `H0STPGM0`이면 KRX 테이블,
`H0NXPGM0`이면 NXT 테이블에 저장한다. 통합 `H0UNPGM0`은 두 원천의 경계를 흐리므로
구독하지 않는다.

### 2.3 시장 종합 시간 추이

공식 예제: [프로그램매매 종합현황 시간](https://github.com/koreainvestment/open-trading-api/blob/main/examples_llm/domestic_stock/comp_program_trade_today/comp_program_trade_today.py)

```text
FID_COND_MRKT_DIV_CODE=J
FID_MRKT_CLS_CODE=K  # 코스피
FID_MRKT_CLS_CODE=Q  # 코스닥
FID_SCTN_CLS_CODE=
FID_INPUT_ISCD=
FID_COND_MRKT_DIV_CODE1=
FID_INPUT_HOUR_1=
```

NXT 조회는 `FID_COND_MRKT_DIV_CODE=NX`로 별도 호출한다. KRX/NXT 지원 범위와 응답 시장
구분은 운영 키로 한 번 검증한 뒤 fixture에 고정한다.

공식 설명의 제약:

- 정규장 09:00~15:30 동안 최근 약 30분 데이터만 확인 가능
- 다음 조회가 없어 한 번의 응답 범위보다 오래된 장중 데이터는 폴링으로 보존해야 함
- 장 마감 뒤 15:30~17:00 행은 시각만 달라지고 값은 같은 마감 데이터일 수 있음

따라서 정규장 중 5분 폴링이 필요하고, 마감 뒤 반복 행은 수집하지 않는다.

## 3. 데이터 모델

`apps/models/market.py`에 같은 스키마의 `KrxProgramTradeSnapshot`,
`NxtProgramTradeSnapshot`을 추가한다.

| 컬럼 | 타입 | 설명 |
| --- | --- | --- |
| `provider` | text | `kis` |
| `scope` | text | `stock` 또는 `market` |
| `target` | text | 두 종목 심볼 또는 `KOSPI`, `KOSDAQ` |
| `observed_at` | timestamptz | KIS 영업일+시각을 UTC로 변환 |
| `reference_price` | numeric nullable | 종목 현재가 또는 시장 기준 가격 |
| `accumulated_volume` | bigint nullable | 응답의 누적 거래량 |
| `sell_volume` | bigint | 해당 시각까지 프로그램 누적 매도 수량 |
| `buy_volume` | bigint | 해당 시각까지 프로그램 누적 매수 수량 |
| `net_buy_quantity` | bigint | 해당 시각까지 프로그램 누적 순매수 수량 |
| `sell_amount` | numeric | 해당 시각까지 프로그램 누적 매도 대금 |
| `buy_amount` | numeric | 해당 시각까지 프로그램 누적 매수 대금 |
| `net_buy_amount` | numeric | 해당 시각까지 프로그램 누적 순매수 대금 |
| `sell_orderbook_balance` | bigint nullable | 실시간 프로그램 매도 호가잔량 |
| `buy_orderbook_balance` | bigint nullable | 실시간 프로그램 매수 호가잔량 |
| `net_orderbook_balance` | bigint nullable | 실시간 전체 순매수 호가잔량 |
| `source_record_id` | bigint FK | 수집 계보 |

멱등 키:

```text
(provider, scope, target, observed_at)
```

물리 테이블은 `krx_program_trade_snapshot`, `nxt_program_trade_snapshot`이다. 두 테이블의
`target`은 동일한 `SAMSUNG_ELECTRONICS`, `SK_HYNIX`, `KOSPI`, `KOSDAQ`을 사용한다.
`venue` 컬럼과 심볼 접미사는 두지 않는다. 저장 테이블 자체가 거래소를 결정한다.

수량과 금액은 KIS 원단위를 그대로 저장한다. 화면에서 억/백만 단위로 바꾸며 DB 값의 단위를
수집기에서 바꾸지 않는다. 순매수는 음수가 정상이다.

WebSocket 종목 행은 수신 시점의 KST 영업일과 `stck_cntg_hour`를 결합해 초 단위로 저장한다.
같은 종목에서 같은 초에 여러 프레임이 오면 마지막 프레임이 자연키를 갱신한다. 원본 틱을 모두
보존할 필요가 생기기 전까지 별도 sequence 컬럼은 만들지 않는다.

테이블의 수량·대금은 REST와 비교 가능한 **누적값**으로 통일한다. 운영 프로브에서 연속
WebSocket 프레임을 REST의 같은 시각 행과 비교한다. WebSocket 값이 이미 누적이면 그대로
저장하고, 개별 증분이면 장 시작 REST 값으로 accumulator를 초기화해 누적으로 바꾼다. 이 의미를
확정하기 전에는 실시간 프레임을 운영 테이블에 쓰지 않는다.

시장 시간 API의 실제 응답 시각 필드명은 운영 키 프로브로 확정한 뒤 Pydantic 모델에 고정한다.
여러 행을 수집 시각 하나로 뭉개지 않는다. 응답에 고유 시각이 없다면 1차 시장 저장은 중단하고
종목별 수집만 배포한다. 행 순번을 가짜 시각으로 만드는 방식은 사용하지 않는다.

## 4. 수집 흐름

### 4.1 WebSocket 상주 수집기

`airflow/modules/collectors/kis_realtime.py`가 문서 1의 주식 체결과 같은 연결에서 두 종목의
KRX `H0STPGM0`과 NXT `H0NXPGM0`을 추가 구독한다. 새 연결이나 새 컨테이너를 만들지 않는다.

- approval key는 연결 시작 때 한 번 발급
- 프로그램 프레임은 11개 필드 수를 먼저 검증
- TR ID에 따라 KRX/NXT 프로그램 테이블에 upsert
- DB 쓰기는 짧은 배치로 묶되 최대 지연은 1초
- 연결 종료 시 exponential backoff 재연결 후 전 채널 재구독
- WebSocket 세션별 `source_record(source_type='websocket')` 생성

### 4.2 REST 수집기

기존 `airflow/modules/collectors/kis.py`에 다음을 추가한다.

- `fetch_stock_program_trade()`
- `fetch_market_program_trade()`
- 종목·시장 응답 파서
- `store_program_trade()`에서 시장 구분 코드로 SQL 경로 선택

기존 `_get()`, 토큰 캐시, KIS 오류, `source_record` 패턴을 그대로 쓴다. 프로그램매매만을
위한 새 HTTP 클라이언트나 base class는 만들지 않는다.

파서 규칙:

- 영업일+`bsop_hour`를 KST로 읽고 UTC 저장
- 쉼표, 공백, 음수 허용
- `rt_cd != "0"` 실패
- 장중 빈 output은 실패로 기록하되 다른 대상은 계속 수집
- 응답 내 같은 시각은 마지막 값으로 upsert
- 시장 마감 뒤 복제 행은 저장하지 않음

### 4.3 REST 조정 DAG

새 파일 `airflow/dags/kis_program_trade_intraday.py`를 만든다.

- 스케줄: 평일 KST 08:00~20:00, 5분마다
- 대상: KRX·NXT 각각 두 종목 + 코스피(`K`) + 코스닥(`Q`)
- 기본 재시도: 1회, 2분 뒤
- 한 대상 실패는 `source_record.metadata`에 남기고 나머지는 저장
- 모든 대상 실패 시 run 실패

각 run은 KRX 정규장과 NXT의 프리·메인·애프터 세션을 확인해 열린 거래소만 호출한다. NXT
시장 종합 REST가 실제로 지원되지 않으면 NXT 시장 행을 만들지 않고 NXT 종목 WebSocket과
종목 REST만 운영한다. KRX 값을 NXT 테이블로 복사해 빈 구간을 채우지 않는다.

REST 프로그램 DAG는 분봉 DAG와 합치지 않는다. 프로그램매매 API의 실패가 가격 봉 수집을
막아서는 안 되고 저장 테이블·파서·운영 목적도 다르다. 반면 WebSocket은 연결 비용과 재연결
로직을 줄이기 위해 문서 1·2의 실시간 채널과 같은 상주 프로세스를 쓴다.

## 5. 변경 파일

### 작업 1 — 응답 프로브

- 운영 키로 5개 API 중 1차 두 종류를 각 1회 호출
- WebSocket 연속 프레임과 같은 시각 REST 행을 비교해 수량·대금이 누적인지 증분인지 확정
- 종목은 삼성전자, 시장은 코스피로 확인
- 응답 키, 배열 방향, 시각 필드, 금액 단위를 테스트 fixture에 고정
- 앱키·토큰·원문 응답은 저장소에 커밋하지 않음

### 작업 2 — 모델과 migration

- 수정: `apps/models/market.py`
- 추가: 새 Alembic revision
- 수정: `tests/models/test_market_models.py`
- 추가: `tests/migrations/test_program_trade_schema.py`

검증:

- 두 물리 테이블의 컬럼·제약이 동일함
- 각 테이블에 독립된 자연키와 `source_record_id` FK가 있음
- KRX 저장이 NXT 행을 갱신할 수 없음

### 작업 3 — SQL과 REST 수집기

- 추가: `airflow/sql/postgres/krx_program_trade_snapshot/upsert.sql`
- 추가: `airflow/sql/postgres/nxt_program_trade_snapshot/upsert.sql`
- 수정: `airflow/modules/collectors/kis.py`
- 수정: `tests/collectors/test_kis.py`

### 작업 4 — WebSocket 수집기

- 수정: `airflow/modules/collectors/kis_realtime.py`
- 수정: `compose/local/airflow/requirements.txt`
- 수정: `compose/local/airflow/docker-compose.yaml`
- 수정: `tests/collectors/test_kis_realtime.py`

검증:

- `H0STPGM0`, `H0NXPGM0` 구독 메시지와 11필드 프레임
- 초 단위 시각 변환과 같은 초 upsert
- 재연결 뒤 자동 재구독
- 문서 1·2 채널의 실패와 프로그램 프레임 실패 격리
- 동일 종목·시각의 KRX와 NXT 값이 서로 덮어쓰지 않음

### 작업 5 — REST 조정 DAG

- 추가: `airflow/dags/kis_program_trade_intraday.py`
- 추가: `tests/dags/test_kis_program_trade_intraday.py`

## 6. 테스트

최소 자동 테스트:

- 삼성전자 종목 응답을 정확한 시각·수량·금액으로 변환
- 코스피 시장 응답의 각 행을 별도 시각으로 변환
- 음수 순매수와 0 허용
- 잘못된 숫자·시각 거부
- 같은 자연키 재저장 시 행 증가 없음
- 마감 뒤 동일 데이터 제거
- 부분 실패와 전체 실패 구분
- SQL 컬럼과 SQLAlchemy 모델 일치
- WebSocket 프레임 수신부터 DB 반영까지 1초 이내
- 강제 연결 종료 뒤 재구독과 REST 누락 복구

실행:

```bash
uv run pytest tests/collectors/test_kis.py tests/models/test_market_models.py tests/migrations -q
DJANGO_SETTINGS_MODULE=config.settings.test uv run python manage.py check
```

실데이터 확인:

```sql
SELECT
    'KRX' AS venue,
    target,
    min(observed_at),
    max(observed_at),
    count(*),
    min(net_buy_quantity),
    max(net_buy_quantity)
FROM krx_program_trade_snapshot
WHERE provider = 'kis'
GROUP BY target
UNION ALL
SELECT
    'NXT' AS venue,
    target,
    min(observed_at),
    max(observed_at),
    count(*),
    min(net_buy_quantity),
    max(net_buy_quantity)
FROM nxt_program_trade_snapshot
WHERE provider = 'kis'
GROUP BY target;
```

## 7. 완료 조건

- 삼성전자·SK하이닉스 프로그램 순매수 수량과 대금이 KRX/NXT 물리 테이블에 각각 저장된다.
- 코스피·코스닥 지원이 실측 확인되면 해당 거래소 테이블에 시장 스냅샷이 저장된다.
- 두 테이블 안의 종목·시장 심볼은 같고 거래소 접미사를 붙이지 않는다.
- 동일 시각 재수집은 중복 행을 만들지 않는다.
- 장 마감 뒤 복제값이 새 시계열처럼 쌓이지 않는다.
- WebSocket 재연결 뒤 5분 이내 REST 조정으로 누락 구간이 복구된다.
- Grafana에서 가격 분봉과 `observed_at` 기준으로 함께 조회할 수 있다.

## 8. 2차 범위

수요가 확인되면 REST 일별 API를 이용해 다음을 추가한다.

- 종목별 일별 프로그램매매 백필 (`FHPPG04650201`)
- 시장 종합 일별 백필 (`FHPPG04600001`)
- 코스피·코스닥 투자자별 프로그램매매 당일 동향 (`HHPPG046600C1`)

KRX+NXT 통합 프로그램매매(`H0UNPGM0`, 시장 구분 `UN`)는 별도 수요가 생길 때만 추가한다.
