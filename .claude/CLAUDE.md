# news 프로젝트 가이드

Claude Code가 이 저장소에서 작업할 때 따르는 규칙과 구조 요약이다.
Codex용 규칙 원본은 [.codex/AGENTS.md](../.codex/AGENTS.md)이며 두 문서는 함께 갱신한다.

## 프로젝트 구조

| 경로 | 역할 |
| --- | --- |
| `../apps/core/config.py` | `config.yaml`을 읽는 Pydantic 설정. `settings` 싱글턴 제공 |
| `../apps/core/database.py` | `Base`, `EntityBase`, 다중 DB 별칭을 관리하는 `Database` |
| `../apps/core/redis.py` | Redis 연결 관리 |
| `../apps/core/container.py` | dependency-injector 컨테이너 |
| `apps/models/` | SQLAlchemy 모델. 파일 이름 = PostgreSQL 스키마 이름 |
| `migrations/` | Alembic. 리비전 파일은 `migrations/versions` 하나를 모든 별칭이 공유한다 |
| `migrations/routing.py` | 어떤 테이블이 어떤 DB 별칭에 속하는지 판단하는 순수 함수 |
| `../airflow/dags/` | Airflow DAG |
| `tests/` | pytest |

`apps/models/`의 모듈은 스키마 단위로 나눈다. `raw.py` → `raw`, `market.py` → `market`,
`reference.py` → `reference`. 새 모델을 추가하면 `apps/models/__init__.py`의 `__all__`에도 넣는다.
그러지 않으면 Alembic autogenerate가 모델을 보지 못한다.

관리 대상 스키마는 `migrations/env.py`의 `MANAGED_SCHEMAS`에 있다:
`raw`, `market`, `reference`, `ops`, `report`, `analysis`. 이 밖의 스키마(Airflow 메타데이터 등)는
autogenerate 결과에 절대 나오면 안 된다.

## 명령어

```bash
just dev
```

```bash
just makemigrations "create instrument table"
```

```bash
just migrate upgrade head
```

```bash
uv run pytest tests -q
```

```bash
uv run ruff check apps core dags migrations tests
```

한 번의 명령이 마이그레이션이 켜진 모든 별칭을 순서대로 처리한다. 별칭을 인자로 주지 않는다.
`just migrate <alembic args>`는 임의의 Alembic 명령을 전달하고,
`just makemigrations "<메시지>"`는 `revision --autogenerate`만 실행한다.

## 마이그레이션 라우팅

테이블이 어느 DB 별칭에 속하는지는 **모델에서 직접 선언한다.** `core.database.table_options`를
`__table_args__`의 마지막 요소로 쓴다.

```python
__table_args__ = (
    UniqueConstraint(...),
    table_options(
        schema="reference",
        comment="시세·뉴스·시그널이 참조하는 추적 종목 마스터",
        database="default",
    ),
)
```

인자는 `schema`, `comment`(둘 다 필수), `database`(기본 `"default"`),
`managed`(기본 `True`)다. 값은 `Table.info`에 들어가고 `migrations/env.py`가 읽는다.

`managed=False`는 이 프로젝트가 스키마를 소유하지 않는 테이블이다. ORM 매핑은 유지돼서
읽고 쓸 수 있지만 어떤 별칭의 autogenerate에도 나오지 않는다. Django `Meta.managed = False`와 같다.
연결 자체를 읽기 전용으로 막는 건 별칭 단위 `read_only: true`이고 이건 별개 층이다.

실행 구조는 Alembic 공식 multidb 템플릿과 같다. `env.py`가 별칭을 순회하며 각각
`context.configure()` 후 `run_migrations(engine_name=alias)`를 부른다. 별칭 목록만
`alembic.ini`가 아니라 `config.yaml`에서 오고, `migrations/cli.py`가 Alembic의
`databases` 옵션으로 넘긴다.

- `env.py`는 마이그레이션이 켜진 **모든** 별칭의 `model_modules`를 import한다.
  현재 별칭이 소유하지 않는 테이블도 metadata에 있어야 autogenerate에서 제외할 수 있기 때문이다.
  그래서 `config.yaml`의 모든 별칭은 `model_modules: [apps.models]`로 둔다.
- `migrations.routing.excluded_tables`가 다른 별칭 소유 테이블과 `managed=False` 테이블을 모으고,
  `include_table`이 그것과 파티션, 비관리 스키마를 autogenerate에서 뺀다.
  이게 없으면 별칭들이 같은 PostgreSQL 인스턴스를 보기 때문에 서로의 테이블에 DROP을 낸다.
