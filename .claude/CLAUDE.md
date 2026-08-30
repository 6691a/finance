# news 프로젝트 가이드

Claude Code가 이 저장소에서 작업할 때 따르는 규칙과 구조 요약이다.
Codex용 규칙 원본은 [.codex/AGENTS.md](../.codex/AGENTS.md)이며 두 문서는 함께 갱신한다.

## 규칙을 스킬로 나눴다

아래 넷은 **그 작업을 할 때만** 필요해서 스킬로 내렸다(2026-08-28). 매 세션 통째로 읽던
614줄이 여기서 빠졌다.

**해당 작업을 시작하기 전에 `Skill` 도구로 반드시 부른다.** 안 부르면 그 규칙은 없는 것과
같다 — 이 표는 규칙의 요약이 아니라 **어디에 있는지의 주소**다.

| 언제 | 스킬 |
| --- | --- |
| `airflow/modules/collectors/` 아래 수집기를 더하거나 고칠 때 | `writing-collectors` |
| LLM을 부르는 코드나 `airflow/modules/prompts/*.yaml`을 건드릴 때 | `writing-llm-flows` |
| Alembic 리비전을 만들거나 모델에 `table_options`를 쓸 때 | `writing-migrations` |
| `source_record`·`indicator_observation`·`indicator_series`·봉 테이블·`instrument`·`document`·`stock_event_*`에 읽거나 쓸 때 | `table-conventions` |

파일은 `.claude/skills/<이름>/SKILL.md`이고 `.agents/skills/<이름>/`에 Codex용 사본이 있다.
**둘은 같은 내용이어야 한다** — 한쪽만 고치면 두 도구가 다른 규칙을 본다.

## 이 프로젝트가 무엇인가

**루트 [README.md](../README.md)를 먼저 읽는다.** 무엇을 만드는 프로젝트이고 데이터가 어느 경로로
흐르는지가 거기 있다. 이 문서는 "어떻게 쓰나"만 갖고 "무엇인가"는 갖지 않는다.

| 알고 싶은 것 | 어디 |
| --- | --- |
| 프로젝트가 무엇이고 하루에 무엇이 도는가 | [README.md](../README.md) |
| 설정·DB alias·마이그레이션·DAG 목록·배포·관측 | [docs/operations.md](../docs/operations.md) |
| 어떤 설계를 왜 그렇게 했나 | [docs/README.md](../docs/README.md)의 색인 |

**README는 밖으로 보이는 문서다**(포트폴리오를 겸한다). 거기 적힌 숫자와 목록 — DAG 개수, 테이블
수, 하루 흐름의 시각, 스택, "지금 상태" 절 — 은 코드가 바뀌면 **같은 커밋에서** 고친다. DAG를
더하거나 슬롯 시각을 옮겼는데 README가 그대로면 다음 사람이 그것을 현재형으로 읽는다.
숫자를 새로 적을 때는 세거나 조회한 실측값만 쓴다. 어림값을 적지 않는다.

## 프로젝트 구조

| 경로 | 역할 |
| --- | --- |
| `../apps/core/config.py` | `config.yaml`을 읽는 Pydantic 설정. `settings` 싱글턴 제공 |
| `../apps/core/database.py` | `Base`, `EntityBase`, 다중 DB 별칭을 관리하는 `Database` |
| `../apps/core/redis.py` | Redis 연결 관리 |
| `../apps/core/container.py` | dependency-injector 컨테이너(상주 서비스는 안 쓴다 — 아래 규칙) |
| `../apps/core/utility.py` | 상태 없는 공통 변환(`utc_text`·`kst_today`). `airflow/modules/utility.py`의 대칭 |
| `apps/models/` | SQLAlchemy 모델. 파일은 도메인 단위로만 나눈다(스키마와 무관) |
| `apps/realtime/` | KIS 실시간 WebSocket 수집 서비스. `python -m apps.realtime.main`, `compose/prod/` 배포 |
| `apps/api/` | 읽기 전용 조회 API(FastAPI). 리소스는 늘어난다 — 지금은 시장 추론. `python -m apps.api.main`, `compose/prod/api/` 배포 |
| `migrations/` | Alembic. 리비전 파일은 `migrations/versions` 하나를 모든 별칭이 공유한다 |
| `migrations/routing.py` | 어떤 테이블이 어떤 DB 별칭에 속하는지 판단하는 순수 함수 |
| `../airflow/dags/` | Airflow DAG. 폴더로 나누지 않는다 — 스케줄·재시도·실패 판정만 갖는 얇은 파일이다 (아래 규칙) |
| `../airflow/modules/` | DAG이 쓰는 공유 코드. 도메인 폴더(`collectors/`·`briefing/`·`expectation/`·`technical/`·`thesis/`)로 나누고 최상위에는 공용 잎만 둔다. 하위 패키지 `__init__.py`는 비운다 — 재수출하면 가벼운 모듈 하나를 import해도 LangChain이 딸려 온다. (아래 규칙) |
| `../airflow/modules/collectors/` | 수집기. 도메인 폴더(`market/`·`document/`·`indicator/`·`calendar/`·`analyst/`)로 나눈다. 전환 진행 상황은 [docs/convention/collectors-class-migration.md](../docs/convention/collectors-class-migration.md) |
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

