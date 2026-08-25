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
| `apps/models/` | SQLAlchemy 모델. 파일은 도메인 단위로만 나눈다(스키마와 무관) |
| `apps/realtime/` | KIS 실시간 WebSocket 수집 서비스. `python -m apps.realtime.main`, `compose/prod/` 배포 |
| `migrations/` | Alembic. 리비전 파일은 `migrations/versions` 하나를 모든 별칭이 공유한다 |
| `migrations/routing.py` | 어떤 테이블이 어떤 DB 별칭에 속하는지 판단하는 순수 함수 |
| `../airflow/dags/` | Airflow DAG. 폴더로 나누지 않는다 — 스케줄·재시도·실패 판정만 갖는 얇은 파일이다 |
| `../airflow/modules/collectors/` | 수집기. 도메인 폴더(`market/`·`document/`·`indicator/`·`calendar/`·`analyst/`)로 나눈다. 전환 진행 상황은 [docs/collectors-class-migration.md](../docs/collectors-class-migration.md) |
| `tests/` | pytest |

`apps/models/`의 모듈은 도메인 단위로 나눈다(`raw.py`, `reference.py`, `content.py`).
한 도메인이 커지면 그 안에서 다시 패키지로 나눈다(2026-08-25) — `market/`이
`sessions.py`·`series.py`·`fundamentals.py`·`positioning.py`·`investor_flow.py`,
`analysis/`가 `thesis.py`·`events.py`·`technical.py`다.
테이블은 스키마를 지정하지 않고 연결의 `search_path`(PostgreSQL 기본 `public`)를 그대로 따르므로
파일 이름이 PostgreSQL 스키마와 대응하지 않는다.

