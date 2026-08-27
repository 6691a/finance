# 14단계 — React 추론 추적·관계 그래프 웹 화면 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** LLM 실행 원장, 툴 호출 순서·인자·결과, 명시적 판단 이유, 인용 근거, 사후 채점을
한 웹 화면에서 읽고, 추론과 근거의 관계를 실제 노드·엣지 그래프로 탐색한다.

**Architecture:** FastAPI는 읽기 전용 JSON API와 빌드된 정적 파일만 제공한다. 화면은
React·TypeScript SPA이고, 관계 그래프는 Cytoscape.js를 React 컴포넌트 안에서 직접
초기화한다. 개발할 때만 Vite 서버가 `/api`를 FastAPI로 프록시하고, 운영에서는 Node
프로세스 없이 FastAPI가 `frontend/dist`를 같은 origin에서 제공한다. 영구 원본은
13단계의 PostgreSQL 원장이고 LangSmith는 선택적인 전체 메시지 추적이다.

**Tech Stack:** React, TypeScript, Vite, Cytoscape.js, React Router, FastAPI,
SQLAlchemy async, Pydantic, HTML/CSS

**Spec:** [12-api.md](12-api.md), [13-llm-ledger.md](13-llm-ledger.md),
[11-expected-return.md](11-expected-return.md), [4-graph.md](4-graph.md)

## Global Constraints

- 여기서 말하는 React는 **클라이언트 렌더링 SPA**다. Next.js·Remix·React SSR·Jinja2를
  함께 넣지 않는다.
