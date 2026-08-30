# 저장소 운영 안내

- 상태: 지금 도는 코드의 설명이다. Grafana 절만 예외 — 사용을 끝내기로 했고(2026-08-26)
  제거는 [analysis/market-thesis/14-web-ui.md](analysis/market-thesis/14-web-ui.md)와 함께 한다.
- 무엇: 설정·DB alias·마이그레이션·DAG 목록·배포·관측. **저장소를 돌리는 사람과 에이전트가 읽는다.**
  프로젝트가 무엇인지는 루트 [README.md](../README.md)가 갖는다.

## 설정

애플리케이션 설정은 루트의 `config.yaml` 하나에서만 읽습니다. 환경 변수와 `.env`는 애플리케이션 설정 소스로 사용하지 않습니다.

```powershell
Copy-Item config.yaml.sample config.yaml
```

`config.yaml`에 실제 API 키와 비밀번호를 입력하세요. 이 파일은 Git에서 제외되며, 모든 설정 항목의 예시는 [config.yaml.sample](../config.yaml.sample)에 있습니다.

## 데이터베이스와 Redis

여러 데이터베이스와 Redis를 alias별로 YAML에 직접 등록합니다. `default` alias는 각각 반드시 있어야 하며, `DATABASE_URL`, `REDIS_URL` 같은 단일 URL은 자동 변환하지 않습니다.

```yaml
databases:
  default:
    url: postgresql+asyncpg://finance:finance@localhost:15432/finance
    runtime_enabled: true
    migration:
      enabled: true
      model_modules:
        - apps.models
  market_read:
    url: postgresql+asyncpg://market_reader:password@localhost:15432/finance
    runtime_enabled: true
    read_only: true
  market_migration:
    url: postgresql+asyncpg://migration_owner:password@localhost:15432/finance
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
from apps.core.database import EntityBase, table_options


class Instrument(EntityBase):
    __tablename__ = "instrument"
    __table_args__ = (
        UniqueConstraint("ticker", "market", name="uq_instrument_ticker_market"),
        table_options(
            comment="시세·뉴스·시그널이 참조하는 추적 종목 마스터",
            database="default",
        ),
    )
```

| 인자 | 기본값 | 의미 |
| --- | --- | --- |
| `comment` | 없음(필수) | 테이블 목적을 적는 한국어 주석 |
| `database` | `"default"` | 이 테이블의 migration을 담당하는 alias |
| `managed` | `True` | 이 프로젝트가 테이블 스키마를 소유하는지 |

스키마는 지정하지 않습니다. 연결의 `search_path`(PostgreSQL 기본 `public`)를 그대로 따르므로 모든
테이블이 `public` 스키마에 있습니다. 선언한 값은 `Table.info`에 들어가고 `migrations/env.py`가
읽습니다. 판단 함수는 [migrations/routing.py](../migrations/routing.py)에 따로 있습니다.

### 동작 방식

Alembic 공식 multidb 템플릿과 같은 구조입니다. `migrations/env.py`가 alias를 순회하며 각각 `context.configure()` 후 `run_migrations(engine_name=alias)`를 호출합니다.

- alias 목록은 `alembic.ini`가 아니라 `config.yaml`에서 옵니다. `migrations/cli.py`가 읽어 Alembic의 `databases` 옵션으로 넘깁니다.
- `env.py`는 migration이 켜진 **모든** alias의 `model_modules`를 import합니다. 현재 alias가 소유하지 않는 테이블도 metadata에 있어야 autogenerate에서 제외할 수 있기 때문입니다.
- alias들이 같은 PostgreSQL 인스턴스를 보므로, 제외하지 않으면 각 alias가 서로의 테이블에 `DROP TABLE`을 만들어 냅니다.
- alias마다 revision 포인터 테이블이 다릅니다. `default`는 `alembic_version`, 나머지는 `alembic_version_<alias>`입니다. 공식 템플릿은 DB가 물리적으로 다르다고 보고 이름을 나누지 않지만, 여기서는 한 인스턴스를 공유하므로 나눠야 합니다.
- MetaData는 **하나만** 씁니다. 공식 템플릿처럼 alias별 MetaData로 쪼개면 `indicator_observation` → `source_record.id` 같은 alias 간 ForeignKey가 resolve되지 않습니다. 대신 Alembic 훅에서 `table.info["database"]`로 걸러냅니다.
- 훅은 `include_name`과 `include_object` **둘 다** 필요합니다. `include_name`은 DB에서 reflection된 이름만 보므로 다른 alias의 테이블이 "사라진 테이블"로 잡히는 것만 막습니다. 모델 metadata까지 보는 `include_object`가 있어야 다른 alias 소유 테이블에 `CREATE TABLE`을 내지 않습니다. 판정은 둘 다 `migrations.routing.include_table` 하나에 위임합니다.
- 테이블을 다른 alias로 옮기려면 모델의 `database=` 값을 바꾸고 `makemigrations`를 한 번 실행합니다. 한 revision 파일 안에서 한쪽 섹션에 `CREATE TABLE`, 다른 쪽에 `DROP TABLE`이 생깁니다. 데이터는 자동으로 옮겨가지 않으므로 필요하면 revision에 직접 씁니다.

### 읽기 전용

읽기 전용은 층이 두 개고, 서로 다른 것을 막습니다.

**스키마 소유권 — `managed=False` (테이블 단위)**

```python
table_options(
    comment="외부 시스템이 만들고 관리하는 테이블",
    managed=False,
)
```

ORM 매핑은 그대로라 읽고 쓸 수 있지만, **어떤 alias의 autogenerate에도 나오지 않습니다.** 이 프로젝트가 만들지 않은 테이블(다른 서비스 소유, 뷰, 외부 ETL 산출물)을 모델로만 읽을 때 씁니다. Django의 `Meta.managed = False`와 같은 뜻입니다.

**연결 권한 — `read_only: true` (alias 단위)**

```yaml
market_read:
  url: postgresql+asyncpg://market_reader:password@localhost:15432/finance
  runtime_enabled: true
  read_only: true
```

해당 alias의 모든 연결에 `default_transaction_read_only=on`을 겁니다. 그 세션으로는 어떤 테이블에도 쓸 수 없습니다. 테이블 하나만 골라 쓰기를 막는 설정이 아니며, 최종 방어선은 DB role 권한입니다.

### 이미 존재하는 외부 테이블 편입 (fake-initial)

다른 시스템이 이미 만들어 운영 데이터가 들어 있는 테이블을, DDL은 건드리지 않으면서 migration 이력에만 올리는 방법입니다. Django의 `migrate --fake-initial`과 같습니다. 하나은행 환율 `exchange_rate`가 이 방식이었습니다(2026-08-19 수집 종료와 함께 삭제).

1. 모델을 실제 DDL 그대로 미러링합니다. 컬럼 타입, nullable, 기본값, 제약·인덱스 **이름**까지 같아야 합니다. 한 글자라도 다르면 다음 autogenerate가 그 차이를 `ALTER`로 뱉습니다.
2. 프로젝트 기본 규칙을 여기서는 적용하지 않습니다. BIGSERIAL 기본키, timezone-aware UTC 시각, 테이블·컬럼 주석 모두 실제 DB를 따릅니다. 주석이 없는 테이블이면 `table_options(comment=None)`으로 둡니다. 모델에만 주석을 달면 `COMMENT ON` 차이가 영구히 남습니다. 편입한 테이블을 이 프로젝트가 이어받아 직접 채우기로 했다면 주석은 나중에 붙일 수 있습니다. 주석은 데이터를 옮기는 데 영향을 주지 않으므로 `COMMENT ON` 리비전 하나로 정리하면 그 뒤 autogenerate는 다시 조용해집니다.
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
    op.create_table("instrument", ...)


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
- revision 파일 형식은 [migrations/script.py.mako](../migrations/script.py.mako)가 정하며 ruff 규칙에 맞춰져 있습니다.

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

