# 프로젝트 시간대 규칙

## Airflow 배치 시간 규칙

- 배치 트리거 시간대는 한국 시간(`Asia/Seoul`)이다. `AIRFLOW__CORE__DEFAULT_TIMEZONE=Asia/Seoul`.
- Airflow cron과 `start_date`는 KST로 작성한다. `start_date`는 `pendulum.datetime(..., tz=KST_TIMEZONE)`로 두고 naive datetime은 쓰지 않는다. `KST_TIMEZONE`은 `modules/utility.py`에 있다.
- 스케줄 코드에는 같은 줄 주석으로 UTC를 반드시 병기한다.
- 예: `schedule="30 7 * * 2-6"  # KST 화~토 07:30 = UTC 월~금 22:30`
- 배치 조회 기간과 날짜 경계는 KST 기준으로 계산한다. `data_interval_end`를 `astimezone(KST_TIMEZONE)`으로 변환한 뒤 날짜를 뽑는다.
- 시간대는 트리거 시점과 날짜 경계 계산에만 쓴다. DB에 저장하는 시각과 로그, 컨테이너 시계는 UTC다.
- 외부 데이터의 원본 시각과 시간대는 보존하되, 비교·저장용 시각은 UTC로 정규화한다.
- 제공처가 날짜의 기준 시간대를 정하는 값(하나은행 고시일자와 ECOS 고시 기준일은 KST, FRED 관측일은 미국 영업일)은 그 제공처 기준을 따르고 주석에 남긴다.

## 백엔드 시간 처리 규칙

- 애플리케이션, 데이터베이스 세션, 로그와 내부 이벤트의 기본 시간대는 `UTC`로 통일한다.
- 모든 내부 시각은 timezone-aware UTC로 생성·전달·저장한다. timezone 정보가 없는 naive datetime은 사용하지 않는다.
- 요청 시각은 ISO 8601 offset 또는 `Z`를 필수로 받고 UTC로 변환한 뒤 조회·저장한다.
- 일반 API 응답은 변환하지 않은 UTC ISO 8601 값과 `Z`를 반환한다. 예: `2026-08-04T22:30:00Z`.
- 웹 화면의 시간대 변환과 표시는 프론트엔드가 담당한다.
- 프론트엔드 시간대 우선순위는 사용자 계정의 IANA 시간대, 브라우저 시간대, UTC fallback 순서다.
- 국가 정보만으로 시간대를 추정하지 않는다. 한 국가에 여러 시간대가 있을 수 있다.
- Slack·이메일·CSV·PDF처럼 프론트엔드가 없는 출력, 현지 날짜 기준 집계와 업무상 현지 시간 경계가 필요한 경우에만 백엔드가 변환한다.
- 백엔드 변환에는 사용자 설정 또는 요청에 명시된 IANA 시간대만 사용한다.
- DST와 과거 시간대 변경은 고정 offset 계산이 아니라 IANA timezone 데이터로 처리한다.
- 시간대 변환은 응답 표현 또는 집계 경계 계산 단계에서만 수행하며 DB의 UTC 원본을 변경하지 않는다.
- 시간대 값은 표시와 날짜 경계 계산에만 사용하고 인증, 권한 또는 데이터 접근 범위 판단에는 사용하지 않는다.

# 개발 규칙

## Airflow와 공유하는 코드

- 저장소의 `airflow/`가 컨테이너의 `/opt/airflow`다. 운영 Airflow가 마운트하는 경로와 1:1로 맞춘다: `dags`, `modules`, `utility`, `sql`, `plugins`, `config`.
- Airflow는 `apps/`, `../apps/core/`, `migrations/`를 보지 못한다. DAG가 실행 시점에 import하는 코드는 전부 `airflow/` 아래 둔다.
- import 뿌리는 `airflow/`다. DAG는 배포와 같은 이름으로 `from modules.collectors import ...`처럼 쓴다. pytest `pythonpath`, pyrefly `search-path`, ruff isort `known-first-party`가 `pyproject.toml`에서 같은 뿌리를 가리킨다.
- 쿼리는 Python 문자열이 아니라 `airflow/sql/<엔진>/<테이블>/<동작>.sql`에 둔다. `modules/sql.py`의 `read_sql`이 `AIRFLOW_HOME` 유무와 관계없이 같은 파일을 읽는다.
- 로컬 Compose와 Dockerfile은 운영 Airflow에 맞춰 둔 상태다. 건드리지 않는다. 배치 문제는 코드 위치로만 해결하고, 실행 코드를 이미지에 굽거나 `apps/`를 볼륨으로 붙이지 않는다.
- `airflow/` 아래에는 DAG가 실제로 import·실행하는 코드만 둔다. Airflow가 실행하지 않는 상주 서비스·API는 `apps/` 아래에 백엔드 규칙(ORM, `config.yaml`, async)으로 두고 FastAPI와 공유하며 배포만 컨테이너로 가른다(`apps/realtime/`가 그 예). 두 트리가 같은 도메인 상수를 쓰면 중복을 허용하되 테스트로 대조한다. 한쪽 트리가 다른 쪽을 import하지 않는 것이 우선이다.
- DAG가 쓰는 코드는 위치는 Airflow를, 규칙은 백엔드를 따른다. DAG가 쓰는 공유 코드는 `airflow/modules` 아래 한 벌만 둔다.
- 외부 입력은 Pydantic으로 검증하고, 시각은 timezone-aware UTC이며, 주석은 한국어로 쓴다.
- `dags/`에는 스케줄, 재시도, 태스크 매핑, Hook 사용, 실패 분류만 둔다. 파싱·검증·저장 규칙은 `modules/`에 둔다.
- 의존성은 Airflow 환경에 있는 것만 쓴다. 표준 라이브러리, Pydantic, PEP 249 연결이다. SQLAlchemy 모델과 `core.config`는 import하지 않는다.
- 테이블 정의의 원본은 백엔드의 `apps/models`다. 수집기는 문자열 SQL을 쓰므로 `tests/collectors/test_fred.py`, `test_ecos.py`, `test_mof.py`, `test_boe.py`, `test_ecb.py`가 INSERT 컬럼과 `ON CONFLICT` 키를 모델 metadata와 대조한다.