- 훅은 `include_name`과 `include_object` **둘 다** 건다. `include_name`은 reflection된 이름만 보므로
  DROP만 막고, 모델 metadata까지 보는 `include_object`가 있어야 남의 테이블에 CREATE를 내지 않는다.
  둘 다 `include_table` 하나에 위임해서 판정이 어긋나지 않게 한다.
- 별칭마다 리비전 포인터 테이블이 다르다(`migrations.routing.version_table`).
  `default`만 `alembic_version`이고 나머지는 `alembic_version_<alias>`다.
  공식 템플릿은 DB가 물리적으로 다르다고 보고 나누지 않지만 여기서는 인스턴스를 공유한다.
- MetaData는 **하나만** 쓴다. 공식 템플릿처럼 별칭별 MetaData로 쪼개면
  `market.indicator_observation` → `raw.source_record.id` 스키마 간 ForeignKey가 resolve되지 않는다.
- 테이블을 다른 별칭으로 옮기려면 모델의 `database=` 값을 바꾸고 `makemigrations`를 한 번 돌린다.
  한 리비전 파일 안에서 한쪽 섹션에 CREATE, 다른 쪽에 DROP이 생긴다. 데이터는 자동으로 옮겨가지 않는다.

Django의 `DATABASE_ROUTERS`와 목적은 같지만 위치가 다르다. Django는 라우터 함수의
`allow_migrate(db, app_label, ...)`로 앱 단위 판단을 하고, 여기서는 테이블마다 직접 선언한다.

### 이미 존재하는 외부 테이블 편입

다른 시스템이 이미 만들어 데이터가 들어 있는 테이블은 Django `migrate --fake-initial`처럼 편입한다.
`apps/models/finance.py`의 `ExchangeRate`가 그 예다.

- 모델은 실제 DDL을 글자 그대로 미러링한다. 컬럼 타입, nullable, 기본값, 제약·인덱스 이름까지 같아야 한다.
- BIGSERIAL 기본키, timezone-aware 시각, 테이블·컬럼 주석 같은 프로젝트 기본 규칙은 적용하지 않는다.
  실제 DB에 주석이 없으면 `table_options(comment=None)`으로 둔다. 모델에만 주석을 달면
  autogenerate가 매번 `COMMENT ON` 차이를 만든다.
- `managed=True`를 유지한다. `managed=False`는 이후 스키마 변경을 추적하지 못한다.
- revision은 손으로 쓴다. 해당 alias 함수 맨 앞에서 `sa.inspect(op.get_bind()).has_table(...)`로
  존재를 확인하고 있으면 반환한다. offline(`--sql`)은 연결이 없으므로 항상 전체 DDL을 찍는다.
- `downgrade_<alias>()`는 `pass`다. 소유자가 이 프로젝트가 아니므로 `DROP TABLE`을 내지 않는다.

전체 사용법과 예시는 [README.md](../README.md)의 `테이블 라우팅` 절에 있다.

## Airflow와 공유하는 코드

저장소의 `airflow/`가 컨테이너의 `/opt/airflow`다. 운영 Airflow의 마운트 경로와 1:1로 맞춘다.

| 저장소 | 컨테이너 |
| --- | --- |
| `airflow/dags/` | `/opt/airflow/dags` |
| `airflow/modules/` | `/opt/airflow/modules` |
| `airflow/utility/` | `/opt/airflow/utility` |
| `airflow/sql/` | `/opt/airflow/sql` |
| `airflow/plugins/` | `/opt/airflow/plugins` |
| `airflow/config/` | `/opt/airflow/config` |

Airflow는 `apps/`, `../apps/core/`, `migrations/`를 보지 못한다. DAG가 실행 시점에 import하는 코드는
전부 `airflow/` 아래 있어야 한다.

import 뿌리는 `airflow/`다. DAG는 배포와 같은 이름으로 `from modules.collectors import ...`처럼
쓴다. 로컬 도구도 같은 뿌리를 쓴다: pytest `pythonpath`, pyrefly `search-path`,
ruff isort `known-first-party`가 `pyproject.toml`에 맞춰져 있다.

쿼리는 Python 문자열이 아니라 `airflow/sql/<엔진>/<테이블>/<동작>.sql`에 둔다.
`modules/sql.py`의 `read_sql`이 `AIRFLOW_HOME`이 있으면 그 아래를, 없으면 저장소의
`airflow/sql`을 읽는다. 컨테이너와 로컬 pytest가 같은 파일을 쓴다.

로컬 Compose와 Dockerfile은 운영 Airflow에 맞춰 둔 상태다. **건드리지 않는다.** 배치 문제는
코드 위치로만 해결한다. 실행 코드를 이미지에 굽거나 `apps/`를 볼륨으로 붙이지 않는다.

겹치는 코드는 **위치는 Airflow를, 규칙은 백엔드를** 따른다.