Airflow는 `apps/`, `apps/core/`, `migrations/`를 **보지 못합니다.** DAG가 실행 시점에 import하는 코드는 전부 `airflow/` 아래 있어야 합니다.

import 뿌리는 `airflow/`입니다. DAG는 배포와 같은 이름으로 `from modules.collectors import ...`, `from utility.alert import ...`처럼 씁니다. 로컬 도구도 같은 뿌리를 쓰도록 [pyproject.toml](../pyproject.toml)에 맞춰 뒀습니다.

- `[tool.pytest.ini_options] pythonpath = [".", "airflow"]`
- `[tool.pyrefly] search-path = [".", "airflow"]`
- `[tool.ruff.lint.isort] known-first-party`에 `modules`, `utility` 포함

쿼리는 Python 문자열이 아니라 `airflow/sql/<엔진>/<테이블>/<동작>.sql`에 둡니다. `modules/sql.py`의 `read_sql`이 `AIRFLOW_HOME`이 있으면 그 아래를, 없으면 저장소의 `airflow/sql`을 읽으므로 컨테이너와 로컬 pytest가 같은 파일을 씁니다.

로컬 Compose와 Dockerfile은 운영 Airflow에 맞춰 둔 상태이므로 코드 배치로만 맞춥니다. 실행 코드를 이미지에 굽거나 `apps/`를 볼륨으로 붙이는 방식은 쓰지 않습니다.

### 겹치는 코드의 위치와 규칙

- **`airflow/` 아래에는 DAG가 실제로 실행하는 코드만 둡니다.** DAG가 쓰는 수집 코드는 `airflow/modules` 아래 한 벌만 둡니다. 배포에서 보이지 않는 경로에 실행 코드를 두면 DAG가 죽습니다. 반대로 Airflow가 실행하지 않는 상주 서비스(`apps/realtime/` — KIS 실시간 WebSocket 수집)는 `apps/` 아래에 백엔드 규칙(ORM, `config.yaml`)으로 두고 별도 컨테이너(`compose/prod/`)로 배포합니다. 두 트리가 겹치는 도메인 상수는 중복을 허용하되 테스트로 대조합니다.
- **규칙은 백엔드를 따릅니다.** 외부 입력은 Pydantic으로 검증하고, 시각은 timezone-aware UTC이며, 주석은 한국어로 씁니다.
- **`dags/`에는 오케스트레이션만 둡니다.** 스케줄, 재시도, 태스크 매핑, Hook 사용, 실패 분류가 여기에 해당합니다. 파싱·검증·저장 규칙은 `modules/`에 둡니다.
- **의존성은 Airflow 환경에 있는 것만 씁니다.** 표준 라이브러리, Pydantic, PEP 249 연결이 기본이고, 여기에 HTML 수집용 `scrapling[fetchers]`와 LLM 호출용 `langchain-xai`·`langgraph`가 더해집니다. 목록은 [compose/local/airflow/requirements.txt](../compose/local/airflow/requirements.txt)에 있고, 새로 쓰려면 운영 Airflow 이미지에 먼저 들어가야 합니다. SQLAlchemy 모델과 `core.config`는 import하지 않습니다.
- **테이블 정의의 원본은 백엔드입니다.** 수집기는 ORM 없이 문자열 SQL을 쓰므로 컬럼 이름이 어긋나면 실행 시점에야 드러납니다. [tests/collectors/test_fred.py](../tests/collectors/test_fred.py)와 [tests/collectors/test_ecos.py](../tests/collectors/test_ecos.py)가 INSERT 컬럼 목록과 `ON CONFLICT` 키를 `apps/models`의 metadata와 대조합니다. 모델을 고치면 이 테스트가 먼저 깨집니다.

### 수집기 작성 규칙

[airflow/modules/collectors/indicator/fred.py](../airflow/modules/collectors/indicator/fred.py)가 기준 예시입니다.

- 요청 값(`FredRequest`), 외부 응답 본문(`FredObservationsPayload`), 정규화 결과(`FredObservation`), 수집 결과(`FredResponse`)를 모두 Pydantic 모델로 선언합니다. `dataclass`를 쓰지 않습니다. 외부 JSON은 `model_validate_json`으로 검증합니다.
- 모델은 `ConfigDict(frozen=True)`로 둡니다. 재시도 경로에서 값이 바뀌면 원본과 저장값이 어긋납니다.
- 시각 필드는 `AwareDatetime`으로 받고 validator에서 UTC로 정규화합니다. naive datetime은 모델 단계에서 거부됩니다.
- 허용 값이 정해진 필드는 validator로 막습니다. 시계열 ID는 `FredSeries` Enum에 있는 값만 받습니다. 이 Enum이 저장 식별자, FRED 좌표, 단위, 종류를 한 줄에 묶습니다. `CPIAUCSL`처럼 제공처 ID만 보고 무슨 값인지 알 수 없는 계열은 `CPI_M` 같은 읽히는 ID로 저장하고, 제공처 좌표는 요청과 `source_record.metadata`에만 씁니다.
- API 키는 `SecretStr`로 받습니다. URL에 키가 들어가므로 예외 메시지와 로그에 URL을 넣지 않습니다. 키는 Git에서 제외된 `compose/local/airflow/.env`의 `FRED_API_KEY`로만 주입합니다.
- 외부 오류는 재시도 가능 여부로 나눕니다. HTTP 상태는 `FredHTTPError`, 형식 오류는 `FredPayloadError`, 연결 실패는 `ConnectionError`입니다. 판단은 DAG가 합니다.

제공처가 FRED처럼 얌전하지 않을 때 더 필요한 규칙은 [airflow/modules/collectors/indicator/ecos.py](../airflow/modules/collectors/indicator/ecos.py)에 있습니다.

- **실패를 HTTP 상태로 알리지 않는 API는 본문 코드를 담는 예외를 따로 둡니다.** ECOS는 인증 실패도 데이터 없음도 HTTP 200에 `{"RESULT": {"CODE": ...}}`로 답합니다. 수집기는 그 코드를 `EcosResultError`에 담아 올리고 해석하지 않습니다. 재시도 여부는 DAG가 정합니다.
- **잘못된 식별자에 정상 응답이 오면 식별자를 Enum으로 좁힙니다.** ECOS는 없는 항목코드에도 데이터 없음과 같은 `INFO-200`으로 답해서, 오타가 조용한 0건이 됩니다. `MarketRateSeries`가 요청 전에 막습니다.
- **응답이 잘릴 수 있으면 전체 건수와 받은 행 수를 대조합니다.** ECOS는 요청한 건수 범위를 넘으면 경고 없이 앞부분만 돌려줍니다. 그대로 저장하면 조회 구간에 조용히 구멍이 남습니다.
- **단위는 제공처 표기가 아니라 정규화한 표기로 저장합니다.** ECOS는 `연%`, FRED는 단위를 주지 않습니다. 둘 다 `Percent`로 저장해야 두 나라 금리를 한 쿼리로 비교할 수 있습니다. 원본 표기는 `source_record`에 남습니다.

### 수집 DAG 목록

DAG마다 절을 두지 않습니다. 상세는 각 DAG 파일의 `doc_md`에 있고, 여기서는 무엇이 언제 도는지까지만 봅니다. 아래 절들은 수집기 성질이 서로 다른 국채 DAG만 골라 설명합니다.