## 수집기 작성 규칙

- 요청 값, 외부 응답 본문, 정규화 결과, 수집 결과를 모두 Pydantic 모델로 선언한다. `dataclass`를 쓰지 않는다. 외부 JSON은 `model_validate_json`으로 검증한다.
- 모델은 `ConfigDict(frozen=True)`로 둔다. 재시도 경로에서 값이 바뀌면 원본과 저장값이 어긋난다.
- 시각 필드는 `AwareDatetime`으로 받고 validator에서 UTC로 정규화한다.
- 허용 값이 정해진 필드는 validator로 막는다. 예: 시계열 ID는 `TREASURY_SERIES` 안의 값만 받는다.
- 제공처가 잘못된 식별자에도 정상 응답으로 답하면 식별자를 Enum으로 좁혀 요청 전에 막는다. ECOS는 없는 항목코드에도 데이터 없음(`INFO-200`)으로 답해서 오타가 조용한 0건이 된다.
- API 키는 `SecretStr`로 받는다. URL에 키가 들어가므로 예외 메시지와 로그에 URL을 넣지 않는다. 인증이 없는 제공처(재무성)는 반대로 URL을 그대로 남긴다. 감출 게 없는데 감추면 디버깅만 어려워진다.
- 제공처가 파일을 여러 개로 쪼개 고시하면 어느 파일을 받을지도 수집 규칙이라 `modules/`가 정한다. 재무성은 이번 달치(`jgbcm.csv`)와 과거 전체(`data/jgbcm_all.csv`)가 겹치지 않아 구간이 달 경계를 넘으면 둘 다 받아야 한다. `mof.fetch_curves`가 그 판단을 한다.
- 수집 단위가 시계열이 아니라 파일이나 조회 한 번이면 `source_record`도 그 단위로 남기고 `source_key`에 파일 이름(`jgbcm`)이나 조회 이름(`gilt_nominal_par_yields`, `YC.B.U2.EUR.4F.G_N_A.SV_C_YM`)을 넣는다. 시계열마다 태스크를 매핑하지 않는다.
- 원본이 JSON이 아니면 `payload`에 넣지 않는다. 컬럼 타입이 jsonb다. 어느 파일이 어느 구간을 담고 있었는지를 `metadata`에 남긴다.
- 응답에 우리가 요청하지 않은 식별자가 섞여 오면 실패시킨다. ECB는 `KEY` 칸을 매 행 대조해 `G_N_C`(전체 발행자) 곡선이 AAA 곡선에 섞이는 것을 막는다.
- 제공처가 값 없음과 잘못된 식별자를 **같은 응답으로** 알리면 조회 구간 앞에 패딩을 붙여 영업일이 반드시 들어가게 한다. BoE IADB는 둘 다 HTTP 200에 HTML 오류 페이지로 답한다. `boe.FETCH_PADDING_DAYS`가 14일을 붙이고 저장은 구간 안만 한다. 패딩을 붙이고도 오류 페이지면 식별자나 구간이 틀린 것이다. 반대로 둘이 갈리는 제공처(ECB는 값 없음이 HTTP 200 빈 본문, 없는 키가 404)에는 패딩을 붙이지 않는다.
- 제공처가 자기 나라 표기로 날짜를 주면 그 변환도 수집기가 하고 모르는 표기는 실패시킨다. 재무성은 和暦(`R8.8.3` = 2026-08-03)을 쓴다.
- 로케일을 타는 표기는 표를 직접 둔다. `strptime`/`strftime`의 `%b`, `%a`는 실행 환경의 `LC_TIME`을 타므로 컨테이너 로케일이 바뀌면 조용히 실패한다. BoE의 `03 Aug 2026`은 `boe.MONTH_NAMES`가 읽는다.
- 날짜 문자열은 모양을 먼저 보고 파싱한다. `date.fromisoformat`은 `2026-W32` 같은 ISO 주 표기도 받아 그 주의 월요일로 바꾼다. 주간·월간 빈도의 값이 섞이면 조용히 엉뚱한 날짜가 된다(`ecb.ISO_DATE_PATTERN`).
- 조회 구간 계산은 `modules/period.py`에 한 벌만 둔다. 이 모듈은 Airflow를 import하지 않는다. 실패는 `PeriodError`로 올리고 `AirflowFailException`으로 바꾸는 건 DAG가 한다.
- 외부 오류는 재시도 가능 여부로 나눠 올린다. HTTP 상태, 응답 형식, 연결 실패를 각각 다른 예외로 구분하고 판단은 DAG가 한다.
- 제공처가 실패를 HTTP 상태가 아니라 본문으로 알리면 그 코드를 담는 예외를 따로 둔다(`EcosResultError`). 수집기는 코드를 해석하지 않고 DAG가 재시도 여부를 정한다.
- 응답이 페이지 단위로 잘릴 수 있으면 제공처가 알려 준 전체 건수와 받은 행 수를 대조해 잘림을 실패로 만든다. 조용히 잘린 응답은 조회 구간에 구멍을 남긴다.
- HTML 수집은 scrapling을 쓴다. 요청은 `Fetcher`(curl_cffi), 파싱은 `Selector`다. `impersonate`로 실제 브라우저 지문을 흉내 내므로 앞단 WAF에 막히지 않는다. 페이지가 JavaScript로 표를 그릴 때만 `DynamicFetcher`나 `StealthyFetcher`를 쓴다. 이건 브라우저를 띄우므로 기본값이 아니다.
- 표를 위치(index)로 읽으면 칸 수를 상수로 두고 응답마다 검증한다. 사이트가 열을 추가하면 값이 조용히 옆 칸으로 밀린다. 칸 수 검사가 먼저 실패해야 그걸 알 수 있다. CSV도 같고, 저장하지 않는 열까지 헤더 전체를 대조한다(`mof.EXPECTED_HEADER`).
- 기준 예시는 `airflow/modules/collectors/fred.py`와 `airflow/dags/fred_treasury_daily.py`, HTML 쪽은 `airflow/modules/collectors/hana.py`와 `airflow/dags/exchange_rate_daily.py`다. 본문으로 실패를 알리는 API는 `airflow/modules/collectors/ecos.py`와 `airflow/dags/ecos_market_rate_daily.py`, 인증 없는 CSV 파일은 `airflow/modules/collectors/mof.py`와 `airflow/dags/mof_jgb_daily.py`, `boe.py`, `ecb.py`를 본다.