**새 모델을 추가하면 `apps/models/__init__.py`의 `__all__`에 넣는다.** 패키지로 나뉜 도메인은
그 패키지의 `__init__.py`에도 넣어야 한다. 등록은 클래스를 import하는 부수효과라, 한 단계라도
빠지면 `Base.metadata`에서 그 테이블이 사라지고 autogenerate가 `DROP TABLE`을 낸다.
`tests/models/test_market_models.py`가 하위 모듈을 훑어 그 누락을 잡는다.

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
uv run ruff check apps airflow migrations tests
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
        comment="시세·뉴스·시그널이 참조하는 추적 종목 마스터",
        database="default",
    ),
)
```

인자는 `comment`(필수), `database`(기본 `"default"`), `managed`(기본 `True`)다.
스키마는 지정하지 않는다 — 연결의 `search_path`(PostgreSQL 기본 `public`)를 따른다.
값은 `Table.info`에 들어가고 `migrations/env.py`가 읽는다.

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
- 라우팅 판단은 `migrations/routing.py`의 순수 함수에 둔다. `env.py`는 Alembic 실행 컨텍스트
  밖에서 import할 수 없어 직접 테스트하지 못한다.
- 별칭마다 리비전 포인터 테이블이 다르다(`migrations.routing.version_table`).
  `default`만 `alembic_version`이고 나머지는 `alembic_version_<alias>`다.
  공식 템플릿은 DB가 물리적으로 다르다고 보고 나누지 않지만 여기서는 인스턴스를 공유한다.
- MetaData는 **하나만** 쓴다. 공식 템플릿처럼 별칭별 MetaData로 쪼개면
  `indicator_observation` → `source_record.id` 같은 별칭 간 ForeignKey가 resolve되지 않는다.
- 테이블을 다른 별칭으로 옮기려면 모델의 `database=` 값을 바꾸고 `makemigrations`를 한 번 돌린다.
  한 리비전 파일 안에서 한쪽 섹션에 CREATE, 다른 쪽에 DROP이 생긴다. 데이터는 자동으로 옮겨가지 않는다.

Django의 `DATABASE_ROUTERS`와 목적은 같지만 위치가 다르다. Django는 라우터 함수의
`allow_migrate(db, app_label, ...)`로 앱 단위 판단을 하고, 여기서는 테이블마다 직접 선언한다.

### 이미 존재하는 외부 테이블 편입

다른 시스템이 이미 만들어 데이터가 들어 있는 테이블은 Django `migrate --fake-initial`처럼 편입한다.
하나은행 환율 `exchange_rate`가 이 방식이었다(2026-08-19 수집 종료와 함께 삭제).

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

**`airflow/` 아래에는 DAG가 실제로 import·실행하는 코드만 둔다.** Airflow가 실행하지
않는 상주 서비스·API는 `apps/` 아래에 백엔드 규칙(ORM, `config.yaml`, async)으로 두고
(앞으로 올) FastAPI와 코드를 공유한다. 배포만 컨테이너로 가른다. `apps/realtime/`(KIS
실시간 WebSocket 수집, `python -m apps.realtime.main`, `compose/prod/` 배포)가 그 예다.
두 트리가 같은 도메인 상수(종목 코드, 세션 창)를 쓰면 **중복을 허용하되 테스트로
대조한다**(`tests/realtime/test_kis_realtime.py`의 `*_match_the_airflow_collector`).
한쪽 트리가 다른 쪽을 import하지 않는 것이 우선이다.

DAG가 쓰는 코드는 **위치는 Airflow를, 규칙은 백엔드를** 따른다.

- DAG가 쓰는 공유 코드는 `airflow/modules` 아래 한 벌만 둔다.
- 외부 입력은 Pydantic으로 검증하고, 시각은 timezone-aware UTC이며, 주석은 한국어로 쓴다.
- `dags/`에는 스케줄, 재시도, 태스크 매핑, Hook 사용, 실패 분류만 둔다.
  파싱·검증·저장 규칙은 `modules/`에 둔다.
- **모든 DAG는 화면용 메타데이터를 채운다.** `dag_display_name`(이모지 + 한글 이름 +
  제공처. 예: `📈 국내 지수·선물 1분봉 (KIS)`), 한 문장 `description`,
  `doc_md=__doc__`(모듈 docstring에 설계 배경)이 필수다. `Param`에도 `title`과
  `description`을 단다. 빈 문자열로 두지 않는다.
- 의존성은 Airflow 환경에 있는 것만 쓴다. 표준 라이브러리, Pydantic, PEP 249 연결,
  HTML 수집용 `scrapling[fetchers]`, 그리고 브리핑 차트용 matplotlib(+한글 폰트
  `fonts-nanum`)이다. SQLAlchemy 모델과 `core.config`는 import하지 않는다.
  여기에 더 넣으려면 운영 Airflow 이미지에 먼저 들어가야 한다. matplotlib은 없어도
  브리핑이 죽지 않도록 함수 안에서 import한다(`modules/briefing/chart.py`).
- 테이블 정의의 원본은 백엔드의 `apps/models`다. 수집기는 문자열 SQL을 쓰므로
  `tests/collectors/`의 `test_fred.py`, `test_ecos.py`, `test_mof.py`, `test_boe.py`,
  `test_ecb.py`가 INSERT 컬럼과 `ON CONFLICT` 키를 모델 metadata와 대조한다.

## 클래스와 함수를 가르는 기준

**상태를 쥔 동작은 클래스로 묶고, 상태 없는 변환은 함수로 둔다.** 이 저장소 전체에 같은
규칙이 적용된다 — LLM 흐름만의 규칙이 아니다.

- **클래스로 묶는다**: 자격 증명·토큰·DB 연결·기준 시각·출처 행처럼 **여러 호출에 걸쳐
  안 변하는 값**을 들고 도는 것. 그 값이 인자로 함수마다 다시 들어가고 있으면 그게 신호다.
  기준 구현은 `modules/collectors/analyst/kis_opinion.py`의 `KisAnalystOpinionCollector`,
  `modules/collectors/document/naver_research.py`의 `NaverResearchCollector`,
  `modules/assessment.py`의 `DocumentAssessor`, `modules/thesis_toolbox.py`의 `ThesisToolbox`·
  `ThesisBuilder`·`FollowupNarrator`다. 연결을 쥐는 흐름 코드는
  `modules/thesis_nxt_review.py`의 `NxtAfterHoursReview`, `modules/thesis_common.py`의
  `ThesisRun`, `modules/thesis_store.py`의 `ThesisStore`가 기준이다.
- **생성자는 그 실행 동안 안 변하는 것만 받는다.** 종목·구간처럼 호출마다 바뀌는 것은
  메서드 인자다.
- **함수로 둔다**: 파싱·정규화·계산처럼 감쌀 상태가 없는 것, 그리고 그 클래스의 관심사가
  아닌 조회(`watched_stocks`는 KIS와 무관하게 마스터만 본다). 클래스 안에 두는 편이 읽기
  좋으면 `@staticmethod`로 둔다.
- **데이터 모양은 언제나 Pydantic 모델이다.** 응답 행·설정·결과를 `dataclass`나 맨 dict로
  두지 않는다. 수집기 클래스 안에 중첩하지 않는다 — 테스트와 다른 모듈이 import한다.
- **감쌀 상태가 없는 것을 클래스로 만들지 않는다.** 메서드가 전부 `@staticmethod`면 그건
  모듈이다.

자격 증명을 쥐는 수집기 10모듈(2026-08-23)과 연결·기준 시각을 쥐는 흐름 코드
9곳(2026-08-25)은 클래스로 옮겼고, 수집기는 도메인 폴더로 내려갔다(2026-08-25).
**`connection`을 첫 인자로 받는 모듈 함수는 이제 `modules/dedup.py`·`market_session.py`·
`technical_signals.py`처럼 진입점이 하나뿐인 곳에만 남아 있다** — 새로 만들 때 그 형태를
따라가지 않는다. 남은 단계는 없다.
[docs/collectors-class-migration.md](../docs/collectors-class-migration.md)가 폴더
구조(도메인별 `market/`·`document/`·`indicator/`·`calendar/`·`analyst/`)와 어디서
갈랐는지, 그리고 **함수로 두는 것이 맞다고 판정한 모듈과 그 이유**를 갖는다.
**새 수집기는 처음부터 그 형태로 쓴다.**

## 수집기 작성

`airflow/modules/collectors/analyst/kis_opinion.py`(KIS 토큰을 쥔 클래스)와
`airflow/modules/collectors/indicator/fred.py`(API 키 하나를 쥔 클래스, 검증 규칙의 기준)를 함께 본다.

- **새 수집기는 클래스로, 도메인 폴더에 둔다.** 위 "클래스와 함수를 가르는 기준"과
  [docs/collectors-class-migration.md](../docs/collectors-class-migration.md)를 따른다.
  하위 패키지 `__init__.py`는 재수출하지 않는다 — 한 수집기의 의존성이 없는 환경에서
  관계없는 DAG이 import 오류로 죽는다.
- `fetch`(외부 호출)와 `store`(DB 쓰기)를 나눈다. DAG이 `fetch` 실패로 재시도를 판단하고
  성공한 것만 트랜잭션 안에서 저장한다.
- 요청 값, 외부 응답 본문, 정규화 결과, 수집 결과를 모두 Pydantic 모델로 선언한다.
  `dataclass`를 쓰지 않는다. 외부 JSON은 `model_validate_json`으로 검증한다.
- 모델은 `ConfigDict(frozen=True)`다. 재시도 경로에서 값이 바뀌면 원본과 저장값이 어긋난다.
- 시각 필드는 `AwareDatetime`으로 받고 validator에서 UTC로 정규화한다.
- 허용 값이 정해진 필드는 validator로 막는다(예: 시계열 ID는 `TREASURY_SERIES` 안의 값만).
- 제공처가 잘못된 식별자에도 정상 응답으로 답하면 식별자를 Enum으로 좁혀 요청 전에 막는다.
  ECOS는 없는 항목코드에도 데이터 없음(`INFO-200`)으로 답해서 오타가 조용한 0건이 된다.
  `airflow/modules/collectors/indicator/ecos.py`의 `MarketRateSeries`가 그 예다.
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
- HTML 수집은 scrapling을 쓴다. 요청은 `Fetcher`(curl_cffi), 파싱은 `Selector`다.
  피드 수집(`documents.py`)이 `Fetcher`를 쓰는 예다. `impersonate`로 실제 브라우저 지문을
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
- 목록 수집이 상세 페이지를 한 번 더 받을 때는 **이미 있는 `(source_slug, external_id)`를 먼저
  빼고 새 항목만 받는다**(`document_listings.ListingSource.enrich`, 네이버 리서치가 그 예다).
  기존 항목을 목록 정보로 다시 upsert하면 `content_hash`가 달라져 상세 요약이 지워지고
  재평가가 돈다. 상세 HTTP는 트랜잭션 바깥에서 부른다.
- **제공처가 우리 관심 밖까지 밀어 주면 수집 단계에서 거른다.** 종목이 붙은 문서는
  `instrument.is_watched` 안의 것만 받는다(`documents.watched_tickers`). 거르기는 상세 요청
  **앞**이고, 거르기에 쓰는 목록 값(`FeedItem.stock_code`)은 저장하지 않는다 — 태그의 원본은
  LLM 평가가 만드는 `document_instrument`다. 종목이 없는 문서(시황·경제·채권)는 시장 전체
  이야기라 받고, 카테고리를 통째로 끄는 손잡이는 `document_source.enabled`다.
- robots.txt가 일반 봇을 막는 출처를 사용자 결정으로 수집할 때는 `document_source.terms_url`·
  `terms_checked_at`과 시드 리비전 주석에 그 결정을 남긴다. 이용조건이 문제가 되면 코드가
  아니라 `enabled`를 내리는 것으로 끝나야 한다. 네이버 증권 리서치(2026-08-21)가 그 예다.

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
- 제공처가 날짜의 기준 시간대를 정하는 값(ECOS 고시 기준일은 KST,
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

## Slack 테스트 발송

- **테스트 메시지를 운영 채널(`SLACK_CHANNEL_MARKET/DOCUMENT/OPS`)로 보내지 않는다.**
  운영 채널 구독자 전체에게 노출되고 되돌릴 수 없다.
- 테스트 채널은 `config.yaml`의 `slack_channel_test`다. 값이 없으면 발송 전에 사용자에게 물어본다.
- 테스트 메시지에는 테스트임을 밝히는 머리표(예: `🧪 테스트 발송`)를 붙인다.

## 차트와 표 표기

**값을 보여 줄 때 어느 시장의 언제 값인지를 함께 적는다.** 숫자만 있으면 읽는 사람이 KRX
정규장 확정값과 NXT 애프터마켓 값을 가릴 수 없고, 오늘 값과 사흘 전 값도 같아 보인다.
브리핑은 프론트엔드가 없는 출력이라 이 표기가 유일한 단서다.

- **차트 제목은 `대상 값 · 시장 · 날짜`다.** 분봉은 날짜와 요일까지 적고 시각은 x축이 말한다.
  일봉은 마지막 확정 거래일을 적는다. Slack에 올리는 파일 제목과 image 블록 라벨도 같은 값을
  담는다 — 이미지가 접혀 있을 때 보이는 것이 그 라벨이다.
- **시장 표기는 거래소가 있으면 거래소다**(`KRX`·`NXT`·둘이 섞이면 `KRX·NXT`). 거래소 개념이
  없는 지수·환율은 제공처를 적는다(`briefing/market_data.py`의 `PROVIDER_VENUES`). **비워 두지
  않는다** — 빈 칸은 "거래소가 없다"가 아니라 "안 밝혔다"로 읽힌다.
- **표의 마지막 열은 언제나 `기준`이다**(날짜, 장중 값이면 날짜와 시각). 시장이 표 전체에
  하나면 제목에 적고(`공매도·대차(주·KRX)`, `기술적 관측(확정 일봉·KRX)`), 행마다 다르면
  열로 둔다(국내 종목 시세의 거래소 열).
- **직전 값과 비교해 보여 줄 때는 그 직전 값의 날짜도 적는다**(`979,289 (08/11)`). 수집이 매일
  도는 것이 아니라 직전 행이 전일이 아닐 수 있고, 날짜가 없으면 사흘 전 값과의 차이를 전일
  대비로 읽는다.
- 값을 만든 기준이 우리가 아니라 제공처에 있으면 그 기준을 코드 주석에 남긴다. 국내 종목·지수
  일봉은 KIS가 **KRX 정규장** 기준으로 준다 — NXT와 시간외는 들어 있지 않아서, 증권사 앱의
  통합 차트와 고가·저가가 다르게 보일 수 있다(2026-08-25 실측).

## 데이터베이스 테이블 주석

- 모든 SQLAlchemy 테이블은 `__table_args__`의 `comment`에 테이블 목적을 한국어로 작성한다.
- 모든 컬럼은 `mapped_column(comment="...")`에 값의 의미를 작성한다.
  시간대, 단위, 허용 상태가 있으면 함께 명시한다.
- `id`, `created_at`, `updated_at` 같은 공통 필드 주석은 `EntityBase`에서 한 번만 정의한다.
- Alembic 마이그레이션에도 모델과 동일한 테이블·컬럼 주석을 넣어 실제 DB 스키마에 반영한다.
- 모델과 마이그레이션의 주석은 함께 변경하고 테스트로 생성 여부를 확인한다.

## 타입 모델링

### 함수가 돌려주는 데이터 모양은 Pydantic 모델이다

**`dict[str, Any]`·`list[dict]`·`Mapping[str, Any]`를 반환 타입으로 쓰지 않는다.** 모듈
경계를 넘는 값은 모델로 선언한다. 이유는 셋이다.

- **키 오타가 런타임까지 산다.** 맨 dict는 `state["technial"]`을 KeyError로 알려 주는 자리가
  실행 시점뿐이다. 프롬프트나 JSONB로 나가는 값이면 아무도 못 잡는다.
- **부르는 쪽이 무슨 키를 기대해도 되는지 코드에 안 남는다.** 나중에 그 값을 읽는 SQL이나
  화면이 생기면 문서와 실제가 갈린다.
- **pyrefly가 대신 볼 수 있는 것을 사람이 본다.** 모델이면 필드 이름 오타가 정적 검사에서 죽는다.

기준 구현은 `airflow/modules/thesis_state.py`(`ObservedState`·`TechnicalState`·`PastThesis`)와
`airflow/modules/technical.py`(`DailyBar`·`TechnicalSnapshot`·`SignalEvent`)다.

- **모델은 `ConfigDict(frozen=True)`다.** 재시도 경로에서 값이 바뀌면 원본과 저장값이 어긋난다.
- **JSON으로 바꾸는 것은 경계에서 한 번뿐이다.** `model_dump(mode="json")`을 프롬프트 조립과
  DB 저장 자리에서만 부른다. 중간 층은 모델을 그대로 들고 간다. `json.dumps(..., default=str)`로
  때우지 않는다 — `date`가 조용히 문자열이 되는 자리가 늘어난다.
- **`dict[str, 모델]`은 괜찮다.** 키가 심볼·종목코드처럼 열린 값이면 매핑이 맞는 모양이고,
  값이 모델이면 검증은 그대로 된다. 금지하는 것은 **값이 `Any`인** 매핑이다.
- **키와 값이 층을 섞으면 한 단 내린다.** `{"as_of_date": ..., "KOSPI": {...}}`는 모델로
  표현할 수 없다. `{"as_of_date": ..., "subjects": {"KOSPI": {...}}}`로 만든다.
- **모델을 두는 곳은 그 값을 만드는 모듈이다.** 단 그 모듈이 LangChain·Airflow를 import하는데
  다른 모듈도 같은 모델을 봐야 하면, 무거운 의존성이 없는 모듈로 따로 뺀다
  (`thesis_state.py`가 그 예다 — `thesis.py`는 LangChain, `thesis_common.py`는 Airflow를
  import해서 서로를 모듈 수준에서 import할 수 없다). 소비자가 하나뿐이어도 그 모듈이 이미
  크면 따로 뺀다(`thesis_tools.py`의 툴 응답 모델 스무 개가 그 예다).
- **테스트도 모델로 넘긴다.** 픽스처가 맨 dict면 프롬프트에 실릴 키가 테스트에서만 존재할 수 있다.

**wire 조립 경계는 예외다.** Slack 블록, LangGraph 노드 반환, JSON Schema, 검증 전
외부 응답 파싱, 그리고 모델을 JSON으로 펴는 자리(`thesis._tool_row`·`_body`)는 dict로 둔다.
그 dict는 제공처 규격이거나 모델을 JSON으로 바꾸는 경계 그 자체라 모델로 감싸면 같은 검증이
두 번이 된다. 그 밖의 도메인 값은 **처음부터 모델로 쓴다.**

### 그 밖의 타입 규칙

- **PEP 249 연결·커서 타입은 `airflow/modules/db.py`의 `Cursor`·`Connection`을 쓴다.**
  모듈마다 `class Cursor(Protocol)`을 다시 쓰지 않는다 — 전에 스무 개가 조금씩 다른 채로
  복사돼 있었다(2026-08-25 통합). 스스로 커밋하는 코드만 `TransactionalConnection`이다.

- 값의 종류가 정해진 상태·분류 필드는 일반 `str` 대신 Python `StrEnum`과 SQLAlchemy `Enum`을 쓴다.
- SQLAlchemy `Enum`은 `native_enum=False, length=20, values_callable=...` 형태로 선언한다.
  PostgreSQL native enum은 값 추가·삭제 마이그레이션 비용이 커서 쓰지 않는다.
- Enum 컬럼에는 허용 값을 제한하는 DB `CHECK` 제약을 함께 둔다.
- API 요청·응답, 설정, 외부 입력 검증에는 Pydantic 모델과 `Field`, validator를 쓴다.
- 제공처 이름, URL, 종목 코드, 외부 식별자처럼 값이 열려 있는 필드는 `str` 또는 `Text`로 유지한다.
- 단순 문자열을 의미 없이 Pydantic 모델이나 Enum으로 감싸지 않고,
  유효성 규칙이나 제한된 값 집합이 있을 때만 사용한다.

## 오류 처리

**해결하지 못하는 문제는 터뜨린다.** 삼키고 계속 가는 코드는 문제가 없는 것처럼 보이게
만들 뿐이고, 그 사이 잘못된 값이 쌓이거나 아무 것도 쌓이지 않는다. 실패를 나중에 알수록
되짚을 구간이 길어진다. 지금 멈춰서 눈에 띄는 편이 항상 낫다.

- **자체 예외 타입을 만드는 것은 좋다.** 원인을 좁혀 부르는 쪽이 판단할 수 있게 하는 것이
  목적이다. 이 저장소의 `FredHTTPError`, `EcosResultError`, `LlmError`가 그 예다.
  단 원래 예외를 `raise ... from error`로 잇는다. 원인을 끊으면 추적이 거기서 멈춘다.
- **예외를 문자열로 뭉개지 않는다.** `str(error)`나 `type(error).__name__`으로 바꿔 담으면
  위에서 종류로 갈라낼 수 없다. 판단을 위에 맡길 거면 종류를 그대로 올리거나, 결과 객체에
  담아야 한다면 예외 객체 자체를 들고 간다.
- **`except Exception`으로 넓게 잡지 않는다.** 잡아야 할 이유가 있으면 잡되 **반드시 다시
  올린다.** 로그만 남기고 넘어가지 않는다. 넓게 잡아야만 하는 자리에는 왜 그런지 주석을
  남긴다.
- **로그는 예외를 대체하지 않는다.** `logger.warning`만 남기고 정상 반환하면 Airflow는 그
  태스크를 성공으로 표시한다. 아무도 보지 않는 경고가 되고, 다음 실행도 같은 자리에서
  같은 경고를 남긴다.
- **부분 실패를 결과로 바꾸는 것은 그것이 정상 흐름일 때만 한다.** 문서 하나가 실패해도
  나머지를 저장하는 것처럼 설계가 그렇게 정해진 경우다. 그때도 실패한 건수와 원인을
  올리고, 전부 실패하면 태스크를 실패시킨다.
- **조용한 성공을 만들지 않는다.** 잘린 응답, 0건, 빈 본문이 오류를 가릴 수 있으면 실패로
  만든다. 수집기 규칙의 "제공처가 알려 준 전체 건수와 받은 행 수를 대조한다"와 같은 이유다.
- **재시도 여부 판단을 위에 맡기려면 판단할 것을 위로 올려야 한다.** 아래에서 분류해 놓고
  위로 문자열만 보내면 그 분류는 존재하지 않는 것과 같다.

### DAG의 실패 판정

이미 스무 개 DAG가 아래 세 형태 중 하나를 따른다. 새 DAG도 이 중 하나를 고른다.

- **항목별 실패 수집** — 여러 항목을 한 태스크에서 돌 때. 항목 하나가 실패하면 원인을
  `failures`에 모으고 계속한다. **마지막에 반드시 판정한다.** `dart_disclosure_intraday`,
  `document_ingestion_hourly`는 전부 실패했을 때, `kis_*`와 `yahoo_*`는 하나라도 실패했을 때
  태스크를 죽인다. 어느 쪽을 고르든 실패를 세고 이름을 메시지에 싣는다.
- **태스크 매핑** — 항목마다 태스크를 매핑한다(`.expand`). 실패가 곧 그 태스크의 실패라
  따로 판정할 것이 없고 재시도도 실패한 항목만 다시 돈다. `fred_*`, `ecos_*`가 그렇다.
- **단일 요청** — 응답 하나가 결과 전부다. 수집기 예외를 그대로 올린다. `bbk`, `boe`,
  `ecb_*`, `mof`, `market_calendar`가 그렇다.

### 슬롯·모드로 갈리는 DAG는 나눈다

**실행 시각으로 "지금 어느 모드인가"를 추론하지 않는다.** 한 DAG가 여러 시각에 돌면서
`logical_date`의 시각으로 모드를 가르면, 모드가 실행자의 의도가 아니라 시계에서 나온다.
`logical_date`가 없는 수동 실행은 벽시계로 떨어져 **UI의 Trigger 버튼이 조용히 다른 모드를
돌린다.** 2026-08-21에 `market_thesis_analysis`를 `market_thesis_forecast`(장전)와
`market_thesis_review`(장후)로 나눈 이유가 이것이다.

나누면 따라오는 것:

- 한쪽 모드에서만 도는 태스크가 다른 쪽 실행에서 **빈 성공으로 보이는 일**이 없어진다.
  전에는 장전 실행의 `grade_followups`·`narrate_followups`가 즉시 반환하면서 성공 표시였다.
- 모드마다 재시도·타임아웃을 따로 줄 수 있다. 앞단이 다르면 기다리는 성격도 다르다.
- 따로 pause 할 수 있고 `max_active_runs`가 서로를 막지 않는다.

**나누는 기준은 "앞단 데이터와 실패 성격이 다른가"다.** 같은 데이터를 같은 이유로
기다리는데 시각만 여럿이면 `MultipleCronTriggerTimetable` 하나로 둔다
(`slack_kr_market_briefing`이 그 예다).

### 모드로 갈리는 함수도 나눈다

DAG를 나눈 뒤 공유 모듈에 `if mode == "..."`가 남으면 절반만 나눈 것이다. 읽는 사람이
함수마다 "지금 어느 쪽 이야기인가"를 따라가야 하고, 한쪽을 고치다 다른 쪽을 깨뜨린다.

- **모드를 모르는 것만 공유 모듈에 둔다.** 연결, 파라미터 검증, 저장, 발송처럼 양쪽이
  글자 그대로 같은 것이다. 모드는 **값으로 흘러갈 수는 있다**(`run_slot`을 저장 함수에
  넘기는 것) — 금지하는 것은 그 값으로 **분기**하는 것이다.
- **모드마다 다른 것은 모드별 모듈이 갖는다.** 기준 시각, readiness guard, 조회 창의 시작,
  어느 세션을 볼지 같은 것이다. 기준 구현은 `airflow/modules/thesis_common.py`와
  `thesis_forecast.py`·`thesis_review.py` 셋이다.
- 공유 함수가 모드별 값을 **인자로 받게** 만들면 분기가 사라진다. `observed_state`가
  슬롯 대신 세션 날짜를 받는 것이 그 형태다 — 어느 세션을 볼지는 부르는 쪽이 정한다.

어느 형태든 **되돌릴 수 없는 오류는 즉시 `AirflowFailException`으로 바꾼다.** 설정·인증·주소
문제(HTTP 4xx)는 재시도해도 같은 답이다. 재시도할 값어치가 있는 것(`ConnectionError`)은
그대로 올려 Airflow가 재시도하게 둔다. **그 판단은 DAG가 한다.** 수집기는 종류만 정확히
올린다.

층이 하나 더 끼면 이 규칙이 새기 쉽다. `document_assessment_hourly`는 LangGraph 배치 노드가
사이에 있어서, 그 노드가 예외를 문자열로 바꾸는 바람에 DAG가 판단할 것을 잃었다. **중간 층은
예외를 통과시킨다.**

## 관측과 Sentry

Sentry 프로젝트는 둘이다. Airflow는 NAS `.env`의 `AIRFLOW__SENTRY__*`로, realtime은
`config.yaml`의 `sentry_*`로 붙는다. realtime의 `sentry_sdk.init`(`apps/realtime/main.py`)이
켜 둔 것: 에러 이벤트, 표준 logging 자동 전달(ERROR 이상 이벤트, WARNING breadcrumb,
INFO 이상 Sentry Logs), 트레이싱(`sentry_traces_sample_rate`), 트랜잭션 연동 프로파일링.
DSN이 비면 전체 비활성이다. 새 상주 서비스(FastAPI 등)도 같은 `settings.sentry_*`로 init한다.

- 메트릭(`sentry_sdk.metrics`의 count/gauge/distribution)은 아직 안 쓴다.
- **측정할 가치가 있는 지점을 발견하면 사용자에게 제안한다.** 처리 건수, 큐·버퍼 깊이,
  외부 API 지연, 저장 실패율처럼 나중에 대시보드나 알림이 필요해질 값이 코드에 생기면
  Sentry metrics 후보로 지점과 이유를 알린다. 사용자가 스스로 인지하지 못할 수 있다는
  전제로 먼저 말하되, 계측 코드를 임의로 추가하지는 않는다.

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
- **흐름은 클래스로 묶는다**(위 "클래스와 함수를 가르는 기준"의 특수한 경우다 — 여기서 상태는
  컴파일된 그래프다). `DocumentAssessor`·`AssessmentBatch`처럼 그래프를 소유한 클래스가
  갖고, 그래프는 생성자에서 한 번 `compile()`한다. 프롬프트 조립과 파싱처럼 상태가 필요 없는
  것은 같은 클래스의 `@staticmethod`로 둔다. 반대로 모델 정의와 오류 분류는 `modules/llm.py`의
  함수다. 감쌀 상태가 없는 것을 클래스로 만들지 않는다.
- **API 키를 그래프 상태에 넣지 않는다.** 상태와 config는 트레이스 입력으로 나간다. `SecretStr`을
  담은 설정 객체는 생성자로만 넘긴다.
- **재시도는 Airflow가 한다.** 모델 클라이언트는 `max_retries=0`으로 만든다. SDK가 먼저 재시도하면
  태스크 타임아웃 안에서 몇 번을 불렀는지 로그와 트레이스가 어긋난다.
- 제공처 예외는 한 곳에서 우리 종류로 바꾼다. 재시도할 값어치가 있는 것(`ConnectionError`)과
  없는 것(`LlmError`)을 가르는 판단은 DAG가 한다.
- **툴은 `StructuredTool`로 정의하고 `ToolNode`가 돌린다.** 인자는 Pydantic 모델로 선언하고
  JSON Schema는 `args_schema`에서 뽑는다. `{"type": "function", "function": {...}}` dict를 손으로
  쓰지 않는다 — 그건 제공처 wire format이라 이름·타입이 실제 함수와 어긋나도 아무도 못 잡는다.
  툴이 연결·기준 시각·레지스트리 같은 상태를 봐야 하면 모듈 수준 `@tool` 대신 **바인드된
  메서드**를 `StructuredTool.from_function(func=self._tool_x, args_schema=XArgs)`로 감싼다.
  기준 구현은 `airflow/modules/thesis_toolbox.py`의 `ThesisToolbox._build_tools`다.
- **툴 실행 루프를 손으로 짜지 않는다.** `langgraph.prebuilt.ToolNode`가 tool_call을 돌리고
  `tool_call_id`마다 `ToolMessage` 하나를 보장한다. 직접 짜면 그 보장이 우리 책임이 되고,
  빠지거나 둘이면 제공처가 다음 요청을 거절한다.
- **`handle_tool_errors`에는 반드시 예외 타입을 준다.** 기본값(`True`)은 **모든** 예외를
  `ToolMessage`로 바꿔서 DB 연결 끊김을 "결과 없음"으로 위장한다. 모델은 그것을 "그 창에
  데이터가 없었다"로 읽고 태스크는 성공으로 끝난다. 모델이 고쳐 부를 수 있는 것(상한 초과,
  모르는 인자)만 타입으로 지정하고 나머지는 올려서 태스크를 죽인다.
  `ToolNode(tools, handle_tool_errors=(ToolLimitExceeded,))`가 그 형태다.
- **상태의 `messages`에는 `add_messages` 리듀서를 단다.** 노드는 새로 생긴 메시지만 돌려주고
  병합은 리듀서가 한다. `ToolNode`가 그 형태로 반환하므로 맞춰야 할 쪽은 우리다.
- **툴 상한은 코드 상수로 강제하고 그 값을 `Field(description=...)`에 f-string으로 싣는다.**
  상수를 고치면 프롬프트가 따라간다. 두 곳에 숫자를 적으면 반드시 어긋난다.
- **모델에게 주는 시각은 표시 시간대로 준다.** 저장·조회는 UTC지만 프롬프트에 UTC ISO를
  그대로 실으면 모델이 "오늘"을 하루 어긋나게 읽는다(장전 기준 시각 KST 08:35는 UTC로 전날
  23:35다). `thesis.kst_label`과 `briefing/documents.pick_input`의 `as_of_kst`가 그 자리다.
  섞어서 줄 수밖에 없으면 **어느 칸이 어느 시간대인지 프롬프트가 직접 알린다.**
- **툴을 늘릴 때 조회 SQL은 새 파일로 만든다.** 브리핑 등 기존 쿼리를 재사용하지 않는다.
  브리핑은 지금까지를 보고 추론 툴은 기준 시각까지만 본다. 기존 쿼리에 상한을 얹으면 그쪽이
  쓰지 않는 파라미터를 매번 넘겨야 하고, 한쪽을 고칠 때 다른 쪽이 조용히 따라 바뀐다.
- **새 툴 SQL은 운영 DB에 읽기 전용으로 한 번 돌려 보고 넣는다.** 테스트는 가짜 연결을 쓰므로
  컬럼 이름과 조인 조건이 틀려도 통과한다. 2026-08-21에 이 확인이 결함 둘을 잡았다 — 공매도
  당일 행이 0으로 이미 들어와 있던 것과, 국내 지수 일봉이 아예 없던 것이다.
- 체크포인터·persistence는 붙이지 않는다. 재실행 단위는 Airflow 태스크다.
- 추적은 `LANGSMITH_*` 환경변수로 켠다. 코드에 추적 호출을 심지 않는다.
  **켜면 프롬프트와 원문이 외부로 나간다는 사실을 문서에 남긴다.**

## 테이블 규칙

### `source_record`

API, 크롤링, 웹소켓 수집 결과의 출처와 상태를 가볍게 보존한다. API는 응답 1회, 크롤링은 문서 버전 1개,
웹소켓은 메시지가 아닌 배치 또는 연결 세션 1개를 레코드 단위로 사용한다.

- 수집 방식, 제공처, 원천 식별자, UTC 수집 구간, 상태와 생성 레코드 수는 항상 저장한다.
- 작은 JSON 원본만 `payload`에 선택적으로 저장한다.
- 대용량 원본은 외부 저장소에 두고 `payload_uri`만 저장한다.
- API 키, 인증 헤더와 개인정보는 `payload`나 `metadata`에 저장하지 않는다.
- 정규화 테이블은 `source_record_id` 외래키와 `ON DELETE RESTRICT`로 출처를 연결한다.
- 웹소켓 메시지별로 `SourceRecord`를 생성하지 않는다.

### `indicator_observation`

여러 제공처에서 추출한 지표 관측값을 날짜와 단위와 함께 조회 가능한 형태로 누적 저장한다.
`(provider, series_id, observation_date)`를 고유키로 사용하고 `source_record_id`로 근거 수집
레코드와 연결한다. 현재 `fred_treasury_daily`(미국 국채), `fred_macro_daily`(미국 물가·소매판매),
`ecos_market_rate_daily`(국내 시장금리), `mof_jgb_daily`(일본 국채), `boe_gilt_daily`(영국 국채),
`bbk_bund_daily`(독일 국채), `ecb_yield_curve_daily`(유로 지역 국채),
`ecb_convergence_monthly`(유로 회원국 10년물 월평균)가 채운다.

- `provider`는 그 값을 준 제공처(`fred`, `ecos`, `mof`, `boe`, `bbk`, `ecb`)이며 같은 수집의
  `source_record.source`와 같다.
- `series_id`는 **제공처 안에서만 고유하다.** 그래서 자연키에 `provider`가 함께 들어간다.
- **`series_id`는 사람이 읽을 수 있어야 한다.** FRED의 `DGS10`처럼 제공처 ID가 이미 읽히면
  그대로 쓰고, ECOS 항목코드(`010210000`)처럼 숫자뿐이면 `KTB10Y` 같은 ID를 만들어 저장한다.
  DB나 대시보드에서 값만 보고 무슨 시계열인지 알 수 없으면 안 된다. 제공처의 원본 좌표는
  수집기 Enum이 들고 있다가 요청에 쓰고 `source_record.metadata`에 남긴다.
- **조회하는 쪽도 `provider`를 함께 건다.** `series_id` 하나로 거는 쿼리는 제공처가 늘어나면
  조용히 틀린다. Grafana 대시보드의 패널 쿼리도 마찬가지다.
- 국가·만기 같은 시계열의 성격은 여기 두지 않고 `indicator_series`에 둔다.
- `unit`은 제공처 표기가 아니라 정규화한 표기다. 연이율 퍼센트는 제공처가 `Percent`든 `연%`든
  `Percent`로 저장한다. 그래야 두 나라 금리를 한 쿼리로 비교할 수 있다. **단위는 계열마다
  다르다.** 금리만 있던 때의 모듈 상수 하나로는 물가지수(`Index 1982-1984=100`)와
  소매판매(`Millions of Dollars`)에 거짓이 실린다. 수집기 Enum이 계열별로 들고 있는다.
- 관측값이 0건이어도 `source_record`는 남긴다. 조회했지만 값이 없는 구간과 아직 조회하지 않은
  구간이 구분돼야 한다.

### `indicator_series`

`indicator_observation`에 쌓이는 시계열이 어느 나라 무슨 값인지 설명하는 마스터다.
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
- **금리 전용 테이블이 아니다.** `kind`가 국채(`government_bond`), 단기 자금시장 금리
  (`money_market`), 물가지수(`price_index`), 실물활동(`activity`) 넷을 가른다. 국채 곡선
  패널이 CD 91일이나 CPI를 집어삼키지 않게 하는 장치다. **조회하는 쪽은 `kind`를 반드시
  건다.** 단위가 다른 값이 한 축에 섞이면 화면이 조용히 거짓말을 한다.
- `maturity_months`는 만기 비교와 정렬의 기준이다. 91일물은 3으로 둔다. **만기 개념이 없는
  지표(물가지수, 소매판매)는 `NULL`이다.** 0으로 채우면 만기별 비교 쿼리가 그 시계열을
  "0개월물"로 그린다.
- **월간 계열은 저장 식별자를 `M`으로 끝낸다**(`CPI_M`, `FR10YM`). 한 테이블에 일별과 월간이
  섞여 있어 표시가 없으면 조회하는 쪽이 주기를 구분할 수 없다.
- **관측값에서 이 테이블로 외래키를 걸지 않는다.** 걸면 마스터 행이 없는 시계열을 수집기가
  저장하지 못해, 수집기 Enum에만 추가하고 마스터 시드를 빠뜨린 순간 DAG가 죽는다. 대신
  `tests/migrations/test_indicator_series_catalog.py`가 수집기 Enum과 시드를 대조한다.
  **시계열을 늘릴 때는 수집기 Enum과 마스터 시드를 같은 커밋에서 함께 늘린다.**
- 시드는 마이그레이션이 넣는다. 리비전 파일에서 앱 코드를 import하지 않는다. import하면
  나중에 Enum이 바뀔 때 과거 리비전의 결과가 따라 바뀐다.
- `country_name`은 이 테이블이 들고 있다. 국가에 붙는 속성이 더 늘면 별도 country 마스터로
  분리한다.

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

### `instrument`

시세·뉴스·시그널이 참조하는 추적 종목 마스터다. 관측값이 아니라 기준 정보이므로
`source_record_id`로 수집 계보를 연결하지 않는다.

- `(ticker, market)`을 자연키로 사용한다. `id`는 다른 테이블이 참조할 대리키다.
- `source_symbol`은 수집 소스 심볼이 티커와 다를 때만 채운다. 같으면 `NULL`로 둔다.
- `is_watched`는 수집·분석 대상 여부만 나타낸다. 상장폐지·거래정지 같은 종목 생애주기 상태가
  필요해지면 별도 `status` enum 컬럼으로 분리한다.
- 한 종목을 여러 소스에서 수집하게 되면 `source_symbol` 한 칸으로 못 버틴다.
  그때는 `instrument_source(instrument_id, source, symbol)` 자식 테이블로 옮긴다.

### `document` 계열

수집한 문서 한 건과 그 문서에 붙은 태그다. `document_ingestion_hourly`가 문서를 넣고
`document_assessment_hourly`가 평가를 채운다.

- 자연키는 `(source_slug, external_id)`다. **`content_hash`를 키에 넣지 않는다.** 넣으면
  본문이 조금만 달라져도 새 행이 생겨 같은 기사가 매시간 쌓인다. 본문이 바뀌면 같은 행을
  갱신하고, 다시 평가할지는 `assessed_content_hash`와 현재 `content_hash`의 비교가 정한다.
- **승인·보류 같은 상태 머신을 두지 않는다.** 소비자가 사람이 아니라 LLM이라 전부 저장하고
  점수(`value_score`)만 남긴다. 상태로 버리면 나중에 기준을 바꿀 때 되돌릴 수 없다.
- 평가에 실패한 문서는 `assessed_at`을 `NULL`로 남긴다. 삭제하거나 다른 상태로 바꾸지 않는다.
  다음 정시 실행이 다시 집는다.
- `document_instrument`와 `document_indicator`는 `instrument`·`indicator_series` 마스터로
  **외래키를 걸지 않는다.** `indicator_observation`이 마스터를 참조하지 않는 것과 같은 이유다.
  마스터에 없는 태그가 오면 태깅 전체가 죽는 대신 그 태그만 빠져야 한다. 후보 목록은
  프롬프트로 주고, 목록 밖의 값은 저장 전에 버린다.
- `body`는 `content_level`이 `metadata_only`면 `NULL`이고 CHECK 제약이 그것을 강제한다.
- 출처 고유 값(증권사, 목표가)은 `document`에 컬럼을 더하지 않고 제목·`summary`에 넣는다.
  **제목 말머리에 대괄호를 쓰지 않는다** — `dedup`이 15자 이하 대괄호 말머리를 벗기고
  비교해서, 같은 날 두 증권사의 같은 제목이 중복으로 묶인다. 증권사는 제목 끝에 낱말로
  붙인다(`… - 대신증권`). 구조화된 숫자(투자의견·목표주가)는 `stock_analyst_opinion`처럼
  별도 테이블이 갖는다.

### `stock_event_*` 계열

같은 사건에 대한 **기대와 실제를 잇는** 테이블 셋이다. `event_expectation_hourly`가 문서에서
주장을 뽑아 `stock_event_claim`에 쌓고, 실제값이 생기면 `stock_event_outcome`에 판정을 남긴다.

- 기대와 실제를 잇는 키는 `(stock_code, event_type, period_key) + metric`이다. **`period_key`는
  세 형식(`2026`·`2026Q2`·`2026H1`)만 허용하고 DB CHECK가 그것을 강제한다.** 느슨하게 받으면
  기대와 실제가 다른 표기로 저장돼 조용히 매칭이 깨진다.
- **단위 컬럼을 두지 않는다.** 단위는 `metric`이 정하고 전부 원(KRW)이다. 원문 표기(조·억)는
  수집 단계에서 정규화하고 **모르는 표기는 그 주장을 버린다** — 조용히 엉뚱한 자릿수로
  저장하는 것보다 낫다.
- 실적 지표(`revenue`·`operating_profit`·`net_income`)는 `earnings_fact.metric`과 **글자 그대로
  같다.** 판정이 대응표 없이 조인하기 위해서다. 테스트가 두 Enum을 대조한다.
- **실적의 실제값은 `earnings_fact`가 원본이다.** 기사 산문에서 다시 뽑지 않는다 — DART 파서가
  원문 표에서 읽는 값과 어긋나면 어느 쪽이 맞는지 고를 수 없다. 추출은 `earnings`+`actual`
  조합을 저장 전에 버린다.
- **판정은 첫 성공본 불변이다**(`INSERT ... ON CONFLICT DO NOTHING`). 발표 뒤 기대 행이 늦게
  추출돼도 다시 내지 않는다. 덮어쓰면 Slack으로 이미 나간 판정과 DB가 어긋난다. 정정 공시로
  판정이 뒤집히는 사례가 관측되면 그때 재판정 정책을 정한다.
- **발표 전 기대만 판정에 쓴다**(`stated_at < announced_at`). 발표 뒤 "기대치는 X였다"라고
  회고한 기사가 기대로 섞이면 판정이 오염된다.
- **실제값 주장이 갈리면 판정하지 않는다.** "총 환원 8조"와 "배당+자사주 8.5조"처럼 집계 범위가
  다른 숫자가 온다. 조용히 한쪽을 고르는 대신 보류하고 다음 실행이 다시 본다.
- **숫자 비교에 LLM을 쓰지 않는다.** 대표 기대치 집계와 beat/meet/miss 분류는 순수 함수다
  (thesis 채점 수식이 SQL이 아니라 Python에 있는 것과 같은 이유 — DB 없이 경계값을 테스트한다).
  LLM은 산문에서 숫자를 꺼내는 추출 단계에만 있다.
- 주장 0건 문서도 `stock_event_extraction` 원장에 남긴다. "뽑았는데 없었다"와 "아직 안 뽑았다"가
  구분돼야 매시간 같은 문서를 다시 뽑지 않는다(`source_record`가 관측값 0건에도 남는 것과 같다).

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