| DAG | 스케줄(KST) | 채우는 테이블 | 제공처 |
| --- | --- | --- | --- |
| `fred_treasury_daily` | 화~토 07:30 | `indicator_observation` | FRED |
| `fred_macro_daily` | 화~토 07:40 | `indicator_observation` | FRED |
| `ecos_market_rate_daily` | 화~토 08:00 | `indicator_observation` | 한국은행 ECOS |
| `bbk_bund_daily` | 화~토 08:10 | `indicator_observation` | 분데스방크 |
| `mof_jgb_daily` | 화~토 08:20 | `indicator_observation` | 일본 재무성 |
| `boe_gilt_daily` | 화~토 08:40 | `indicator_observation` | 잉글랜드은행 |
| `ecb_yield_curve_daily` | 화~토 08:50 | `indicator_observation` | ECB |
| `ecb_convergence_monthly` | 수 08:30 | `indicator_observation` | ECB |
| `market_calendar_daily` | 매일 07:00 | `market_session` | KIS·NYSE |
| `kis_quote_intraday` | 평일 08~16시 5분마다 | `quote_bar`, `market_movement_snapshot` | KIS |
| `kis_investor_flow_intraday` | 평일 09~15시 5분마다 | `market_investor_flow_snapshot` | KIS |
| `kis_investor_estimate_intraday` | 평일 09:35·10:05·11:25·13:25·14:35 | `stock_investor_estimate_snapshot` | KIS |
| `kis_investor_trade_daily` | 평일 18:10 | `stock_investor_trade_daily` | KIS |
| `kis_stock_minute_bars_daily` | 평일 20:40 | `stock_bar` | KIS |
| `kis_market_positioning_daily` | 화~토 08:10 | `krx_*` 6종(신용·공매도·대차·증시자금) | KIS |
| `kis_overseas_index_close` | 화~토 07:30 | `index_bar`(S&P500·나스닥 종합 마감 분봉) | KIS |
| `yahoo_quote_intraday` | 5분마다(시간 창 없음) | `quote_bar` | Yahoo |
| `yahoo_quote_daily` | 매일 07:30 | `quote_daily` | Yahoo |
| `dart_disclosure_intraday` | 평일 07~20시 2분마다 | `disclosure_event`, `earnings_fact` | DART |
| `document_ingestion_hourly` | 매시 05분 | `document`, `document_source` | 공식기관·언론 피드 |
| `document_body_hourly` | 매시 15분 | `document`(본문), `document_attachment` | 문서 원문 페이지 |
| `document_assessment_hourly` | 매시 25분 | `document`, `document_instrument`, `document_indicator` | LLM (`gpt-5.6-luna`) |

수집하는 DAG는 전부 `source_record`도 함께 남깁니다. 관측값이 0건이어도 남겨서, 조회했지만 값이 없는 구간과 아직 조회하지 않은 구간을 구분합니다. 예외는 둘입니다. `document_body_hourly`와 `document_assessment_hourly`는 새 문서를 발견하지 않고 이미 저장된 문서의 행을 채웁니다.

`yahoo_quote_intraday`에만 시간 창이 없습니다. 한국 장중의 미국 선물 변동을 보는 것이 이 수집의 목적이라 미국 장 시간에만 도는 스케줄로는 목적을 못 이룹니다.

구현 계약은 [KIS 시장 수급·포지션·캘린더](collection/kis-market-data-collection.md),
[DART 공시·실적](collection/dart-disclosure-earnings.md),
[ECB 회원국 10년물 월평균](collection/ecb-convergence-monthly.md) 문서에 정리했습니다.

### Slack 브리핑 DAG 목록

수집하지 않고 **읽어서 내보내기만 하는** DAG들입니다. 설계는 [docs/briefing/slack-report-design.md](briefing/slack-report-design.md)에 있습니다.

| DAG | 스케줄(KST) | 채널 | 내용 |
| --- | --- | --- | --- |
| `slack_kr_market_briefing` | 평일 08:10·09:00·10~19시 매시·15:30·20:15 | `SLACK_CHANNEL_MARKET` | NXT·KRX 시세, 수급, 전일 비교, 당일·일봉 기술 차트 |
| `slack_us_market_briefing` | 화~토 08:00 | `SLACK_CHANNEL_MARKET` | 밤사이 미국 지수·선물(현물 옆에 선물)·원자재·크립토·ADR, 주요국 10년 금리, 전일 국내 복기 |
| `slack_document_briefing` | 매일 08:00·12:00·15:30·20:00 | `SLACK_CHANNEL_DOCUMENT` | 직전 발송 이후 평가 집계와 LLM 선별 문서 |
| `slack_ops_briefing` | 매일 08:00 | `SLACK_CHANNEL_OPS` | 지난 24시간 수집 성공·실패·무소식 |

표와 비교값은 SQL 집계가 만듭니다. 시장·운영 브리핑은 LLM을 쓰지 않고, 문서 브리핑만
후보 선별에 사용합니다. 선별이 실패해도 점수순 대체 목록으로 리포트는 나갑니다.

미국 정규장은 KST로 밤이라 장중 알림을 보내지 않습니다. 대신 다음 날 아침 리포트가 밤사이
결과와 전일 한국장을 같은 메시지에 놓습니다.

### 미국 국채 수집 DAG

[airflow/dags/fred_treasury_daily.py](../airflow/dags/fred_treasury_daily.py)는 FRED에서 국채 수익률 곡선(`DGS3MO`, `DGS2`, `DGS10`, `DGS30`)을 한국 시간 화~토 07:30(UTC 월~금 22:30)에 수집합니다.

배치 트리거 시간대는 한국 시간입니다. `AIRFLOW__CORE__DEFAULT_TIMEZONE=Asia/Seoul`이고, cron과 `start_date`를 KST로 선언한 뒤 같은 줄 주석에 UTC를 병기합니다. 조회 기간과 날짜 경계도 KST로 계산합니다. DB에 저장하는 시각과 로그는 UTC 그대로입니다.

- 시계열마다 태스크를 매핑합니다. 하나가 실패해도 나머지는 저장되고, 재시도도 실패한 시계열만 다시 호출합니다.
- 실행마다 최근 7일을 다시 조회합니다. 휴장일과 발표 지연을 별도 캘린더 없이 흡수합니다.
- 정규화 멱등 키는 `(provider, series_id, observation_date)`입니다. 재조회분은 행을 늘리지 않고 최신 발표로 갱신합니다.
- FRED가 결측을 뜻하는 `.`을 보내면 정규화하지 않습니다. 원본 응답에는 그대로 남습니다.
- 원본 INSERT와 정규화 UPSERT는 하나의 트랜잭션입니다. 커밋과 롤백은 DAG가 결정합니다.
- `FRED_API_KEY`가 없거나 HTTP 400·401·403·404면 재시도하지 않고 즉시 실패합니다. 429는 `Retry-After`를 로그에 남기고 재시도합니다.

### 국내 시장금리 수집 DAG

[airflow/dags/ecos_market_rate_daily.py](../airflow/dags/ecos_market_rate_daily.py)는 한국은행 ECOS의 통계표 `1.3.2.1. 시장금리(일별)`(817Y002)에서 국고채 2·3·10·30년과 CD 91일을 한국 시간 화~토 08:00(UTC 월~금 23:00)에 수집합니다. 미국 국채와 같은 `indicator_observation` 테이블에 쌓이고 `provider`로 갈립니다.

