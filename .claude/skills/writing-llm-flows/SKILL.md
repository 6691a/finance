---
name: writing-llm-flows
description: Use when writing or changing code that calls an LLM in this repo — LangChain, LangGraph, StateGraph, ChatXAI, ToolNode, StructuredTool, response_format, or a prompt YAML under airflow/modules/prompts/. Covers 호출–교정 그래프 모양, 툴 정의, 프롬프트를 코드에서 분리하는 규칙, PROMPT_VERSION 잠금. Also use when a LangSmith trace shows an unnamed ChatOpenAI run, when a tool error is being swallowed as "no result", or when editing a prompt sentence.
---

# LLM 흐름 작성

**LLM을 부르는 코드는 Pydantic, LangChain, LangGraph 위에서만 쓴다.** 세 층의 역할이 겹치지 않는다.

기준 구현: `airflow/modules/llm.py`(모델 정의·오류 분류)와 `airflow/modules/assessment.py`(그래프).

## 층 셋이 하는 일

| 층 | 맡는 것 | 하면 안 되는 것 |
| --- | --- | --- |
| **LangChain** | 모델 호출. `BaseChatModel`(`langchain_xai.ChatXAI` 등), `SystemMessage`·`HumanMessage`·`AIMessage` | HTTP를 직접 치는 것. 하면 LangSmith 추적이 끊기고 툴 왕복을 손으로 짜야 한다 |
| **LangGraph** | 흐름 제어. 재시도·교정 재요청·분기·팬아웃(`Send`)을 노드와 엣지로 | `if`·`for`로 흩어 놓는 것. 노드 이름이 트레이스에 남는 것이 이 규칙의 목적이다 |
| **Pydantic** | 데이터 모양. 설정·모델 응답·노드가 주고받는 결과 | `dataclass`나 맨 dict |

## 그래프는 예외 없이 소유한다

**모델을 부르는 흐름은 컴파일된 그래프를 갖는다. 호출이 하나뿐이어도 그렇다.**
교정 재요청이 붙는 순간 분기가 생기고, 그것을 `if`로 쓰면 트레이스에 이름 없는
`ChatOpenAI` 호출만 남는다. `causal`이 실제로 그 상태였다(LangSmith run 이름이
`ChatOpenAI`, `tags` 빈 목록). **`with_config`로 모델 호출마다 이름을 붙이고 있으면
그래프가 없다는 신호다** — 이름은 그래프 실행 하나에만 붙인다.

```python
graph.invoke(state, config={"run_name": ..., "tags": [...], "metadata": {...}})
```

`metadata`에는 그 실행을 특정하는 값(기준 주, 프롬프트 판)을 넣고 **자격 증명은 넣지
않는다** — 상태와 config는 트레이스 입력으로 나간다.

## 호출–교정: 모양이 하나다

저장소의 다섯이 글자 그대로 같다 — `assessment`·`briefing/disclosure_picks`·
`briefing/picks`·`expectation/extraction`·`causal/generation`. **새 흐름은 이것을 베낀다.**

```python
graph.add_node("call", self._call)
graph.add_node("repair", self._repair)
graph.add_edge(START, "call")
graph.add_conditional_edges("call", self._next, {"repair": "repair", END: END})
graph.add_edge("repair", "call")
```

- `_call`이 묻고 **검증까지 한다.** 쓸 것이 없으면 빈 결과를 상태에 남긴다.
- `_next`가 `attempts == 0`일 때만 `repair`로 보낸다 — **교정은 한 번뿐이고 재시도는
  Airflow가 한다.**

**축이 둘 더 있고 겹쳐 쓸 수 있다.**

- **툴이 붙으면 앞에 두 노드가 는다**(`thesis/generation`·`thesis/outcomes`):
  `investigate` → 조건부 `tools` → `answer` → 조건부 `repair`. 뒤쪽 둘은 위와 같고
  `ToolNode`가 `tools`에 들어간다.