- 그래프는 [Cytoscape.js 공식 패키지](https://js.cytoscape.org/index.html)를 직접 쓴다.
  `react-cytoscapejs` 같은 얇은 래퍼를 추가하지 않는다.
- 서버 상태는 브라우저 기본 `fetch`, 화면 상태는 React 로컬 상태와 URL로 처리한다.
  TanStack Query·Redux·Zustand를 넣지 않는다.
- JSON API 시각은 UTC `Z`다. 화면은 `Intl.DateTimeFormat`으로 `Asia/Seoul`을 표시하며
  원본 시각을 `<time datetime>`에 보존한다.
- 데이터베이스 연결은 12단계의 `read_only: true` 별칭만 사용한다. 재추론·수정·승인
  API를 만들지 않는다.
- 툴 결과, 모델 이유, 문서 제목은 React의 기본 text escaping으로 렌더링한다.
  `dangerouslySetInnerHTML`은 금지한다.
- 모델의 `up_reasoning`·`down_reasoning`·`flat_reasoning`과 검증된 `claims`를
  **명시적 판단 근거**로 표시한다. 숨은 chain-of-thought를 복원하거나 제공한다고 쓰지 않는다.
- 실행 상세의 툴 목록에는 결과 전문을 싣지 않는다. 결과는 호출 하나를 선택했을 때만
  단건 API로 가져온다.
- Brier, 기대 등락률 오차, `verdict`는 서로 다른 평가다. 합친 종합 점수를 만들지 않는다.
- Grafana 서비스·프로비저닝·대시보드·전용 테스트는 제거한다(7절). **화면을 대체해서가
  아니라 Grafana를 더 안 쓰기로 했기 때문이다**(사용자 결정 2026-08-26). 기존 Docker
  volume은 자동 삭제하지 않는다.
- 별도 프런트 상주 서비스, WebSocket, SSE, 인증, 캐시를 만들지 않는다. 사설망 접근 결정은
  12단계를 따른다.

---

- 상위: [README.md](README.md)
- 날짜: 2026-08-26
- 상태: **설계만. 구현 전.** 사용자 검토 뒤 11·13 → 12 → 14 순서로 착수한다.
- 산출물(예정): `frontend/` React 앱, 실행·품질 API 넷, FastAPI 정적 자산 제공,
  React·API 테스트, Grafana 로컬 서비스·대시보드·테스트·문서 제거

## 0. 결정 — FastAPI SSR이 아니라 React CSR

React와 SSR은 엄밀히 반대말이 아니다. React도 SSR할 수 있다. 이 프로젝트에서 선택할 것은
**Jinja2로 완성 HTML을 내는 화면**과 **브라우저에서 상태를 가진 React 화면** 중 하나다.

관계 그래프는 서버가 HTML로 그려 끝낼 수 없다. 노드 선택, pan/zoom, 이웃 강조, 필터,
상세 패널은 브라우저의 DOM·canvas/WebGL과 이벤트 상태를 쓴다. Jinja2 SSR을 택해도 결국
그래프 영역만 별도 JavaScript 앱으로 다시 만들어야 한다. 내부 운영 화면이라 SEO와
검색 엔진용 첫 HTML도 필요 없다. 두 렌더링 방식을 같이 유지할 이유가 없으므로 React CSR
하나로 정한다.

운영 복잡도는 별도 프런트 서버를 두지 않아 줄인다.

```text
개발: browser → Vite :5173 ──/api proxy──→ FastAPI :18000
운영: browser → FastAPI :8000 ──/api──→ PostgreSQL
                              └──/assets, SPA fallback──→ frontend/dist
```

Vite는 정적 배포용 bundle을 만드는 기능을 공식 제공한다
([Vite build guide](https://vite.dev/guide/build)). 운영 컨테이너의 Node는 **빌드 stage에만**
있고 런타임에는 Python 프로세스 하나만 남는다.

### 0.1 그래프 라이브러리

| 후보 | 판단 |
| --- | --- |
| **Cytoscape.js** | 채택. 노드·엣지, 레이아웃, 선택 이벤트, pan/zoom이 한 패키지에 있다. 현재 1홉 관계 그래프에 맞다. |
| Sigma.js | 보류. WebGL로 수천 노드를 그리는 데 강하지만 현재 API는 중심 thesis의 1홉이다([공식 문서](https://www.sigmajs.org/docs/)). |
| React Flow | 미채택. 노드 편집기·워크플로 빌더가 중심이고 이 화면은 읽기 전용 관계 탐색기다([공식 API](https://reactflow.dev/api-reference)). |
| D3·직접 canvas | 미채택. 레이아웃·hit testing·zoom을 직접 조립할 이유가 없다. |

Cytoscape는 framework-agnostic이므로 React wrapper가 필요 없다. `ref`로 container를 받고
`useEffect`에서 instance를 만들며 cleanup에서 `destroy()`한다. 관계 그래프가 1홉을 넘어
수천 노드가 되고 실제 기기에서 pan이 30fps 아래로 떨어질 때만 Cytoscape와 Sigma를 같은
payload로 benchmark한다.

## 1. 화면과 API 경계

### 1.1 클라이언트 라우트

| 경로 | 화면 |
| --- | --- |
| `/` | `/runs`로 이동 |
| `/runs` | 종료 미기록·성공·실패 LLM 실행 목록과 필터 |
| `/runs/:llmRunId` | 실행 요약, 기록된 호출 흐름, 산출물 |
| `/runs/:llmRunId/tool-calls/:seq` | 툴 하나의 인자·결과 전문·오류 |
| `/theses` | 판단 목록과 평가 여부 |
| `/theses/:thesisId` | 판단·명시적 이유·근거·사후 평가 |
| `/theses/:thesisId/graph` | `CITES`·`INFORMED_BY` 관계 그래프 |
| `/quality` | 주 단위 정확도·크기 오차·판정 추이 |

실패 실행에는 thesis가 없으므로 화면 루트는 `/runs`다. `/theses`는 12단계 목록 API를
그대로 쓰는 보조 탐색 경로다.

### 1.2 JSON API

12단계의 API는 그대로 사용한다.

```text
GET /healthz
GET /api/theses
GET /api/theses/{thesis_id}
GET /api/theses/{thesis_id}/graph
```

실행 추적과 품질을 위해 넷을 추가한다.

```text
GET /api/llm-runs?from=&to=&kind=&status=&slot=&limit=&offset=
GET /api/llm-runs/{llm_run_id}
GET /api/llm-runs/{llm_run_id}/tool-calls/{seq}
GET /api/theses/quality?from=&to=&slot=&subject_code=&horizon_days=
```

`/api/theses/quality`는 응답 하나에 **`forecast`와 `narrative` 두 배열**을 담는다
(2.6절). 라우트를 둘로 가르지 않는 이유는 화면이 늘 둘을 함께 그리기 때문이고,
배열을 가르는 이유는 키가 서로 다른 판이기 때문이다.

`/api/theses/quality`는 정적 경로이므로 `/api/theses/{thesis_id}`보다 먼저 등록한다. route
집합 테스트가 실제 요청까지 보내 `quality`가 동적 id의 422로 잡히지 않는지 확인한다.

실행 목록의 `from`·`to`는 원 추론의 `run_date`가 아니라 `started_at`의 KST 실행일을
양끝 포함으로 거른다. T+5 narration의 `run_date`는 과거 원 추론일이라 실행일 필터에 쓰면
오늘 실행한 해설이 빠진다. run 하나가 여러 subject를 다루고 실패·중단 run에는 산출물이
없으므로 run 목록에는 `subject_code` 필터를 두지 않는다. 대상 필터는 `/theses`에서 쓴다.
기본은 KST 오늘까지 14일(`to=오늘`, `from=to-13일`), `limit=50`, `offset=0`이고 limit
상한은 200이다. 정렬은 `started_at DESC, id DESC`다.

`GET /api/llm-runs/{id}`의 `tool_calls`는 `seq`, `round_no`, `tool_call_id`, `tool_name`,
원시 `arguments`, `validated_arguments`, `requested_at`, nullable `duration_ms`,
`result_chars`, `delivered`, `error_kind`, `error`와 상세 URL만 낸다. `result` 전문은 단건 응답에만 있다.
생성 실행은 `produced_theses`, 해설 실행은 `narrated_outcomes`가 붙으며 실패·running 실행은
둘 다 빈 배열일 수 있다.

`GET /api/theses/{id}`는 연결된 `llm_run` 요약과 URL만 내고 같은 대화의 툴 배열을
복제하지 않는다. 대화 하나가 여러 thesis를 만들고 실패 대화는 thesis가 없으므로 실행과
판단의 조회 단위를 섞지 않는다. 이 결정으로 12단계 1.2절의 큰 `tool_calls` 중첩 계약을
구현 전에 실행 단위로 확정했다.

프런트는 Pydantic 모델과 같은 TypeScript type을 손으로 한 번 적는다. OpenAPI client
generator는 넣지 않는다. API 응답을 바꿀 때 backend 응답 테스트와 frontend fixture를
같은 변경에서 고치는 것을 계약으로 삼는다.

## 2. 화면 내용

### 2.1 공통 shell

상단 내비게이션은 실행, 판단, 품질 셋뿐이다. 모든 목록 필터는 URL query string에 둬서
새로고침·뒤로 가기·링크 공유가 같은 화면을 복원한다. 로딩, 빈 결과, 404, 일반 오류는
페이지마다 제각각 만들지 않고 작은 공통 컴포넌트 넷을 쓴다.

### 2.2 실행 목록과 추적 `/runs`, `/runs/:id`

실행 목록은 시작 시각 KST, 대상 `run_date`, kind, 슬롯, 지평, 모델, 프롬프트 판, 시도 번호,
상태, 왕복, 툴 호출 수, 결과 문자 수, 소요, 산출물 수를 보인다. 실행일·kind·status·slot을
필터하고 status는 `running`·`succeeded`·`failed`다. 대상 필터는 두지 않는다. 총건수는
세지 않고 12단계의 `limit + 1` 규칙을 재사용한다.

실행 상세는 다음 순서로 읽는다.

1. 실행 메타데이터와 성공·실패 사유
2. `round_no`로 묶고 `seq`·`requested_at` 순서로 읽는 호출 타임라인과 접근 가능한 표
3. 생성된 thesis 또는 해설된 outcome 링크

타임라인 한 항목에는 툴명, round, `seq`, 요청 시각, 소요, 전달 여부, 성공·오류 종류를
보이고 클릭하면 인자와 결과 상세를 연다. CSS 선은 시간축만 나타내고 tool 사이에 인과
화살표를 그리지 않는다.

13단계가 보장하는 것은 **기록된 툴 호출 순서와 시각**이다. 툴 사이의 숨은 모델 사고를
node로 지어내지 않는다. 같은 `round_no`의 여러 tool call은 한 모델 응답의 sibling이고
병렬일 수 있으므로, 그 안의 `seq`를 인과 순서가 아니라 기록 순서라고 표시한다. 전체 메시지
turn과 LangGraph 내부 span이 필요하면 같은 실행의 LangSmith trace를 개발 중에 본다.

### 2.3 툴 호출 `/runs/:id/tool-calls/:seq`

`arguments`는 모델 요청값, `validated_arguments`는 StructuredTool 검증·기본값 적용 뒤
실제 함수 입력값으로 나란히 표시한다. unknown tool·validation 오류는 후자가 NULL이다.
두 인자와 `result`는 JSON이면 들여쓰기하고, 아니면 원문 그대로 `<pre><code>`에 렌더링한다.
JSON parse 실패는 화면 오류가 아니다. 실패 호출은 `error_kind`와 `error`를 표시하고
`result`와 동시에 보이지 않는다. `delivered = false`인 성공 결과에는 "실행됐지만 모델에게
전달되지 않음"을 붙여 모델이 본 입력으로 오해하지 않게 한다.

이전·다음 `seq`, 실행 상세로 돌아가기만 둔다. 전체 결과 검색·다운로드·복사 버튼은
첫 판에 넣지 않는다.

### 2.4 판단 상세 `/theses/:id`

다음을 한 화면에 둔다.

1. 세 방향 확률과 방향별 기대 등락률
2. `up_reasoning`·`down_reasoning`·`flat_reasoning`
3. 당시 `input_state`
4. 이 판단을 만든 `llm_run` 링크
5. 인용 근거의 kind, rank, direction, mechanism, detail, 원문 URL
6. 지평별 실제 수익률, 실제 방향, Brier, 예측 크기, 크기 오차
7. 사후 `narrative`·`verdict`와 사후 인용 근거
8. 프롬프트에서 본 과거 판단과 관계 그래프 링크

`llm_run_id IS NULL`인 리비전 전 행은 화면 전체를 실패시키지 않고 "실행 원장 도입 전
기록"으로 표시한다.

화면은 정보의 의미를 다음처럼 구분한다.

| 화면 표기 | 원본 | 말할 수 있는 것 |
| --- | --- | --- |
| 입력 상태 | `thesis.input_state` | 모델의 최초 프롬프트에 제공됨 |
| 툴 결과 | `thesis_tool_call.result` + `delivered` | `delivered = true`일 때만 모델 대화에 반환됨 |
| 인용 근거 | `thesis_evidence`·`CITES` | 모델 최종 응답이 명시적으로 선택함 |
| 판단 이유 | 세 `reasoning`과 `mechanism` | 모델이 최종 응답으로 설명함 |

툴 결과에 있었다는 사실만으로 그 값이 결론을 **바꿨다**고 쓰지 않는다. 그런 인과는 같은
입력에서 해당 툴만 가린 ablation 실행을 비교해야 말할 수 있다.

### 2.5 관계 그래프 `/theses/:id/graph`

`GET /api/theses/{id}/graph`의 스키마를 그대로 그린다. 새 그래프 API나 프런트 전용
node model을 만들지 않는다. 그래프의 `CITES`는 원 판단의 인용
(`outcome_horizon_days IS NULL`)만 포함한다. 지평별 사후 인용은 판단 상세에 남기며,
원 판단을 만든 근거 그래프에 섞지 않는다.

| 요소 | 표현 |
| --- | --- |
| 중심 `Thesis` | 가장 큰 원, 대상·날짜·슬롯 표시 |
| 이웃 `Thesis` | 작은 원, 클릭하면 해당 판단 상세로 이동 |
| `Evidence` | 둥근 사각형, kind와 제목 표시 |
| `CITES` | 중심 thesis → evidence, rank·direction으로 색과 label |
| `INFORMED_BY` | thesis → precedent thesis, 방향 화살표 |

기본 layout은 중심 thesis를 root로 한 `breadthfirst`다. 현재 계약이 1홉이라 force layout보다
방향과 중심이 안정적으로 보인다. 사용자가 임의로 node를 옮겨도 서버에 저장하지 않는다.

선택한 node·edge의 properties는 오른쪽 상세 패널에 표시한다. 필터는 node kind와 edge type
둘뿐이고, `fit`·`reset` 버튼을 둔다. graph canvas만으로는 스크린리더가 관계를 읽을 수
없으므로 같은 node·edge를 아래 목록으로도 제공한다. 색 외에 label·선 모양을 함께 쓴다.

API node는 `id`를 갖지만 edge는 갖지 않는다. 원 판단 `CITES`와 precedent pair가 각각
유일하다는 API 계약 아래 프런트가 `type:start:end`로 Cytoscape edge id를 만든다. backend
schema 테스트는 같은 조합이 두 번 나오지 않음을 보장한다.

### 2.6 품질 `/quality`

기본 창은 최근 28일이다. **표를 둘로 나눈다.** 판이 서로 독립으로 움직이기 때문이다.

### 예측 품질

```text
키: (week_start, horizon_days, run_slot, thesis_llm_model, thesis_prompt_version)
값: mean_brier·brier_samples, 균등확률 0.667 통과 여부,
    mean_return_error_pct·mae_return_pct·return_samples,
    mean_tool_calls·mean_tool_result_chars·run_samples
```

키의 모델·판은 **원 thesis를 생성한 실행의 값**이다. 값 넷 다 그 실행이 만든 것이라
키와 짝이 맞는다. tool 평균은 중복 `llm_run_id`를 제거한 succeeded 실행 단위이고,
result 문자는 모델에게 전달된 것(`delivered = true`)만 센다.

### 해설 품질

```text
키: (week_start, horizon_days, narrative_llm_model, narrative_prompt_version)
값: supported·contradicted·unresolved·verdict_samples
```

`verdict`는 **사후 해설 LLM이 내린 판정**이라 만든 판이 다르다. 원 추론이 판 6에서 7로
올라가도 해설은 `2/informed` 그대로일 수 있다. 두 값을 한 행에 놓으면 **"판 7이 이유
지지율도 올렸다"로 읽히는데 그 손잡이는 움직인 적이 없다** — `TUNING.md` 1절의 "한 번에
한 손잡이"를 표가 어기는 자리다. `run_slot`을 키에서 뺀 것은 해설이 슬롯이 아니라
지평으로 갈리기 때문이다.

두 표는 화면 하나에 세로로 놓는다. 서로 결측 조건이 다른 표본 수를 합치지 않고 각 평균
옆에 표시하는 규칙은 둘 다 같다.

첫 판은 접근 가능한 주별 표를 기준 화면으로 하고 정확도 선 그래프와 차트 패키지는 넣지
않는다. 표본이 쌓인 뒤 표보다 추이를 읽는 시간이 실제로 오래 걸리면 같은 응답 위에
표본·지평·slot을 섞지 않는 선 그래프 하나만 추가한다.

이 화면은 모델을 자동 학습시키지 않는다. 정확도를 올리는 루프는 다음이다.

```text
원장·결과 축적
  → model + prompt_version + horizon별 비교
  → 실패 실행·나쁜 Brier·큰 크기 오차의 trace 확인
  → 프롬프트 또는 툴 하나 변경
  → prompt_version 증가
  → 같은 지표와 해당 metric의 충분한 samples로 이전 판과 비교
  → 유지 또는 되돌리기
```

시장 상태가 바뀌므로 단순 누적 평균만 보고 자동 승격하지 않는다. 버전별 표본과 기간을
함께 읽고 승격은 사람이 결정한다. 고정 입력 replay eval이 필요해질 때 13단계가 저장한
툴 snapshot을 재사용하는 별도 단계를 만든다.

## 3. 프런트 구조

```text
frontend/
  package.json
  package-lock.json
  index.html
  tsconfig.json
  vite.config.ts
  src/
    main.tsx
    App.tsx
    api.ts
    types.ts
    graph.ts
    styles.css
    components/
      GraphView.tsx
      AsyncState.tsx
    pages/
      RunsPage.tsx
      RunDetailPage.tsx
      ToolCallPage.tsx
      ThesesPage.tsx
      ThesisDetailPage.tsx
      ThesisGraphPage.tsx
      QualityPage.tsx
```

- `api.ts`는 base URL 없이 상대 `/api`에 `fetch`하고 non-2xx를 `ApiError` 하나로 바꾼다.
- `types.ts`는 API 응답 계약만 가진다. component props와 섞지 않는다.
- `graph.ts`는 API node·edge를 Cytoscape elements로 바꾸는 순수 함수다.
- `GraphView.tsx`는 Cytoscape lifecycle, 선택, fit/reset만 맡는다.
- page가 fetch와 URL filter를 맡는다. 별도 service·hook 계층은 만들지 않는다.
- `styles.css` 하나를 쓰고 CSS framework와 icon package를 넣지 않는다.

### 3.1 Cytoscape lifecycle

`GraphView`는 container가 생겼을 때 instance 하나를 만들고 elements·layout 입력이 바뀌면
해당 instance를 갱신한다. unmount 때 `destroy()`하고 event listener를 정리한다. node
선택은 Cytoscape 내부 상태에만 두지 않고 선택된 id를 React state로 올려 상세 패널과 URL이
같은 값을 보게 한다.

서버 properties를 Cytoscape style selector에 직접 넣지 않는다. `graph.ts`가
`nodeType`·`edgeType`·`direction`처럼 허용된 style key만 만든다. 예상하지 못한 label이나
edge type은 회색 기본 모양으로 보이게 하며 화면을 죽이지 않는다.

## 4. 서버와 배포

FastAPI는 API route를 먼저 등록하고 마지막에 정적 자산과 SPA fallback을 붙인다.
`/api/*`, `/docs`, `/openapi.json`, `/healthz`의 404를 `index.html`로 바꾸면 안 된다.
그 밖의 GET만 `frontend/dist/index.html`로 보낸다.

`dist/index.html`이 없으면 API만 정상 기동하고 UI 경로는 404로 둔다. Vite 개발과 Python
단위 테스트가 매번 frontend build를 선행하지 않게 하기 위해서다. 운영 image 검사는
`dist/index.html`이 실제로 포함됐는지 별도로 실패시킨다.

운영 web 이미지는 multi-stage build다.

1. `node:24-alpine` stage가 `npm ci`와 `npm run build`를 실행한다. Node 24는 문서 작성
   시점의 LTS다([Node.js release table](https://nodejs.org/en/about/previous-releases)).
2. 기존 Python web image가 산출물 `dist`만 `/app/frontend/dist`로 복사한다.
3. Python source는 12단계와 같이 read-only bind mount하고 FastAPI가 정적 파일을 제공한다.

Docker가 `frontend/`를 읽을 수 있도록 local·prod web compose의 `build.context`는 저장소
루트로 두고 `dockerfile`만 각각의 web Dockerfile 경로를 가리킨다. 런타임의 `apps/`
read-only mount 계약은 바꾸지 않는다. 새 루트 `.dockerignore`는 기본적으로 전부 제외하고
`frontend/**`와 local·prod web의 `requirements.txt`만 허용한 뒤 `frontend/node_modules`,
`frontend/dist`, 모든 `.env*`와 key·credential을 다시 제외한다. gitignore 여부와 무관하게
`config.yaml`을 포함한 비밀 파일은 Docker daemon에 보내지 않는다.

프런트 변경은 `build-web`으로 이미지를 다시 만들고, Python만 바뀌면 기존 `deploy-web`
restart 흐름을 따른다. 운영 포트는 12단계의 `8000:8000`, 로컬 API는 `18000:8000`을
유지한다. Vite 개발 포트는 개발자 머신에서만 열고 compose service로 만들지 않는다.

`vite.config.ts`의 dev proxy는 `/api`·`/healthz`만 `http://127.0.0.1:18000`으로 보낸다.
운영은 같은 origin이라 CORS 설정이 필요 없다.

### 4.1 의존성

런타임 프런트 의존성은 넷이다.

```text
react
react-dom
react-router-dom
cytoscape
```

개발 의존성은 TypeScript, Vite React plugin, Vitest, Testing Library, jsdom과 필요한
`@types`만 둔다. Jinja2, React query/state library, chart library, Cytoscape React wrapper,
Playwright는 넣지 않는다.

`package.json`의 `build`는 `tsc --noEmit && vite build`다. Vite bundle만으로는 TypeScript
type 오류를 검사하지 않으므로 두 명령을 한 script에 고정한다. 별도 ESLint는 첫 판에 넣지 않는다.

## 5. 안전성과 접근성

- React text escaping을 유지하고 `dangerouslySetInnerHTML`을 쓰지 않는다.
- SPA 자산은 외부 CDN을 쓰지 않는다. script, CSS, font는 build 산출물에서 같은 origin으로 제공한다.
- SPA `index.html` 응답에만 `default-src 'self'; script-src 'self'; style-src 'self';
  img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none';
  frame-ancestors 'none'` CSP를 단다. CDN을 쓰는 FastAPI 기본 `/docs`에는 이 헤더를 전역
  적용하지 않는다.
- 외부 evidence URL은 `new URL()`로 읽어 `http:`·`https:`만 링크하고 나머지는 text로
  표시한다. 새 탭 링크에는 `rel="noopener noreferrer"`를 단다.
- 색만으로 성공·실패·방향을 구분하지 않는다.
- filter control은 label이 있고, 표에는 caption·thead·scope가 있다.
- graph에는 키보드로 접근할 수 있는 node·edge 목록과 선택 상세를 함께 둔다.
- 500 응답과 화면에는 내부 SQL·stack trace를 싣지 않는다. Sentry에는 기존 방식으로 남긴다.
- 툴 결과가 커도 DOM에 HTML로 해석하지 않는다. 단건 조회 뒤 접힌 `pre` 하나만 렌더링한다.

## 6. 오류와 경계값

| 상황 | 화면 |
| --- | --- |
| API 404 | 해당 리소스가 없다는 페이지와 목록 링크 |
| API 500·network error | 일반 오류와 재시도 버튼. 이전 성공 데이터를 거짓으로 최신처럼 남기지 않음 |
| 실패한 LLM run | 실패 전 툴 기록과 마지막 error, 빈 산출물 |
| `running` run | `종료 미기록`, 시작 후 경과, `finished_at`·전체 소요 `—`. 현재 실행과 중단 잔재를 구분하거나 tool 기록이 완전하다고 주장하지 않음 |
| `llm_run_id IS NULL` | 원장 도입 전 기록 안내 |
| tool result가 JSON 아님 | 원문 text |
| graph node 0개 | 빈 상태. Cytoscape instance를 만들지 않음 |
| 알 수 없는 graph label/type | 회색 기본 요소와 원본 type |
| 품질 표본 0 | 빈 상태와 filter 초기화 |

fetch는 페이지 이동 시 `AbortController`로 취소한다. retry library와 global toast queue는
만들지 않는다.

## 7. Grafana 제거 범위

**Grafana를 더 안 쓰기로 했다**(사용자 결정 2026-08-26). 그래서 14단계 배포와 함께
런타임에서 뺀다.

**이 화면이 대체해서가 아니다.** `compose/local/grafana/dashboards/`의 열여덟 개 중 시장
추론을 보는 것은 하나도 없다 — 국채 곡선 다섯(미국·일본·영국·유로·한국 시장금리), 시세
여섯(지수·지수선물·환율·원자재·크립토·분봉), 그리고 DART 공시, 문서 평가, 투자자 수급,
시장 움직임, 포지셔닝이다. **제거하면 그 화면들은 대체 없이 사라진다.** 안 쓰기로 한
결정의 결과이지 이 단계가 그 자리를 메우는 것이 아니라는 뜻이고, 나중에 그중 하나가
다시 필요해지면 그때 이 화면에 붙이는 것이 아니라 별도로 판단한다.

**지우기 전에 대시보드 JSON 열여덟을 스크래치패드로 백업한다.** 되돌리려면 JSON과
테스트 일곱을 다시 만들어야 하고, git 이력에 남아도 찾아 꺼내는 비용이 백업보다 크다.

### 삭제

- `compose/local/grafana/` 전체 (대시보드 JSON 18, provisioning 2)
- `tests/dashboards/` 전체 (테스트 7)

### 수정

| 대상 | 무엇 |
| --- | --- |
| `compose/local/docker-compose.yaml` | `grafana` service와 named volume |
| `justfile` | 6행 주석의 로컬 스택 설명을 web UI로 |
| 루트 `README.md` | `## Grafana` 절 전체와 대시보드별 설명(약 160행). 그 절이 설명하던 **테이블·DAG 대응**은 각 수집 문서에 이미 있으므로 함께 사라지지 않는다 |
| `docs/collection-map.html` | "최신성은 Grafana에서 확인한다" → Airflow와 web UI |
| `docs/collection/us-macro-indicators.md`·`kis-program-trading.md` | Grafana를 소비자로 적은 줄 |
| `docs/analysis/economic-document-archive-design.md` | "Grafana에서 바로 쓴다" 세 줄 |
| `.claude/CLAUDE.md`(과 `.codex/AGENTS.md`) | 조회 예시의 "Grafana 대시보드의 패널 쿼리도 마찬가지다"를 일반 조회로 |
| `4-graph.md`·`12-api.md` | "`grafana` 옆", "Airflow·Grafana도 그렇게 뜬다" 배포 설명 |

### 지우지 않는 것

- `migrations/versions/e5b2d7a41c93_split_quote_tables_by_kind.py`와
  `tests/migrations/test_quote_split_revision.py`의 `Grafana` — **과거 결정의 이유**를
  설명하는 자리다. 지우면 왜 뷰를 만들었는지가 사라진다.
- `quote_bar`·`quote_daily` 뷰 자체. Grafana를 위해 만들었지만 브리핑 SQL도 읽는다.
- 개발자 머신에 이미 있는 Docker volume. 자동 삭제하지 않는다.

## 8. 구현 작업

### Task 1: 실행·툴·품질 API 계약을 완성한다

**Files:**

- Modify: `apps/api/schemas.py`
- Modify: `apps/api/repository/`
- Modify: `apps/api/routes/`
- Test: `tests/api/test_routes.py`
- Test: `tests/api/test_repository.py`
- Create: `tests/api/test_quality.py`

- [ ] 실행 목록·상세, 툴 단건, 품질 API의 실패 테스트를 쓴다.
- [ ] thesis 상세는 `llm_run` 요약·URL만 내고 tool call은 실행 API에서만 내린다.
- [ ] 툴 결과 전문을 단건 API로 분리한다.
- [ ] raw·validated 인자, round, `delivered`, error kind와 running 종료시각 계약을 구현한다.
- [ ] 주·지평·slot·model·prompt_version 집계를 구현한다.
- [ ] `uv run pytest tests/api -q`를 통과시킨다.

### Task 2: React shell과 FastAPI 정적 제공을 붙인다

**Files:**

- Create: `frontend/package.json`, `frontend/package-lock.json`
- Create: `frontend/index.html`, `frontend/tsconfig.json`, `frontend/vite.config.ts`
- Create: `frontend/src/main.tsx`, `frontend/src/App.tsx`
- Create: `frontend/src/api.ts`, `frontend/src/types.ts`, `frontend/src/styles.css`
- Create: `frontend/src/components/AsyncState.tsx`
- Test: `frontend/src/App.test.tsx`, `frontend/src/api.test.ts`
- Modify: `apps/api/app.py`
- Test: `tests/api/test_spa.py`

- [ ] client route와 API 오류 상태의 component 테스트를 먼저 쓴다.
- [ ] Vite·React Router shell과 공통 fetch를 최소 구현한다.
- [ ] FastAPI의 asset mount와 API를 침범하지 않는 SPA fallback을 구현한다.
- [ ] `npm --prefix frontend test -- --run`, `npm --prefix frontend run build`,
  `uv run pytest tests/api/test_spa.py -q`를 통과시킨다.

### Task 3: 실행·툴·판단 화면을 만든다

**Files:**

- Create: `frontend/src/pages/RunsPage.tsx`
- Create: `frontend/src/pages/RunDetailPage.tsx`
- Create: `frontend/src/pages/ToolCallPage.tsx`
- Create: `frontend/src/pages/ThesesPage.tsx`
- Create: `frontend/src/pages/ThesisDetailPage.tsx`
- Test: `frontend/src/pages/pages.test.tsx`

- [ ] 필터의 URL round-trip과 running·성공·실패 run의 화면 테스트를 쓴다.
- [ ] 목록, 접근 가능한 trace 표, lazy tool detail, 명시적 이유·근거·outcome을 구현한다.
- [ ] 악성 `<script>` 문자열이 text로 보이는 테스트를 추가한다.
- [ ] `npm --prefix frontend test -- --run`을 통과시킨다.

### Task 4: 실제 관계 그래프를 그린다

**Files:**

- Create: `frontend/src/graph.ts`
- Create: `frontend/src/components/GraphView.tsx`
- Create: `frontend/src/pages/ThesisGraphPage.tsx`
- Test: `frontend/src/graph.test.ts`
- Test: `frontend/src/components/GraphView.test.tsx`

- [ ] 12단계 graph payload를 Cytoscape elements로 바꾸는 순수 함수 테스트를 쓴다.
- [ ] Cytoscape를 직접 초기화하고 unmount `destroy()`를 검사한다.
- [ ] `CITES`·`INFORMED_BY`에 `breadthfirst` root, 선택 상세, filter, fit/reset을 구현한다.
- [ ] 같은 node·edge의 접근 가능한 목록을 구현한다.
- [ ] `npm --prefix frontend test -- --run`과 `npm --prefix frontend run build`를 통과시킨 뒤
  실제 브라우저에서 한 그래프를 확인한다.

### Task 5: 품질 화면과 개선 루프를 보이게 한다

**Files:**

- Create: `frontend/src/pages/QualityPage.tsx`
- Test: `frontend/src/pages/QualityPage.test.tsx`

- [ ] 예측 품질 표와 해설 품질 표가 **서로 다른 판을 키로 쓴다는** 테스트를 쓴다 —
  원 추론 판을 바꿔도 해설 표의 행이 갈라지지 않고, 반대도 같다.
- [ ] sample count, Brier, baseline, 크기 오차, verdict가 섞이지 않는 테스트를 쓴다.
- [ ] 날짜·slot·subject·horizon filter와 표 둘을 구현한다.
- [ ] `npm --prefix frontend test -- --run`을 통과시킨다.

### Task 6: web 이미지를 만들고 Grafana를 제거한다

**Files:**

- Modify: `compose/local/api/Dockerfile`, `compose/prod/api/Dockerfile`
- Modify: `compose/local/docker-compose.yaml`, `justfile`
- Create: `.dockerignore`
- Modify: `tests/config/test_api_stack.py`
- Delete: `compose/local/grafana/`, `tests/dashboards/`
- Modify: 7절 "수정" 표의 문서

- [ ] Node 24 build stage와 Python runtime의 `dist` copy를 구현한다.
- [ ] web compose의 build context를 저장소 루트로 바꾸고 Dockerfile 경로를 명시한다.
- [ ] `.dockerignore`가 필요한 frontend·requirements만 허용하고 `config.yaml`·`.env*`·key를
  제외하는 config 테스트를 쓴다.
- [ ] local·prod 이미지가 같은 frontend build를 쓰는지 config 테스트를 쓴다.
- [ ] 대시보드 JSON 열여덟을 스크래치패드로 백업한 뒤 Grafana service·volume·자산·테스트를 제거한다.
- [ ] 7절 "수정" 표의 문서에서 Grafana 참조를 걷어내고 "지우지 않는 것"은 남긴다.
- [ ] compose config, `npm --prefix frontend run build`, web/config 테스트를 통과시킨다.

### Task 7: 전체 흐름을 검증한다

- [ ] `npm --prefix frontend test -- --run`
- [ ] `npm --prefix frontend run build`
- [ ] `uv run ruff check apps/api tests/api tests/config/test_api_stack.py`
- [ ] `uv run pytest tests/api tests/config/test_api_stack.py -q`
- [ ] `uv run pytest tests -q`
- [ ] 브라우저에서 `/runs`, 실패 run, 툴 결과, thesis 상세, 실제 관계 그래프, `/quality`,
  404와 새로고침 SPA fallback을 확인한다.
- [ ] `docs/analysis/market-thesis/12-api.md`와 이 문서의 응답 계약을 일치시킨다.

## 9. 테스트 계약

- API route 집합과 client route 집합을 각각 리터럴로 대조한다.
- graph mapper는 입력을 변경하지 않고 API node id를 보존하며 edge id를 `type:start:end`로 만든다.
- backend graph schema 테스트는 `(type, start, end)`가 응답 안에서 유일한지 확인한다.
- `GraphView`는 instance를 하나만 만들고 unmount에서 `destroy()`한다.
- graph filter 뒤에도 접근 가능한 node·edge 목록이 같은 집합을 보인다.
- tool result JSON pretty print와 평문 fallback을 각각 검사한다.
- raw·validated arguments가 나란히 보이고 `delivered = false`가 별도 경고인지 검사한다.
- 같은 round의 sibling을 한 묶음으로 보이되 `seq` 사이 인과 화살표는 만들지 않는다.
- URL query filter를 새로고침해도 같은 request를 만든다.
- 품질 행은 metric별 sample count를 갖고 `null` metric을 0으로 바꾸지 않는다.
- 예측 품질 표의 키에는 `thesis`의 모델·판만, 해설 품질 표의 키에는 `thesis_outcome`의
  모델·판만 들어간다. 한 응답에서 둘이 섞이지 않는다.
- 같은 `llm_run_id`가 thesis 여럿에 연결돼도 `run_samples`는 한 번만 센다.
- React escaping은 이유·툴 결과·evidence title 각각에 악성 문자열을 넣어 확인한다.
- `javascript:` evidence URL은 anchor가 되지 않는다.
- FastAPI SPA fallback은 `/api/missing`과 `/assets/missing.js`의 404를 `index.html` 200으로
  바꾸지 않는다.
- E2E framework는 첫 판에 넣지 않는다. component·API·build 검사와 브라우저 수동 확인으로
  부족해지는 회귀가 반복될 때 Playwright 한 smoke test를 추가한다.

## 10. 만들지 않는 것

- FastAPI Jinja2 SSR과 React SSR
- 별도 상주 frontend service
- Grafana 패널의 1:1 이식
- 숨은 chain-of-thought 표시
- 툴 결과가 결론을 바꿨다는 자동 인과 점수
- 모델 자동 재학습·자동 prompt 승격
- Brier·크기 오차·verdict의 종합 점수
- graph node·edge 수정과 위치 저장
- 로그인·사용자별 설정
- client cache·global state store
- chart·icon·CSS framework
- Cytoscape React wrapper
- 전체 tool result 선조회

## 11. 완료 조건

- `/runs`에서 종료 미기록·성공·실패 실행을 찾고 기록된 툴 호출의 순서·인자·결과·오류를
  읽을 수 있다.
- thesis 상세에서 당시 입력, 세 방향의 명시적 이유, 인용 근거, 결과와 사후 판정을 읽을 수 있다.
- `/theses/:id/graph`가 `Thesis`·`Evidence` node와 `CITES`·`INFORMED_BY` edge를
  실제 interactive graph로 그린다.
- graph 선택·filter·fit/reset과 접근 가능한 관계 목록이 동작한다.
- `/quality`가 예측 품질과 해설 품질을 **표 둘로** 나눠, 각각 자기 판을 키로 지평별
  표본과 함께 보여 준다.
- 운영에는 Node process·Jinja2·별도 frontend service가 없고 FastAPI가 build 산출물을 제공한다.
- local compose와 저장소에 Grafana runtime·프로비저닝·전용 테스트가 없고, 과거 결정을
  설명하는 마이그레이션 주석만 남는다.
- frontend test/build, web lint/test, 전체 Python test가 통과한다.

## 12. 남은 확인

1. **1홉 그래프 크기는 쟀다**(2026-08-26 운영 DB 읽기 전용, thesis 38행 기준):
   노드 평균 10.7·**최대 18**, 엣지 평균 9.7·**최대 17**. Cytoscape `breadthfirst`로
   충분하고 Sigma를 볼 이유가 없다(0.1절의 발동 조건인 수천 노드와 두 자릿수 차이다).
   툴 결과 크기는 13단계 원장이 배포된 뒤에 잰다 — 아직 저장하는 테이블이 없다.
2. LLM 메시지 turn까지 영구 타임라인에 필요해지면 13단계 원장을 확장한다. 현재 원장의
   tool round 사이 모델 메시지 내용을 UI가 추측하지 않는다.
3. `quality` 기본 28일에 해당 metric의 sample count가 10개 미만이면 기간을 자동 확대하지
   않고 그 metric 옆에 "표본 부족"을 표시한다.