## `apps/` 상주 서비스의 파일 구조

Airflow가 실행하지 않는 상주 서비스는 `apps/` 아래 **패키지 하나**다. 그 안의 파일 이름이
곧 역할이고, 아래 이름이 이미 둘(`realtime`·`api`)에서 같은 뜻으로 쓰인다.

**`web`은 나중에 올 프론트엔드의 이름이다.** 조회 API는 `apps/api/`이고 화면이 생기면
`apps/web/`이 그 자리를 받는다 — 지금 API를 `web`으로 부르면 그때 둘을 가를 이름이 없다.

| 파일 | 무엇 |
| --- | --- |
| `__init__.py` | 이 서비스가 무엇이고 왜 이렇게 배포되는지. 코드는 안 둔다 |
| `main.py` | **진입점 하나.** `python -m apps.<name>.main`. 설정을 읽고 Sentry를 붙이고 조립한다 |
| `container.py` | dependency-injector 컨테이너. 있으면 여기가 **composition root**다 |
| `app.py` | 조립된 것을 받아 앱을 만든다. 설정을 스스로 읽지 않는다 |
| `routes/` | **HTTP만** 안다. 컨테이너가 보이는 유일한 자리(`@inject`) |
| `service/` | **계약만** 안다. 행을 응답 모양으로 바꾼다 |
| `repository/` | **store만** 안다. **세션 팩토리를 생성자로 받고** 행을 준다 |
| `schemas/` | 밖으로 나가는 데이터 모양(API 응답 등) |

**뒤의 넷은 리소스마다 파일이 느는 층이라 패키지다.** 파일 하나로 두면 리소스가 둘만
돼도 서로 관계없는 것이 한 파일에 쌓이고, 고칠 때 diff가 남의 리소스까지 건드린다.

### 층 셋은 아는 것으로 가른다

`routes` → `service` → `repository`. **리포지토리가 응답 모양을 알면 store를 갈아끼울 때
계약까지 함께 흔들린다.** 서비스가 얇아 보여도 그 경계가 값어치다 — 통과 층이 되지 않게
매핑은 모듈 수준 순수 함수로 두고 클래스는 순서만 엮는다.

응답 하나가 세션 하나다. 리포지토리 메서드마다 세션을 열면 한 응답이 커넥션을 여러 번
빌린다 — 그래서 리포지토리가 "그 응답에 필요한 행 묶음"을 한 번에 준다.

### `main.py`만 설정을 읽는다

`apps/core/config.py`는 모듈 본문에서 `settings = Settings()`를 불러 **import만으로
`config.yaml`을 요구한다.** 그래서 `settings` import는 `main()` 함수 안에서 한다 —
테스트와 도구가 설정 파일 없이 그 모듈을 import할 수 있어야 한다.

같은 이유로 **컨테이너도 설정을 스스로 읽지 않는다.** `providers.Dependency()`로 선언하고
`main.py`가 채운다. `apps/core/container.py`가 그 규칙 밖에 있는데(본문에서 settings를
읽는다), 그래서 상주 서비스가 그것을 쓰지 않는다.

### 의존성은 생성자로 주입한다

컨테이너에 선언한 것을 **파라미터로** 받는다. 업무 코드가 `container.thing()`을 직접
부르면 그건 Service Locator이지 의존성 주입이 아니다. 컨테이너 이름이 보이는 자리는
`WiringConfiguration`이 지정한 패키지 하나(라우터)로 좁힌다.