- **팬아웃**은 바깥 그래프가 `Send`로 항목마다 안쪽 그래프를 부르는 것이다(`assessment`).

상태는 `TypedDict`로 선언하고 병합이 필요한 칸에 리듀서(`Annotated[list, operator.add]`)를 단다.

## 툴

- **`StructuredTool`로 정의하고 `ToolNode`가 돌린다.** 인자는 Pydantic 모델이고 JSON Schema는
  `args_schema`에서 뽑는다. `{"type": "function", "function": {...}}` dict를 손으로 쓰지
  않는다 — 그건 제공처 wire format이라 이름·타입이 실제 함수와 어긋나도 아무도 못 잡는다.
- **툴이 연결·기준 시각·레지스트리 같은 상태를 봐야 하면** 모듈 수준 `@tool` 대신
  **바인드된 메서드**를 감싼다: `StructuredTool.from_function(func=self._tool_x, args_schema=XArgs)`.
  기준 구현은 `airflow/modules/thesis/toolbox.py`의 `ThesisToolbox._build_tools`.
- **툴 실행 루프를 손으로 짜지 않는다.** `ToolNode`가 `tool_call_id`마다 `ToolMessage`
  하나를 보장한다. 직접 짜면 그 보장이 우리 책임이 되고, 빠지거나 둘이면 제공처가 다음
  요청을 거절한다.
- **상태의 `messages`에는 `add_messages` 리듀서를 단다.** 노드는 새로 생긴 메시지만
  돌려주고 병합은 리듀서가 한다 — `ToolNode`가 그 형태로 반환하므로 맞출 쪽은 우리다.
- **툴 상한은 코드 상수로 강제하고 그 값을 `Field(description=...)`에 f-string으로 싣는다.**
  두 곳에 숫자를 적으면 반드시 어긋난다.

### `handle_tool_errors`는 반드시 타입을 준다

**기본값(`True`)은 모든 예외를 `ToolMessage`로 바꿔서 DB 연결 끊김을 "결과 없음"으로
위장한다.** 모델은 그것을 "그 창에 데이터가 없었다"로 읽고 태스크는 성공으로 끝난다.

```python
ToolNode(tools, handle_tool_errors=(ToolLimitExceeded,))
```

모델이 **고쳐 부를 수 있는 것**(상한 초과, 모르는 인자)만 타입으로 지정하고 나머지는
올려서 태스크를 죽인다.

### 툴 SQL

- **툴을 늘릴 때 조회 SQL은 새 파일로 만든다.** 브리핑 등 기존 쿼리를 재사용하지 않는다 —
  브리핑은 지금까지를 보고 추론 툴은 기준 시각까지만 본다. 기존 쿼리에 상한을 얹으면 그쪽이
  안 쓰는 파라미터를 매번 넘겨야 하고, 한쪽을 고칠 때 다른 쪽이 조용히 따라 바뀐다.
- **새 툴 SQL은 운영 DB에 읽기 전용으로 한 번 돌려 보고 넣는다.** 테스트는 가짜 연결을
  쓰므로 컬럼 이름과 조인 조건이 틀려도 통과한다. 2026-08-21에 이 확인이 결함 둘을 잡았다.

## 모델 선택과 자격 증명

- **어떤 모델을 쓸지는 코드가 정한다.** 모델 정의는 `airflow/modules/llm.py`에 LangChain
  문법 그대로 모아 두고(`document_model()`·`thesis_model()`·`causal_model()` 등) 바꿀 때 그
  함수를 고친다. `base_url`·모델명을 환경변수로 빼서 제공처를 갈아 끼우지 않는다 —
  LangChain은 제공처마다 클래스와 인자가 달라 문자열 설정 몇 개로 흉내 내면 어느 쪽도
  제대로 못 쓴다.
- **API 키만 환경에서 오고, 그것도 우리가 읽지 않는다.** LangChain 클래스가 자기
  이름(`XAI_API_KEY` 등)으로 읽는다. 키를 우리 설정 객체에 담으면 로그와 예외에 실릴
  자리만 늘어난다.
