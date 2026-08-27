# 프롬프트를 파이썬 밖으로 — 남은 다섯 모듈

- 상위: [collectors-class-migration.md](collectors-class-migration.md)와 같은 층의 전환 문서다.
- 날짜: 2026-08-27
- 상태: **설계만. 구현 전.** 1단계부터 사용자 재승인 뒤 착수한다.
- 의존: `airflow/modules/prompt.py`와 `modules/prompts/disclosure_picks.yaml`
  (2026-08-27에 만든 기준 구현). 규칙은 `.claude/CLAUDE.md`의 "프롬프트는 코드가 아니다".
- 산출물(예정): `modules/prompts/*.yaml` 다섯, 각 모듈의 프롬프트 상수 교체, 그리고
  버전이 있는 프롬프트를 위한 해시 가드 테스트 하나.

## 0. 왜 — 문장과 흐름은 주기가 다르다

지금 파이썬 파일 안에 프롬프트가 **354줄** 있다. 문장을 한 낱말 고치는 일이 모듈을 여는
일이고, 그 diff는 로직 변경과 구분되지 않는다. `sql/`을 파이썬 문자열로 두지 않는 것과
같은 이유이고, 2026-08-27에 `slack_disclosure_briefing`이 먼저 그 형태로 나왔다.

**이 문서는 나머지를 옮기는 순서와 그때 깨질 것을 적는다.**

## 1. 지금 어디에 얼마나 있나

AST로 최상위 프롬프트 상수만 센 값이다(2026-08-27).

| 모듈 | 줄 | 상수 | 버전 칸 | 특징 |
| --- | ---: | --- | --- | --- |
| `thesis_generation.py` | 174 | `SYSTEM_PROMPT`(103)·`INSTRUCTION`(44)·`SLOT_INSTRUCTION`(23)·`REPAIR_INSTRUCTION`(4) | `thesis_domain.PROMPT_VERSION` | **가장 크고 가장 얽혀 있다.** 자리표시자 10개, 슬롯별 딕셔너리 |
| `assessment.py` | 50 | `INSTRUCTION`(29)·`PERSPECTIVES`(15)·`SYSTEM_PROMPT_TEMPLATE`(5)·`REPAIR_INSTRUCTION`(1) | `assessment.PROMPT_VERSION` | 유일하게 `.format()`을 실제로 쓴다 |
| `thesis_outcomes.py` | 49 | `NARRATIVE_SYSTEM_PROMPT`(33)·`NARRATIVE_INSTRUCTION`(12)·`NARRATIVE_REPAIR_INSTRUCTION`(4) | `NARRATIVE_PROMPT_VERSION` | |
| `briefing/picks.py` | 36 | `SYSTEM_PROMPT`(31)·`INSTRUCTION`(4)·`REPAIR_INSTRUCTION`(1) | **없음** | 채점하지 않는다 |
| `expectation/extraction.py` | 35 | `INSTRUCTION`(29)·`SYSTEM_PROMPT`(5)·`REPAIR_INSTRUCTION`(1) | `expectation.domain.PROMPT_VERSION` | |
| `llm.py` | 10 | `NUMBER_STYLE`(10) | — | **조각이다.** 위 넷 중 셋이 끼워 쓴다 |

`briefing/disclosure_picks.py`는 이미 옮겼다(3줄만 남았다).

## 2. 실측으로 확인한 것 둘

**어느 프롬프트에도 `$`가 없다.** `string.Template` 치환이 기존 문장을 건드리지 않는다.
새 자리표시자를 넣는 것 말고는 문장이 한 글자도 안 바뀐다.

**테스트가 상수 이름으로 import한다.** `test_llm.py`·`test_thesis_common.py`·
`test_assessment.py`·`test_briefing_picks.py`가 `SYSTEM_PROMPT`·`NUMBER_STYLE`·`PERSPECTIVES`
같은 이름을 직접 가져다 쓴다. **모듈이 같은 이름을 계속 내보내면**(import 시점에 렌더해서
같은 상수에 담으면) **대부분의 테스트는 한 글자도 안 바뀐다.** 이것이 이 전환이 싼 이유다.

예외는 하나다 — `test_llm.py`가 `SYSTEM_PROMPT_TEMPLATE.format(perspective=...)`을 직접
부른다. 3단계에서 함께 고친다.

## 3. 순서 — 작고 안 얽힌 것부터

각 단계가 worktree/PR 하나다. **한 번에 한 모듈만 옮긴다.**