구간 계산, 태스크 매핑, 트랜잭션 경계는 미국 국채 DAG와 같습니다. 제공처 성질이 달라 아래가 다릅니다.

- 저장하는 `series_id`는 `KTB10Y`처럼 읽을 수 있는 ID입니다. ECOS 통계표 코드와 항목코드(`817Y002`, `010210000`)는 `MarketRateSeries` Enum이 들고 있다가 요청 URL에만 쓰고 `source_record.metadata`에 남깁니다. 숫자 코드를 그대로 저장하면 DB와 대시보드에서 무슨 값인지 읽을 수 없습니다.
- 조회 구간 밖의 날짜가 딸려 오지 않습니다. 되돌아본 만큼이 그대로 조회 구간이라 백필이 요청 범위를 넘지 않습니다. FRED는 넘칩니다.
- 휴장일은 행 자체가 없습니다. 구간 전체가 휴장이면 `INFO-200`이 오고, 이건 실패가 아니라 관측값 0건입니다. 그래도 `source_record`는 남겨 조회한 구간과 아직 조회하지 않은 구간을 구분합니다.
- `INFO-100`(인증키 무효)과 `ERROR-1xx~4xx`(요청 인자 문제)는 즉시 실패합니다. 그 밖의 `RESULT` 코드는 제공처 쪽 오류로 보고 재시도합니다. 실제 응답으로 확인한 코드는 `INFO-100`과 `INFO-200`뿐이라 모르는 코드는 재시도 쪽에 둡니다.
- 관측일 기준은 국내 영업일입니다. 인증키는 `compose/local/airflow/.env`의 `ECOS_API_KEY`로 주입하며, ECOS는 이 키를 질의 문자열이 아니라 URL 경로에 받습니다.

### 일본 국채 수집 DAG

[airflow/dags/mof_jgb_daily.py](../airflow/dags/mof_jgb_daily.py)는 일본 재무성 국채금리정보 CSV에서 JGB 2·5·10·20·30·40년을 한국 시간 화~토 08:20(UTC 월~금 23:20)에 수집합니다. 미국·국내 금리와 같은 `indicator_observation` 테이블에 쌓이고 `provider = 'mof'`로 갈립니다. 조회 구간 계산과 트랜잭션 경계는 앞의 두 DAG와 같고, 제공처 성질이 달라 아래가 다릅니다.

- **태스크가 하나입니다.** 파일 하나가 곡선 전체를 담고 있어 시계열마다 나눠 요청할 것이 없습니다. `source_record`도 시계열이 아니라 파일 단위로 남고 `source_key`가 파일 이름입니다.
- **파일이 둘로 나뉘고 겹치지 않습니다.** `jgbcm.csv`는 이번 달치만 담고 매달 1일에 비워지며, `data/jgbcm_all.csv`는 1974-09-24부터 지난달 말까지입니다. 어느 한쪽도 최근 며칠과 과거를 함께 담지 못하므로 구간이 달 경계를 넘으면 둘 다 받습니다. `fetch_curves`가 이번 달 파일의 첫 날짜를 보고 정합니다. 이 판단이 없으면 매달 초 며칠 동안 되돌아본 구간이 조용히 사라집니다.
- **인증이 없습니다.** API 키도 등록도 없어 환경 변수는 `AIRFLOW_CONN_FINANCE` 하나면 됩니다. URL에 비밀이 없으므로 예외 메시지와 로그에 URL을 그대로 남깁니다. 대신 기본 `Python-urllib/3.x`가 막히는 경우가 있어 User-Agent를 명시합니다.
- **인코딩이 CP932이고 날짜가 和暦입니다.** `R8.8.3`은 令和8年8月3日, 즉 2026-08-03입니다. 모르는 연호 글자는 실패시킵니다. 조용히 엉뚱한 연도로 저장되는 것보다 멈추는 편이 낫습니다.
- **헤더 열여섯 칸을 전부 대조합니다.** 재무성 CSV는 1~40년 열다섯 개를 주지만 실제 입찰 발행되는 연한만 저장합니다. 저장 대상만 확인하면 재무성이 열을 추가했을 때 값이 옆 칸으로 밀린 것을 못 잡습니다.
- **`payload`는 비웁니다.** 원본이 CSV라 jsonb 컬럼에 들어가지 않고 과거 전체 파일은 1MB가 넘습니다. 어느 파일이 어느 구간을 담고 있었는지는 `metadata`가 남깁니다.
- 아직 발행되지 않은 만기는 `-`로 옵니다. 1974년 구간에는 2년과 5년만 값이 있습니다. 결측이지 오류가 아니므로 건너뜁니다.
- 관측일 기준은 일본 영업일입니다. 일본(JST)과 한국(KST)은 offset이 같아 날짜 경계가 어긋나지 않습니다.

전 구간(1974-09-24 ~ 현재)을 넣으면 관측값이 6만 행쯤 됩니다. 한 트랜잭션이 커지므로 5년 단위로 잘라 `source_file`, `observation_start`, `observation_end`를 직접 넘깁니다.

```powershell
airflow dags trigger mof_jgb_daily --conf '{\"source_file\": \"all\", \"observation_start\": \"2020-01-01\", \"observation_end\": \"2024-12-31\"}'
```

### 영국 국채 수집 DAG

[airflow/dags/boe_gilt_daily.py](../airflow/dags/boe_gilt_daily.py)는 잉글랜드은행 IADB에서 gilt 5·10·20년 명목 par yield를 한국 시간 화~토 08:40(UTC 월~금 23:40)에 수집합니다. 앞의 DAG들과 같은 `indicator_observation` 테이블에 쌓이고 `provider = 'boe'`로 갈립니다. 구간 계산과 트랜잭션 경계는 같고, 제공처 성질이 달라 아래가 다릅니다.

- **태스크가 하나입니다.** IADB가 `SeriesCodes`에 코드를 여러 개 받아 한 응답에 담아 주므로 시계열마다 나눠 요청할 것이 없습니다. `source_record`도 시계열이 아니라 조회 단위로 남고 `source_key`가 `gilt_nominal_par_yields`입니다.
- **만기가 셋뿐입니다.** IADB의 명목 par yield 노드가 일별로 고시하는 만기는 5·10·20년입니다(`IUDSNPY`, `IUDMNPY`, `IUDLNPY`). 제로쿠폰 노드도 같은 셋입니다. 0.5~40년 전 구간을 담은 일별 수익률 곡선은 BoE가 따로 내지만 형식이 xlsx라 Airflow 이미지의 의존성으로는 읽을 수 없습니다. 그래서 이 나라만 2년물이 없고 장단기 금리차도 20년-5년으로 봅니다.
- **값이 없는 구간과 잘못된 코드를 응답만으로 가를 수 없습니다.** 요청 구간에 데이터가 한 행도 없으면 CSV가 아니라 HTTP 200에 HTML 오류 페이지가 오고, 존재하지 않는 코드를 물었을 때도 같은 페이지가 옵니다. 그래서 수집기가 조회 구간보다 `FETCH_PADDING_DAYS`(14일)만큼 앞에서부터 받아 영업일이 반드시 들어가게 합니다. 구간 밖의 행은 저장 전에 버리므로 저장 결과는 달라지지 않습니다. 패딩을 붙이고도 오류 페이지가 오면 코드나 구간 자체가 틀린 것이라 즉시 실패합니다.
- **인증이 없습니다.** 다만 기본 `Python-urllib/3.x`로 요청하면 `Access Denied`가 오므로 User-Agent를 명시합니다. 인증이 아니라 차단 회피이므로 값 자체에 의미는 없고, URL에 비밀이 없어 예외 메시지와 로그에 URL을 그대로 남깁니다.
- **날짜가 `03 Aug 2026` 꼴입니다.** 달 이름을 `strptime`의 `%b`에 맡기지 않고 표를 직접 둡니다. `%b`는 실행 환경의 `LC_TIME`을 타므로 컨테이너 로케일이 바뀌면 조용히 실패합니다.
- 관측일 기준은 영국 영업일입니다. 주말과 영국 공휴일(bank holiday)에는 행이 없습니다.