## 마이그레이션 라우팅 규칙

- 테이블이 어느 마이그레이션 DB 별칭에 속하는지는 모델에서 선언한다. `core.database.table_options`를 `__table_args__`의 마지막 요소로 쓴다.
- `table_options(schema=..., comment=..., database="default", managed=True)` 형태다. `database`를 생략하면 `default`다.
- `managed=False`는 이 프로젝트가 스키마를 소유하지 않는 테이블이다. ORM 매핑은 유지되지만 어떤 별칭의 autogenerate에도 나오지 않는다. Django `Meta.managed = False`와 같다.
- 별칭 단위 `read_only: true`는 연결을 읽기 전용 트랜잭션으로 설정하는 별개 층이다. 테이블 하나만 골라 쓰기를 막는 설정이 아니다.
- 실행 구조는 Alembic 공식 multidb 템플릿과 같다. `env.py`가 별칭을 순회하며 `run_migrations(engine_name=alias)`를 부른다. 별칭 목록만 `alembic.ini`가 아니라 `config.yaml`에서 오고 `migrations/cli.py`가 Alembic `databases` 옵션으로 넘긴다.
- `migrations/env.py`는 마이그레이션이 켜진 모든 별칭의 `model_modules`를 import한다. `config.yaml`의 모든 별칭은 `model_modules: [apps.models]`로 둔다.
- MetaData는 하나만 쓴다. 공식 템플릿처럼 별칭별 MetaData로 쪼개면 스키마 간 ForeignKey가 resolve되지 않는다. 대신 Alembic 훅에서 `table.info["database"]`로 거른다.
- 훅은 `include_name`과 `include_object` 둘 다 건다. `include_name`은 reflection된 이름만 보므로 DROP만 막고, 모델 metadata까지 보는 `include_object`가 있어야 다른 별칭 소유 테이블에 CREATE를 내지 않는다. 둘 다 `migrations.routing.include_table`에 위임한다.
- autogenerate는 모델과 실제 DB 상태를 비교한다. 리비전 이력이 아니다. `makemigrations` 전에 `just migrate upgrade head`로 모든 별칭을 최신 상태로 맞춘다.
- 별칭마다 리비전 포인터 테이블이 다르다. `default`는 `alembic_version`, 나머지는 `alembic_version_<alias>`다. 별칭들이 같은 PostgreSQL 인스턴스를 공유하기 때문이다.
- 라우팅 판단은 `migrations/routing.py`의 순수 함수에 둔다. `env.py`는 Alembic 실행 컨텍스트 밖에서 import할 수 없어 직접 테스트하지 못한다.
- 리비전 파일은 `migrations/versions` 하나에 모이고 파일 안에서 `upgrade_<alias>()`로 갈라진다. 해당 함수가 없으면 아무 것도 하지 않으므로 별칭을 나중에 추가해도 과거 리비전을 고칠 필요가 없다.
- 마이그레이션 파일은 `just makemigrations "<메시지>"`로 만들고 생성된 파일을 반드시 읽어본다. 별칭을 인자로 주지 않는다.
- `--autogenerate`는 모든 별칭에 실제로 연결한다. 하나라도 접속 불가면 리비전을 만들 수 없다.
- autogenerate는 `CREATE SCHEMA`를 만들지 않는다. 새 스키마를 쓰는 리비전은 `op.execute("CREATE SCHEMA IF NOT EXISTS <schema>")`를 해당 별칭 함수 맨 앞에 직접 넣는다.
- 마이그레이션 테스트는 `alembic_command.upgrade(config, "head", sql=True)`로 SQL만 뽑아 테이블 단위 사실만 검증한다. 특정 리비전 ID에 고정하지 않는다.