- **API 키를 그래프 상태에 넣지 않는다.** `SecretStr`을 담은 설정 객체는 생성자로만 넘긴다.
- **재시도는 Airflow가 한다.** 모델 클라이언트는 `max_retries=0`이다. SDK가 먼저 재시도하면
  태스크 타임아웃 안에서 몇 번을 불렀는지 로그와 트레이스가 어긋난다.
- 제공처 예외는 한 곳에서 우리 종류로 바꾼다. 재시도할 값어치가 있는 것(`ConnectionError`)과
  없는 것(`LlmError`)을 가르는 판단은 DAG가 한다. **중간 층은 예외를 통과시킨다** —
  문자열로 뭉개면 DAG가 판단할 것을 잃는다.

## 그 밖

- **흐름은 클래스로 묶는다**(상태가 컴파일된 그래프다). `DocumentAssessor`·`AssessmentBatch`처럼
  그래프를 소유한 클래스가 갖고, 그래프는 생성자에서 한 번 `compile()`한다. 프롬프트 조립과
  파싱처럼 상태가 필요 없는 것은 같은 클래스의 `@staticmethod`다.
- **응답 스키마는 Pydantic 모델에서 뽑아 `response_format`으로 강제하고**(`modules/schema.py`),
  강제가 안 되는 제공처를 위해 **검증을 그대로 남긴다.**
- **모델에게 주는 시각은 표시 시간대로 준다.** 저장·조회는 UTC지만 프롬프트에 UTC ISO를
  그대로 실으면 모델이 "오늘"을 하루 어긋나게 읽는다(장전 기준 KST 08:35 = UTC 전날 23:35).
  `thesis.kst_label`과 `briefing/documents.pick_input`의 `as_of_kst`가 그 자리다. 섞어서 줄
  수밖에 없으면 **어느 칸이 어느 시간대인지 프롬프트가 직접 알린다.**
- **체크포인터·persistence는 붙이지 않는다.** 재실행 단위는 Airflow 태스크다.
- **추적은 `LANGSMITH_*` 환경변수로 켠다.** 코드에 추적 호출을 심지 않는다.
  **켜면 프롬프트와 원문이 외부로 나간다는 사실을 문서에 남긴다.**

---

# 프롬프트는 코드가 아니다

**모델에게 주는 문장은 `airflow/modules/prompts/<이름>.yaml`에 둔다.** 파이썬 파일 안의 긴
문자열로 두지 않는다. 기준 구현은 `airflow/modules/prompt.py`와
`modules/prompts/disclosure_picks.yaml`, 그것을 쓰는 `modules/briefing/disclosure_picks.py`다.

**왜 나누나.** 문장은 흐름보다 훨씬 자주 바뀐다. 한 파일에 두면 문장만 고친 변경도 코드
diff가 되고 리뷰하는 사람이 로직 변경과 표현 변경을 눈으로 갈라야 한다. `sql/`을 파이썬
문자열로 두지 않는 것과 같은 이유다.

| 규칙 | 이유 |
| --- | --- |
| 자리는 `modules/prompts/` | 컨테이너가 `dags`·`modules`·`utility`·`sql`·`plugins`·`config`만 마운트하고 `config/`는 `.gitignore`다. 남는 곳이 `modules/`이고 쓰는 코드가 거기 있다 |
| 치환은 `string.Template`(`$이름`). **`str.format`을 쓰지 않는다** | 프롬프트에 출력 예시로 `{"picks": [...]}` 같은 JSON이 들어가는데 `format`은 그 중괄호를 자리표시자로 읽고 죽는다 |
| **빠진 값은 실패다.** `safe_substitute`를 쓰지 않는다 | 자리표시자가 그대로 모델에게 나가는 것보다 태스크가 죽는 편이 낫다 |
| 숫자 상한은 YAML에 적지 않는다 | `MAX_TOOL_CALLS`·`MAX_REASON_CHARS`는 코드 상수가 원본이고 자리표시자로 들어간다 |
| 파일은 **import 시점에** 읽고 검증한다 | 칸이 빠지거나 오타가 나면 DagBag 단계에서 죽는다. 실행 중에 프롬프트가 비는 것보다 낫다 |
| 여러 흐름이 함께 쓰는 조각은 `modules/prompts/fragments/`(`read_fragments`) | 위쪽 파일 하나가 흐름 하나라는 규칙을 지키려고 자리를 나눈다 |