provider 수명은 뜻을 갖는다 — 엔진 풀처럼 프로세스에 한 벌인 것만 `Singleton`이고,
조회마다 새로 만드는 것은 `Factory`다. `Singleton`으로 두면 나중에 요청 상태를 담게 될 때
조용히 새어 나간다.

테스트는 **provider override**로 가짜를 끼운다(`container.x.override(...)`). 그것이 먹는다는
사실 자체가 wiring이 풀렸다는 증거이기도 하다 — 마커가 안 풀리면 주입 자리에 `Provide`
객체가 그대로 들어와 조용히 틀린다.

### 층 넷은 패키지로, 리소스마다 파일 하나

`routes/`·`service/`·`repository/`·`schemas/` 넷에 같은 규칙을 쓴다. `apps/models/`가
도메인 단위로 나뉜 것과 같다.

- `<리소스>.py` — 그 리소스 하나의 것. 파일 이름이 리소스 이름이고 네 폴더에서 같다
  (`thesis.py`가 넷에 하나씩). **한 리소스를 고칠 때 열 파일이 넷으로 정해진다.**
- `common.py` — **그 층의 리소스들이 공유하는 것만.** 리포지토리는 행 묶음 베이스와
  목록 상한, 서비스는 `Decimal` → JSON number 같은 변환, 스키마는 공통 베이스와 시각
  표기 애노테이션이다. 리소스 하나에만 쓰이는 것을 여기 두지 않는다 — 쓰는 쪽이 하나면
  그건 그 리소스의 파일에 있어야 한다.
- `__init__.py` — **재수출만.** 부르는 쪽은 `from apps.api.schemas import X` 하나로 끝내고
  어느 파일에 있는지 몰라도 된다. 층의 경계 설명(무엇을 알고 무엇을 모르는지)은 여기
  docstring에 둔다. 이름을 빠뜨려도 `ruff`가 잡지 못하므로 더할 때 함께 넣는다.

`routes/`만 둘이 더 붙는다.

- **`router`는 파일마다 하나**이고 경로 접두와 `tags`도 그 파일이 정한다
  (`APIRouter(prefix="/api/theses", tags=["thesis"])`). 그래야 리소스를 더할 때 `app.py`가
  아니라 새 파일 하나만 는다. `__init__.py`는 그것들을 `routers` 튜플로 재수출하고
  `app.py`가 순회한다.
- **wiring은 패키지를 통째로 건다**(`WiringConfiguration(packages=["apps.api.routes"])`).
  모듈을 하나씩 적으면 새 리소스를 더할 때 `container.py`도 함께 고쳐야 하고, 빠뜨리면
  `Provide` 객체가 그대로 주입되어 조용히 틀린다.

### 상태 없는 변환은 `apps/core/utility.py`에 한 벌

시각 표기(`utc_text`), 날짜 경계(`kst_today`)처럼 **여러 서비스가 같은 답을 내야 하는 변환**은
거기 둔다. 같은 로직을 두 모듈이 각자 갖고 있으면 한쪽만 고친 날 한 응답 안에서 표기가
갈린다. 이 모듈은 `config`·`database`·`redis`를 import하지 않아 어디서 불러도 `config.yaml`을
요구하지 않는다.

`airflow/modules/utility.py`가 Airflow 쪽의 같은 자리다. 두 트리는 서로를 import하지 않으므로
같은 규칙이 양쪽에 한 벌씩 있고, 어긋나면 테스트가 잡는다.

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

## `airflow/modules/`의 폴더

**한 도메인의 파일이 셋 이상이면 폴더로 내리고 접두어를 뗀다.** `collectors/`·`briefing/`·
`expectation/`·`technical/`·`thesis/`가 그 형태다(뒤의 셋은 2026-08-27에 내렸다 — 최상위
`.py`가 31개에서 12개로 줄었다). `modules.thesis.thesis_domain`은 말을 더듬으므로
`modules.thesis.domain`이다. `collectors/`가 파일 이름에 제공처를 남긴 것
(`market/kis_positioning.py`)과 다른 판단인데, 저기는 접두어가 **제공처**라 뜻이 있고
`thesis_`는 **폴더가 될 것**이 이름에 붙어 있던 것이다.

- **하위 패키지 `__init__.py`는 빈 파일이다.** 재수출하면 `modules.thesis.domain` 하나를
  import해도 LangChain이 딸려 와 DagBag이 그 무게를 문다. `tests/modules/test_import_weight.py`가
  그 경계를 재고 있어 재수출은 그 테스트를 즉시 깬다. 한 수집기의 의존성이 없는 환경에서
  관계없는 DAG이 import 오류로 죽는 것도 같은 이유다.