- 공유 코드는 `airflow/modules` 아래 한 벌만 둔다. `apps/`에 사본을 만들지 않는다.
- 외부 입력은 Pydantic으로 검증하고, 시각은 timezone-aware UTC이며, 주석은 한국어로 쓴다.
- `dags/`에는 스케줄, 재시도, 태스크 매핑, Hook 사용, 실패 분류만 둔다.
  파싱·검증·저장 규칙은 `modules/`에 둔다.
- 의존성은 Airflow 환경에 있는 것만 쓴다. 표준 라이브러리, Pydantic, PEP 249 연결,
  그리고 HTML 수집용 `scrapling[fetchers]`다. SQLAlchemy 모델과 `core.config`는 import하지 않는다.
  여기에 더 넣으려면 운영 Airflow 이미지에 먼저 들어가야 한다.
- 테이블 정의의 원본은 백엔드의 `apps/models`다. 수집기는 문자열 SQL을 쓰므로
  `tests/collectors/`의 `test_fred.py`, `test_ecos.py`, `test_mof.py`, `test_boe.py`,
  `test_ecb.py`가 INSERT 컬럼과 `ON CONFLICT` 키를 모델 metadata와 대조한다.

## 수집기 작성

`airflow/modules/collectors/fred.py`가 기준이다.

- 요청 값, 외부 응답 본문, 정규화 결과, 수집 결과를 모두 Pydantic 모델로 선언한다.
  `dataclass`를 쓰지 않는다. 외부 JSON은 `model_validate_json`으로 검증한다.
- 모델은 `ConfigDict(frozen=True)`다. 재시도 경로에서 값이 바뀌면 원본과 저장값이 어긋난다.
- 시각 필드는 `AwareDatetime`으로 받고 validator에서 UTC로 정규화한다.
- 허용 값이 정해진 필드는 validator로 막는다(예: 시계열 ID는 `TREASURY_SERIES` 안의 값만).
- 제공처가 잘못된 식별자에도 정상 응답으로 답하면 식별자를 Enum으로 좁혀 요청 전에 막는다.
  ECOS는 없는 항목코드에도 데이터 없음(`INFO-200`)으로 답해서 오타가 조용한 0건이 된다.
  `airflow/modules/collectors/ecos.py`의 `MarketRateSeries`가 그 예다.
- API 키는 `SecretStr`로 받는다. URL에 키가 들어가므로 예외 메시지와 로그에 URL을 넣지 않는다.
  ECOS는 질의 문자열이 아니라 URL 경로에 키를 받는데 규칙은 같다. 반대로 인증이 없는 제공처는
  URL을 그대로 남긴다(`mof.py`, `boe.py`, `ecb.py`). 감출 게 없는데 감추면 디버깅만 어려워진다.
  인증이 아니라 차단을 피하려고 User-Agent를 명시하는 것은 별개다. 재무성과 BoE는 기본
  `Python-urllib/3.x`를 막는다.
- 제공처가 파일을 여러 개로 쪼개 고시하면 어느 파일을 받을지도 수집 규칙이라 `modules/`가 정한다.
  재무성은 이번 달치와 과거 전체를 따로 두고 둘이 겹치지 않아, 구간이 달 경계를 넘으면 둘 다
  받아야 한다. `mof.fetch_curves`가 그 판단을 하고 DAG는 결과만 저장한다.
- 수집 단위가 시계열이 아니라 파일이나 조회 한 번이면 `source_record`도 그 단위로 남기고
  `source_key`에 파일 이름(`jgbcm`)이나 조회 이름(`gilt_nominal_par_yields`,
  `YC.B.U2.EUR.4F.G_N_A.SV_C_YM`)을 넣는다. 시계열마다 태스크를 매핑하지 않는다.
  응답 하나가 곡선 전체를 담고 있어 나눠 요청할 것이 없기 때문이다.
- 원본이 JSON이 아니면 `payload`에 넣지 않는다. 컬럼 타입이 jsonb다. 대신 어느 파일이 어느
  구간을 담고 있었는지를 `metadata`에 남겨 재현할 수 있게 한다.
- 응답에 우리가 요청하지 않은 식별자가 섞여 오면 실패시킨다. ECB는 `KEY` 칸을 매 행 대조해
  `G_N_C`(전체 발행자) 곡선이 AAA 곡선에 섞이는 것을 막는다. 조용히 같은 만기에 값이 두 개
  생기는 것보다 멈추는 편이 낫다.
- 제공처가 값 없음과 잘못된 식별자를 **같은 응답으로** 알리면 조회 구간 앞에 패딩을 붙여
  영업일이 반드시 들어가게 한다. BoE IADB는 둘 다 HTTP 200에 HTML 오류 페이지로 답해서
  응답만으로는 가를 수 없다. `boe.FETCH_PADDING_DAYS`가 14일을 붙이고 저장은 구간 안만
  한다. 패딩을 붙이고도 오류 페이지면 식별자나 구간이 틀린 것이다. 반대로 둘이 갈리는
  제공처에는 패딩을 붙이지 않는다. ECB는 값 없음이 HTTP 200 빈 본문, 없는 키가 404다.