**프롬프트는 전부 옮겼다**(2026-08-27). 남은 파이썬 프롬프트는 없다.

## 판(版) 잠금 — 문장을 고칠 때 가장 중요한 규칙

**판이 붙는 프롬프트는 파일 해시로 판과 함께 잠근다**(`tests/modules/test_prompt_versions.py`).
문장이 파일로 나가면 코드를 안 건드리고 고칠 수 있는데 판은 코드에 있다. 판을 안 올리고
문장만 바꾸면 채점·판정이 서로 다른 프롬프트의 결과를 한 판으로 섞는다.

**문장을 고쳤으면 판을 올리고 해시를 같은 커밋에서 갱신한다.**

- 판이 없는 파일은 그 표의 `UNVERSIONED`에 적어 둔다 — 새 파일이 어느 쪽인지 안 밝히고
  들어오는 것을 테스트가 잡는다.
- 판을 세는 상수(`thesis.domain.PROMPT_VERSION` 등)는 **코드에 그대로 둔다.** 채점과
  이어져 있어 코드가 원본이다.
- **`fragments/`는 그 가드 밖이다** — 조각을 고치면 그것을 끼워 쓰는 흐름들의 문장이 함께
  바뀌는데 해시는 각 흐름의 파일 내용이라 안 깨진다. **그 흐름들의 판을 손으로 함께 올린다.**

## 옮기지 않는 것 셋

1. **툴 인자 설명**(`Field(description=...)`) — 상한 값을 f-string으로 싣는 것이 위 규칙이고
   Pydantic 모델 선언과 한 몸이라, 떼면 스키마와 설명이 두 파일로 갈린다.
2. **오류 메시지·로그 문자열** — 모델에게 가지 않는다.
3. **`apps/` 트리** — 지금 LLM을 부르는 상주 서비스가 없다. 생기면 같은 규칙을 쓰되
   `apps/`는 `config.yaml`을 읽으므로 자리는 다시 정한다.

## 프롬프트를 DB나 환경변수로 빼지 않는다

DB에 두면 화면에서 고칠 수 있지만 문장이 **코드 리뷰를 안 거친다** — 한 줄이 채점 판을
가르는데 리뷰가 없는 것은 이 저장소의 다른 어떤 규칙과도 안 맞는다. 환경변수는 `llm.py`가
모델 선택을 안 빼는 것과 같은 이유다 — 어느 문장으로 돌았는지가 배포 환경에 흩어지면
재현이 안 된다. **파일이면 diff가 남고 되돌릴 수 있다.**

## 흔한 실수

| 실수 | 무엇이 터지나 |
| --- | --- |
| 호출이 하나라 그래프 없이 `if`로 교정 | 트레이스에 이름 없는 `ChatOpenAI`만 남아 어디서 몇 번 불렀는지 사라진다 |
| `handle_tool_errors=True`(기본값) | DB 연결 끊김이 "결과 없음"이 되고 태스크가 성공한다 |
| 모델 클라이언트에 `max_retries` 기본값 | SDK가 먼저 재시도해 로그·트레이스와 실제 호출 수가 어긋난다 |
| 프롬프트 문장만 고치고 판을 안 올림 | 서로 다른 프롬프트의 채점이 한 판으로 섞인다 |
| `str.format`으로 치환 | 출력 예시 JSON의 중괄호에서 죽는다 |
| 상태·config에 키를 넣음 | 트레이스 입력으로 외부에 나간다 |
