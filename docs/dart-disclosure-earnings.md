# DART 공시·실적 수집

- 기준일: 2026-08-25
- 상태: 구현 완료

`dart_disclosure_intraday`가 삼성전자와 SK하이닉스의 새 공시를 이벤트로 저장하고, 잠정실적
공시 원문이나 정기보고서 재무제표에서 실적 숫자를 추출한다.

## 실행 계약

| 항목 | 값 |
| --- | --- |
| 스케줄 | KST 평일 07:00~20:58, 2분마다 |
| 기본 조회 창 | 최근 7일 |
| 대상 | 삼성전자 `005930` / DART `00126380`, SK하이닉스 `000660` / DART `00164779` |
| 저장 | `disclosure_event`, `earnings_fact`, `source_record` |
| 재시도 | 1회, 2분 뒤 |

`lookback_days`로 목록 조회와 미완료 실적 재시도 창을 함께 늘릴 수 있다.

```bash
airflow dags trigger dart_disclosure_intraday --conf '{"lookback_days":30}'
```

## 처리 순서

1. `collect_disclosures`가 회사별 `list.json`을 끝 페이지까지 읽고 접수번호로 공시를 upsert한다.
2. `extract_earnings`가 최근 공시 중 실적 행이 아직 없는 잠정·정기 공시를 다시 찾는다.
3. 잠정 공시는 `document.xml`, 정기 공시는 `fnlttSinglAcntAll.json`에서 세 지표를 추출한다.

공시 저장이 실적 추출보다 먼저다. 원문 표가 바뀌거나 재무제표 반영이 늦어도 공시 이벤트는
남고, 미완료 공시는 별도 큐 없이 다음 2분 실행에서 다시 선택된다.

## 공시 이벤트

자연키는 `(provider, rcept_no)`다. 재조회 시 이름·접수일·비고와 계보는 갱신하지만
`detected_at`은 최초값을 지킨다.

- `receipt_date`: DART가 주는 접수 날짜. 시·분이 없으므로 자정 시각을 만들지 않는다.
- `detected_at`: 수집기가 접수번호를 처음 본 UTC 시각. 공시 시각의 상한은 폴링 주기와 DART
  목록 반영 지연의 합이다.
- 분 단위 접수 시각은 최신 50건뿐인 RSS에 의존해야 해 수집하지 않는다.

목록은 한 페이지 100건, 최대 40페이지다. `total_count`보다 적게 읽혔으면 잘린 목록을
저장하지 않고 실패시킨다. 공시 0건은 정상이며 0건 `source_record`를 남긴다.

## 잠정실적 추출

대상 공시명은 정정 접두사를 제거한
`연결재무제표기준영업(잠정)실적(공정공시)`다. 원문 ZIP 안 XML에서 위치가 아니라 공식 표 ID를
사용한다.

| 표 | ID | 용도 |
| --- | --- | --- |
| 실적 기간 | `XFormD1_Form0_Table0` | 당해·누계 기간 종료일 |
| 실적 값 | `XFormD1_Form0_RepeatTable0` | 매출액·영업이익·당기순이익 |

정정 공시는 앞에 `XFormD8_*` 표가 추가되므로 첫 번째 표를 읽지 않는다. 지표 이름은 정확히
일치시켜 `지배기업 소유주지분 순이익`을 당기순이익으로 오인하지 않는다.

각 지표에서 당해실적(`period`)과 누계실적(`cumulative`)을 저장하며 전년 동기 값이 있으면
함께 보존한다. `-`는 0이 아니라 결측이다. 공시 안의 `원`, `천원`, `백만원`, `십억원`,
`억원`, `조원` 단위를 읽어 원 단위로 정규화한다. 연결 여부도 원문 표에서 읽어 `CFS` 또는
`OFS`로 남긴다.

## 정기보고서 추출

`분기보고서 (YYYY.MM)`, `반기보고서`, `사업보고서`에서 사업연도와 OpenDART 보고서코드를
결정한다. 재무제표 API는 접수번호가 아니라 회사·사업연도·보고서코드로 조회하므로 응답의
`rcept_no`가 처리 중인 공시와 다르면 아직 반영되지 않은 것으로 보고 다음 실행까지 기다린다.

연결(`CFS`)을 먼저 사용하고 데이터가 없을 때만 별도(`OFS`)를 사용한다. 둘을 합치지 않는다.
손익계산서 `IS`만 읽어 `CIS` 중복을 피하고, 변하는 계정명이 아니라 다음 `account_id`로
지표를 식별한다.

| 지표 | account_id |
| --- | --- |
| 매출액 | `ifrs-full_Revenue` |
| 영업이익 | `dart_OperatingIncomeLoss` |
| 당기순이익 | `ifrs-full_ProfitLoss` |

`thstrm_amount`는 당기, `thstrm_add_amount`는 누계다. 금액은 원 단위이며 통화는 응답값이
없을 때 `KRW`를 사용한다.

## 실적 저장과 정정

`earnings_fact` 자연키는
`(provider, rcept_no, statement_scope, amount_basis, metric)`다. 같은 접수번호의 원문이
바뀌면 값을 갱신하고 잠정 원문 ZIP의 SHA-256을 `source_record.metadata`에 남긴다. 정정
공시는 새 접수번호이므로 원 공시를 덮지 않고 별도 행으로 남는다.

저장 지표는 `revenue`, `operating_profit`, `net_income`, 발표 종류는 `provisional` 또는
`periodic`, 금액 기준은 `period` 또는 `cumulative`다.

## 실패와 보안

- 한 회사의 목록 호출이 실패해도 다른 회사는 저장하며 둘 다 실패해야 목록 태스크가 실패한다.
- HTTP 400·401·403·404와 인증·요청 본문 오류는 즉시 실패한다.
- HTTP 5xx와 네트워크 오류는 Airflow 재시도 대상으로 둔다.
- DART `013`(데이터 없음)은 0건, `014`(원문 없음)는 다음 실행 재시도다.
- `020`(요청 제한)은 즉시 반복하지 않고 다음 예약 실행으로 넘긴다.
- 한 공시의 파싱 실패는 다른 공시 추출을 막지 않고 그 접수번호만 다음 실행에서 재시도한다.

`DART_API_KEY`와 `CONNECTION_ID`가 필요하다. API 키가 질의 문자열에 들어가므로 URL을 로그나
예외에 넣지 않고 `source_record.payload`에도 원본 요청을 저장하지 않는다.

## 구현과 검증 위치

- DAG: `airflow/dags/dart_disclosure_intraday.py`
- 수집·파싱: `airflow/modules/collectors/document/dart.py`
- SQL: `airflow/sql/postgres/disclosure_event/`, `earnings_fact/`
- 테스트: `tests/collectors/test_dart.py`, `tests/dags/test_dart_disclosure_intraday.py`