- 외부 오류는 재시도 가능 여부로 나눠 올린다. 판단은 DAG가 한다.
- 제공처가 실패를 HTTP 상태가 아니라 본문으로 알리면 그 코드를 담는 예외를 따로 둔다.
  `EcosResultError`가 그 예다. 수집기는 코드를 해석하지 않고 DAG가 재시도 여부를 정한다.
- 응답이 페이지 단위로 잘릴 수 있으면 제공처가 알려 준 전체 건수와 받은 행 수를 대조해
  잘림을 실패로 만든다. 조용히 잘린 응답은 조회 구간에 구멍을 남긴다.
- HTML 수집은 scrapling을 쓴다. `airflow/modules/collectors/hana.py`가 기준이다.
  요청은 `Fetcher`(curl_cffi), 파싱은 `Selector`다. `impersonate`로 실제 브라우저 지문을
  흉내 내므로 앞단 WAF에 막히지 않는다. 페이지가 JavaScript로 표를 그릴 때만
  `DynamicFetcher`나 `StealthyFetcher`를 쓴다. 이건 브라우저를 띄우므로 기본값이 아니다.
- 표를 위치(index)로 읽으면 칸 수를 상수로 두고 응답마다 검증한다. 사이트가 열을 추가하면
  값이 조용히 옆 칸으로 밀린다. 칸 수 검사가 먼저 실패해야 그걸 알 수 있다.
  CSV도 같다. **저장하지 않는 열까지 헤더 전체를 대조한다.** 저장 대상만 확인하면 그 사고를
  못 잡는다. `mof.EXPECTED_HEADER`가 그 예다.
- 제공처가 자기 나라 표기로 날짜를 주면 그 변환도 수집기가 한다. 모르는 표기는 실패시킨다.
  재무성은 和暦(`R8.8.3` = 2026-08-03)을 쓴다. 조용히 엉뚱한 연도로 저장되는 것보다 멈추는
  편이 낫다.
- 로케일을 타는 표기는 표를 직접 둔다. `strptime`과 `strftime`의 `%b`, `%a`는 실행 환경의
  `LC_TIME`을 타므로 컨테이너 로케일이 바뀌면 조용히 실패한다. BoE의 `03 Aug 2026`은
  `boe.MONTH_NAMES`가 읽고 쓴다.
- 날짜 문자열은 모양을 먼저 보고 파싱한다. `date.fromisoformat`은 `2026-W32` 같은 ISO 주
  표기도 받아 그 주의 월요일로 바꾼다. 주간·월간 빈도의 값이 섞이면 조용히 엉뚱한 날짜로
  저장된다. `ecb.ISO_DATE_PATTERN`이 달력 하루인지 먼저 본다.
- 조회 구간 계산은 `modules/period.py`에 한 벌만 둔다. DAG마다 복사하지 않는다.
  이 모듈은 Airflow를 import하지 않는다. import하면 수집기 테스트가 배포 환경 없이 돌지 않는다.
  실패는 `PeriodError`로 올리고 `AirflowFailException`으로 바꾸는 건 DAG가 한다.

## 시간대 규칙

### Airflow 배치

- 배치 트리거 시간대는 한국 시간(`Asia/Seoul`)이다. `AIRFLOW__CORE__DEFAULT_TIMEZONE=Asia/Seoul`.
- Airflow cron과 `start_date`는 KST로 작성한다. `start_date`는 반드시 timezone-aware로 두고
  `pendulum.datetime(..., tz=KST_TIMEZONE)`을 쓴다. naive datetime은 쓰지 않는다.
  `KST_TIMEZONE`은 `modules/utility.py`에 있다.
- 스케줄 코드에는 같은 줄 주석으로 UTC를 반드시 병기한다.
  예: `schedule="30 7 * * 2-6"  # KST 화~토 07:30 = UTC 월~금 22:30`
- 배치 조회 기간과 날짜 경계는 KST 기준으로 계산한다. `data_interval_end`는 aware 값이므로
  `astimezone(KST_TIMEZONE)`으로 변환한 뒤 날짜를 뽑는다.
- 시간대는 트리거 시점과 날짜 경계 계산에만 쓴다. **DB에 저장하는 시각과 로그는 UTC다.**
  컨테이너 시계도 UTC로 둔다.