### 유로 지역 국채 수집 DAG

[airflow/dags/ecb_yield_curve_daily.py](../airflow/dags/ecb_yield_curve_daily.py)는 ECB Data Portal의 SDMX API에서 유로 지역 국채 수익률 곡선 3개월·6개월·1·2·3·5·7·10·15·20·30년을 한국 시간 화~토 08:50(UTC 월~금 23:50)에 수집합니다. `provider = 'ecb'`로 갈립니다.

- **나라가 아니라 통화권입니다.** 유로 지역 전체의 AAA 등급 국채 스팟 곡선 하나이고 독일·프랑스 같은 개별 회원국 곡선이 아닙니다. ECB가 Svensson 모형으로 추정해 고시하며 `indicator_series.country`에 통화권 코드 `XM`이 들어갑니다. `country_name`은 `유로 지역`입니다.
- **태스크가 하나입니다.** SDMX 키의 마지막 차원에 `+`를 넣어 만기를 한꺼번에 물을 수 있습니다. `source_record`의 `source_key`는 키 접두사 `YC.B.U2.EUR.4F.G_N_A.SV_C_YM`입니다.
- **값이 없는 구간과 잘못된 키가 갈립니다.** 구간에 데이터가 없으면 HTTP 200에 빈 본문(헤더 줄조차 없음)이 오고, 없는 키를 물으면 HTTP 404가 옵니다. 앞은 휴장이라 관측값 0건으로 저장하고 `source_record`는 남기며, 뒤는 설정 오류라 즉시 실패합니다. 그래서 영국과 달리 구간에 패딩을 붙이지 않습니다.
- **`KEY` 칸을 매 행 대조합니다.** `G_N_C`(전체 발행자) 곡선이 섞여 오면 같은 만기에 값이 두 개가 됩니다. 우리가 물어본 키와 다르면 실패시킵니다.
- **`TIME_PERIOD`가 달력 하루인지 먼저 봅니다.** `date.fromisoformat`은 `2026-W32` 같은 ISO 주 표기도 받아 그 주의 월요일로 바꿉니다. 주간·월간 빈도의 값이 섞이면 조용히 엉뚱한 날짜로 저장됩니다.
- 관측일 기준은 유로 지역 영업일(TARGET 결제일)입니다. 곡선은 유럽 시간 정오 무렵에 갱신되므로 최근 1~2 영업일이 비어 있는 것은 정상이고, 되돌아보는 구간이 다음 run에서 채웁니다. 곡선은 2004-09-06부터 고시됩니다.

## 문서 수집과 LLM 평가

시세와 금리는 값이지만 뉴스와 공식 발표는 글입니다. 글을 시세와 같은 좌표계에 올리는 것이 이 두 DAG의 일입니다.

- [airflow/dags/document_ingestion_hourly.py](../airflow/dags/document_ingestion_hourly.py)가 매시 05분에 공식기관·언론 피드에서 문서를 발견해 `document`에 정규화합니다.
- [airflow/dags/document_body_hourly.py](../airflow/dags/document_body_hourly.py)가 매시 15분에 `body_status`가 비어 있는 문서의 원문을 받아 본문을 채우고, 첨부 파일을 내려받아 `document_attachment`에 경로를 남기며, 기사가 영상이면 그 링크를 남깁니다. **이 DAG은 `/opt/airflow/files` 마운트를 요구하고 없으면 즉시 실패합니다.**
- [airflow/dags/document_assessment_hourly.py](../airflow/dags/document_assessment_hourly.py)가 매시 25분에 아직 평가하지 않은 문서를 LLM에 보내 종목·지표 태그, 방향, 0~8점 점수와 근거를 받아 `document`, `document_instrument`, `document_indicator`에 저장합니다.

**평가는 제목과 요약만 봅니다.** 본문을 채우기 시작한 뒤에도 그렇습니다(2026-08-30 결정). 본문의 소비자는 평가가 아니라 검색이고, `content_hash`도 제목과 요약만 보므로 본문이 바뀌어도 재평가가 돌지 않습니다.

**문서를 버리지 않습니다.** 승인·보류 같은 상태 머신을 두면 나중에 기준을 바꿀 때 이미 버린 문서를 되돌릴 수 없습니다. 전부 저장하고 점수만 남긴 뒤, 리포트를 만들 때 상위 몇 개를 고릅니다. 평가에 실패한 문서는 `assessed_at`이 `NULL`로 남아 다음 정시 실행이 다시 집습니다.

태그 테이블(`document_instrument`, `document_indicator`)은 마스터로 **외래키를 걸지 않습니다.** `indicator_observation`이 `indicator_series`를 참조하지 않는 것과 같은 이유입니다. 마스터에 없는 값이 오면 태깅 전체가 죽는 대신 그 태그만 빠져야 합니다. LLM에게는 후보 목록을 프롬프트로 주고, 목록 밖의 값은 저장 전에 버립니다.

흐름도는 [docs/analysis/document-assessment-workflow.md](analysis/document-assessment-workflow.md)에 있습니다.

### LLM 계층은 세 층으로 나뉩니다

층마다 맡는 것이 다르고 겹치지 않습니다. 기준 구현은 [airflow/modules/llm.py](../airflow/modules/llm.py)와 [airflow/modules/assessment.py](../airflow/modules/assessment.py)입니다.

- **모델 호출은 LangChain입니다.** `BaseChatModel`(`ChatOpenAI`·`ChatXAI`)을 쓰고 HTTP를 직접 치지 않습니다. 요청·응답을 손으로 조립하면 추적이 끊기고 툴 호출 왕복을 직접 짜야 합니다.
- **흐름 제어는 LangGraph입니다.** 재시도, 교정 재요청, 문서별 팬아웃(`Send`)을 `StateGraph`의 노드와 엣지로 표현합니다. 노드 이름이 그대로 트레이스에 남아 어디서 몇 번 불렀는지 보이는 것이 이 규칙의 목적입니다.
- **데이터 모양은 Pydantic입니다.** 설정, 모델 응답, 노드가 주고받는 결과를 `BaseModel`로 선언하고, 응답 스키마는 그 모델에서 뽑아 `response_format`으로 강제합니다. 강제를 지원하지 않는 제공처를 위해 스키마 없이 한 번 더 부르는 경로와 검증을 그대로 남겨 둡니다.

**어떤 모델을 쓸지는 코드가 정합니다.** `llm.py`의 `document_model()`·`thesis_model()`·`expectation_model()`이 LangChain 문법 그대로 모델을 만들고, 바꿀 때 그 함수를 고칩니다. 지금 문서 평가와 이벤트 추출은 `ChatOpenAI`로 `gpt-5.6-luna`를, 시장 추론은 `ChatXAI`로 `grok-4.6`을 부릅니다. `base_url`과 모델명을 환경변수로 빼서 제공처를 갈아 끼우지 않습니다. LangChain은 제공처마다 클래스와 인자가 달라 문자열 설정 몇 개로 흉내 내면 어느 쪽도 제대로 못 씁니다. **환경에서 오는 것은 API 키뿐이고 그것도 우리가 읽지 않습니다.** LangChain 클래스가 자기 이름(`OPENAI_API_KEY`·`XAI_API_KEY`)으로 스스로 읽습니다. 키를 우리 설정 객체에 담으면 로그와 예외에 실릴 자리만 늘어납니다.

