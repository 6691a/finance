# News2

## 설정

애플리케이션 설정은 루트의 `config.yaml` 하나에서만 읽습니다. 환경 변수와 `.env`는 애플리케이션 설정 소스로 사용하지 않습니다.

```powershell
Copy-Item config.yaml.sample config.yaml
```

`config.yaml`에 실제 API 키와 비밀번호를 입력하세요. 이 파일은 Git에서 제외되며, 모든 설정 항목의 예시는 [config.yaml.sample](config.yaml.sample)에 있습니다.

## 데이터베이스와 Redis

여러 데이터베이스와 Redis를 alias별로 YAML에 직접 등록합니다. `default` alias는 각각 반드시 있어야 하며, `DATABASE_URL`, `REDIS_URL` 같은 단일 URL은 자동 변환하지 않습니다.

```yaml
databases:
  default:
    url: postgresql+asyncpg://news2:news2@localhost:15432/news2
    runtime_enabled: true
    migration:
      enabled: true
      model_modules:
        - apps.models
  market_read:
    url: postgresql+asyncpg://market_reader:password@localhost:15432/news2
    runtime_enabled: true
    read_only: true
  market_migration:
    url: postgresql+asyncpg://migration_owner:password@localhost:15432/news2
    runtime_enabled: false
    migration:
      enabled: true
      model_modules:
        - apps.models

redises:
  default:
    url: redis://localhost:16379/0
  stream:
    url: redis://localhost:16379/1
    decode_responses: false
```