- 외부 데이터의 원본 시각과 시간대는 보존하되, 비교·저장용 시각은 UTC로 정규화한다.
- 제공처가 날짜의 기준 시간대를 정하는 값(하나은행 고시일자와 ECOS 고시 기준일은 KST,
  FRED 관측일은 미국 영업일, 재무성은 일본 영업일, BoE는 영국 영업일, ECB는 유로 지역
  영업일)은 그 제공처 기준을 따르고 코드 주석에 어느 기준인지 남긴다.

### 백엔드

- 애플리케이션, DB 세션, 로그와 내부 이벤트의 기본 시간대는 `UTC`로 통일한다.
- 모든 내부 시각은 timezone-aware UTC로 생성·전달·저장한다. naive datetime은 쓰지 않는다.
- 요청 시각은 ISO 8601 offset 또는 `Z`를 필수로 받고 UTC로 변환한 뒤 조회·저장한다.
- 일반 API 응답은 변환하지 않은 UTC ISO 8601 값과 `Z`를 반환한다. 예: `2026-08-04T22:30:00Z`.
- 웹 화면의 시간대 변환과 표시는 프론트엔드가 담당한다.
- 프론트엔드 시간대 우선순위는 사용자 계정의 IANA 시간대, 브라우저 시간대, UTC fallback 순서다.
- 국가 정보만으로 시간대를 추정하지 않는다. 한 국가에 여러 시간대가 있을 수 있다.
- Slack·이메일·CSV·PDF처럼 프론트엔드가 없는 출력, 현지 날짜 기준 집계와 업무상 현지 시간 경계가
  필요한 경우에만 백엔드가 변환한다.
- 백엔드 변환에는 사용자 설정 또는 요청에 명시된 IANA 시간대만 사용한다.
- DST와 과거 시간대 변경은 고정 offset 계산이 아니라 IANA timezone 데이터로 처리한다.
- 시간대 변환은 응답 표현 또는 집계 경계 계산 단계에서만 수행하며 DB의 UTC 원본을 변경하지 않는다.
- 시간대 값은 표시와 날짜 경계 계산에만 사용하고 인증, 권한 또는 데이터 접근 범위 판단에는 쓰지 않는다.

## 데이터베이스 테이블 주석

- 모든 SQLAlchemy 테이블은 `__table_args__`의 `comment`에 테이블 목적을 한국어로 작성한다.
- 모든 컬럼은 `mapped_column(comment="...")`에 값의 의미를 작성한다.
  시간대, 단위, 허용 상태가 있으면 함께 명시한다.
- `id`, `created_at`, `updated_at` 같은 공통 필드 주석은 `EntityBase`에서 한 번만 정의한다.
- Alembic 마이그레이션에도 모델과 동일한 테이블·컬럼 주석을 넣어 실제 DB 스키마에 반영한다.
- 모델과 마이그레이션의 주석은 함께 변경하고 테스트로 생성 여부를 확인한다.

## 타입 모델링

- 값의 종류가 정해진 상태·분류 필드는 일반 `str` 대신 Python `StrEnum`과 SQLAlchemy `Enum`을 쓴다.
- SQLAlchemy `Enum`은 `native_enum=False, length=20, values_callable=...` 형태로 선언한다.
  PostgreSQL native enum은 값 추가·삭제 마이그레이션 비용이 커서 쓰지 않는다.
- Enum 컬럼에는 허용 값을 제한하는 DB `CHECK` 제약을 함께 둔다.
- API 요청·응답, 설정, 외부 입력 검증에는 Pydantic 모델과 `Field`, validator를 쓴다.
- 제공처 이름, URL, 종목 코드, 외부 식별자처럼 값이 열려 있는 필드는 `str` 또는 `Text`로 유지한다.
- 단순 문자열을 의미 없이 Pydantic 모델이나 Enum으로 감싸지 않고,
  유효성 규칙이나 제한된 값 집합이 있을 때만 사용한다.

## LLM 코드

LLM을 부르는 코드는 **Pydantic, LangChain, LangGraph 위에서만 쓴다.** 세 층의 역할이 겹치지 않는다.
기준 구현은 `airflow/modules/llm.py`와 `airflow/modules/assessment.py`다.

- **모델 호출은 LangChain이다.** `langchain_xai.ChatXAI` 같은 `BaseChatModel`을 쓰고 HTTP를 직접
  치지 않는다. 요청·응답을 손으로 조립하면 LangSmith 추적이 끊기고 툴 호출 왕복을 직접 짜야 한다.
  메시지는 dict가 아니라 `SystemMessage`, `HumanMessage`, `AIMessage`로 다룬다.