**재시도는 Airflow가 합니다.** 모델 클라이언트는 `max_retries=0`으로 만듭니다. SDK가 먼저 재시도하면 태스크 타임아웃 안에서 몇 번을 불렀는지 로그와 트레이스가 어긋납니다. 체크포인터도 붙이지 않습니다. 재실행 단위는 Airflow 태스크입니다.

### 추적

`LANGSMITH_TRACING`, `LANGSMITH_API_KEY`, `LANGSMITH_PROJECT`를 환경에 주면 켜집니다. 코드에는 추적 호출이 없습니다. 비워 두면 아무것도 보내지 않고 호출 경로도 그대로입니다.

**켜면 프롬프트 전문과 문서 본문이 LangSmith로 나갑니다.** 저장 위치가 문제가 되면 `LANGSMITH_ENDPOINT`로 다른 인스턴스를 가리킵니다.

## Grafana

> **2026-08-26에 사용 종료를 결정했습니다.** 아래는 아직 로컬에 남아 있는 구성 설명이고,
> 대시보드 JSON·compose 서비스·`tests/dashboards/`는 웹 화면(market-thesis 14단계) 배포와
> 함께 제거합니다. 지금 새 대시보드를 늘리지 않습니다.

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
| `compose/local/grafana/provisioning/datasources/` | 로컬 `finance` datasource 정의. Docker의 `db:5432/finance`만 사용합니다. |
| `compose/local/grafana/provisioning/dashboards/` | dashboard provider 정의. |
| `compose/local/grafana/dashboards/` | 대시보드 JSON. 하위 디렉터리 구조가 Grafana 폴더 구조가 됩니다. |

datasource는 UI에서 수정할 수 없습니다(`editable: false`). 변경은 YAML을 고치고 컨테이너를 재시작합니다.

```powershell
docker compose -f compose/local/docker-compose.yaml restart grafana
docker compose -f compose/local/docker-compose.yaml logs -f grafana
```

### 대시보드 구성

금리 대시보드는 여섯입니다. 나라(통화권)별로 하나씩, 그리고 그것들을 가로지르는 통합 하나입니다. 독일은 나라별 대시보드가 없고 통합에만 나옵니다.

| 대시보드 | 파일 | 보는 것 |
| --- | --- | --- |
| 미국 국채 금리 | `us-treasury.json` | 미국 곡선과 미국 장단기 금리차 |
| 국내 시장금리 | `korea-market-rate.json` | 국내 곡선, 국내 장단기 금리차, CD 91일 |
| 일본 국채 금리 | `japan-treasury.json` | 일본 곡선, 일본 장단기 금리차, 초장기 구간 |
| 영국 국채 금리 | `uk-treasury.json` | 영국 5·10·20년, 20년-5년 금리차 |
| 유로 지역 국채 금리 | `euro-area-treasury.json` | 유로 지역 AAA 곡선(3개월~30년), 10년-2년 금리차 |
| 통합 국채 금리 | `global-treasury.json` | 나라 간 비교, 나라 간 금리차, 최신 수익률 곡선 |

나라별 대시보드는 그 나라 이야기만 담습니다. 나라를 가로지르는 비교는 전부 통합에 둡니다. 나라가 늘어날 때 나라별 대시보드는 새로 하나 만들면 되고, 통합은 패널을 **고치지 않습니다.** 통합이 국가와 만기를 `indicator_series` 마스터에서 읽기 때문입니다. 일본을 붙일 때 통합에서 바꾼 것은 `비교 만기` 변수 쿼리 한 줄뿐이고, 영국과 유로 지역을 붙일 때는 그 한 줄조차 고치지 않았습니다. 나라마다 고시하는 만기가 달라, 두 나라 이상이 가진 만기만 목록에 남기도록 `HAVING count(DISTINCT country) > 1`을 걸었습니다. 일본 40년이나 유로 지역 6개월처럼 한 나라만 고시하는 만기는 골라도 비교할 대상이 없습니다.

나머지 대시보드는 금리가 아닌 값을 봅니다. 아래 절들이 다루지 않는 것도 이 표에서 파일 이름을 찾을 수 있습니다.

| 대시보드 | 파일 | 보는 것 |
| --- | --- | --- |
| 지수·선물 통합 장중 | `quote-intraday.json` | 국내외 지수·선물 1분봉을 한 화면에 |
| 지수 장중 | `quote-index.json` | 지수 1분봉 |
| 지수선물 장중 | `quote-index-future.json` | 지수선물 1분봉 |
| 종목 장중 | `quote-equity.json` | 삼성전자·SK하이닉스 1분봉 |
| 환율 장중 | `quote-fx.json` | 환율 1분봉 |
| 원자재 장중 | `quote-commodity.json` | 원자재 1분봉 |
| 암호화폐 장중 | `quote-crypto.json` | 암호화폐 1분봉 |
| 외국인·기관·개인 수급 | `investor-flow.json` | 시장 수급과 종목 추정 수급 |
| 상승·보합·하락 종목 분포 | `market-movement.json` | 장중 시장 폭(breadth) |
| 신용·공매도·대차 포지션 | `market-positioning.json` | 신용잔고·공매도·대차·증시자금 |
| 공시·실적 (DART) | `dart-disclosure.json` | 공시 타임라인과 실적 추이 |
| 문서 평가 (LLM) | `document-assessment.json` | 점수 분포, 점수 높은 문서, 태그·출처별 집계 |

미국 물가·소매판매(`fred_macro_daily`)만 대시보드가 없습니다. 소비자가 화면이 아니라 리포트 쪽 계산이라 세 계열로 화면을 만들지 않았습니다. 계열이 늘면 그때 만듭니다.

### 미국 국채 대시보드

[compose/local/grafana/dashboards/us-treasury.json](../compose/local/grafana/dashboards/us-treasury.json)은 `indicator_observation` 테이블의 FRED 국채 수익률을 그립니다. `fred_treasury_daily` DAG가 채우는 테이블입니다.

- 만기별 최신 금리 stat. `만기` 변수로 패널이 반복됩니다.
- 만기별 금리 추이 시계열.
- 장단기 금리차(10Y - 3M) 시계열. 0 아래는 금리 역전이라 임계선을 함께 그립니다. 이 패널만 `만기` 변수와 무관하게 항상 `DGS10`과 `DGS3MO`를 씁니다.
- 만기별 최신 관측값과 수집 계보 테이블. `source_record`를 조인해 그 값이 어느 수집 실행에서 왔는지 보여 줍니다.
- 수집 실행별 정규화 행 수. 실행마다 최근 7일을 다시 조회하므로 정상이면 5~7행이 찍힙니다.

`observation_date`는 시간대가 없는 `date`입니다. 시계열 패널이 쓰려면 timestamptz가 필요하므로 서브쿼리에서 `observation_date::timestamp AT TIME ZONE 'UTC'`로 만든 뒤 매크로에는 컬럼 이름만 넘깁니다. Grafana 매크로 인자 파서가 중첩 괄호를 읽지 못하기 때문입니다.

모든 패널이 `provider = 'fred'`를 함께 겁니다. `indicator_observation`은 제공처가 여럿이고 `series_id`는 제공처 안에서만 고유합니다. `series_id` 하나로 거는 쿼리는 지금은 맞지만 제공처가 늘어나면 조용히 틀립니다.

