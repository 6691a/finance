# 개발 문서 5 — 삼성전자·SK하이닉스 DART 공시·실적

> 작성 기준: 2026-08-11  
> 상태: 미구현 기능의 실행 계획  
> 대상: 삼성전자(`005930`), SK하이닉스(`000660`)

## 1. 결론

공시, 잠정실적, 정기실적을 각각 다른 수집기로 만들지 않는다. 세 데이터는 모두 DART의
접수번호(`rcept_no`)로 연결되므로 다음 한 흐름으로 수집한다.

1. 두 회사의 새 공시를 2분마다 확인해 `disclosure_event`에 저장한다.
2. 잠정실적 공시이면 공시 원문에서 매출액·영업이익·당기순이익을 추출한다.
3. 사업·반기·분기보고서이면 OpenDART 재무제표 API에서 같은 세 지표를 추출한다.
4. 추출한 숫자는 `earnings_fact`에 저장하고 원래 공시와 `rcept_no`로 연결한다.

DART는 WebSocket 실시간 데이터가 아니다. 장중에도 새 공시를 조회할 수 있지만, 실제 반영
속도는 **폴링 주기 + DART 반영 지연**이다. 따라서 저장하는 시각은 `최초 감지 시각`이고
화면에도 그렇게 표시한다. 접수일에는 시·분이 없으므로 자정 같은 값으로 꾸미지 않는다.

**분 단위 접수 시각은 수집하지 않는다.** 초판은 공식 RSS로 그 값을 보강하려 했지만
구현 중 실측에서 접었다. 근거는 §3.3에 있다.

250일 일봉, 공시 AI 요약, 투자 판단 점수는 이번 범위에 넣지 않는다.

## 2. 수집 범위

### 2.1 모든 공시 이벤트

두 회사에 접수된 공시를 종류와 관계없이 보존한다.

- 접수번호와 보고서명
- 회사·종목코드
- 제출인
- 접수일과 확인 가능한 경우 접수 시각
- 유가증권시장·코스닥시장 등 법인 구분
- 정정·철회·유가증권신고서 관련 비고

이 이벤트가 있어야 분봉·수급과 같은 시간축에서 “가격이 움직이기 전에 어떤 공시가
나왔는가”를 확인할 수 있다.

### 2.2 잠정실적

두 회사가 실제 사용하는 `연결재무제표기준영업(잠정)실적(공정공시)`와 그 정정 공시를
대상으로 한다. OpenDART의 정기 재무제표 API가 아니라 공시 원문 표를 읽어야 하므로, 공시
이벤트 저장과 숫자 추출 성공을 분리한다. 원문 형식이 바뀌어 숫자 추출이 실패해도 공시
이벤트까지 버리지 않는다.

1차 저장 지표는 매출액, 영업이익, 당기순이익뿐이다. 전망치 대비 차이와 전년 동기 대비
증감률은 저장하지 않고 조회할 때 계산한다.

### 2.3 정기실적

사업보고서, 반기보고서, 분기보고서와 그 정정 보고서를 대상으로 한다. 연결재무제표(`CFS`)를
우선하고, 연결 값이 없을 때만 별도재무제표(`OFS`)를 저장한다. 두 값을 합치거나 서로 대체한
것처럼 표시하지 않는다.

## 3. 공식 원천 계약

### 3.1 회사 고유번호