- **어떤 모델을 쓸지는 코드가 정한다.** 모델 정의는 `airflow/modules/llm.py`에 LangChain 문법
  그대로 모아 두고(`document_model()`), 바꿀 때 그 함수를 고친다. `base_url`·모델명을 환경변수로
  빼서 제공처를 갈아 끼우지 않는다. LangChain은 제공처마다 클래스와 인자가 달라 문자열 설정
  몇 개로 흉내 내면 어느 쪽도 제대로 못 쓴다. **API 키만 환경에서 오고, 그것도 우리가 읽지
  않는다** — LangChain 클래스가 자기 이름(`XAI_API_KEY` 등)으로 읽는다. 키를 우리 설정 객체에
  담으면 로그와 예외에 실릴 자리만 늘어난다.
- **흐름 제어는 LangGraph다.** 재시도, 교정 재요청, 분기, 문서·항목 팬아웃(`Send`)은 `StateGraph`의
  노드와 엣지로 표현한다. `if`와 `for`로 흩어 놓지 않는다. 노드 이름이 그대로 트레이스에 남아
  어디서 몇 번 불렀는지 보이는 것이 이 규칙의 목적이다. 상태는 `TypedDict`로 선언하고 병합이
  필요한 칸에는 리듀서(`Annotated[list, operator.add]`)를 단다.
- **데이터 모양은 Pydantic이다.** 설정, 모델 응답, 노드가 주고받는 결과는 `BaseModel`로 선언한다.
  `dataclass`나 맨 dict를 쓰지 않는다. 응답 스키마는 Pydantic 모델에서 뽑아 `response_format`으로
  강제하고(`modules/schema.py`), 강제가 안 되는 제공처를 위해 검증을 그대로 남긴다.
- **흐름은 클래스로 묶는다.** `DocumentAssessor`·`AssessmentBatch`처럼 그래프를 소유한 클래스가
  갖고, 그래프는 생성자에서 한 번 `compile()`한다. 프롬프트 조립과 파싱처럼 상태가 필요 없는
  것은 같은 클래스의 `@staticmethod`로 둔다. 반대로 모델 정의와 오류 분류는 `modules/llm.py`의
  함수다. 감쌀 상태가 없는 것을 클래스로 만들지 않는다.
- **API 키를 그래프 상태에 넣지 않는다.** 상태와 config는 트레이스 입력으로 나간다. `SecretStr`을
  담은 설정 객체는 생성자로만 넘긴다.
- **재시도는 Airflow가 한다.** 모델 클라이언트는 `max_retries=0`으로 만든다. SDK가 먼저 재시도하면
  태스크 타임아웃 안에서 몇 번을 불렀는지 로그와 트레이스가 어긋난다.
- 제공처 예외는 한 곳에서 우리 종류로 바꾼다. 재시도할 값어치가 있는 것(`ConnectionError`)과
  없는 것(`LlmError`)을 가르는 판단은 DAG가 한다.
- 체크포인터·persistence는 붙이지 않는다. 재실행 단위는 Airflow 태스크다.
- 추적은 `LANGSMITH_*` 환경변수로 켠다. 코드에 추적 호출을 심지 않는다.
  **켜면 프롬프트와 원문이 외부로 나간다는 사실을 문서에 남긴다.**

## 테이블 규칙

### `raw.source_record`

API, 크롤링, 웹소켓 수집 결과의 출처와 상태를 가볍게 보존한다. API는 응답 1회, 크롤링은 문서 버전 1개,
웹소켓은 메시지가 아닌 배치 또는 연결 세션 1개를 레코드 단위로 사용한다.

- 수집 방식, 제공처, 원천 식별자, UTC 수집 구간, 상태와 생성 레코드 수는 항상 저장한다.
- 작은 JSON 원본만 `payload`에 선택적으로 저장한다.
- 대용량 원본은 외부 저장소에 두고 `payload_uri`만 저장한다.
- API 키, 인증 헤더와 개인정보는 `payload`나 `metadata`에 저장하지 않는다.
- 정규화 테이블은 `source_record_id` 외래키와 `ON DELETE RESTRICT`로 출처를 연결한다.
- 웹소켓 메시지별로 `SourceRecord`를 생성하지 않는다.

### `market.indicator_observation`

여러 제공처에서 추출한 지표 관측값을 날짜와 단위와 함께 조회 가능한 형태로 누적 저장한다.
`(provider, series_id, observation_date)`를 고유키로 사용하고 `source_record_id`로 근거 수집
레코드와 연결한다. 현재 `fred_treasury_daily`(미국 국채), `ecos_market_rate_daily`(국내
시장금리), `mof_jgb_daily`(일본 국채), `boe_gilt_daily`(영국 국채),
`ecb_yield_curve_daily`(유로 지역 국채)가 채운다.

- `provider`는 그 값을 준 제공처(`fred`, `ecos`, `mof`, `boe`, `ecb`)이며 같은 수집의
  `source_record.source`와 같다.