### 국내 시장금리 대시보드

[compose/local/grafana/dashboards/korea-market-rate.json](../compose/local/grafana/dashboards/korea-market-rate.json)은 같은 `indicator_observation` 테이블의 ECOS 국내 시장금리를 그립니다. `ecos_market_rate_daily` DAG가 채웁니다.

- 만기별 최신 금리 stat. `만기` 변수로 패널이 반복됩니다.
- 만기별 금리 추이 시계열.
- 장단기 금리차(국고채 10년 - 3년) 시계열. 국내에서 쓰는 스프레드입니다.
- 만기별 최신 관측값과 수집 계보 테이블. 원본 `series_id`를 함께 보여 줍니다.
- 수집 실행별 정규화 행 수. 실행마다 최근 7일을 다시 조회하므로 정상이면 4~5행이 찍히고, 공휴일이 낀 주에는 더 적습니다.

만기 변수의 값은 화면에 보이는 한글 이름이 아니라 저장된 `series_id`(`KTB10Y`)입니다. ECOS 항목코드(`010210000`)는 `MarketRateSeries` Enum과 `source_record.metadata`가 들고 있습니다.

### 일본 국채 대시보드

[compose/local/grafana/dashboards/japan-treasury.json](../compose/local/grafana/dashboards/japan-treasury.json)은 같은 `indicator_observation` 테이블의 재무성 국채 금리를 그립니다. `mof_jgb_daily` DAG가 채웁니다.

- 만기별 최신 금리 stat. `만기` 변수로 패널이 반복됩니다.
- 만기별 금리 추이 시계열. 계열 이름은 `indicator_series`의 `label`을 조인해 씁니다.
- 최신 수익률 곡선 막대. 2·5·10·20·30·40년을 만기 순으로 세웁니다. 초장기 구간이 곡선의 끝입니다.
- 장단기 금리차(10년 - 2년) 시계열.
- 만기별 최신 관측값과 수집 계보 테이블. 원천 식별자가 시계열이 아니라 파일 이름(`jgbcm` 또는 `jgbcm_all`)입니다.
- 수집 실행별 정규화 행 수. 매달 초 며칠은 한 run이 파일을 둘 받아 점이 두 개 찍힙니다.

`만기` 변수 목록도 손으로 적지 않고 `indicator_series`에서 읽습니다. 미국·국내 대시보드는 목록을 `CASE`나 custom 변수로 들고 있는데, 이쪽은 마스터가 이미 `label`을 갖고 있어 그럴 이유가 없습니다.

### 영국 국채 대시보드

[compose/local/grafana/dashboards/uk-treasury.json](../compose/local/grafana/dashboards/uk-treasury.json)은 같은 `indicator_observation` 테이블의 잉글랜드은행 gilt 금리를 그립니다. `boe_gilt_daily` DAG가 채웁니다. 패널 구성은 일본 대시보드와 같고 두 곳이 다릅니다.

- 장단기 금리차가 **20년 - 5년**입니다. IADB에 2년물이 없어 다른 대시보드가 쓰는 10년-2년을 만들 수 없습니다.
- 최신 수익률 곡선 막대의 점이 셋뿐이라 곡선이라기보다 세 점의 기울기입니다.

수집 계보 표의 원천 식별자는 시계열이 아니라 조회 단위(`gilt_nominal_par_yields`)입니다. IADB가 세 시계열을 한 응답에 담아 주기 때문입니다.

### 유로 지역 국채 대시보드

[compose/local/grafana/dashboards/euro-area-treasury.json](../compose/local/grafana/dashboards/euro-area-treasury.json)은 같은 테이블의 ECB 유로 지역 곡선을 그립니다. `ecb_yield_curve_daily` DAG가 채웁니다.

- 만기가 열하나라 최신 금리 stat이 두 줄로 반복됩니다. 12개월 미만은 개월, 그 이상은 연 단위로 이름을 만듭니다.
- 수집 실행별 정규화 행 수에서 **0으로 찍힌 점은 그 구간이 전부 휴장이었다는 뜻**입니다. 조회했지만 값이 없는 구간과 아직 조회하지 않은 구간을 가르려고 0건도 남깁니다.

값은 Svensson 모형이 추정한 스팟 금리라 만기 사이가 매끄럽게 이어집니다. 개별 국채의 실제 체결 수익률이 아닙니다.

### 통합 국채 대시보드

[compose/local/grafana/dashboards/global-treasury.json](../compose/local/grafana/dashboards/global-treasury.json)은 나라를 가리지 않고 국채 금리를 비교합니다. 국가·만기·기준국이 전부 변수이고, 그 목록을 `indicator_series` 마스터에서 읽습니다.

- 국가별 같은 만기 비교 시계열. 만기는 `비교 만기` 변수로 고릅니다.
- 최신 수익률 곡선 막대. 나라마다 마지막 고시값을 만기 순으로 세웁니다.
- 기준국 대비 금리차 시계열. `금리차 기준국`을 미국으로 두면 다른 나라가 미국보다 얼마나 높은지 봅니다.
- 선택 국가의 만기별 추이. 범례 이름은 `indicator_series.label`입니다.
- 시계열별 최신 관측값과 수집 계보 표. 마스터에는 있는데 관측값이 없는 시계열은 여기서 빠지므로, 수집이 안 붙은 시계열을 찾는 데 씁니다.

**이 대시보드에는 나라 이름도 시계열 ID도 하드코딩돼 있지 않습니다.** 나라를 추가하면 수집기와 마스터 시드만 늘리면 되고, 국가 변수 목록과 모든 패널이 저절로 따라옵니다. 일본·영국·유로 지역을 붙일 때 실제로 그랬습니다. 국채가 아닌 금리(CD 91일 등)는 `kind = 'government_bond'` 조건에서 빠집니다.

### 문서 평가 대시보드

[compose/local/grafana/dashboards/document-assessment.json](../compose/local/grafana/dashboards/document-assessment.json)은 `document_assessment_hourly`가 매긴 점수와 태그를 봅니다. 다른 대시보드가 값의 추이를 보는 것과 달리 **평가가 쓸 만한지**를 보는 화면입니다.

- 수집 문서, 평가 대기, 평균 점수, 마지막 평가 이후 경과 stat.
- 시간별 수집·평가 시계열. 두 선이 벌어지면 평가가 수집을 못 따라가는 것입니다.
- 점수 분포 막대. **이 패널이 이 대시보드의 목적입니다.** 한 점수에 몰려 있으면 그 점수는 문서를 가르지 못하고 리포트가 상위 몇 건을 고를 수 없습니다. 0건인 점수도 칸을 남기려고 `generate_series(0, 8)`이 축을 만듭니다.
- 점수 높은 문서 표. 리포트가 고르는 것과 같은 순서입니다.
- 태그별·출처별 집계 표.

`최소 점수` 변수는 **점수 높은 문서 표에만** 걸립니다. 집계 패널까지 걸면 화면이 "낮은 점수가 없다"고 말하게 되어 점수가 눌린 것을 못 잡습니다. 걸러진 문서도 삭제되지 않고 DB에 그대로 있습니다.

시간 필터는 `published_at`이 아니라 `detected_at`에 겁니다. 피드가 발행 시각을 주지 않는 출처가 있어 `published_at`은 `NULL`일 수 있고 `detected_at`은 항상 있습니다.

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

### finance datasource

`finance` datasource는 Docker Compose가 실행하는 로컬 PostgreSQL의 `finance` DB를 바라봅니다. 모든 대시보드가 이 datasource 하나를 직접 사용합니다.