### 1단계 — `briefing/picks.py`

가장 안전하다. **버전 칸이 없어** 문장이 바뀌어도 채점과 어긋날 것이 없고, 소비자가
`slack_document_briefing` 하나다. `disclosure_picks.py`와 형태가 거의 같아 그대로 베낀다.

자리표시자: `$max_reads`·`$max_watches`·`$max_why_chars`·`$number_style`(system),
`$window_hours`·`$candidates`(instruction).

### 2단계 — `expectation/extraction.py`

버전(`expectation.domain.PROMPT_VERSION`)이 붙지만 **`INSTRUCTION`이 29줄로 단순하고
자리표시자가 적다.** 여기서 4절의 해시 가드를 처음 만든다.

**`NUMBER_STYLE`을 안 쓰는 유일한 프롬프트다**(`test_llm.py`가 그 사실을 지킨다 — 그쪽
숫자는 산문이 아니라 JSON 숫자 칸으로 가고 쉼표가 파싱을 깬다). YAML에도 넣지 않는다.

### 3단계 — `assessment.py`

**`.format()`을 `string.Template`으로 바꾸는 유일한 단계다.** 그 대가로 `llm.py`의
"`NUMBER_STYLE`에 중괄호를 넣지 마라" 제약이 사라진다 — 그 규칙은 `assessment`가 유일한
`.format()` 소비자라서 존재했다.

`PERSPECTIVES`(관점 셋)도 YAML로 간다. 값이지 코드가 아니고, 관점을 늘리는 일이 파이썬을
여는 일일 이유가 없다. **다만 허용 값 검증은 코드에 남긴다** — `LlmSettings`가
`PERSPECTIVES` 키로 환경변수를 막고 있고, 그 판정은 YAML이 할 수 없다.

같이 고칠 것: `test_llm.py`의 `.format(perspective=...)` 호출과 중괄호 검사 둘.

### 4단계 — `thesis_outcomes.py`

`NARRATIVE_PROMPT_VERSION`이 붙는다. 자리표시자는 `$run_date`·`$slot_label`·
`$horizon_days` 정도로 단순하다. 3단계까지 왔으면 기계적이다.

### 5단계 — `thesis_generation.py`

**가장 크고 마지막이다.** 앞의 넷에서 형태가 검증된 뒤에 손댄다.

어려운 것이 둘이다.

- **자리표시자 10개 중 셋이 계산식이다** — `{int(RSI_OVERBOUGHT)}`, `{int(RSI_OVERSOLD)}`,
  `{FLAT_THRESHOLD_PCT[0]}`. YAML은 식을 못 쓰므로 **부르는 쪽이 이름 붙인 값으로 넘긴다**
  (`rsi_overbought=int(RSI_OVERBOUGHT)`). f-string 안에 계산이 숨어 있던 것이 밖으로
  드러나는 것이라 오히려 낫다.
- **`SLOT_INSTRUCTION`이 enum 키 딕셔너리다.** 장중 슬롯 넷은 같은 문장을 컴프리헨션으로
  복제하고 있다. YAML에는 슬롯 종류별로 한 벌만 두고(`pre_open`·`intraday`·`post_close`·
  `post_nxt_close`) **코드가 `INTRADAY_SLOTS`로 펼친다.** 슬롯이 늘 때 YAML을 안 고쳐도
  되는 쪽이 맞다 — 슬롯 목록은 코드가 정한다.

### 6단계 — `llm.NUMBER_STYLE`

**마지막이다.** 앞의 소비자 셋이 전부 `$number_style`로 받게 된 뒤에 옮긴다. 그 전에
옮기면 `assessment`가 아직 `.format()`이라 중괄호 제약이 파일로 따라간다.

자리는 `modules/prompts/shared.yaml`이고, `llm.NUMBER_STYLE`은 그 값을 읽는 얇은 상수로
남긴다 — 네 소비자와 테스트가 이름으로 쓰고 있어 한꺼번에 고칠 이유가 없다.

## 4. 버전이 있는 프롬프트를 어떻게 지키나

**이 전환이 만드는 유일한 새 위험이다.** 문장이 파일로 나가면 코드를 안 건드리고 고칠 수
있는데, `PROMPT_VERSION`은 코드에 있다. 판을 안 올리고 문장만 바뀌면 **ops 창의 Brier가
서로 다른 프롬프트의 결과를 한 판으로 섞는다.** 지금은 같은 파일이라 눈에 띄지만 파일이
갈리면 안 보인다.

