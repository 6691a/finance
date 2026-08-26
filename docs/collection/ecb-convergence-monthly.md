# ECB 유로 회원국 10년물 월평균 수집

- 기준일: 2026-08-25
- 상태: 구현 완료

`ecb_convergence_monthly`가 ECB Data Portal의 `IRS` dataflow에서 프랑스·이탈리아·스페인의
EMU 수렴 기준 10년 국채 금리 월평균을 받아 `indicator_observation`에 저장한다.

## 수집 범위

| 국가 | ECB 코드 | 저장 series_id | 라벨 |
| --- | --- | --- | --- |
| 프랑스 | `FR` | `FR10YM` | 프랑스 10년물(월평균) |
| 이탈리아 | `IT` | `IT10YM` | 이탈리아 10년물(월평균) |
| 스페인 | `ES` | `ES10YM` | 스페인 10년물(월평균) |

SDMX 키는 `M.<국가>.L.L40.CI.0000.EUR.N.Z`이고 세 국가를 `+`로 묶어 한 번 호출한다.
단위는 `Percent`, 만기는 120개월이다. 독일은 `bbk_bund_daily`가 같은 목적의 일별 값을
수집하므로 여기 넣지 않는다.

월별 값이라는 사실을 식별자 끝 `M`과 라벨에 남긴다. `observation_date`는 그 달의 1일이지만
값은 특정 날짜 고시가 아니라 월평균이다.

## 스케줄과 조회 구간

값은 월별이지만 KST 매주 수요일 08:30에 실행한다. 공표가 다음 달 중순 무렵이고 개정될 수
있어 월 1회보다 짧게 확인한다. 기본 190일을 되돌아보므로 한 번 놓친 공표와 사후 개정을
함께 흡수한다.

| param | 기본값 | 의미 |
| --- | --- | --- |
| `observation_start` | `null` | 조회 시작일 |
| `observation_end` | `null` | 조회 종료일, 비우면 실행일 KST |
| `lookback_days` | `190` | 명시 구간이 없을 때 되돌아볼 일수 |

날짜는 API 요청 직전에 `YYYY-MM`으로 바뀐다. 저장 필터는 다시 달의 1일을 사용하므로 시작일이
달 중간이면 그 달의 1일 관측값은 범위 밖이다. 특정 달부터 받으려면 시작일을 1일로 준다.

```bash
airflow dags trigger ecb_convergence_monthly \
  --conf '{"observation_start":"2015-01-01","observation_end":"2026-08-01"}'
```

## 파싱·저장 계약

ECB CSV 헤더 전체와 열 순서를 검증한 뒤 `KEY`, `REF_AREA`, `TIME_PERIOD`, `OBS_VALUE`를
읽는다. 다음 조건은 저장 전에 실패한다.

- 요청하지 않은 국가가 포함됨
- 응답 시계열 키가 예상 키와 다름
- `TIME_PERIOD`가 `YYYY-MM`이 아님
- 값이 숫자가 아니거나 NaN·무한대임

빈 본문과 빈 값은 아직 공표되지 않은 정상 구간이다. 관측값이 0건이어도 `source_record`를
남겨 조회하지 않은 구간과 구분한다.

자연키는 `(provider, series_id, observation_date)`다. 매주 같은 여섯 달을 다시 받아도 행이
늘지 않고 최신 공표값으로 갱신된다. 제공처는 다른 ECB 수집과 같은 `ecb`, source key는
`IRS.M..L.L40.CI.0000.EUR.N.Z`라 일별 수익률 곡선과 계보가 갈린다. 요청·응답 구간과 국가,
시계열 목록은 `source_record.metadata`에 남기며 CSV 원문은 JSONB payload에 넣지 않는다.

## 실패와 운영

- HTTP 400·401·403·404는 키·경로 계약 오류로 즉시 실패한다.
- 그 밖의 HTTP·네트워크 오류는 1시간 간격으로 최대 2회 재시도한다.
- `Retry-After`가 있으면 로그에 남긴다.
- 파싱과 저장은 한 트랜잭션이며 형식 오류 시 아무 행도 쓰지 않는다.
- 인증 키는 없고 `CONNECTION_ID`가 가리키는 PostgreSQL 연결만 필요하다.

## 구현과 검증 위치

- DAG: `airflow/dags/ecb_convergence_monthly.py`
- 수집·파싱: `airflow/modules/collectors/ecb_irs.py`
- SQL: `airflow/sql/postgres/indicator_observation/upsert.sql`
- 테스트: `tests/collectors/test_ecb_irs.py`
