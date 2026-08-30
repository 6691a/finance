---
name: writing-migrations
description: Use when creating or reviewing an Alembic revision in migrations/versions/, adding or moving a table between DB aliases, running just makemigrations or just migrate, or writing table_options / __table_args__ on a SQLAlchemy model. Covers 마이그레이션 라우팅, 별칭별 upgrade 함수, alembic_version 포인터, 시드 리비전, 이미 존재하는 외부 테이블 편입. Also use when autogenerate emits a DROP for someone else's table or a repeated COMMENT ON diff.
---

# 마이그레이션 작성과 라우팅

## 이 저장소에서 리비전을 만드는 실제 방법

**`just makemigrations`는 이 워크트리에서 못 돈다.** `--autogenerate`가 마이그레이션이 켜진
**모든 별칭에 실제로 접속**하는데 `config.yaml`이 운영 DB를 가리키고 그 DB는 읽기 전용이다.
그래서 최근 리비전은 전부 **손으로 쓴다.**

| 상황 | 어떻게 |
| --- | --- |
| 워크트리·로컬(운영 DB를 가리킴) | **리비전 파일을 손으로 쓴다.** `revision`·`down_revision`을 직접 잇고 검증은 오프라인 SQL까지 |
| 로컬 DB에 붙일 수 있을 때 | `just migrate upgrade head` 먼저, 그다음 `just makemigrations "<메시지>"` |
| 적용 | `just migrate upgrade head`. **별칭을 인자로 주지 않는다** — 한 번이 켜진 별칭 전부를 순서대로 처리한다 |

**어느 쪽이든 생성된 파일을 반드시 읽어본다.**

autogenerate를 돌릴 수 있는 환경이라면 **먼저 `just migrate upgrade head`를 돌린다** —
autogenerate는 리비전 이력이 아니라 **실제 DB 상태**와 모델을 비교하므로, 밀린 리비전이
있으면 이미 만든 테이블을 또 만들려 한다.

## 라우팅은 모델이 선언한다

테이블이 어느 DB 별칭에 속하는지는 설정 파일이 아니라 **모델에서** 선언한다.
`core.database.table_options`를 `__table_args__`의 **마지막 요소**로 쓴다.

```python
__table_args__ = (
    UniqueConstraint(...),
    table_options(
        comment="시세·뉴스·시그널이 참조하는 추적 종목 마스터",
        database="default",
    ),
)
```

- 인자는 `comment`(**필수**), `database`(기본 `"default"`), `managed`(기본 `True`).
- **스키마는 지정하지 않는다** — 연결의 `search_path`(PostgreSQL 기본 `public`)를 따른다.
- 값은 `Table.info`에 들어가고 `migrations/env.py`가 읽는다.

`managed=False`는 **이 프로젝트가 스키마를 소유하지 않는** 테이블이다. ORM 매핑은 유지돼
읽고 쓸 수 있지만 어떤 별칭의 autogenerate에도 나오지 않는다(Django `Meta.managed = False`).
연결 자체를 읽기 전용으로 막는 것은 별칭 단위 `read_only: true`이고 **별개 층이다.**

Django의 `DATABASE_ROUTERS`와 목적은 같지만 위치가 다르다 — Django는 `allow_migrate`로 앱
단위 판단을 하고, 여기서는 테이블마다 직접 선언한다.

## 실행 구조

Alembic 공식 multidb 템플릿과 같다. `env.py`가 별칭을 순회하며 각각 `context.configure()` 후
`run_migrations(engine_name=alias)`를 부른다. 별칭 목록만 `alembic.ini`가 아니라
`config.yaml`에서 오고 `migrations/cli.py`가 Alembic의 `databases` 옵션으로 넘긴다.

**공식 템플릿과 갈리는 곳 둘이 중요하다.**