- `runtime_enabled: false`인 alias는 애플리케이션 런타임에서 제외되고 migration 명령에서만 사용할 수 있습니다.
- `read_only: true`는 PostgreSQL 연결을 읽기 전용 transaction으로 설정합니다. 최종 권한 보호는 DB role 권한으로 관리하세요.
- 런타임에서 사용할 DB와 migration 전용 DB를 분리할 수 있습니다.
- migration이 켜진 alias의 `model_modules`는 모두 `apps.models`로 둡니다. 어떤 테이블이 어떤 alias에 속하는지는 모델이 직접 선언하며, 자세한 내용은 아래 [테이블 라우팅](#테이블-라우팅)에 있습니다.

## DB alias 추가

1. `config.yaml`의 `databases`에 alias 블록을 추가합니다.
2. migration 전용이면 `runtime_enabled: false`와 `migration` 설정을 함께 추가합니다.
3. 다음 `just makemigrations`부터 새 alias의 `upgrade_<alias>()` 섹션이 revision 파일에 함께 생성됩니다. 이전 revision 파일은 고치지 않아도 됩니다. 해당 alias의 섹션이 없으면 그 revision은 그 alias에서 아무 것도 하지 않습니다.

`model_modules`는 프로젝트의 ORM 모델 모듈 경로를 직접 입력하는 값입니다. 모델이나 revision 파일은 자동으로 만들지 않습니다.

## 테이블 라우팅

테이블이 어느 alias에 속하는지는 모델에서 선언합니다. `core.database.table_options`를 `__table_args__`의 **마지막** 요소로 씁니다.

```python
from core.database import EntityBase, table_options


class Instrument(EntityBase):
    __tablename__ = "instrument"
    __table_args__ = (
        UniqueConstraint("ticker", "market", name="uq_instrument_ticker_market"),
        table_options(
            schema="reference",
            comment="시세·뉴스·시그널이 참조하는 추적 종목 마스터",
            database="default",
        ),
    )
```

| 인자 | 기본값 | 의미 |
| --- | --- | --- |
| `schema` | 없음(필수) | PostgreSQL 스키마 이름 |
| `comment` | 없음(필수) | 테이블 목적을 적는 한국어 주석 |
| `database` | `"default"` | 이 테이블의 migration을 담당하는 alias |
| `managed` | `True` | 이 프로젝트가 테이블 스키마를 소유하는지 |

선언한 값은 `Table.info`에 들어가고 `migrations/env.py`가 읽습니다. 판단 함수는 [migrations/routing.py](migrations/routing.py)에 따로 있습니다.

### 동작 방식

Alembic 공식 multidb 템플릿과 같은 구조입니다. `migrations/env.py`가 alias를 순회하며 각각 `context.configure()` 후 `run_migrations(engine_name=alias)`를 호출합니다.

- alias 목록은 `alembic.ini`가 아니라 `config.yaml`에서 옵니다. `migrations/cli.py`가 읽어 Alembic의 `databases` 옵션으로 넘깁니다.
- `env.py`는 migration이 켜진 **모든** alias의 `model_modules`를 import합니다. 현재 alias가 소유하지 않는 테이블도 metadata에 있어야 autogenerate에서 제외할 수 있기 때문입니다.
- alias들이 같은 PostgreSQL 인스턴스를 보므로, 제외하지 않으면 각 alias가 서로의 테이블에 `DROP TABLE`을 만들어 냅니다.
- alias마다 revision 포인터 테이블이 다릅니다. `default`는 `alembic_version`, 나머지는 `alembic_version_<alias>`입니다. 공식 템플릿은 DB가 물리적으로 다르다고 보고 이름을 나누지 않지만, 여기서는 한 인스턴스를 공유하므로 나눠야 합니다.
- MetaData는 **하나만** 씁니다. 공식 템플릿처럼 alias별 MetaData로 쪼개면 `market.indicator_observation` → `raw.source_record.id` 같은 스키마 간 ForeignKey가 resolve되지 않습니다. 대신 Alembic 훅에서 `table.info["database"]`로 걸러냅니다.
- 훅은 `include_name`과 `include_object` **둘 다** 필요합니다. `include_name`은 DB에서 reflection된 이름만 보므로 다른 alias의 테이블이 "사라진 테이블"로 잡히는 것만 막습니다. 모델 metadata까지 보는 `include_object`가 있어야 다른 alias 소유 테이블에 `CREATE TABLE`을 내지 않습니다. 판정은 둘 다 `migrations.routing.include_table` 하나에 위임합니다.
- 테이블을 다른 alias로 옮기려면 모델의 `database=` 값을 바꾸고 `makemigrations`를 한 번 실행합니다. 한 revision 파일 안에서 한쪽 섹션에 `CREATE TABLE`, 다른 쪽에 `DROP TABLE`이 생깁니다. 데이터는 자동으로 옮겨가지 않으므로 필요하면 revision에 직접 씁니다.

### 읽기 전용

읽기 전용은 층이 두 개고, 서로 다른 것을 막습니다.

**스키마 소유권 — `managed=False` (테이블 단위)**

```python
table_options(
    schema="market",
    comment="외부 시스템이 만들고 관리하는 테이블",
    managed=False,
)
```

ORM 매핑은 그대로라 읽고 쓸 수 있지만, **어떤 alias의 autogenerate에도 나오지 않습니다.** 이 프로젝트가 만들지 않은 테이블(다른 서비스 소유, 뷰, 외부 ETL 산출물)을 모델로만 읽을 때 씁니다. Django의 `Meta.managed = False`와 같은 뜻입니다.

**연결 권한 — `read_only: true` (alias 단위)**

```yaml
market_read:
  url: postgresql+asyncpg://market_reader:password@localhost:15432/news2
  runtime_enabled: true
  read_only: true
```

해당 alias의 모든 연결에 `default_transaction_read_only=on`을 겁니다. 그 세션으로는 어떤 테이블에도 쓸 수 없습니다. 테이블 하나만 골라 쓰기를 막는 설정이 아니며, 최종 방어선은 DB role 권한입니다.

### 이미 존재하는 외부 테이블 편입 (fake-initial)

다른 시스템이 이미 만들어 운영 데이터가 들어 있는 테이블을, DDL은 건드리지 않으면서 migration 이력에만 올리는 방법입니다. Django의 `migrate --fake-initial`과 같습니다. `finance` alias의 `exchange_rate`가 이 방식입니다.

1. 모델을 실제 DDL 그대로 미러링합니다. 컬럼 타입, nullable, 기본값, 제약·인덱스 **이름**까지 같아야 합니다. 한 글자라도 다르면 다음 autogenerate가 그 차이를 `ALTER`로 뱉습니다.
2. 프로젝트 기본 규칙을 여기서는 적용하지 않습니다. BIGSERIAL 기본키, timezone-aware UTC 시각, 테이블·컬럼 주석 모두 실제 DB를 따릅니다. 주석이 없는 테이블이면 `table_options(comment=None)`으로 둡니다. 모델에만 주석을 달면 `COMMENT ON` 차이가 영구히 남습니다.
3. `managed=True`로 둡니다. `managed=False`는 autogenerate에서 완전히 빼는 설정이라 이후 스키마 변경을 추적하지 못합니다.
4. revision은 손으로 씁니다. 해당 alias 함수 맨 앞에서 테이블 존재 여부를 확인하고, 있으면 그대로 반환합니다.

   ```python
   def _already_exists() -> bool:
       if context.is_offline_mode():
           return False
       return sa.inspect(op.get_bind()).has_table(TABLE)


   def upgrade_finance() -> None:
       if _already_exists():
           return

       op.create_table(TABLE, ...)
   ```

   본문은 그 DB를 처음부터 만들 때 나올 DDL이고, 실제 DB에는 실행되지 않습니다. offline(`--sql`)은 연결이 없어 항상 전체 DDL을 찍으므로 테스트에서 스키마를 검증할 수 있습니다.
5. `downgrade_<alias>()`는 `pass`로 둡니다. 데이터 소유자가 이 프로젝트가 아니므로 `DROP TABLE`을 내면 안 됩니다.
6. `just migrate upgrade head`를 한 번 실행하면 그 DB에 `alembic_version_<alias>` 테이블만 생기고 대상 테이블은 그대로입니다. 이후 `just makemigrations`에서 해당 alias 섹션이 비어 있으면 미러링이 정확한 것입니다.

Django에는 테이블 단위 읽기 전용 옵션이 없습니다. `Meta.managed = False`는 위의 스키마 소유권만 담당하고 ORM 쓰기는 그대로 허용하며, 읽기/쓰기 분리는 라우터의 `db_for_read`와 `db_for_write`로 DB 단위로 합니다. 이 프로젝트의 두 층도 같은 구분을 따릅니다.

## Migration 명령

한 번의 명령이 migration이 켜진 모든 alias를 순서대로 처리합니다. alias를 인자로 주지 않습니다.

```powershell
just makemigrations "create instrument table"
just migrate upgrade head
just migrate downgrade -1
```

`just makemigrations "<메시지>"`는 `revision --autogenerate`만 실행합니다. `just migrate <args>`는 임의의 Alembic 명령을 그대로 전달합니다.

migration 설정이 있는 alias가 하나도 없거나, 목록에 없는 alias를 만나면 DB 연결 전에 오류가 발생합니다.

`--autogenerate`는 **모든** alias에 실제로 연결합니다. alias 중 하나라도 접속할 수 없으면 revision을 만들 수 없습니다.

### revision 파일 구조

revision 파일은 `migrations/versions` 하나에 모이고, 파일 안에서 alias별로 갈라집니다.

```python
def upgrade(engine_name: str) -> None:
    _run(f"upgrade_{engine_name}")


def upgrade_default() -> None:
    op.create_table("instrument", ..., schema="reference")


def upgrade_market_migration() -> None:
    pass
```

`_run`은 해당 alias의 함수가 없으면 아무 것도 하지 않습니다. alias를 나중에 추가해도 과거 revision 파일을 전부 고칠 필요가 없습니다.

### 생성된 revision 확인

autogenerate 결과는 **반드시 열어서 확인합니다.**

- autogenerate는 `CREATE SCHEMA`를 절대 만들지 않습니다. 새 스키마를 쓰는 revision은 해당 alias 함수 맨 앞에 직접 넣습니다.

  ```python
  op.execute("CREATE SCHEMA IF NOT EXISTS reference")
  ```

- autogenerate는 모델과 **실제 DB 상태**를 비교합니다. revision 이력이 아닙니다. 밀린 revision이 있으면 이미 만든 테이블을 또 만들려고 하므로, `makemigrations` 전에 `just migrate upgrade head`로 모든 alias를 최신 상태로 맞춥니다.
- `op.create_table(...)`에 `info={'database': ..., 'managed': ...}`가 함께 렌더링됩니다. 라우팅 선언이 `Table.info`에 있어서 그대로 따라온 값이며 DDL에는 영향이 없습니다. 지워도 됩니다.

- 의도하지 않은 `DROP TABLE`이 보이면 그 테이블의 `database=` 선언이 빠졌거나 `model_modules`에 모델 모듈이 없는 경우입니다.
- 엉뚱한 alias 섹션에 작업이 들어갔으면 모델의 `database=` 값을 확인합니다.
- revision 파일 형식은 [migrations/script.py.mako](migrations/script.py.mako)가 정하며 ruff 규칙에 맞춰져 있습니다.

## Airflow와 공유하는 코드

저장소의 `airflow/`가 컨테이너의 `/opt/airflow`입니다. 운영 Airflow가 마운트하는 경로와 1:1로 맞춰 둡니다.

| 저장소 | 컨테이너 | 용도 |
| --- | --- | --- |
| `airflow/dags/` | `/opt/airflow/dags` | 스케줄과 오케스트레이션만 |
| `airflow/modules/` | `/opt/airflow/modules` | DAG가 import하는 실행 코드 |
| `airflow/utility/` | `/opt/airflow/utility` | 알림 등 공용 유틸리티 |
| `airflow/sql/` | `/opt/airflow/sql` | 쿼리 파일 |
| `airflow/plugins/` | `/opt/airflow/plugins` | Airflow 플러그인 |
| `airflow/config/` | `/opt/airflow/config` | Airflow 설정 |

Airflow는 `apps/`, `core/`, `migrations/`를 **보지 못합니다.** DAG가 실행 시점에 import하는 코드는 전부 `airflow/` 아래 있어야 합니다.

import 뿌리는 `airflow/`입니다. DAG는 배포와 같은 이름으로 `from modules.collectors import ...`, `from utility.alert import ...`처럼 씁니다. 로컬 도구도 같은 뿌리를 쓰도록 [pyproject.toml](pyproject.toml)에 맞춰 뒀습니다.

- `[tool.pytest.ini_options] pythonpath = [".", "airflow"]`
- `[tool.pyrefly] search-path = [".", "airflow"]`
- `[tool.ruff.lint.isort] known-first-party`에 `modules`, `utility` 포함

쿼리는 Python 문자열이 아니라 `airflow/sql/<엔진>/<테이블>/<동작>.sql`에 둡니다. `modules/sql.py`의 `read_sql`이 `AIRFLOW_HOME`이 있으면 그 아래를, 없으면 저장소의 `airflow/sql`을 읽으므로 컨테이너와 로컬 pytest가 같은 파일을 씁니다.

로컬 Compose와 Dockerfile은 운영 Airflow에 맞춰 둔 상태이므로 코드 배치로만 맞춥니다. 실행 코드를 이미지에 굽거나 `apps/`를 볼륨으로 붙이는 방식은 쓰지 않습니다.

### 겹치는 코드의 위치와 규칙

- **위치는 Airflow를 따릅니다.** 배포에서 보이지 않는 경로에 실행 코드를 두면 DAG가 죽습니다. 백엔드와 Airflow가 함께 쓰는 수집 코드는 `airflow/modules` 아래 한 벌만 둡니다. 사본을 `apps/`에 만들지 않습니다.
- **규칙은 백엔드를 따릅니다.** 외부 입력은 Pydantic으로 검증하고, 시각은 timezone-aware UTC이며, 주석은 한국어로 씁니다.
- **`dags/`에는 오케스트레이션만 둡니다.** 스케줄, 재시도, 태스크 매핑, Hook 사용, 실패 분류가 여기에 해당합니다. 파싱·검증·저장 규칙은 `modules/`에 둡니다.
- **의존성은 Airflow 환경에 있는 것만 씁니다.** 표준 라이브러리, Pydantic, PEP 249 연결입니다. SQLAlchemy 모델과 `core.config`는 import하지 않습니다.
- **테이블 정의의 원본은 백엔드입니다.** 수집기는 ORM 없이 문자열 SQL을 쓰므로 컬럼 이름이 어긋나면 실행 시점에야 드러납니다. [tests/collectors/test_fred.py](tests/collectors/test_fred.py)가 INSERT 컬럼 목록과 `ON CONFLICT` 키를 `apps/models`의 metadata와 대조합니다. 모델을 고치면 이 테스트가 먼저 깨집니다.

### 수집기 작성 규칙

[airflow/modules/collectors/fred.py](airflow/modules/collectors/fred.py)가 기준 예시입니다.

- 요청 값(`FredRequest`), 외부 응답 본문(`FredObservationsPayload`), 정규화 결과(`FredObservation`), 수집 결과(`FredResponse`)를 모두 Pydantic 모델로 선언합니다. `dataclass`를 쓰지 않습니다. 외부 JSON은 `model_validate_json`으로 검증합니다.
- 모델은 `ConfigDict(frozen=True)`로 둡니다. 재시도 경로에서 값이 바뀌면 원본과 저장값이 어긋납니다.
- 시각 필드는 `AwareDatetime`으로 받고 validator에서 UTC로 정규화합니다. naive datetime은 모델 단계에서 거부됩니다.
- 허용 값이 정해진 필드는 validator로 막습니다. 시계열 ID는 `TREASURY_SERIES`에 있는 값만 받습니다.
- API 키는 `SecretStr`로 받습니다. URL에 키가 들어가므로 예외 메시지와 로그에 URL을 넣지 않습니다. 키는 Git에서 제외된 `compose/local/airflow/.env`의 `FRED_API_KEY`로만 주입합니다.
- 외부 오류는 재시도 가능 여부로 나눕니다. HTTP 상태는 `FredHTTPError`, 형식 오류는 `FredPayloadError`, 연결 실패는 `ConnectionError`입니다. 판단은 DAG가 합니다.

### 미국 국채 수집 DAG

[airflow/dags/fred_treasury_daily.py](airflow/dags/fred_treasury_daily.py)는 FRED에서 국채 수익률 곡선(`DGS3MO`, `DGS2`, `DGS10`, `DGS30`)을 한국 시간 화~토 07:30(UTC 월~금 22:30)에 수집합니다.

배치 트리거 시간대는 한국 시간입니다. `AIRFLOW__CORE__DEFAULT_TIMEZONE=Asia/Seoul`이고, cron과 `start_date`를 KST로 선언한 뒤 같은 줄 주석에 UTC를 병기합니다. 조회 기간과 날짜 경계도 KST로 계산합니다. DB에 저장하는 시각과 로그는 UTC 그대로입니다.

- 시계열마다 태스크를 매핑합니다. 하나가 실패해도 나머지는 저장되고, 재시도도 실패한 시계열만 다시 호출합니다.
- 실행마다 최근 7일을 다시 조회합니다. 휴장일과 발표 지연을 별도 캘린더 없이 흡수합니다.
- 정규화 멱등 키는 `(series_id, observation_date)`입니다. 재조회분은 행을 늘리지 않고 최신 발표로 갱신합니다.
- FRED가 결측을 뜻하는 `.`을 보내면 정규화하지 않습니다. 원본 응답에는 그대로 남습니다.
- 원본 INSERT와 정규화 UPSERT는 하나의 트랜잭션입니다. 커밋과 롤백은 DAG가 결정합니다.
- `FRED_API_KEY`가 없거나 HTTP 400·401·403·404면 재시도하지 않고 즉시 실패합니다. 429는 `Retry-After`를 로그에 남기고 재시도합니다.

## Grafana

수집한 지수를 차트와 대시보드로 확인하는 용도입니다. `just dev`로 PostgreSQL, Redis와 함께 올라갑니다.

```powershell
just dev
```

접속은 <http://localhost:13000>, 계정은 `admin` / `admin` 입니다. 로컬 개발 전용 값이므로 이 포트를 외부에 노출하기 전에 반드시 변경하세요.

`GF_SECURITY_ADMIN_USER`와 `GF_SECURITY_ADMIN_PASSWORD`는 `grafana` 볼륨이 처음 만들어질 때만 적용됩니다. 이미 볼륨이 있는 상태에서 비밀번호를 바꾸려면 컨테이너 안에서 직접 재설정합니다.

```powershell
docker exec local-grafana-1 grafana cli admin reset-admin-password <new-password>
```

### Provisioning

| 경로 | 역할 |
| --- | --- |
| `compose/local/grafana/provisioning/datasources/` | datasource 정의. 접속 정보는 `compose/local/.env`가 넘기고, 비어 있으면 compose가 로컬 `db` 컨테이너 값을 채웁니다. |
| `compose/local/grafana/provisioning/dashboards/` | dashboard provider 정의. |
| `compose/local/grafana/dashboards/` | 대시보드 JSON. 하위 디렉터리 구조가 Grafana 폴더 구조가 됩니다. |

datasource는 UI에서 수정할 수 없습니다(`editable: false`). 변경은 YAML을 고치고 컨테이너를 재시작합니다.

```powershell
docker compose -f compose/local/docker-compose.yaml restart grafana
docker compose -f compose/local/docker-compose.yaml logs -f grafana
```

### DB를 바꿔서 보기

같은 대시보드를 다른 데이터베이스로 돌려 보는 길이 둘 있습니다. 대시보드 JSON은 어느 쪽에서도 고치지 않습니다.

1. **대시보드 상단 `데이터소스` 드롭다운.** 재시작이 필요 없습니다. 프로비저닝된 postgres datasource 중에서 고르면 모든 패널이 따라갑니다. 잠깐 다른 환경을 확인할 때 씁니다.
2. **`compose/local/.env`의 `NEWS_DB_*`.** `news2` datasource 자체가 가리키는 DB가 바뀝니다. 값을 비우면 로컬 `db` 컨테이너를 씁니다. 반영하려면 컨테이너를 다시 만듭니다.

```powershell
docker compose -f compose/local/docker-compose.yaml up -d grafana
```

datasource YAML은 `$NEWS_DB_URL` 같은 이름만 참조하므로 접속 정보가 저장소에 들어가지 않습니다. 값은 compose 서비스의 `environment`가 넘깁니다.

### 미국 국채 대시보드

[compose/local/grafana/dashboards/us-treasury.json](compose/local/grafana/dashboards/us-treasury.json)은 `indicator_observation` 테이블의 FRED 국채 수익률을 그립니다. `fred_treasury_daily` DAG가 채우는 테이블입니다.

- 만기별 최신 금리 stat. `만기` 변수로 패널이 반복됩니다.
- 만기별 금리 추이 시계열.
- 장단기 금리차(10Y - 3M) 시계열. 0 아래는 금리 역전이라 임계선을 함께 그립니다. 이 패널만 `만기` 변수와 무관하게 항상 `DGS10`과 `DGS3MO`를 씁니다.
- 만기별 최신 관측값과 수집 계보 테이블. `source_record`를 조인해 그 값이 어느 수집 실행에서 왔는지 보여 줍니다.
- 수집 실행별 정규화 행 수. 실행마다 최근 7일을 다시 조회하므로 정상이면 5~7행이 찍힙니다.

`observation_date`는 시간대가 없는 `date`입니다. 시계열 패널이 쓰려면 timestamptz가 필요하므로 서브쿼리에서 `observation_date::timestamp AT TIME ZONE 'UTC'`로 만든 뒤 매크로에는 컬럼 이름만 넘깁니다. Grafana 매크로 인자 파서가 중첩 괄호를 읽지 못하기 때문입니다.

### 대시보드를 Git에 남기기

대시보드는 UI에서 만들고, 완성되면 JSON으로 내보내 저장소에 커밋합니다.

1. 대시보드 상단의 **Export** → **Export as JSON** → **Save to file**.
2. 저장한 파일을 `compose/local/grafana/dashboards/` 아래에 둡니다.
3. provider가 10초 간격으로 감시하므로 재시작 없이 반영됩니다.

이 단계를 건너뛰면 대시보드는 `grafana` 볼륨에만 남고 볼륨 삭제 시 사라집니다.

### 지수 테이블 설계

Grafana 시계열 패널은 `time`, 값, 계열 이름 형태의 결과를 기대합니다. 지표마다 컬럼을 늘리는 대신 `(ts, symbol, value)` 형태로 세로로 쌓으면 지표를 추가할 때 migration이 필요 없고 쿼리도 단순해집니다.

```sql
SELECT ts AS time, value, symbol AS metric
FROM index_quote
WHERE $__timeFilter(ts)
ORDER BY ts
```

캔들 차트는 Grafana 코어의 Candlestick 패널을 사용하며, `open`, `high`, `low`, `close`, `volume` 이름의 컬럼을 자동으로 매핑합니다.

### finance datasource와 환율 대시보드

`finance` datasource는 이 프로젝트가 소유하지 않는 외부 읽기 전용 PostgreSQL(`config.yaml`의 `databases.finance`와 같은 서버)을 바라봅니다. 접속 정보는 `compose/local/.env`에서 오고 이 파일은 커밋하지 않습니다. `compose/local/.env.sample`을 복사해서 채웁니다.

```powershell
Copy-Item compose/local/.env.sample compose/local/.env
```

비밀번호에 `$`가 들어가면 compose가 변수로 해석하므로 `.env`에서 작은따옴표로 감쌉니다. 값을 바꾼 뒤에는 컨테이너를 다시 만들어야 반영됩니다.

```powershell
docker compose -f compose/local/docker-compose.yaml up -d grafana
```

[compose/local/grafana/dashboards/exchange-rate.json](compose/local/grafana/dashboards/exchange-rate.json)은 이 datasource의 `exchange_rate` 테이블을 그립니다. 통화별 최신 매매기준율 stat, 통화별 매매기준율·살 때·팔 때 시계열, 선택 구간 변화율 비교, 최신 고시 테이블로 구성되고 `통화` 변수로 패널이 반복됩니다.

쿼리를 고칠 때 지켜야 할 제약이 세 가지 있습니다.

- **날짜 범위 조건을 반드시 함께 겁니다.** `$__timeFilter((date + time) AT TIME ZONE '${tz}')`만으로는 `idx_exchange_rate_date`를 타지 못해 전체 스캔이 됩니다. 약 1.9 GB, 700만 행이고 원격 서버에 손상된 블록이 있어 전체 스캔은 `could not read block ...` 오류로 끝납니다. `date BETWEEN ($__timeFrom()::timestamptz AT TIME ZONE '${tz}')::date AND ($__timeTo()::timestamptz AT TIME ZONE '${tz}')::date`를 함께 둡니다.
- **`date`와 `time`은 분리된 컬럼이고 시간대 정보가 없습니다.** `(date + time) AT TIME ZONE '${tz}'`로 timestamptz를 만들어 씁니다. `tz` 변수 기본값은 `Asia/Seoul`이며, 원본이 UTC 벽시계로 밝혀지면 대시보드에서 `UTC`로 바꿉니다.
- **Grafana 매크로 인자에는 괄호를 넣을 수 없습니다.** 매크로 파서의 정규식이 `\$__(\w+)\(([^)]*)\)`라 첫 `)`에서 인자가 잘립니다. `$__timeGroupAlias((date + time) AT TIME ZONE '${tz}', $__interval)`은 인자가 `(date + time` 하나로 잘려 `macro __timeGroup needs time column and interval and optional fill value`로 실패합니다. 서브쿼리나 CTE에서 timestamptz 컬럼을 먼저 만들고 매크로에는 그 컬럼 이름만 넘깁니다.

  ```sql
  SELECT $__timeGroupAlias(quoted_at, $__interval), avg(rate) AS "매매기준율"
  FROM (
    SELECT (date + time) AT TIME ZONE '${tz}' AS quoted_at, ... FROM exchange_rate WHERE ...
  ) AS quotes
  WHERE $__timeFilter(quoted_at)
  GROUP BY 1 ORDER BY 1
  ```
- **`exchange_standard_rate`가 0인 통화가 있습니다.** 최근 구간의 CNY·RUB·TWD가 그렇습니다(2024년 데이터는 정상). 패널은 `CASE WHEN exchange_standard_rate > 0 THEN exchange_standard_rate ELSE (buy + sell) / 2 END`로 대체값을 씁니다.

1분 간격 고시라 구간이 길면 행이 많아집니다. 시계열 패널은 `$__timeGroupAlias(..., $__interval)`로 다운샘플링합니다.