## 이미 존재하는 외부 테이블 편입

다른 시스템이 이미 만들어 데이터가 들어 있는 테이블은 Django `migrate --fake-initial`처럼 편입한다. `apps/models/finance.py`의 `ExchangeRate`가 그 예다.

- 모델은 실제 DDL을 글자 그대로 미러링한다. 컬럼 타입, nullable, 기본값, 제약·인덱스 이름까지 같아야 한다. 다르면 다음 autogenerate가 그 차이를 ALTER로 뱉는다.
- BIGSERIAL 기본키, timezone-aware 시각, 테이블·컬럼 주석 같은 프로젝트 기본 규칙은 적용하지 않고 실제 DB를 따른다. 주석이 없으면 `table_options(comment=None)`으로 둔다.
- `managed=True`를 유지한다. `managed=False`는 이후 스키마 변경을 추적하지 못한다.
- 리비전은 손으로 쓴다. 해당 별칭 함수 맨 앞에서 `sa.inspect(op.get_bind()).has_table(...)`로 존재를 확인하고 있으면 반환한다. offline(`--sql`)은 연결이 없으므로 항상 전체 DDL을 찍는다.
- `downgrade_<alias>()`는 `pass`다. 소유자가 이 프로젝트가 아니므로 `DROP TABLE`을 내지 않는다.

### `exchange_rate`

- 통화별·회차별 환율 고시다. `default` 별칭에 있고 `exchange_rate_daily` DAG가 채운다. 2026-08-06에 `finance` 별칭에서 옮겼고 이전 데이터는 가져오지 않았다.
- 컬럼 형태는 외부 finance DB의 같은 이름 테이블을 글자 그대로 복사한 것이다. serial `id`, naive `timestamp`, `date`/`time` 분리를 그대로 둔다. 나중에 외부 DB의 과거 행을 옮기거나 Grafana를 우리 DB로 돌릴 때 컬럼이 1:1로 맞아야 하기 때문이다. 우리 DB 이름도 `finance`라 원본을 가리킬 때는 `외부 finance DB`라고 쓴다.
- 프로젝트 기본 규칙 중 BIGSERIAL과 timezone-aware 시각을 적용하지 않는 유일한 테이블이다. 타입이나 컬럼 구성을 바꾸려면 그 이관 계획을 먼저 접는다. 지금 상태는 `tests/models/test_finance_models.py`가 컬럼 단위로 고정한다.
- 주석은 예외로 단다. 주석은 데이터 이관에 영향을 주지 않으므로 테이블·컬럼 주석을 기본 규칙대로 채운다.
- `currency`는 `apps/models/finance.py`의 `Currency` StrEnum이고 저장 타입은 `VARCHAR(10)` 그대로다. CHECK 제약은 걸지 않는다. 원본에 없는 제약이 생기고 통화를 추가할 때마다 다시 만들어야 하기 때문이다. 값은 `modules.collectors.hana.HanaCurrency`와 같아야 하며 테스트가 둘을 대조한다.
- 모델은 `apps/models/finance.py`에 있다. 파일 이름은 스키마도 별칭도 아니고 형태를 가져온 원본 DB 이름이다.
- 멱등 키는 `(currency, date, time, round)`, 제약 이름은 `unique_currency_date_time_round`다. 수집기 upsert가 이 이름을 그대로 쓴다.
- `finance` 별칭은 매핑된 모델이 없어 `migration.enabled: false`다. 런타임 읽기 전용 연결만 남아 있다. 여기에 테이블을 편입하려면 `enabled: true`와 `model_modules`를 함께 켠다.