- `series_id`는 **제공처 안에서만 고유하다.** 그래서 자연키에 `provider`가 함께 들어간다.
- **`series_id`는 사람이 읽을 수 있어야 한다.** FRED의 `DGS10`처럼 제공처 ID가 이미 읽히면
  그대로 쓰고, ECOS 항목코드(`010210000`)처럼 숫자뿐이면 `KTB10Y` 같은 ID를 만들어 저장한다.
  DB나 대시보드에서 값만 보고 무슨 시계열인지 알 수 없으면 안 된다. 제공처의 원본 좌표는
  수집기 Enum이 들고 있다가 요청에 쓰고 `source_record.metadata`에 남긴다.
- **조회하는 쪽도 `provider`를 함께 건다.** `series_id` 하나로 거는 쿼리는 제공처가 늘어나면
  조용히 틀린다. Grafana 대시보드의 패널 쿼리도 마찬가지다.
- 국가·만기 같은 시계열의 성격은 여기 두지 않고 `reference.indicator_series`에 둔다.
- `unit`은 제공처 표기가 아니라 정규화한 표기다. 연이율 퍼센트는 제공처가 `Percent`든 `연%`든
  `Percent`로 저장한다. 그래야 두 나라 금리를 한 쿼리로 비교할 수 있다.
- 관측값이 0건이어도 `source_record`는 남긴다. 조회했지만 값이 없는 구간과 아직 조회하지 않은
  구간이 구분돼야 한다.

### `reference.indicator_series`

`indicator_observation`에 쌓이는 시계열이 어느 나라 무슨 금리인지 설명하는 마스터다.
`(provider, series_id)`가 자연키이고 대시보드가 이 키로 관측값을 조인한다.

- 이 테이블이 있는 이유는 **나라를 추가할 때 조회 쪽을 안 고치기 위해서다.** 일본을 붙일 때
  `global-treasury.json`에서 바꾼 건 만기 변수 쿼리 한 줄뿐이고 패널은 하나도 손대지 않았다.
  영국과 유로 지역을 붙일 때는 그 한 줄조차 고치지 않았다.
- `country`는 ISO 3166-1 alpha-2다. 유로 지역처럼 나라가 아닌 통화권은 `XM`을 쓰고
  `country_name`에 `유로 지역`을 넣는다. 저장 식별자는 제공처가 부르는 이름을 따라도 된다.
  ECB 시계열의 `series_id`가 `EA10Y`인 것과 `country`가 `XM`인 것은 쓰임이 달라 어긋나지 않는다.
- 나라마다 고시하는 만기가 다르다. 국가 비교 패널의 만기 목록은 두 나라 이상이 가진 만기로
  좁힌다(`HAVING count(DISTINCT country) > 1`). 일본 40년이나 유로 지역 6개월처럼 한 나라만
  고시하는 만기는 골라도 비교할 대상이 없다. 한 나라의 만기 전부는 곡선 패널이 그린다.
- `kind`가 국채(`government_bond`)와 단기 자금시장 금리(`money_market`)를 가른다. 국채 곡선
  패널이 CD 91일 같은 값을 집어삼키지 않게 하는 장치다.
- `maturity_months`는 만기 비교와 정렬의 기준이다. 91일물은 3으로 둔다.
- **관측값에서 이 테이블로 외래키를 걸지 않는다.** 걸면 마스터 행이 없는 시계열을 수집기가
  저장하지 못해, 수집기 Enum에만 추가하고 마스터 시드를 빠뜨린 순간 DAG가 죽는다. 대신
  `tests/migrations/test_indicator_series_catalog.py`가 수집기 Enum과 시드를 대조한다.
  **시계열을 늘릴 때는 수집기 Enum과 마스터 시드를 같은 커밋에서 함께 늘린다.**
- 시드는 마이그레이션이 넣는다. 리비전 파일에서 앱 코드를 import하지 않는다. import하면
  나중에 Enum이 바뀔 때 과거 리비전의 결과가 따라 바뀐다.
- `country_name`은 이 테이블이 들고 있다. 국가에 붙는 속성이 더 늘면 별도 country 마스터로
  분리한다.

### `reference.instrument`

시세·뉴스·시그널이 참조하는 추적 종목 마스터다. 관측값이 아니라 기준 정보이므로
`source_record_id`로 수집 계보를 연결하지 않는다.

- `(ticker, market)`을 자연키로 사용한다. `id`는 다른 테이블이 참조할 대리키다.
- `source_symbol`은 수집 소스 심볼이 티커와 다를 때만 채운다. 같으면 `NULL`로 둔다.
- `is_watched`는 수집·분석 대상 여부만 나타낸다. 상장폐지·거래정지 같은 종목 생애주기 상태가
  필요해지면 별도 `status` enum 컬럼으로 분리한다.