## 배포

운영은 Synology NAS 한 대이고 저장소 clone 하나(`/volume1/docker/finance`)에서 두 compose
스택을 실행합니다. 두 스택 모두 코드를 이미지에 굽지 않고 clone 안의 트리를 bind-mount
하므로, 배포는 clone을 `git pull` 하는 것이 전부입니다.

| 스택 | compose | 마운트하는 트리 |
| --- | --- | --- |
| Airflow | `compose/prod/airflow/docker-compose.yaml` | `airflow/{dags,logs,config,plugins,modules,utility,sql}` (`config/airflow.cfg`는 추적하지 않는 호스트 파일) |
| KIS 실시간 수집기 | `compose/prod/docker-compose.yaml` | `apps/`, clone 루트의 `config.yaml` |
| 조회 API | `compose/prod/api/docker-compose.yaml` | `apps/`, clone 루트의 `config.yaml` |

배포 순서: `main`에 push → NAS clone에서:

```bash
git pull
just deploy            # 두 스택 전부
just deploy-airflow    # airflow만
just deploy-realtime   # realtime만
just deploy-api        # 조회 API만
just build-airflow     # Dockerfile·requirements 변경 시에만, 이어서 deploy
just build-realtime
```

`deploy`는 두 스택을 `up -d` 하고 realtime을 재시작합니다. 이미지 빌드는 분리돼
있습니다 — NAS buildkit이 느리고 코드가 bind-mount라 매 배포에 빌드할 이유가
없습니다. 빌드가 `DeadlineExceeded`로 죽으면 `DOCKER_BUILDKIT=0`을 앞에 붙입니다.
just가 없으면 레시피 안의 docker compose 명령을 그대로 실행합니다. 변경 종류별
반영 방식은 다음과 같습니다.

- `airflow/dags`·`modules` 등 bind-mount 코드 — dag-processor가 재파싱하고 태스크는
  실행마다 새 프로세스이므로 pull만으로 반영됩니다. `just deploy`도 필요 없습니다.
- `apps/` — 상주 프로세스라 pull로는 반영되지 않습니다. deploy 레시피가 realtime
  컨테이너를 재시작합니다. 열린 분은 원래 저장하지 않고 마감 후 REST가 확정본을
  채우므로 재시작 비용은 몇 초 끊김뿐입니다.
- compose 파일 — `up -d`가 설정 변경을 감지해 재생성합니다.
- Dockerfile·requirements — `just build-<스택>` 후 `just deploy-<스택>`. 드문 일이라
  deploy에 넣지 않았습니다.
- `airflow/config/airflow.cfg` — 컨테이너 재시작이 필요합니다. Airflow는 프로세스가
  뜰 때 cfg를 한 번 읽습니다. NAS에서 airflow 스택을 `docker compose restart` 합니다
  (드문 일이라 deploy 레시피에 넣지 않았습니다).

NAS에만 두는 파일은 넷이고 전부 gitignore 대상입니다: `compose/prod/airflow/.env`(Airflow
환경변수·API 키, Sentry DSN 포함), `compose/prod/.env`(realtime 노브), clone 루트의
`config.yaml`(KIS 키·DB·Sentry — 조회 API와 같은 파일), 그리고 `airflow/config/airflow.cfg`입니다.
조회 API 스택은 `.env`를 갖지 않습니다 — 읽을 별칭은 `apps/api/main.py`의 상수입니다.
키 구성은 각 디렉터리의 `.env.sample`이 기준입니다. Sentry는 `airflow.cfg`가 아니라 `.env`의
`AIRFLOW__SENTRY__*`로 켭니다 — cfg는 Airflow가 실행 중에 덮어쓰는 파일이라 값을 넣어도
호스트 밖으로 나가지 않고, 다음 재생성에 지워질 수 있습니다.

**`airflow/config/airflow.cfg`는 추적하지 않습니다**(2026-08-24). Airflow가 실행 중에 값을
덮어써 매번 diff에 뜨고 DSN이 커밋될 위험이 있었습니다. 파일이 없어도 `airflow-init`이
`airflow config list`로 기본 설정을 찍어 만들고, 그전까지는 Airflow가 내장 기본값과 `.env`로
동작합니다. 따로 준비할 것이 없습니다.

**cfg는 파일이 아니라 `config` 디렉터리째 마운트합니다**(2026-08-25). 파일 하나만
bind-mount 하면 컨테이너가 생성 시점의 inode에 못 박힙니다. 호스트에서 에디터가
rename-replace로 저장하는 순간 inode가 갈리고, 컨테이너는 이름이 사라진 옛 파일을 계속
읽습니다 — `docker compose restart`로도 붙지 않아 `--force-recreate`가 필요했습니다.
운영에서 실제로 이 상태가 관측됐습니다(컨테이너 쪽 `stat`이 `Links: 0`). 디렉터리 마운트는
경로를 읽을 때마다 풀므로 이 덫이 없고, 로컬 compose와 배치도 같아집니다.

기존 clone은 `git pull` 후 파일을 한 번 옮기고 스택을 재생성합니다.

```bash
# NAS clone에서 1회. 실행 중인 태스크가 없을 때 한다 — 컨테이너를 새로 만든다.
mv airflow/airflow.cfg airflow/config/airflow.cfg
docker compose -f compose/prod/airflow/docker-compose.yaml up -d --force-recreate
```

최초 세팅(1회): NAS에 deploy key를 등록해 `git clone git@github.com:6691a/finance.git
/volume1/docker/finance`, 세 파일을 `.env.sample`과 대조해 채우고, 각 compose를
`up -d --build` 합니다. Airflow 과거 태스크 로그를 유지하려면 이전 `logs/` 내용을
`airflow/logs/`로 복사합니다(생략해도 동작에는 지장 없음).

## 관측 (Sentry)

Sentry 프로젝트는 둘입니다. Airflow는 NAS `.env`의 `AIRFLOW__SENTRY__*`로, realtime은
`config.yaml`의 `sentry_*`로 붙습니다(`sentry_dsn`이 비면 전체 비활성).

realtime(`apps/realtime/main.py`)에서 켜 둔 것:

| 기능 | 동작 |
| --- | --- |
| 에러 이벤트 | 잡히지 않은 예외와 `logger.error` 이상. 샘플링은 `sentry_error_sample_rate` |
| 로그 | 표준 logging 자동 전달 — WARNING은 breadcrumb, INFO 이상은 Sentry Logs 탭(`enable_logs`) |
| 트레이싱 | `sentry_traces_sample_rate`. HTTP 트랜잭션이 없어 DB 스팬 위주 |
| 프로파일링 | `profile_lifecycle="trace"` — 트랜잭션이 있을 때만 돔 |

메트릭(`sentry_sdk.metrics`)은 아직 쓰지 않습니다. 처리 건수·지연·실패율처럼 측정할
가치가 생기는 지점은 개발 중에 후보로 제안받아 판단합니다(`.claude/CLAUDE.md`의
관측 규칙).

## graphify

저장소의 코드를 지식 그래프로 뽑아 `graphify-out/`에 둡니다. 에이전트가 파일을 훑는 대신 `graphify query "<질문>"`으로 필요한 부분만 받아 가는 용도입니다. 결과물은 Git에서 제외합니다.

```
uv tool install "graphifyy[sql, postgres, openai]"
graphify hook install
graphify install --project --platform codex
graphify install --project --platform claude
graphify extract . --code-only --force
```

코드를 고친 뒤에는 `graphify update .`로 그래프를 맞춥니다. AST만 다시 읽으므로 API 비용이 들지 않습니다.