공식 문서: [OpenDART 고유번호](https://opendart.fss.or.kr/guide/detail.do?apiGrpCd=DS001&apiId=2019018)

```text
GET https://opendart.fss.or.kr/api/corpCode.xml
crtfc_key=<DART_API_KEY>
```

응답 ZIP 안의 `CORPCODE.xml`에서 다음 매핑을 운영 시 다시 검증한다.

| 회사 | 종목코드 | DART 회사 고유번호 |
| --- | --- | --- |
| 삼성전자 | `005930` | `00126380` |
| SK하이닉스 | `000660` | `00164779` |

대상이 두 회사뿐이므로 별도 회사 마스터 테이블은 만들지 않고 검증된 값을 `DartCompany`
상수에 둔다. 대상 회사가 설정으로 늘어날 때만 마스터 테이블을 추가한다.

### 3.2 공시 목록

공식 문서: [OpenDART 공시검색](https://opendart.fss.or.kr/guide/detail.do?apiGrpCd=DS001&apiId=2019001)

| 항목 | 값 |
| --- | --- |
| Method | `GET` |
| Path | `https://opendart.fss.or.kr/api/list.json` |
| 인증 | `crtfc_key` |
| 대상 | `corp_code`로 회사별 조회 |
| 정렬 | 접수일 내림차순 |

요청값:

```text
corp_code=<검증된 회사 고유번호>
bgn_de=<오늘-7일, YYYYMMDD>
end_de=<오늘, YYYYMMDD>
sort=date
sort_mth=desc
last_reprt_at=N
page_no=1
page_count=100
```

사용 필드:

| 저장 의미 | OpenDART 필드 |
| --- | --- |
| 회사 구분 | `corp_cls` |
| 회사명 | `corp_name` |
| 회사 고유번호 | `corp_code` |
| 종목코드 | `stock_code` |
| 보고서명 | `report_nm` |
| 접수번호 | `rcept_no` |
| 제출인 | `flr_nm` |
| 접수일 | `rcept_dt` |
| 비고 | `rm` |

`rcept_dt`는 `YYYYMMDD` 날짜이며 시·분을 주지 않는다. 이를 자정이나 장 마감 시각으로
꾸며 저장하지 않는다.

매번 최근 7일을 다시 조회해 프로세스 중단, 휴일, 늦은 정정을 흡수한다. 회사별 공시량이
100건을 넘으면 `total_page`까지 페이지를 진행하며, 커서가 움직이지 않으면 중단한다.

### 3.3 분 단위 접수 시각 — 수집하지 않기로 했다

공식 안내: [DART RSS 서비스](https://dart.fss.or.kr/introduction/content6.do)

```text
GET https://dart.fss.or.kr/api/todayRSS.xml
```

공식 RSS는 링크의 `rcpNo`와 `dc:date`를 준다. `dc:date`는 UTC이고 초가 항상 `00`이라
**분 단위 시각**이다. 초판 설계는 이 값으로 `published_at`을 보강하려 했다. **구현 중
실측에서 접었다.**

| 측정(2026-08-12) | 값 |
| --- | --- |
| RSS 항목 수 | 50건(전 상장사) |
| RSS가 덮는 구간 | 08:28Z ~ 10:03Z = **1시간 35분** |
| 우리가 저장한 공시 | 52건, 접수일 2026-05-15 ~ 08-12 |
| 겹치는 접수번호 | **0건** |

- **과거는 원리적으로 못 채운다.** RSS는 오늘 것만 주고 그마저 1시간 반치다.
- **현재도 얻는 게 작다.** 2분 폴링이면 `detected_at`이 이미 2분 해상도다. 분 단위 시각과의
  차이는 1분봉 두 개다.
- **비용은 작지 않다.** 2분마다 호출 하나와 `source_record` 한 행이다. 하루 720행이 시각
  하나를 위해 쌓인다.
- 게다가 이 호스트는 브라우저처럼 보이지 않는 요청의 연결을 끊어서(실측: `RemoteDisconnected`)
  User-Agent 우회가 붙는다. 제공처가 정책을 바꾸면 그 태스크만 죽는다.

그래서 `disclosure_event`는 `receipt_date`와 `detected_at` 둘만 갖는다. `detected_at`은
공시 시각의 **상한**이라 "공시가 이 시각 이전에 나왔다"는 판단에는 오히려 안전하다.
DART 목록 반영 지연 자체를 재야 하면 그때 회사별 RSS(`corpRSS.do`)를 계약 실측부터 붙인다.

### 3.4 공시 원문

공식 문서: [OpenDART 공시서류 원본파일](https://opendart.fss.or.kr/guide/detail.do?apiGrpCd=DS001&apiId=2019003)

```text
GET https://opendart.fss.or.kr/api/document.xml
crtfc_key=<DART_API_KEY>
rcept_no=<접수번호>
```

응답 ZIP의 XML/HTML 표에서 잠정실적 숫자를 읽는다. HTTP 200이라도 오류 XML일 수 있으므로
ZIP magic(`PK`)과 `status/message` 오류 응답을 구분한다. ZIP은 메모리에서 처리하고, 현재
없는 원문 파일 저장소를 이 기능 때문에 새로 만들지 않는다. `source_record`에는 접수번호,
응답 상태, 파일명, SHA-256과 단위만 metadata로 남기고 추출 숫자는 `earnings_fact`에 저장한다.
같은 접수번호의 첨부가 바뀌면 SHA-256 변화로 감지하고 경고한 뒤 최신 값을 upsert한다. 원문과
이전 첨부 버전의 영구 보존이 실제로 필요해지면 그때 `payload_uri`가 가리킬 객체 저장소를
붙인다.

### 3.5 정기 재무제표

공식 문서: [OpenDART 단일회사 전체 재무제표](https://opendart.fss.or.kr/guide/detail.do?apiGrpCd=DS003&apiId=2019020)

| 항목 | 값 |
| --- | --- |
| Path | `https://opendart.fss.or.kr/api/fnlttSinglAcntAll.json` |
| `reprt_code=11013` | 1분기보고서 |
| `reprt_code=11012` | 반기보고서 |
| `reprt_code=11014` | 3분기보고서 |
| `reprt_code=11011` | 사업보고서 |
| `fs_div` | `CFS` 우선, 없으면 `OFS` |

필요 필드는 `rcept_no`, `account_id`, `account_nm`, 당기·전기 금액, 누계 금액, 통화다. 실제
삼성전자·SK하이닉스 응답 fixture에서 손익계산서의 매출액·영업이익·당기순이익 계정 ID와
분기/누계 필드 조합을 확정한다. 계정명이 비슷하다는 이유만으로 숫자를 추측해 저장하지 않는다.

이 API는 접수번호가 아니라 회사·사업연도·보고서코드로 조회한다. 응답 `rcept_no`가 처리 중인
공시의 접수번호와 같을 때만 연결하고, 다르면 저장하지 않고 다음 run에서 재확인한다. 수집기
가동 전에 이미 정정된 과거 보고서는 정정 전 수치를 이 API만으로 복원할 수 없다. 이 경우에도
공시 이벤트 이력은 남기되, 얻지 못한 과거 실적을 추정해 만들지 않는다.

## 4. 시각 의미

`disclosure_event`는 다음 두 시각을 구분한다.

| 컬럼 | 의미 |
| --- | --- |
| `receipt_date` | OpenDART가 준 접수일. 날짜뿐이고 시·분이 없다 |
| `detected_at` | 우리 수집기가 해당 접수번호를 처음 본 시각(UTC) |

`detected_at`은 재수집 때 갱신하지 않고 최초값을 보존한다. 2분 폴링이라 공시 시각의
**상한**이며 오차는 폴링 주기와 DART 목록 반영 지연의 합이다.

장전·장중·장후 구분은 저장 컬럼으로 만들지 않는다. `detected_at`을 근사값으로 써서
조회에서 구분한다. 휴장일을 정확히 분류할 필요가 생기면 그때 거래일 캘린더를 붙인다.
`market_session`이 이미 KRX 거래일을 갖고 있다.

## 5. 데이터 모델

### 5.1 `disclosure_event`

`apps/models/market.py`에 `DisclosureEvent`를 추가한다.

| 컬럼 | 타입 | 설명 |
| --- | --- | --- |
| `provider` | text | `dart` |
| `corp_code` | text | DART 회사 고유번호 |
| `stock_code` | text | `005930`, `000660` |
| `company_name` | text | 회사명 |
| `rcept_no` | text | DART 접수번호 |
| `report_name` | text | 보고서명 원문 |
| `filer_name` | text | 제출인 |
| `corp_class` | text | DART 법인 구분 |
| `receipt_date` | date | DART 접수일 |
| `detected_at` | timestamptz | 최초 수집 시각 UTC |
| `remarks` | text nullable | DART 비고 원문 |
| `source_record_id` | bigint FK | 근거가 되는 수집 레코드 |

멱등 키는 `(provider, rcept_no)`다. 같은 접수번호를 반복 조회해도 행이 늘지 않고
`detected_at`은 최초값을 지킨다.

### 5.2 `earnings_fact`

실적은 한 공시에 지표와 기간 기준이 여러 개 있으므로 지표당 한 행으로 저장한다.

| 컬럼 | 타입 | 설명 |
| --- | --- | --- |
| `provider` | text | `dart` |
| `stock_code` | text | `005930`, `000660` |
| `rcept_no` | text | 원문 또는 재무제표 API가 반환한 접수번호 |
| `release_type` | text | `provisional` 또는 `periodic` |
| `period_end` | date | 실적 대상 기간 종료일 |
| `statement_scope` | text | `CFS` 또는 `OFS` |
| `amount_basis` | text | `period` 또는 `cumulative` |
| `metric` | text | `revenue`, `operating_profit`, `net_income` |
| `current_amount` | numeric | 당기 금액, 원단위 |
| `prior_year_amount` | numeric nullable | 비교 가능한 전년 동기 금액 |
| `currency` | text | 원문 통화 |
| `source_account_id` | text nullable | OpenDART 원계정 ID |
| `source_account_name` | text | 원문 항목명 |
| `source_record_id` | bigint FK | 원문/API 수집 계보 |

멱등 키:

```text
(provider, rcept_no, statement_scope, amount_basis, metric)
```

금액은 원문 단위를 원화로 임의 환산하지 않는다. 원문이 백만원 단위이면 숫자와 단위를 함께
읽어 원 단위로 정규화하고, 변환 배수는 `source_record.metadata`에 남긴다. 음수와 0은
정상값이다. 공시에 없는 지표는 행을 만들지 않으며 `-`를 0으로 바꾸지 않는다.

새 접수번호로 들어온 정정 공시는 새 행으로 저장한다. 이전 행을 덮어쓰지 않으며, 조회에서
같은 회사·기간·지표 중 가장 최근 접수번호를 선택한다. 같은 접수번호의 첨부 교체는 1차에서
최신 정규화 값만 유지하고 `source_record`의 SHA-256 변경 이력으로 식별한다.

`release_type`, `statement_scope`, `amount_basis`, `metric`은 `StrEnum`과 비원시 SQLAlchemy
Enum으로 선언하고 같은 허용값을 CHECK로 고정한다. 공급자 원문 필드는 새 값 수용이 필요하므로
무리하게 Enum으로 막지 않는다.

## 6. 수집 흐름

### 6.1 공시 폴링

새 `airflow/dags/dart_disclosure_intraday.py`를 만든다.

- 스케줄: 평일 KST 07:00~20:59, 2분마다
- 대상: 삼성전자, SK하이닉스 두 회사
- 조회 범위: 실행일 포함 최근 7일
- 재시도: 네트워크/5xx만 1회, 2분 뒤
- 인증·파싱 오류: 즉시 실패
- 한 회사 실패: 다른 회사는 저장하고 실패 회사는 `source_record.metadata`에 기록
- 두 회사 모두 실패: DAG run 실패

공시가 없는 것은 정상이다. 빈 목록도 성공한 `source_record`로 남긴다. 호출 제한 오류는
즉시 재시도해 요청 폭주를 만들지 않고 다음 예약 실행으로 넘긴다.

OpenDART 상태 처리:

- `013` 데이터 없음: 공시 목록은 정상 0건, 새 재무제표는 다음 run에서 재확인
- `014` 원문 파일 없음: 새 공시 직후이면 다음 run에서 재확인
- `020` 요청 제한: 즉시 반복하지 않고 다음 run으로 넘김
- 인증·권한·요청 오류: 재시도 없이 실패
- 점검·네트워크·HTTP 5xx: 제한된 backoff 후 재시도

공식 안내의 요청 제한 수치는 계정마다 달라질 수 있으므로 고정된 일일 한도로 가정하지 않는다.

### 6.2 새 공시 처리

```text
회사별 공시 목록
  → 접수번호 기준 disclosure_event upsert
  → 잠정실적이면 원문 파서 실행
  → 정기보고서이면 재무제표 API 실행
  → earnings_fact upsert
```

보고서 분류는 공시명 원문과 정기보고서 코드를 함께 사용한다. 정정 접두사가 붙어도 잠정실적
공시를 찾을 수 있게 정규화하되, 원래 `report_name`은 그대로 보존한다.

재무제표 API나 원문 파싱이 아직 준비되지 않은 새 공시는 다음 폴링에서 다시 시도한다.
`disclosure_event`에는 있지만 해당 `rcept_no`의 `earnings_fact`가 없는 행만 재처리하면 별도
작업 큐가 필요 없다.

### 6.3 수집기

새 `airflow/modules/collectors/dart.py`에 다음만 둔다.

- `DartCompany`
- 공시 목록·원문·재무제표 HTTP 호출
- 잠정·정기 실적 분류와 세 지표 파서
- `source_record`, `disclosure_event`, `earnings_fact` 저장

HTTP, RSS XML과 ZIP 처리는 표준 라이브러리를 우선하고, 공시 원문 HTML 파싱은 프로젝트에
이미 설치된 파서를 재사용한다. DART만을 위한 범용 클라이언트 계층이나 새 패키지는 만들지
않는다.

회사별 목록 호출은 회사마다 `source_record`를 만든다. 목록 레코드는 회사 고유번호, 기간,
페이지 수, 전체 건수를 metadata에 남긴다. 원문·재무 API도 접수번호와 응답 SHA-256, 단위를 metadata에 남기고 추출 숫자는
`earnings_fact`에만 저장한다. API 키와 원문 XML은 `payload`나 metadata에 넣지 않는다.

## 7. 변경 파일

### 작업 1 — 운영 응답 확인

- `corpCode.xml`에서 두 회사 고유번호 확인
- 두 회사의 일반 공시·잠정실적·정기보고서·정정 공시 각 fixture 확보
- RSS의 `rcpNo`, `dc:date`와 목록 접수번호 매칭 확인
- 재무제표의 세 계정 ID, 분기/누계 필드, 통화 확인
- API 키와 원문 전체는 fixture에 넣지 않음

### 작업 2 — 모델과 migration

- 수정: `apps/models/market.py`
- 추가: 새 Alembic revision
- 수정: `tests/models/test_market_models.py`
- 추가: `tests/migrations/test_disclosure_earnings_schema.py`

### 작업 3 — SQL과 수집기

- 추가: `airflow/modules/collectors/dart.py`
- 추가: `airflow/sql/postgres/disclosure_event/upsert.sql`
- 추가: `airflow/sql/postgres/earnings_fact/upsert.sql`
- 추가: `tests/collectors/test_dart.py`

### 작업 4 — DAG

- 추가: `airflow/dags/dart_disclosure_intraday.py`
- 추가: `tests/dags/test_dart_disclosure_intraday.py`
- 수정: `compose/local/airflow/.env.sample`

필요 환경 변수는 `DART_API_KEY` 하나다.

### 작업 5 — 대시보드

- 추가: `compose/local/grafana/dashboards/dart-disclosure.json` (uid `dart-disclosure`)
- 추가: `tests/dashboards/test_dart_dashboard.py`

패널은 여섯이다. 공시 건수·실적 행 수·마지막 수집 지연(stat 셋), 공시 타임라인,
실적 추이, 최신 실적이다.

- **접수일과 최초 감지를 다른 열로 보여 준다.** 빈 시각을 자정으로 채우지 않고, 최초 감지를
  공시 시각이라고 부르지 않는다.
- 실적 패널은 같은 회사·기간·지표에서 `rcept_no`가 가장 큰 행만 고른다(`DISTINCT ON`).
  접수번호가 시간순으로 커지므로 이 규칙이 정정 공시와 정기보고서 확정치를 자연히 고른다.
- 전년 대비 증감률은 저장하지 않고 이 패널이 계산한다. 전년 값이 없거나 0이면 비워 둔다.
- 기간 기준(분기·누계)과 재무제표 범위(연결·별도)는 변수로 고른다. 값을 늘리면
  `tests/dashboards/test_dart_dashboard.py`가 화면에 빠진 값을 잡는다.
- **수집 경로별 상태 표는 두지 않는다.** 이 화면은 공시와 실적을 읽는 곳이고 수집이 도는지는
  Airflow가 이미 보여 준다. 화면에 남은 운영 지표는 `마지막 공시 수집` 지연 하나뿐이다.
  값이 안 늘어나는 이유를 화면에서 바로 알 수 있어야 해서 그것만 남긴다.
- "실적 대기 공시" 패널도 두지 않는다. 그걸 세려면 보고서 이름 분류 규칙을 SQL에 복사해야
  하고, 그러면 수집기와 화면 둘 중 하나만 고쳐도 조용히 어긋난다.

## 8. 최소 테스트

- 같은 접수번호 재수집 시 행이 늘지 않고 최초 `detected_at`이 유지됨
- 일반 공시 저장은 실적 파서 실패에 영향받지 않음
- 잠정실적 표의 단위·쉼표·괄호 음수 처리
- 정기 재무제표의 연결 우선·별도 fallback
- 분기 금액과 누계 금액을 다른 자연키로 저장
- 정정 공시가 이전 접수번호 행을 덮어쓰지 않음
- OpenDART 오류 상태와 빈 목록 구분
- API 키가 URL·로그·예외·`source_record`에 남지 않음
- 한 회사 실패 시 다른 회사 저장

검증 명령:

```bash
uv run pytest tests/collectors/test_dart.py tests/models/test_market_models.py \
  tests/migrations/test_disclosure_earnings_schema.py tests/dags/test_dart_disclosure_intraday.py -q
uv run ruff check apps airflow migrations tests
uv run pyrefly check
```

이 저장소에는 Django가 없다. 이전 판에 있던 `manage.py check`는 다른 프로젝트의 명령이었다.

## 9. 완료 조건

- 운영 시간 중 삼성전자·SK하이닉스의 새 공시가 DART에 노출된 뒤 다음 정상 폴링에서 저장된다.
- 잠정·정기실적의 세 지표가 기간·연결 여부·단위와 함께 저장된다.
- 새 접수번호의 정정 공시는 과거 행을 덮어쓰지 않고, 같은 접수번호의 첨부 변화는 경고된다.
- 공시 원문 파싱 실패가 일반 공시 이벤트 수집을 막지 않는다.
- 같은 구간을 다시 수집해도 중복 행이 생기지 않는다.

## 10. 이번 범위 아님

- 250일 일봉
- 전체 상장사 공시
- 공시 본문 전체의 영구 보관
- 같은 접수번호의 첨부 파일 버전별 영구 보관
- AI 요약·호재/악재 분류·투자 점수
- 컨센서스 대비 어닝 서프라이즈
- 공시 후 수익률 계산
