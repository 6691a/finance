# `airflow/modules/`를 도메인 폴더로

- 상위: [collectors-class-migration.md](collectors-class-migration.md)와 같은 층의 전환 문서다.
  그 문서가 **한 파일을 어디서 가르나**를 정한다면, 이 문서는 **가른 파일들을 어떻게 묶나**다.
- 날짜: 2026-08-27
- 상태: **설계만. 구현 전.** 1단계부터 사용자 재승인 뒤 착수한다.
- 의존: 없다. 동작을 바꾸지 않는 이동이다.
- 산출물(예정): `modules/expectation/`·`technical/`·`thesis/` 세 패키지, import 경로 수정,
  `tests/modules/test_import_weight.py`의 경로 갱신.

## 0. 왜 — 최상위에 31개가 평평하게 있다

`airflow/modules/`의 최상위 `.py`가 **31개, 9,853줄**이다. 그중 절반이 `thesis_`로 시작하고,
파일 이름의 접두어가 폴더를 대신하고 있다. `collectors/`는 이미 도메인 폴더로 나뉘어 있고
(`market/`·`document/`·`indicator/`·`calendar/`·`analyst/`, 2026-08-25) `briefing/`도 폴더다.
**최상위만 그 관례 밖에 있다.**

접두어가 폴더 노릇을 하면 두 가지가 는다 — 파일을 찾을 때 이름을 외워야 하고, 새 파일을
만들 때 접두어를 붙일지 말지를 매번 판단해야 한다.

## 1. 지금 무엇이 있나

| 묶음 | 파일 | 줄 | 이 이동이 건드릴 파일 |
| --- | ---: | ---: | ---: |
| `thesis_*` 13개 | 13 | 5,565 | **27** |
| `expectation_*` 3개 | 3 | 893 | 7 |
| `technical`·`technical_signals`·`base_rate` | 3 | 702 | 11 |
| `assessment`·`dedup` | 2 | 916 | 6 |
| 공용 잎(`db`·`sql`·`upsert`·`utility`·`period`·`schema`·`slack`·`llm`·`prompt`·`market_session`) | 10 | 640 | **114** |

"건드릴 파일"은 그 묶음의 import를 갖고 있는 `airflow/`·`tests/`·`apps/` 파일 수다(2026-08-27 실측).

## 2. 공용 잎은 안 옮긴다 — 숫자가 그렇게 말한다

`db.py`·`sql.py`·`utility.py` 같은 것을 `modules/core/`로 모으면 **114개 파일 226줄**을
고쳐야 한다. 저장소 거의 전부다.

**얻는 것이 그 값을 못 한다.** 이 열 개는 합쳐 640줄이고 파일당 평균 64줄이며, 최상위에
있어도 목록에서 눈에 걸리지 않는다. 문제는 `thesis_` 열셋이지 이쪽이 아니다.

같은 이유로 `assessment.py`·`dedup.py`도 그대로 둔다. 둘뿐이라 폴더가 벌어 주는 것이 없다.

**옮기는 것은 셋뿐이다.**

## 3. 옮긴 뒤 모습

```
airflow/modules/
  briefing/        (그대로)
  collectors/      (그대로)
  expectation/
    domain.py          ← expectation_domain.py
    extraction.py      ← expectation_extraction.py
    judgment.py        ← expectation_judgment.py
  technical/
    indicators.py      ← technical.py
    signals.py         ← technical_signals.py
    base_rate.py       ← base_rate.py
  thesis/
    common.py          ← thesis_common.py
    domain.py          ← thesis_domain.py
    forecast.py        ← thesis_forecast.py
    generation.py      ← thesis_generation.py
    intraday.py        ← thesis_intraday.py
    nxt_review.py      ← thesis_nxt_review.py
    outcomes.py        ← thesis_outcomes.py
    render.py          ← thesis_render.py
    review.py          ← thesis_review.py
    state.py           ← thesis_state.py
    store.py           ← thesis_store.py
    toolbox.py         ← thesis_toolbox.py
    tools.py           ← thesis_tools.py
  prompts/         (그대로 — 데이터 폴더)
  assessment.py  dedup.py  db.py  llm.py  market_session.py
  period.py  prompt.py  schema.py  slack.py  sql.py  upsert.py  utility.py
```

최상위 `.py`가 **31개에서 12개로** 줄고, 남는 열둘은 전부 300줄 미만의 공용 잎이다.

**접두어를 뗀다.** `modules.thesis.thesis_domain`은 말을 더듬는다. `modules.thesis.domain`이다.
`collectors/`가 제공처 이름을 파일에 남긴 것(`market/kis_positioning.py`)과 다른 판단인데,
저기는 접두어가 **제공처**라 뜻이 있고 여기 `thesis_`는 **폴더가 될 것**이 이름에 붙어 있던
것이다.

## 4. 가장 중요한 것 — `__init__.py`는 재수출하지 않는다