- 한 종목을 여러 소스에서 수집하게 되면 `source_symbol` 한 칸으로 못 버틴다.
  그때는 `reference.instrument_source(instrument_id, source, symbol)` 자식 테이블로 옮긴다.

### `exchange_rate`

통화별·회차별 환율 고시다. `default` alias에 있고 `exchange_rate_daily` DAG가 채운다.
2026-08-06에 `finance` alias에서 `default`로 옮겼다. 이전 데이터는 가져오지 않았다.

- **컬럼 형태는 외부 finance DB의 같은 이름 테이블을 글자 그대로 복사한 것이다.** serial `id`,
  naive `timestamp`, `date`/`time` 분리를 그대로 둔다. 나중에 외부 DB의 과거 행을 옮기거나
  Grafana를 우리 DB로 돌릴 때 컬럼이 1:1로 맞아야 하기 때문이다. **우리 DB 이름도 `finance`라**
  원본을 가리킬 때는 `외부 finance DB`라고 쓴다. 프로젝트 기본 규칙 중
  BIGSERIAL과 timezone-aware 시각을 적용하지 않는 유일한 테이블이다.
- 그래서 타입이나 컬럼 구성을 바꾸려면 그 이관 계획을 먼저 접는다. 지금 상태는
  `tests/models/test_finance_models.py`가 컬럼 단위로 고정한다.
- **주석은 예외로 단다.** 주석은 데이터를 옮기는 데 영향을 주지 않으므로 테이블·컬럼 주석은
  프로젝트 기본 규칙대로 채운다.
- `currency`는 `apps/models/finance.py`의 `Currency` StrEnum이다. 저장 타입은 `VARCHAR(10)`
  그대로고 CHECK 제약도 없다. 이 프로젝트의 다른 Enum 컬럼과 다른 점인데, CHECK를 걸면 원본에
  없는 제약이 생기고 통화를 추가할 때마다 제약을 다시 만들어야 하기 때문이다. 허용 값은
  수집기와 Enum이 막는다. `Currency`의 값은 `modules.collectors.hana.HanaCurrency`와 같아야
  하고 `tests/models/test_finance_models.py`가 둘을 대조한다.
- 모델은 `apps/models/finance.py`에 있다. 파일 이름은 스키마도 alias도 아니고 형태를 가져온
  원본 DB 이름이다.
- 멱등 키는 `(currency, date, time, round)`, 제약 이름은 `unique_currency_date_time_round`다.
  수집기 upsert가 이 이름을 그대로 쓴다.
- `finance` alias는 이제 매핑된 모델이 없어서 `migration.enabled: false`다. 런타임 읽기 전용
  연결만 남아 있다. 여기에 테이블을 편입하려면 `enabled: true`와 `model_modules`를 함께 켠다.

## 마이그레이션 작성

- `just makemigrations "<메시지>"`로 생성하고 **생성된 파일을 반드시 읽어본다.**
- 리비전 파일은 `migrations/versions` 하나에 모이고 파일 안에서 별칭별로 갈라진다.
  `upgrade(engine_name)`이 `upgrade_<alias>()`로 디스패치한다. 해당 함수가 없으면 아무 것도 하지 않으므로
  별칭을 나중에 추가해도 과거 리비전을 고칠 필요가 없다.
- `--autogenerate`는 모든 별칭에 실제로 연결한다. 하나라도 접속 불가면 리비전을 만들 수 없다.
- autogenerate는 모델과 **실제 DB 상태**를 비교한다. 리비전 이력이 아니다.
  밀린 리비전이 있으면 이미 만든 테이블을 또 만들려 하므로 `makemigrations` 전에 `just migrate upgrade head`를 먼저 돌린다.
- autogenerate는 `CREATE SCHEMA`를 절대 만들지 않는다. 새 스키마를 쓰는 리비전은
  `op.execute("CREATE SCHEMA IF NOT EXISTS <schema>")`를 해당 별칭 함수 맨 앞에 직접 넣는다.
- 리비전 파일 형식은 `migrations/script.py.mako`가 정한다.
  ruff 규칙(`from collections.abc import Sequence`, `X | Y` 어노테이션)에 맞춰 둔 상태다.
- 마이그레이션 테스트는 `alembic_command.upgrade(config, "head", sql=True)`로 SQL만 뽑아
  테이블 단위 사실만 검증한다. 특정 리비전 ID에 고정하거나 전체 문자열을 세지 않는다.
  리비전을 다시 만들 때마다 깨진다.
# graphify
- **graphify** (`.claude/skills/graphify/SKILL.md`) - any input to knowledge graph. Trigger: `/graphify`
When the user types `/graphify`, use the installed graphify skill or instructions before doing anything else.