- **최상위에 남는 것은 공용 잎이다.** `db`·`sql`·`upsert`·`utility`·`period`·`schema`·
  `slack`·`llm`·`prompt`·`market_session`·`assessment`·`dedup` 열둘이고 전부 300줄 미만이다.
  **이것들을 `core/` 같은 폴더로 모으지 않는다** — 114개 파일 226줄을 고치고 얻는 것이 목록
  열 줄이다(2026-08-27 실측). 폴더는 파일이 많아서 만드는 것이지 정리해 보이려고 만드는
  것이 아니다.
- **접두어를 떼면 바인딩 이름이 짧아져 지역 변수와 겹칠 수 있다.**
  `from modules import technical`이 `from modules.technical import indicators`가 되는 식이다.
  `ruff`의 `F823`(할당 전 참조)이 그것을 잡는 유일한 장치이므로 **기계적 치환 직후에 `ruff`를
  먼저 돌린다.** 2026-08-27 이동에서 셋이 걸렸다(`briefing/chart.py`의 `indicators`, 테스트
  둘의 `forecast`·`review`).
- **이동과 파일 분리를 같은 커밋에 두지 않는다.** 어느 쪽이 회귀를 만들었는지 못 가른다.
  `thesis/toolbox.py`가 1,440줄로 저장소 최대이고 다음 분리 후보인데, 기준은
  [collectors-class-migration.md](../docs/convention/collectors-class-migration.md)의
  "파일을 나누는 기준"에 있다.

**`dags/`는 폴더로 나누지 않는다.** Airflow의 DagBag은 하위 폴더를 재귀로 훑으므로
기술적으로는 되지만, `dag_id`가 파일 경로와 무관해 **UI에는 그룹이 생기지 않는다**(그 일은
`tags`가 한다). 얻는 것이 파일 탐색기에서뿐이고, DAG은 파일당 얇은 데다 접두어
(`kis_`·`fred_`·`slack_`·`market_thesis_`)가 이미 정렬을 해 준다.

## 클래스와 함수를 가르는 기준

**상태를 쥔 동작은 클래스로 묶고, 상태 없는 변환은 함수로 둔다.** 이 저장소 전체에 같은
규칙이 적용된다 — LLM 흐름만의 규칙이 아니다.

- **클래스로 묶는다**: 자격 증명·토큰·DB 연결·기준 시각·출처 행처럼 **여러 호출에 걸쳐
  안 변하는 값**을 들고 도는 것. 그 값이 인자로 함수마다 다시 들어가고 있으면 그게 신호다.
  기준 구현은 `modules/collectors/analyst/kis_opinion.py`의 `KisAnalystOpinionCollector`,
  `modules/collectors/document/naver_research.py`의 `NaverResearchCollector`,
  `modules/assessment.py`의 `DocumentAssessor`, `modules/thesis/toolbox.py`의 `ThesisToolbox`·
  `ThesisBuilder`·`FollowupNarrator`다. 연결을 쥐는 흐름 코드는
  `modules/thesis/nxt_review.py`의 `NxtAfterHoursReview`, `modules/thesis/common.py`의
  `ThesisRun`, `modules/thesis/store.py`의 `ThesisStore`가 기준이다.
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
`technical/signals.py`처럼 진입점이 하나뿐인 곳에만 남아 있다** — 새로 만들 때 그 형태를
따라가지 않는다. 남은 단계는 없다.
[docs/convention/collectors-class-migration.md](../docs/convention/collectors-class-migration.md)가 폴더
구조(도메인별 `market/`·`document/`·`indicator/`·`calendar/`·`analyst/`)와 어디서
갈랐는지, 그리고 **함수로 두는 것이 맞다고 판정한 모듈과 그 이유**를 갖는다.
**새 수집기는 처음부터 그 형태로 쓴다.**

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

기준 구현은 `airflow/modules/thesis/state.py`(`ObservedState`·`TechnicalState`·`PastThesis`)와
`airflow/modules/technical/indicators.py`(`DailyBar`·`TechnicalSnapshot`·`SignalEvent`)다.

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
  (`thesis/state.py`가 그 예다 — `thesis/generation.py`는 LangChain, `thesis/common.py`는 Airflow를
  import해서 서로를 모듈 수준에서 import할 수 없다). 소비자가 하나뿐이어도 그 모듈이 이미
  크면 따로 뺀다(`thesis/tools.py`의 툴 응답 모델 스무 개가 그 예다).
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
  만든다. `writing-collectors` 스킬의 "전체 건수와 받은 행 수를 대조한다"와 같은 이유다.