**빈 파일이다.** `collectors/`에 이미 있는 규칙이고(`.claude/CLAUDE.md`: "하위 패키지
`__init__.py`는 재수출하지 않는다") 여기서는 그것이 더 날카롭다.

`modules/thesis/__init__.py`가 `from .generation import ThesisBuilder`를 하면
**`modules.thesis.domain` 하나를 import해도 LangChain이 딸려 온다.** 슬롯 모듈과
`thesis_common`이 가벼운 이유가 그것을 피한 것이고, `tests/modules/test_import_weight.py`가
그 경계를 재고 있다. 재수출은 그 테스트를 즉시 깬다.

`expectation/`도 같다 — `judgment`는 LLM이 없고 `extraction`은 있다. 그 갈라짐이 파일 분리의
이유였다(2026-08-25).

## 5. 순서 — 작은 것으로 형태를 검증하고 큰 것을 옮긴다

각 단계가 worktree/PR 하나다.

### 1단계 — `expectation/` (파일 3, 건드릴 파일 7)

가장 작다. 여기서 확인할 것 셋이다.

- 빈 `__init__.py`로 import가 도는가.
- `test_import_weight.py`의 `EXPECTATION_LIGHT_MODULES` 경로를 새 이름으로 바꿨을 때
  여전히 LangChain이 안 딸려 오는가.
- `pyproject.toml`의 ruff `known-first-party`·pyrefly `search-path`가 그대로 도는가
  (`modules`가 뿌리라 하위 패키지가 늘어도 안 바뀌어야 정상이다).

### 2단계 — `technical/` (파일 3, 건드릴 파일 11)

`technical.py`가 `technical/indicators.py`가 되면서 **`from modules import technical`이
패키지 import로 바뀐다.** 지금 그 형태로 쓰는 자리가 있다
(`thesis_toolbox.py`, `thesis_common.py`, 테스트 넷). `from modules.technical import indicators`로
바꾼다. 재수출로 때우지 않는다(4절).

`base_rate.py`를 여기 넣는 이유: 그 모듈이 세는 것이 **기술 신호 뒤의 결과 빈도**이고,
소비자가 thesis뿐이지만 다루는 대상은 `technical_signal`이다. `technical.py`(계산)·
`technical_signals.py`(검출·저장)·`base_rate.py`(사후 빈도)가 한 이야기다.

### 3단계 — `thesis/` (파일 13, 건드릴 파일 27)

가장 크다. 앞의 둘에서 형태가 검증된 뒤에 손댄다.

- **한 커밋에 전부 옮긴다.** 절반만 옮기면 `modules.thesis_domain`과 `modules.thesis.domain`이
  공존하고, 그 사이에 새 파일을 만드는 사람이 어느 쪽을 따를지 모른다.
- **순환을 새로 만들지 않는다.** 지금 의존이 그대로 유지되므로 이동만으로는 안 생기지만,
  옮기면서 "이 참에" import를 정리하고 싶어진다. 하지 않는다 — 이동과 정리를 같은 커밋에
  두면 어느 쪽이 회귀를 만들었는지 못 가른다.
- 마이그레이션 리비전 파일과 DAG docstring이 `modules/thesis_*.py`를 **문장으로** 가리키는
  자리가 있다. 코드가 아니라 글이라 grep으로 함께 고친다.

## 6. 단계마다 지키는 것

**동작이 한 줄도 안 바뀌는 이동이다.** 파일 내용은 import 문 말고 손대지 않는다.

1. `git mv`로 옮긴다. 이름이 바뀌므로 git이 rename으로 잡게 한 번에 옮긴다.
2. import를 기계적으로 치환한다. **손으로 고치지 않는다** — 놓친 한 줄이 런타임까지 산다.
3. `uv run pytest tests -q` — 그 단계에서 늘거나 주는 테스트가 없어야 한다.
4. `uv run ruff check apps airflow migrations tests`와 `uv run pyrefly check`.
5. **`tests/modules/test_import_weight.py`가 새 경로로 여전히 통과하는가.** 이 전환에서
   유일하게 깨질 수 있는 계약이다.
6. DagBag이 도는지는 테스트가 대신 본다(`tests/dags/`가 DAG 객체를 import한다).
   DAG을 돌리지 않는다.
7. `graphify update .`.

## 7. 안 하기로 한 것

- **공용 잎을 `core/`로 모으기.** 2절 — 114개 파일을 고치고 얻는 것이 목록 열 줄이다.
- **`__init__.py`에서 재수출해 import 경로를 짧게 하기.** 4절 — DagBag 무게 계약을 깬다.
  경로가 한 단 길어지는 것은 그 값을 치를 만하다.
- **이동하면서 파일을 더 쪼개기.** 쪼갤지는 별도 판단이고 기준이
  [collectors-class-migration.md](collectors-class-migration.md)의 "파일을 나누는 기준"에 있다.
  `thesis_toolbox.py`가 1,440줄로 저장소 최대인데, **이동과 분리를 같은 커밋에 두지 않는다.**
- **`apps/` 트리.** `apps/models/`와 `apps/realtime/`은 이미 정리돼 있다.

## 8. 이 전환이 끝나면

- 최상위 `.py`가 31개에서 12개가 되고, 남는 것은 전부 공용 잎이다.
- 새 파일을 만들 때 접두어를 고민할 자리가 없어진다 — 폴더가 정한다.
- `modules/`가 `collectors/`·`briefing/`와 같은 모양이 된다.
- **`thesis_toolbox.py` 분리**가 다음 후보로 남는다. 1,440줄이고 이 이동 뒤에는
  `thesis/toolbox.py`라 나누기도 쉬워진다.