- **별칭마다 리비전 포인터 테이블이 다르다**(`migrations.routing.version_table`).
  `default`만 `alembic_version`이고 나머지는 `alembic_version_<alias>`다. 공식 템플릿은 DB가
  물리적으로 다르다고 보고 안 나누지만 **여기서는 인스턴스를 공유한다.**
- **MetaData는 하나만 쓴다.** 별칭별로 쪼개면 `indicator_observation` → `source_record.id`
  같은 **별칭 간 ForeignKey가 resolve되지 않는다.**

### 남의 테이블에 DROP을 안 내는 장치

- `env.py`는 마이그레이션이 켜진 **모든** 별칭의 `model_modules`를 import한다. 현재 별칭이
  소유하지 않는 테이블도 metadata에 있어야 autogenerate에서 제외할 수 있다. 그래서
  `config.yaml`의 모든 별칭은 `model_modules: [apps.models]`다.
- `migrations.routing.excluded_tables`가 다른 별칭 소유 테이블과 `managed=False` 테이블을
  모으고, `include_table`이 그것과 파티션·비관리 스키마를 autogenerate에서 뺀다.
  **이게 없으면 별칭들이 같은 PostgreSQL 인스턴스를 보기 때문에 서로의 테이블에 DROP을 낸다.**
- **훅은 `include_name`과 `include_object` 둘 다 건다.** `include_name`은 reflection된 이름만
  보므로 DROP만 막고, 모델 metadata까지 보는 `include_object`가 있어야 남의 테이블에
  CREATE를 안 낸다. 둘 다 `include_table` 하나에 위임해 판정이 어긋나지 않게 한다.
- 라우팅 판단은 `migrations/routing.py`의 **순수 함수**에 둔다. `env.py`는 Alembic 실행
  컨텍스트 밖에서 import할 수 없어 직접 테스트하지 못한다.

## 리비전 파일에서 확인할 것

- 파일은 `migrations/versions` 하나에 모이고 **파일 안에서 별칭별로 갈린다.**
  `upgrade(engine_name)`이 `upgrade_<alias>()`로 디스패치한다. 해당 함수가 없으면 아무 것도
  하지 않으므로 **별칭을 나중에 추가해도 과거 리비전을 고칠 필요가 없다.**
- **의도한 별칭 섹션에만 CREATE/DROP이 있는지 본다.** 다른 섹션에 생겼으면 `table_options`의
  `database=`가 틀린 것이다.
- **`down_revision`이 현재 head를 가리키는지 본다.** 브랜치 둘이 같은 부모 위에 리비전을
  얹으면 head가 둘이 되고 `upgrade head`가 거절된다. **머지 뒤에는 head가 하나인지 확인한다.**
- **테이블·컬럼 주석이 모델과 글자 그대로 같은지 대조한다.** 다르면 다음 autogenerate가
  매번 `COMMENT ON` 차이를 만든다.
- **autogenerate는 `CREATE SCHEMA`를 절대 만들지 않는다.** 새 스키마를 쓰는 리비전은
  `op.execute("CREATE SCHEMA IF NOT EXISTS <schema>")`를 해당 별칭 함수 맨 앞에 직접 넣는다.
- 형식의 원본은 `migrations/script.py.mako`다. ruff 규칙(`from collections.abc import Sequence`,
  `X | Y` 어노테이션)에 맞춰 둔 상태다.

## 시드 데이터

- **시드는 마이그레이션이 넣는다.**
- **리비전 파일에서 앱 코드를 import하지 않는다.** import하면 나중에 Enum이 바뀔 때 **과거
  리비전의 결과가 따라 바뀐다.** 값을 리터럴로 적는다.
- 마스터 시드가 있는 테이블은 코드 Enum과 시드가 어긋나지 않는지 테스트가 대조한다
  (`tests/migrations/test_indicator_series_catalog.py`). **계열을 늘릴 때 수집기 Enum과
  마스터 시드를 같은 커밋에서 함께 늘린다.**

## 테이블을 다른 별칭으로 옮기기