- **재시도 여부 판단을 위에 맡기려면 판단할 것을 위로 올려야 한다.** 아래에서 분류해 놓고
  위로 문자열만 보내면 그 분류는 존재하지 않는 것과 같다.

### DAG의 실패 판정

이미 스무 개 DAG가 아래 세 형태 중 하나를 따른다. 새 DAG도 이 중 하나를 고른다.

- **항목별 실패 수집** — 여러 항목을 한 태스크에서 돌 때. 항목 하나가 실패하면 원인을
  `failures`에 모으고 계속한다. **마지막에 반드시 판정한다.** 어느 쪽을 고르든 실패를 세고
  **이름과 사유를 함께** 메시지에 싣는다(`failures.append(f"{name}({error})")`. 사유에 쉼표가
  들어가므로 구분자는 `;`다).
  - **다음 run이 곧 같은 창을 다시 보는 수집은 전부 실패했을 때만 죽인다.** 하나로 죽이면
    경보만 늘고 고쳐지는 것은 없다. `kis_equity_bar_reconcile`(30분), `kis_quote_intraday`,
    `yahoo_quote_daily`, `yahoo_quote_intraday`, `dart_disclosure_intraday`,
    `document_ingestion_hourly`가 그렇다.
  - **하루 한 번 도는 확정 수집은 하나라도 실패하면 죽인다.** 그날 값을 다시 집는 실행이
    없다. `kis_index_daily`, `kis_investor_trade_daily`, `kis_stock_minute_bars_daily`,
    `kis_analyst_opinion_daily`, `kis_market_positioning_daily`가 그렇다.
  - **어느 쪽인지는 DAG가 정하고 그 근거를 모듈 docstring의 "실패와 재시도" 절에 남긴다.**
    근거가 없으면 다음 사람이 규칙 위반으로 읽는다(2026-08-25에 실제로 그렇게 읽혔다).
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

**한 DAG에 슬롯이 여럿이면 슬롯을 벽시계로 떨어뜨리지 않는 장치를 함께 둔다.**
`market_thesis_intraday`(장중 전망 넷)가 그 형태다 — 넷이 같은 봉과 같은 문서 평가를 같은
이유로 기다려 DAG 하나이고, `thesis.intraday.resolve_slot`이 ① Param → ② `logical_date` →
③ **실패** 순으로 슬롯을 정한다. 가까운 슬롯으로 반올림하지도 않는다. 조용히 다른 슬롯을
도는 것보다 안 도는 편이 낫다는 것이 2026-08-21에 얻은 교훈이고, 그것을 지키면 시각이
여럿인 것 자체는 문제가 아니다. 슬롯 시각의 원본은 상수 하나(`INTRADAY_SLOT_TIMES`)이고
DAG의 cron과 어긋나지 않게 테스트가 둘을 대조한다.

### 모드로 갈리는 함수도 나눈다

DAG를 나눈 뒤 공유 모듈에 `if mode == "..."`가 남으면 절반만 나눈 것이다. 읽는 사람이
함수마다 "지금 어느 쪽 이야기인가"를 따라가야 하고, 한쪽을 고치다 다른 쪽을 깨뜨린다.

- **모드를 모르는 것만 공유 모듈에 둔다.** 연결, 파라미터 검증, 저장, 발송처럼 양쪽이
  글자 그대로 같은 것이다. 모드는 **값으로 흘러갈 수는 있다**(`run_slot`을 저장 함수에
  넘기는 것) — 금지하는 것은 그 값으로 **분기**하는 것이다.
- **모드마다 다른 것은 모드별 모듈이 갖는다.** 기준 시각, readiness guard, 조회 창의 시작,
  어느 세션을 볼지 같은 것이다. 기준 구현은 `airflow/modules/thesis/common.py`와
  `thesis/forecast.py`·`thesis/review.py` 셋이다.
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

# graphify
- **graphify** (`.claude/skills/graphify/SKILL.md`) - any input to knowledge graph. Trigger: `/graphify`
When the user types `/graphify`, use the installed graphify skill or instructions before doing anything else.