## 데이터베이스 테이블 주석

- 모든 SQLAlchemy 테이블은 `__table_args__`의 `comment`에 테이블 목적을 한국어로 작성한다.
- 모든 컬럼은 `mapped_column(comment="...")`에 값의 의미를 작성한다. 시간대, 단위, 허용 상태가 있으면 함께 명시한다.
- `id`, `created_at`, `updated_at` 같은 공통 필드 주석은 `EntityBase`에서 한 번만 정의한다.
- Alembic 마이그레이션에도 모델과 동일한 테이블·컬럼 주석을 넣어 실제 데이터베이스 스키마에 반영한다.
- 모델과 마이그레이션의 주석은 함께 변경하고 테스트로 생성 여부를 확인한다.

## 타입 모델링 규칙

- 값의 종류가 정해진 상태·분류 필드는 일반 `str` 대신 Python `StrEnum`과 SQLAlchemy `Enum`을 사용한다.
- SQLAlchemy `Enum`은 `native_enum=False, length=20, values_callable=...` 형태로 선언한다. PostgreSQL native enum은 값 추가·삭제 마이그레이션 비용이 커서 쓰지 않는다.
- Enum 컬럼에는 허용 값을 제한하는 데이터베이스 `CHECK` 제약을 함께 둔다.
- API 요청·응답, 설정, 외부 입력 검증에는 Pydantic 모델과 `Field`, validator를 사용한다.
- 제공처 이름, URL, 종목 코드, 외부 식별자처럼 값이 열려 있는 필드는 `str` 또는 `Text`로 유지한다.
- 단순 문자열을 의미 없이 Pydantic 모델이나 Enum으로 감싸지 않고, 유효성 규칙이나 제한된 값 집합이 있을 때 사용한다.

## 오류 처리 규칙

**해결하지 못하는 문제는 터뜨린다.** 삼키고 계속 가는 코드는 문제가 없는 것처럼 보이게 만들 뿐이고, 그 사이 잘못된 값이 쌓이거나 아무 것도 쌓이지 않는다. 실패를 나중에 알수록 되짚을 구간이 길어진다. 지금 멈춰서 눈에 띄는 편이 항상 낫다.

- **자체 예외 타입을 만드는 것은 좋다.** 원인을 좁혀 부르는 쪽이 판단할 수 있게 하는 것이 목적이다. `FredHTTPError`, `EcosResultError`, `LlmError`가 그 예다. 단 원래 예외를 `raise ... from error`로 잇는다. 원인을 끊으면 추적이 거기서 멈춘다.
- **예외를 문자열로 뭉개지 않는다.** `str(error)`나 `type(error).__name__`으로 바꿔 담으면 위에서 종류로 갈라낼 수 없다. 판단을 위에 맡길 거면 종류를 그대로 올리거나, 결과 객체에 담아야 한다면 예외 객체 자체를 들고 간다.
- **`except Exception`으로 넓게 잡지 않는다.** 잡아야 할 이유가 있으면 잡되 **반드시 다시 올린다.** 로그만 남기고 넘어가지 않는다. 넓게 잡아야만 하는 자리에는 왜 그런지 주석을 남긴다.
- **로그는 예외를 대체하지 않는다.** `logger.warning`만 남기고 정상 반환하면 Airflow는 그 태스크를 성공으로 표시한다. 아무도 보지 않는 경고가 되고, 다음 실행도 같은 자리에서 같은 경고를 남긴다.
- **부분 실패를 결과로 바꾸는 것은 그것이 정상 흐름일 때만 한다.** 문서 하나가 실패해도 나머지를 저장하는 것처럼 설계가 그렇게 정해진 경우다. 그때도 실패한 건수와 원인을 올리고, 전부 실패하면 태스크를 실패시킨다.
- **조용한 성공을 만들지 않는다.** 잘린 응답, 0건, 빈 본문이 오류를 가릴 수 있으면 실패로 만든다. 수집기 규칙의 "제공처가 알려 준 전체 건수와 받은 행 수를 대조한다"와 같은 이유다.
- **재시도 여부 판단을 위에 맡기려면 판단할 것을 위로 올려야 한다.** 아래에서 분류해 놓고 위로 문자열만 보내면 그 분류는 존재하지 않는 것과 같다.

### DAG의 실패 판정

이미 스무 개 DAG가 아래 세 형태 중 하나를 따른다. 새 DAG도 이 중 하나를 고른다.

