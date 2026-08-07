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
  `tests/collectors/test_fred.py`가 INSERT 컬럼과 `ON CONFLICT` 키를 모델 metadata와 대조한다.

## 수집기 작성

`airflow/modules/collectors/fred.py`가 기준이다.

- 요청 값, 외부 응답 본문, 정규화 결과, 수집 결과를 모두 Pydantic 모델로 선언한다.
  `dataclass`를 쓰지 않는다. 외부 JSON은 `model_validate_json`으로 검증한다.
- 모델은 `ConfigDict(frozen=True)`다. 재시도 경로에서 값이 바뀌면 원본과 저장값이 어긋난다.
- 시각 필드는 `AwareDatetime`으로 받고 validator에서 UTC로 정규화한다.
- 허용 값이 정해진 필드는 validator로 막는다(예: 시계열 ID는 `TREASURY_SERIES` 안의 값만).
- API 키는 `SecretStr`로 받는다. URL에 키가 들어가므로 예외 메시지와 로그에 URL을 넣지 않는다.
- 외부 오류는 재시도 가능 여부로 나눠 올린다. 판단은 DAG가 한다.
- HTML 수집은 scrapling을 쓴다. `airflow/modules/collectors/hana.py`가 기준이다.
  요청은 `Fetcher`(curl_cffi), 파싱은 `Selector`다. `impersonate`로 실제 브라우저 지문을
  흉내 내므로 앞단 WAF에 막히지 않는다. 페이지가 JavaScript로 표를 그릴 때만
  `DynamicFetcher`나 `StealthyFetcher`를 쓴다. 이건 브라우저를 띄우므로 기본값이 아니다.
- 표를 위치(index)로 읽으면 칸 수를 상수로 두고 응답마다 검증한다. 사이트가 열을 추가하면
  값이 조용히 옆 칸으로 밀린다. 칸 수 검사가 먼저 실패해야 그걸 알 수 있다.

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
- 제공처가 날짜의 기준 시간대를 정하는 값(하나은행 고시일자는 KST, FRED 관측일은 미국 영업일)은
  그 제공처 기준을 따르고 코드 주석에 어느 기준인지 남긴다.

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

원본에서 추출한 DGS10 관측값을 날짜와 단위와 함께 조회 가능한 형태로 누적 저장한다.
`(series_id, observation_date)`를 고유키로 사용하고 `source_record_id`로 근거 수집 레코드와 연결한다.

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

통화별·회차별 환율 고시다. `default` alias(news2)에 있고 `exchange_rate_daily` DAG가 채운다.
2026-08-06에 `finance` alias에서 `default`로 옮겼다. 이전 데이터는 가져오지 않았다.

- **컬럼 형태는 외부 finance DB의 같은 이름 테이블을 글자 그대로 복사한 것이다.** serial `id`,
  naive `timestamp`, `date`/`time` 분리를 그대로 둔다. 나중에 finance의 과거 행을 옮기거나
  Grafana를 news2로 돌릴 때 컬럼이 1:1로 맞아야 하기 때문이다. 프로젝트 기본 규칙 중
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