막는 방법은 해시 가드 하나다.

```python
# tests/modules/test_prompt_versions.py
PROMPT_HASHES = {
    # 문장을 고쳤으면 판을 올리고 이 해시도 같이 바꾼다. 둘을 같은 커밋에서 만진다.
    ("expectation_extraction", "1"): "sha256:…",
    ("thesis_generation", "7"): "sha256:…",
}
```

- 파일 내용의 SHA-256과 그 흐름의 현재 `PROMPT_VERSION`을 함께 잠근다.
- 문장만 고치면 해시가 어긋나 테스트가 깨진다. **판을 올리고 표를 갱신하는 것이 통과 조건이다.**
- 버전이 없는 흐름(`briefing/picks`·`disclosure_picks`)은 표에 넣지 않는다. 채점하지 않으므로
  판을 가를 이유가 없다.
- **주석은 해시에 들어간다.** 주석만 고쳐도 깨지는데, 그건 받아들인다 — 프롬프트 파일의
  주석은 모델에게 안 가지만 문장을 고치는 사람이 읽는 것이라 같은 무게로 다룬다.

2단계에서 만들고 이후 단계가 한 줄씩 더한다.

## 5. 단계마다 지키는 것

**문장이 한 글자도 안 바뀌는 것을 증명한다.** 이 전환은 표현을 고치는 작업이 아니다.

1. YAML은 **현재 상수에서 그대로 떠 온다.** 손으로 다시 쓰지 않는다.
2. 옮긴 뒤 렌더 결과가 옛 상수와 **바이트 단위로 같은지** 확인한다. 커밋 전에 워크트리에서
   한 번 대조하고, 다르면 그 차이를 설명할 수 있어야 한다.
3. `uv run pytest tests -q`와 `uv run ruff check apps airflow migrations tests`.
4. 그 흐름의 기존 테스트가 **한 글자도 안 바뀌는 것이 정상이다**(3단계 제외). 테스트를
   고쳐야 한다면 상수 이름을 안 지켰다는 뜻이다.
5. `graphify update .`.

DAG은 돌리지 않는다. 프롬프트가 실제로 모델에게 잘 가는지는 배포 뒤 LangSmith 트레이스로
본다.

## 6. 옮기지 않는 것

- **`thesis_toolbox.py`의 `Field(description=...)`.** 툴 인자 설명도 모델이 읽는 문장이지만
  **상한 값을 f-string으로 싣는 것이 저장소 규칙**이고(`MAX_TOOL_CALLS`를 고치면 프롬프트가
  따라간다), Pydantic 모델 선언과 한 몸이다. 떼면 스키마와 설명이 두 파일로 갈린다.
- **`PROMPT_VERSION`·`NARRATIVE_PROMPT_VERSION` 상수와 그 위 판별 주석.** 채점과 이어져
  있어 코드가 원본이다. 4절 해시 가드가 문장과 판을 잇는다.
- **오류 메시지·로그 문자열.** 모델에게 가지 않는다.
- **`apps/` 트리.** 지금 LLM을 부르는 상주 서비스가 없다. 생기면 그때 같은 규칙을
  적용하되, `apps/`는 `config.yaml`을 읽으므로 자리는 다시 정한다.

## 7. 안 하기로 한 대안 둘

**프롬프트를 DB에 두기.** 화면에서 고치고 판을 자동으로 세는 것까지 가면 좋지만, 그러면
프롬프트가 코드 리뷰를 안 거친다. 문장 한 줄이 채점 판을 가르는데 리뷰가 없는 것은 지금
저장소의 다른 어떤 규칙과도 안 맞는다. **파일이면 diff가 남고 되돌릴 수 있다.**

**환경변수로 빼기.** `llm.py`가 모델 선택을 환경변수로 안 빼는 것과 같은 이유다 — 어느
문장으로 돌았는지가 배포 환경에 흩어지면 재현이 안 된다.

## 8. 이 전환이 끝나면

- 파이썬 파일에서 354줄이 빠진다. `thesis_generation.py`는 713줄에서 540줄 안팎이 된다.
- 문장만 고친 PR이 코드 diff 0줄이 된다.
- `llm.py`의 중괄호 제약이 사라진다.
- 프롬프트 판과 문장이 해시로 묶여, **판을 안 올리고 문장을 고치는 사고**가 테스트에서 죽는다.