- **항목별 실패 수집** — 여러 항목을 한 태스크에서 돌 때. 항목 하나가 실패하면 원인을
  `failures`에 모으고 계속한다. **마지막에 반드시 판정한다.** `dart_disclosure_intraday`,
  `document_ingestion_hourly`는 전부 실패했을 때, `kis_*`와 `yahoo_*`는 하나라도 실패했을 때
  태스크를 죽인다. 어느 쪽을 고르든 실패를 세고 이름을 메시지에 싣는다.
- **태스크 매핑** — 항목마다 태스크를 매핑한다(`.expand`). 실패가 곧 그 태스크의 실패라
  따로 판정할 것이 없고 재시도도 실패한 항목만 다시 돈다. `fred_*`, `ecos_*`,
  `exchange_rate_daily`가 그렇다.
- **단일 요청** — 응답 하나가 결과 전부다. 수집기 예외를 그대로 올린다. `bbk`, `boe`,
  `ecb_*`, `mof`, `market_calendar`가 그렇다.

어느 형태든 **되돌릴 수 없는 오류는 즉시 `AirflowFailException`으로 바꾼다.** 설정·인증·주소
문제(HTTP 4xx)는 재시도해도 같은 답이다. 재시도할 값어치가 있는 것(`ConnectionError`)은
그대로 올려 Airflow가 재시도하게 둔다. **그 판단은 DAG가 한다.** 수집기는 종류만 정확히
올린다.

층이 하나 더 끼면 이 규칙이 새기 쉽다. `document_assessment_hourly`는 LangGraph 배치 노드가
사이에 있어서, 그 노드가 예외를 문자열로 바꾸는 바람에 DAG가 판단할 것을 잃었다. **중간 층은
예외를 통과시킨다.**

## LLM 코드 규칙

LLM을 부르는 코드는 **Pydantic, LangChain, LangGraph 위에서만 쓴다.** 세 층의 역할이 겹치지 않는다.

- **어떤 모델을 쓸지는 코드가 정한다.** 모델 정의는 `airflow/modules/llm.py`에 LangChain 문법 그대로 모아 두고(`document_model()`) 바꿀 때 그 함수를 고친다. `base_url`·모델명을 환경변수로 빼서 제공처를 갈아 끼우지 않는다. 제공처마다 클래스와 인자가 달라 문자열 설정 몇 개로 흉내 낼 수 없다. **API 키만 환경에서 오고 그것도 우리가 읽지 않는다** — LangChain 클래스가 자기 이름(`XAI_API_KEY` 등)으로 읽는다.
- **모델 호출은 LangChain이다.** `langchain_xai.ChatXAI` 같은 `BaseChatModel`을 쓰고 HTTP를 직접 치지 않는다. 요청·응답을 손으로 조립하면 LangSmith 추적이 끊기고 툴 호출 왕복을 직접 짜야 한다. 메시지는 dict가 아니라 `SystemMessage`, `HumanMessage`, `AIMessage`로 다룬다.
- **흐름 제어는 LangGraph다.** 재시도, 교정 재요청, 분기, 문서·항목 팬아웃(`Send`)은 `StateGraph`의 노드와 엣지로 표현한다. `if`와 `for`로 흩어 놓지 않는다. 노드 이름이 그대로 트레이스에 남아 어디서 몇 번 불렀는지 보이는 것이 이 규칙의 목적이다. 상태는 `TypedDict`로 선언하고 병합이 필요한 칸에는 리듀서(`Annotated[list, operator.add]`)를 단다.
- **데이터 모양은 Pydantic이다.** 설정, 모델 응답, 노드가 주고받는 결과는 `BaseModel`로 선언한다. `dataclass`나 맨 dict를 쓰지 않는다. 응답 스키마는 Pydantic 모델에서 뽑아 `response_format`으로 강제하고(`modules/schema.py`), 강제가 안 되는 제공처를 위해 검증을 그대로 남긴다.
- **흐름은 클래스로 묶는다.** `DocumentAssessor`·`AssessmentBatch`처럼 그래프를 소유한 클래스가 갖는다. 모델 정의와 오류 분류는 `modules/llm.py`의 함수다. 감쌀 상태가 없는 것을 클래스로 만들지 않는다. 그래프는 생성자에서 한 번 `compile()`한다. 프롬프트 조립과 파싱처럼 상태가 필요 없는 것은 같은 클래스의 `@staticmethod`로 둔다.
- **API 키를 그래프 상태에 넣지 않는다.** 상태와 config는 트레이스 입력으로 나간다. `SecretStr`을 담은 설정 객체는 생성자로만 넘긴다.
- **재시도는 Airflow가 한다.** 모델 클라이언트는 `max_retries=0`으로 만든다. SDK가 먼저 재시도하면 태스크 타임아웃 안에서 몇 번을 불렀는지 로그와 트레이스가 어긋난다.
- 제공처 예외는 한 곳에서 우리 종류로 바꾼다. 재시도할 값어치가 있는 것(`ConnectionError`)과 없는 것(`LlmError`)을 가르는 판단은 DAG가 한다.
- 체크포인터·persistence는 붙이지 않는다. 재실행 단위는 Airflow 태스크다.
- 추적은 `LANGSMITH_*` 환경변수로 켠다. 코드에 추적 호출을 심지 않는다. **켜면 프롬프트와 원문이 외부로 나간다는 사실을 문서에 남긴다.**