모델의 `database=` 값을 바꾸고 `makemigrations`를 한 번 돌린다. 한 리비전 파일 안에서
한쪽 섹션에 CREATE, 다른 쪽에 DROP이 생긴다. **데이터는 자동으로 옮겨가지 않는다.**

## 이미 존재하는 외부 테이블 편입

다른 시스템이 이미 만들어 데이터가 들어 있는 테이블은 Django `migrate --fake-initial`처럼
편입한다. 하나은행 환율 `exchange_rate`가 이 방식이었다(2026-08-19 수집 종료와 함께 삭제).

- **모델은 실제 DDL을 글자 그대로 미러링한다.** 컬럼 타입, nullable, 기본값, 제약·인덱스
  이름까지 같아야 한다.
- **프로젝트 기본 규칙을 적용하지 않는다** — BIGSERIAL 기본키, timezone-aware 시각,
  테이블·컬럼 주석. 실제 DB에 주석이 없으면 `table_options(comment=None)`이다. 모델에만
  주석을 달면 autogenerate가 매번 `COMMENT ON` 차이를 만든다.
- **`managed=True`를 유지한다.** `managed=False`는 이후 스키마 변경을 추적하지 못한다.
- **revision은 손으로 쓴다.** 해당 alias 함수 맨 앞에서
  `sa.inspect(op.get_bind()).has_table(...)`로 존재를 확인하고 있으면 반환한다.
  offline(`--sql`)은 연결이 없으므로 항상 전체 DDL을 찍는다.
- **`downgrade_<alias>()`는 `pass`다.** 소유자가 이 프로젝트가 아니므로 `DROP TABLE`을 내지
  않는다. (우리가 소유하는 새 테이블은 반대로 DROP을 낸다.)

## 모델 쪽에서 함께 해야 하는 것

- **새 모델을 `apps/models/__init__.py`의 `__all__`에 넣는다.** 패키지로 나뉜 도메인은 그
  패키지 `__init__.py`에도. 등록은 클래스를 import하는 부수효과라 **한 단계라도 빠지면
  `Base.metadata`에서 그 테이블이 사라지고 autogenerate가 `DROP TABLE`을 낸다.**
  `tests/models/test_market_models.py`가 그 누락을 잡는다.
- 테이블·컬럼 주석을 한국어로 채운다. `id`·`created_at`·`updated_at`은 `EntityBase`가 갖는다.
- **마이그레이션에도 모델과 동일한 주석을 넣는다.**

## 검증

```bash
uv run pytest tests -q
uv run ruff check apps airflow migrations tests
```

마이그레이션 테스트는 `alembic_command.upgrade(config, "head", sql=True)`로 **SQL만 뽑아
테이블 단위 사실만** 검증한다. 특정 리비전 ID에 고정하거나 전체 문자열을 세지 않는다 —
리비전을 다시 만들 때마다 깨진다. 헬퍼는 `tests/helpers.py`의 `head_sql`이다.

**운영 DB에 직접 적용하지 않는다.** 반영은 사용자가 한다.

## 흔한 실수

| 실수 | 무엇이 터지나 |
| --- | --- |
| `__all__` 등록을 빠뜨림 | autogenerate가 그 테이블에 `DROP TABLE`을 낸다 |
| `table_options`를 `__table_args__` 중간에 둠 | 마지막 요소여야 `Table.info`로 들어간다 |
| 리비전에서 앱 Enum을 import해 시드 | 나중에 Enum이 바뀌면 과거 리비전 결과가 따라 바뀐다 |
| 모델에만 주석을 달고 리비전에 안 넣음 | 매 autogenerate가 `COMMENT ON` 차이를 낸다 |
| 머지 뒤 head 확인 생략 | head가 둘이 되어 `upgrade head`가 거절된다 |
| 테스트를 리비전 ID에 고정 | 리비전을 다시 만들 때마다 깨진다 |

전체 사용법과 예시는 [README.md](../../../README.md)의 `테이블 라우팅` 절에 있다.
