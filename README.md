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
2. 프로젝트 기본 규칙을 여기서는 적용하지 않습니다. UUID 기본키, timezone-aware UTC 시각, 테이블·컬럼 주석 모두 실제 DB를 따릅니다. 주석이 없는 테이블이면 `table_options(comment=None)`으로 둡니다. 모델에만 주석을 달면 `COMMENT ON` 차이가 영구히 남습니다.
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
| `compose/local/grafana/provisioning/datasources/` | datasource 정의. `news2`는 `db` 서비스를, `finance`는 외부 읽기 전용 DB를 바라봅니다. |
| `compose/local/grafana/provisioning/dashboards/` | dashboard provider 정의. |
| `compose/local/grafana/dashboards/` | 대시보드 JSON. 하위 디렉터리 구조가 Grafana 폴더 구조가 됩니다. |

datasource는 UI에서 수정할 수 없습니다(`editable: false`). 변경은 YAML을 고치고 컨테이너를 재시작합니다.

```powershell
docker compose -f compose/local/docker-compose.yaml restart grafana
docker compose -f compose/local/docker-compose.yaml logs -f grafana
```

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