기준 구현은 `airflow/modules/llm.py`와 `airflow/modules/assessment.py`다.

## 수집 계보 테이블 규칙

### `raw.source_record`

API, 크롤링, 웹소켓 수집 결과의 출처와 상태를 가볍게 보존한다. API는 응답 1회, 크롤링은 문서 버전 1개, 웹소켓은 메시지가 아닌 배치 또는 연결 세션 1개를 레코드 단위로 사용한다.

- 수집 방식, 제공처, 원천 식별자, UTC 수집 구간, 상태와 생성 레코드 수는 항상 저장한다.
- 작은 JSON 원본만 `payload`에 선택적으로 저장한다.
- 대용량 원본은 외부 저장소에 두고 `payload_uri`만 저장한다.
- API 키, 인증 헤더와 개인정보는 `payload`나 `metadata`에 저장하지 않는다.
- 정규화 테이블은 `source_record_id` 외래키와 `ON DELETE RESTRICT`로 출처를 연결한다.
- 웹소켓 메시지별로 `SourceRecord`를 생성하지 않는다.

## 지표 관측값 테이블 목적

### `market.indicator_observation`

여러 제공처에서 추출한 지표 관측값을 날짜와 단위와 함께 조회 가능한 형태로 누적 저장한다. `(provider, series_id, observation_date)`를 고유키로 사용하고 `source_record_id`로 근거 수집 레코드와 연결한다.

- `provider`는 그 값을 준 제공처(`fred`, `ecos`, `mof`, `boe`, `bbk`, `ecb`)이며 같은 수집의 `source_record.source`와 같은 값이다.
- `series_id`는 **제공처 안에서만 고유하다.** 그래서 자연키에 `provider`가 함께 들어간다.
- `series_id`는 사람이 읽을 수 있어야 한다. FRED의 `DGS10`처럼 제공처 ID가 이미 읽히면 그대로 쓰고, ECOS 항목코드(`010210000`)처럼 숫자뿐이면 `KTB10Y` 같은 ID를 만들어 저장한다. 제공처의 원본 좌표는 수집기 Enum이 들고 있다가 요청에 쓰고 `source_record.metadata`에 남긴다.
- 조회하는 쪽도 `provider`를 함께 건다. `series_id` 하나로 거는 쿼리는 제공처가 늘어나면 조용히 틀린다.
- 국가·만기 같은 시계열의 성격은 여기 두지 않고 `reference.indicator_series`에 둔다.

### `reference.indicator_series`

`indicator_observation`의 시계열이 어느 나라 무슨 값인지 설명하는 마스터다. `(provider, series_id)`가 자연키이고 대시보드가 이 키로 관측값을 조인한다.

- 존재 이유는 나라를 추가할 때 조회 쪽을 안 고치기 위해서다. 국가·만기를 조회 쿼리가 알고 있으면 나라가 늘 때마다 대시보드 SQL을 고쳐야 한다. 영국과 유로 지역을 붙일 때 통합 대시보드는 한 줄도 고치지 않았다.
- `country`는 ISO 3166-1 alpha-2다. 유로 지역처럼 나라가 아닌 통화권은 `XM`을 쓰고 `country_name`에 `유로 지역`을 넣는다.
- **금리 전용 테이블이 아니다.** `kind`가 국채(`government_bond`), 단기 자금시장 금리(`money_market`), 물가지수(`price_index`), 실물활동(`activity`) 넷을 가른다. 조회하는 쪽은 `kind`를 반드시 건다. 단위가 다른 값이 한 축에 섞이면 화면이 조용히 거짓말을 한다.
- `maturity_months`는 비교와 정렬 기준이며 91일물은 3으로 둔다. **만기 개념이 없는 지표(물가지수, 소매판매)는 `NULL`이다.** 0으로 채우면 만기별 비교 쿼리가 "0개월물"로 그린다.
- **월간 계열은 저장 식별자를 `M`으로 끝낸다**(`CPI_M`, `FR10YM`). 한 테이블에 일별과 월간이 섞여 있어 표시가 없으면 조회하는 쪽이 주기를 구분할 수 없다.
- 국가 비교 패널의 만기 목록은 두 나라 이상이 가진 만기로 좁힌다(`HAVING count(DISTINCT country) > 1`). 일본 40년이나 유로 지역 6개월처럼 한 나라만 고시하는 만기는 골라도 비교할 대상이 없다.
- 관측값에서 이 테이블로 외래키를 걸지 않는다. 걸면 마스터 행이 없는 시계열을 수집기가 저장하지 못해 Enum에만 추가한 순간 DAG가 죽는다. `tests/migrations/test_indicator_series_catalog.py`가 Enum과 시드를 대조한다.
- 시계열을 늘릴 때는 수집기 Enum과 마스터 시드를 같은 커밋에서 함께 늘린다. 시드는 마이그레이션이 넣고 리비전 파일에서 앱 코드를 import하지 않는다.
- `unit`은 제공처 표기가 아니라 정규화한 표기다. 연이율 퍼센트는 제공처가 `Percent`든 `연%`든 `Percent`로 저장해야 두 나라 금리를 한 쿼리로 비교할 수 있다. **단위는 계열마다 다르다.** 물가지수(`Index 1982-1984=100`)와 소매판매(`Millions of Dollars`)가 같은 테이블에 있으므로 모듈 상수 하나로 두지 않고 수집기 Enum이 계열별로 들고 있는다.

## 종목 마스터 테이블 목적

### 봉 테이블 (`<kind>_bar` / `<kind>_daily` / `stock_bar`)

시세 봉은 kind별 물리 테이블에 쌓는다(2026-08-18 분리): `index_bar`, `index_future_bar`,
`fx_bar`, `rate_bar`, `bond_future_bar`, `commodity_bar`, `crypto_bar`와 각각의 `_daily`,
그리고 개별 종목의 `stock_bar`/`stock_daily`다. 심볼의 성격(라벨·국가·kind)은
`quote_symbol` 마스터가 갖는다.

- **`quote_bar`/`quote_daily`는 이들을 UNION ALL 한 읽기 전용 뷰다.** 조회(브리핑 SQL,
  Grafana)는 뷰를 써도 되지만 **쓰기는 반드시 물리 테이블로 간다.** 수집기가 kind별
  upsert 파일(`airflow/sql/postgres/<table>/upsert.sql`)을 쓴다.
- 매크로 테이블의 자연키는 `(provider, symbol, bar_at|business_date)`다. `contract_code`는
  `index_future_bar`에만 있다.
- `stock_bar`는 **거래소(`exchange`: KRX/NXT/NYSE)가 자연키의 한 축이다.** 같은 종목이
  KRX와 NXT에서 따로 체결되므로 거래소 없이 시각만 키로 쓰면 서로를 덮어쓴다. 통합(`UN`)
  시세는 받지 않는다. 뷰에는 KRX·NYSE만 태워 심볼이 겹치지 않게 한다. NXT는 물리 테이블을
  직접 조회한다.
- 국내 종목 일봉은 `stock_daily`가 아니라 `stock_investor_trade_daily`가 갖는다(수급과 함께).
  `stock_daily`는 해외 상장 종목(TSMC ADR)용이다.

### `reference.instrument`

시세·뉴스·시그널이 참조하는 추적 종목 마스터다. 관측값이 아니라 기준 정보이므로 `source_record_id`로 수집 계보를 연결하지 않는다.

- `(ticker, market)`을 자연키로 사용한다. `id`는 다른 테이블이 참조할 대리키다.
- `source_symbol`은 수집 소스 심볼이 티커와 다를 때만 채운다. 같으면 `NULL`로 둔다.
- `is_watched`는 수집·분석 대상 여부만 나타낸다. 상장폐지·거래정지 같은 종목 생애주기 상태가 필요해지면 별도 `status` enum 컬럼으로 분리한다.
- 한 종목을 여러 소스에서 수집하게 되면 `source_symbol` 한 칸으로 못 버틴다. 그때는 `reference.instrument_source(instrument_id, source, symbol)` 자식 테이블로 옮긴다.

## 문서 테이블 목적

### `content.document` 계열

수집한 문서 한 건과 그 문서에 붙은 태그다. `document_ingestion_hourly`가 문서를 넣고 `document_assessment_hourly`가 평가를 채운다.

- 자연키는 `(source_slug, external_id)`다. **`content_hash`를 키에 넣지 않는다.** 넣으면 본문이 조금만 달라져도 새 행이 생겨 같은 기사가 매시간 쌓인다. 재평가 여부는 `assessed_content_hash`와 현재 `content_hash`의 비교가 정한다.
- **승인·보류 같은 상태 머신을 두지 않는다.** 소비자가 사람이 아니라 LLM이라 전부 저장하고 점수(`value_score`)만 남긴다. 상태로 버리면 나중에 기준을 바꿀 때 되돌릴 수 없다.
- 평가에 실패한 문서는 `assessed_at`을 `NULL`로 남긴다. 삭제하거나 다른 상태로 바꾸지 않고 다음 정시 실행이 다시 집는다.
- `document_instrument`와 `document_indicator`는 마스터로 **외래키를 걸지 않는다.** 마스터에 없는 태그가 오면 태깅 전체가 죽는 대신 그 태그만 빠져야 한다. 후보 목록은 프롬프트로 주고 목록 밖의 값은 저장 전에 버린다.
- `body`는 `content_level`이 `metadata_only`면 `NULL`이고 CHECK 제약이 그것을 강제한다.
