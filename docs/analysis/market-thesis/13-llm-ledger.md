# 13단계 — LLM 실행 원장: 툴 호출과 결과를 전부 남긴다

- 상위: [README.md](README.md)
- 날짜: 2026-08-26
- 상태: **구현 완료**(2026-08-26). 리비전 `a8c5f207d1e6`은 **운영 반영 전**이고 올릴 창은
  [11-expected-return.md](11-expected-return.md) 7절과 같다. 검증은 `uv run pytest tests -q`와
  `uv run ruff check`.
- 의존: [2-agent.md](2-agent.md)(툴박스와 그래프), [5-followup.md](5-followup.md)(해설 흐름),
  [7-nxt-review.md](7-nxt-review.md)(NXT 리뷰). [12-api.md](12-api.md)가 이 원장을 응답에
  싣는다 — **12보다 먼저 나가는 것이 낫다.**
- 산출물(예정): `thesis_llm_run`·`thesis_tool_call` 테이블과 수기 리비전(+`thesis`·
  `thesis_outcome`에 연결 칸 하나씩), `thesis/toolbox.py`의 기록 래퍼,
  `thesis/common.py`·`thesis/review.py`의 저장 호출, SQL 넷, 테스트

## 0. 왜 — 판단만 남고 과정이 사라진다

지금 남는 것은 **프롬프트에 넣은 관측 상태**와 **모델이 인용한 근거**뿐이다. 그 사이에
있었던 일 — 모델이 어떤 툴을 어떤 인자로 몇 번 불렀고, 무엇이 돌아왔고, 무엇을 보고도
인용하지 않았는지 — 은 실행이 끝나면 사라진다.

목적은 **시간이 쌓인 뒤에 묻는 질문들**이다(사용자 2026-08-26).

- 모델의 **툴 호출 패턴**이 판마다·모델마다 어떻게 달라지나
- 어떤 **툴 결과**를 인용했고 무엇을 보고도 버렸나 — 실제로 판단을 바꿨는지는 별도 ablation
- 툴을 많이 본 실행이 **더 정확했나**(`brier_score`·`return_error_pct`와의 상관)
- 어떤 정보끼리 **같이 나타나나**

**저장 크기는 제약이 아니다**(사용자 명시). 하루 대화 수는 **최대 25**다 — 생성 일곱
(`pre_open` + 장중 넷 + `post_close` + `post_nxt_close`)과 해설 최대 열여덟
(지평 셋 × `NARRATED_SLOTS` 여섯). 실측 실행당 툴 결과 54,555자를 그대로 쓰면
**하루 약 1MB, 연 수백 MB**다. 그 값을 못 남겨서 못 하는 분석이 위 넷 전부다.

### 지금 상태

| 것 | 남나 | 어디 |
| --- | --- | --- |
| 프롬프트의 관측 상태 | ○ | `thesis.input_state` |
| 툴 **왕복** 수 | ○ | `thesis.tool_rounds` — 왕복이지 호출 수가 아니다 |
| 모델이 **인용한** 근거 | ○ | `thesis_evidence` |
| 툴 호출 이름·인자·순서·시각 | ✗ | 없다 |
| 툴 결과 본문 | ✗ | 없다 |
| 인용하지 않은 근거 | ✗ | `ThesisToolbox._registry`(메모리)에 있다가 사라진다 |
| 상한(`ToolLimitExceeded`)에 걸렸는지 | ✗ | Airflow 로그의 `logger.warning`뿐 |
| 실패한 대화 자체 | ✗ | 행이 아예 안 생긴다 |

툴 14개 중 레지스트리에 들어가는 것은 다섯(`recent_documents`, `recent_disclosures`,
`macro_changes`, `us_market_close`, `daily_history`의 신호)이고, 그중에서도 **모델이 인용한
것만** 저장된다. 나머지 아홉(`macro_indicators`, `market_funds`, `stock_investor_flows`,
`short_and_credit`, `analyst_opinions`, `event_surprises`, `market_breadth`,
`market_investor_flows`, `past_theses`)은 결과가 통째로 증발한다.

저장소가 이 결핍을 이미 두 곳에 적어 뒀다 — `ThesisPrecedent` docstring("툴 호출 흔적은
트레이스에만 남아 DB에서 보이지 않는다")과 `ThesisToolbox._charge` docstring("상한에 걸려
근거를 덜 보고 답한 실행과 다 보고 답한 실행이 `thesis` 행에서 구분되지 않는다. Airflow
로그가 유일한 단서다"). 2026-08-26 장중 슬롯이 `21 calls, 54555 chars`로 상한에 걸린 것을
DB에서 볼 수 없었던 것이 정확히 이것이다.

**LangSmith는 답이 아니다.** 추적은 `LANGSMITH_*`가 켜졌을 때만 있고, 보존 기간이 우리
것이 아니며, SQL로 채점과 조인할 수 없다. 위 질문 넷은 전부 조인이다.

## 1. 테이블 둘

### 1.1 `thesis_llm_run` — 대화 한 번

**"대화 하나"가 이 원장의 단위다.** 한 대화가 여러 대상을 한 번에 다루므로
(`ThesisBuilder`: "실행당 대화 하나에 모든 subject를 한 번에") 툴 호출은 추론 한 건이
아니라 대화에 속한다.

| 컬럼 | 타입 | 뜻 |
| --- | --- | --- |
| `kind` | enum | `forecast`·`review`·`nxt_review`·`narration` |
| `run_date` | date | 대화가 대상으로 삼은 세션 날짜(KST). 해설이면 **원 추론일**이다 |
| `run_slot` | enum | 대상 슬롯 |
| `horizon_days` | int, null | 해설만. 나머지는 NULL |
| `as_of_at` | timestamptz | 툴 조회의 기준 시각(UTC) |
| `dag_run_id` | text | 이 대화를 돌린 Airflow 실행 |
| `try_number` | int | 그 태스크의 시도 번호. 재시도가 새 대화라는 사실을 남긴다 |
| `llm_model` | text | 모델 식별자 |
| `prompt_version` | text | 프롬프트 판(해설은 `<판>/<변형>`) |
| `started_at`·`finished_at` | timestamptz | 대화의 시작·끝(UTC). `finished_at`은 running 동안 NULL |
| `status` | enum | `running`·`succeeded`·`failed` |
| `error` | text, null | 실패 사유. `status`가 `failed`일 때만 |
| `tool_rounds` | int | 조사 왕복 수 |
| `tool_calls` | int | 툴 호출 수(거절된 것 포함) |
| `tool_result_chars` | int | 누적 결과 문자 수 |

**자연키를 두지 않는다.** 실패한 대화도 남겨야 하고 재시도는 새 대화라, 같은
`(kind, run_date, run_slot, horizon_days)`에 행이 여럿일 수 있다. 그것이 사실이고
패턴 분석에 필요한 정보다. 이 테이블은 **원장이지 판단이 아니라서** "첫 성공본 불변"이
적용되지 않는다.

`tool_calls`·`tool_result_chars`는 `thesis_tool_call`을 세면 나오지만 칸으로 둔다 —
상관 분석이 매번 집계 서브쿼리를 달지 않게 하려는 것이고, 실패한 대화에서 원장 쓰기가
부분 실패해도 총량은 남는다. `tool_result_chars`는 `delivered = true`인 결과 문자만 더한다.
**상한에 걸렸는지는 칸으로 두지 않는다** — 자식에 `error_kind = 'limit'`가 있으면 그것이다.

⚠️ **이 두 칸은 상한을 재는 카운터와 다른 수다.** `ThesisToolbox`의 예산 카운터는
`MAX_TOOL_CALLS`·`MAX_TOOL_RESULT_CHARS`와 비교되는 값인데 정의가 다르다.

| | 원장 칸 | 툴박스 예산 카운터 |
| --- | --- | --- |
| 호출 수 | 기록된 행 수 — unknown tool과 인자 검증 실패도 **센다** | `_charge()`가 세므로 그 둘은 **안 센다**(함수 진입 전이다) |
| 결과 문자 | `delivered = true`만 | `_body`·`_as_evidence_body`가 반환 직전에 더하므로 **버려진 결과도 센다** |

**그래서 `tool_result_chars`를 `MAX_TOOL_RESULT_CHARS`와 직접 비교하면 안 된다.**
원장 칸은 "모델이 실제로 무엇을 몇 번 봤나"를 답하고, 예산 카운터는 "얼마를 썼나"를
답한다. 4절의 상관 분석에는 앞쪽이 맞고, "상한에 걸렸나"는 `error_kind = 'limit'` 행의
유무로 본다 — 그것이 이 칸들을 상한 판정에 쓰지 않는 이유다.

대화를 호출하기 전에 `running` 행을 먼저 커밋하고 정상·예외 종료 때 finish update를 한다.
프로세스 kill이나 전원 장애는 `finally`도 못 지나므로 `finished_at IS NULL`인 running 행으로
남는다. 이것은 삭제할 찌꺼기가 아니라 "시작했지만 종료를 기록하지 못했다"는 감사 기록이다.
heartbeat가 없으므로 running이 지금 실행 중인지 중간에 끊겼는지는 이 행만으로 구분하지 않는다.
CHECK는 상태를 다음 셋으로만 허용한다: running이면 `finished_at`·`error` 둘 다 NULL,
succeeded면 `finished_at`만 non-NULL, failed면 `finished_at`·`error` 둘 다 non-NULL이다.

### 1.2 `thesis_tool_call` — 그 대화 안의 툴 호출 하나

| 컬럼 | 타입 | 뜻 |
| --- | --- | --- |
| `llm_run_id` | bigint FK → `thesis_llm_run.id` `ON DELETE CASCADE` | |
| `seq` | int | 대화 안의 순서(1부터) |
| `round_no` | int | 몇 번째 tool round의 요청인가(1부터) |
| `tool_call_id` | text | AIMessage 요청과 ToolMessage·실제 함수를 연결하는 제공처 call id |
| `tool_name` | text | `recent_documents` 등 |
| `arguments` | jsonb | `AIMessage.tool_calls`의 모델 요청 인자(StructuredTool 검증 전) |
| `validated_arguments` | jsonb, null | 검증·기본값 적용 뒤 실제 함수에 들어간 인자. 진입 전 거절은 NULL |
| `requested_at` | timestamptz | 모델의 호출 요청을 등록한 시각(UTC) |
| `duration_ms` | int, null | 실제 함수가 돈 시간. 진입 전 거절은 NULL |
| `result_chars` | int | 결과 문자 수 |
| `result` | text, null | **결과 본문 전문.** 성공이면 툴이 돌려준 JSON 문자열이다 |
| `error_kind` | enum, null | `unknown_tool`·`validation`·`limit`·`execution`·`cancelled` |
| `delivered` | bool | 이 결과·오류가 모델 대화에 실제로 돌아갔나. 기본값 없이 명시 |
| `error` | text, null | 거절·실패 사유(`ToolLimitExceeded` 등). 있으면 `result`는 NULL |

- UNIQUE `(llm_run_id, seq)`.
- UNIQUE `(llm_run_id, round_no, tool_call_id)`.
- **`result`는 `text`다.** 툴 반환이 JSON 문자열이지만 실패면 평문이라 `jsonb`로 못 굳힌다.
  저장소의 "원본이 JSON이 아니면 `payload`에 넣지 않는다"(`source_record`)와 같은 판단이다.
  분석은 `WHERE error IS NULL` 뒤에 `result::jsonb`를 쓴다.
- **`result`와 `error`는 배타다.** CHECK로 강제한다 — 둘 다 비면 "돌았는데 아무 것도 없다"가
  되어 조용히 틀린다. `delivered`는 이 배타와 무관한 축이다.
- **모델에게 전달된 오류 본문은 `ToolMessage` 그대로 저장한다.** `handle_tool_errors`가 튜플이면 langgraph가
  `"Error: {repr(예외)}\n Please fix your mistakes."`로 한 겹 감싸므로, 래퍼가 직접 잡은
  예외 문자열과 모양이 다르다. 파싱하지 않는 설계라 문제는 없지만 **어느 쪽을 담을지 정해
  두지 않으면 같은 사건이 두 모양으로 남는다.** 모델이 실제로 읽은 문자열이 그 자리에
  맞으므로 `delivered = true`면 ToolMessage 본문을 담는다. 처리되지 않은 실행 예외처럼
  ToolMessage가 없으면 래퍼가 잡은 예외 문자열과 `delivered = false`를 남긴다.
- `error_kind`는 **`StrEnum` + `Enum(native_enum=False, length=20, values_callable=...)` +
  CHECK**다(저장소 타입 규칙). `error`가 있을 때만 반드시 있고 위 다섯 값만 허용한다.
  오류 문자열을 파싱해 상한·검증 실패를 분류하지 않는다.
- **`delivered = false`가 "돌았지만 모델에게 안 간" 행이다.** 병렬 sibling 하나가 처리되지
  않은 예외를 올리면 `ToolNode`는 나머지 결과를 **버린다.** 그런데 sync 경로가
  `executor.map`이라 tool_call이 전부 먼저 submit돼, **이미 시작된 sibling은 취소되지 않고
  끝까지 돈다**(`with executor`가 `shutdown(wait=True)`다). 그래서 래퍼가 `duration_ms`와
  `result`까지 정상으로 채운 행이 남는다. 정말 실행조차 안 되는 것은 워커가 포화됐을
  때뿐이고(`max_concurrency` 지정, 또는 호출 수 > `min(32, cpu+4)`) 그때가 `cancelled`다.
  둘을 한 칸에 뭉개면 **"모델이 봤나"를 못 읽는다** — 인용 분석이 정확히 그 구분 위에 선다.
  `delivered = false`는 성공 결과에도 붙으므로 `error_kind`가 아니라 별도 칸이어야
  `result`/`error` 배타 CHECK를 깨지 않는다.
- `duration_ms`를 두는 이유는 툴 SQL이 느려지는 것을 나중에 보기 위해서다. 툴 하나가
  대화 전체 시간을 먹으면 `BUILD_TIMEOUT`을 올리기 전에 그 SQL을 본다.
- unknown tool·Pydantic 인자 검증 실패도 행이다. 이 둘은 함수에 진입하지 않으므로
  `validated_arguments`·`duration_ms`가 NULL이고 ToolMessage의 오류가 `error`에 남는다.

### 1.3 연결 칸 둘 — 원장만으로는 조인이 안 된다

원장이 따로 서 있으면 "이 판단이 무엇을 보고 나왔나"를 **시각으로 추정**해야 한다.
그건 재시도가 있는 순간 틀린다. 그래서 FK를 건다.

- `thesis.llm_run_id` → `thesis_llm_run.id`, nullable, `ON DELETE SET NULL`
- `thesis_outcome.narration_run_id` → `thesis_llm_run.id`, nullable, `ON DELETE SET NULL`

nullable인 이유는 리비전 전 행을 채울 수 없어서다(`thesis`는 사후 갱신하지 않는다).
`ON DELETE SET NULL`인 이유는 원장이 판단을 인질로 잡으면 안 되기 때문이다 — 원장을
지워도 추론은 남는다. 반대 방향(`thesis_llm_run` → `thesis`)은 걸지 않는다. 대화 하나가
추론 여럿을 만들고, 실패한 대화는 추론이 없다.

`thesis_outcome`의 채점(순수 함수)에는 LLM이 없으므로 연결 칸이 해설 하나뿐이다.

**산출물이 없는 성공 대화가 있을 수 있다.** `thesis` INSERT가 `ON CONFLICT DO NOTHING`이라,
같은 (날짜·슬롯·대상)에 행이 이미 있으면 새 대화의 `llm_run_id`는 저장되지 않는다. 보통은
`existing_theses` 조기 반환이 대화 자체를 안 열지만(8절 2번) 경쟁 실행에서는 생긴다.
그 대화는 원장에만 남고 어느 추론도 가리키지 않는다 — 버그가 아니라 사실이고,
[14-web-ui.md](14-web-ui.md)의 `produced_theses` 빈 배열이 그것을 그린다.

## 2. 어디서 잡나

### 2.1 툴 호출은 툴박스가 기록한다

함수 래퍼만으로는 부족하다. unknown tool과 Pydantic 인자 오류는 원래 함수에 도달하기 전에
`ToolNode`가 오류 ToolMessage로 바꾸기 때문이다. 그래서 요청 shell과 실제 실행을 두
자리에서 잡는다.

1. 두 그래프의 `_tools`가 직전 `AIMessage.tool_calls`를
   `ThesisToolbox.begin_round()`에 먼저 넘긴다. 여기서 `round_no`·`seq`·`tool_call_id`·
   `tool_name`·원시 `arguments`·`requested_at`을 만든다.
2. `ThesisToolbox._build_tools`의 공통 래퍼가 숨은
   `Annotated[str, InjectedToolCallId]`를 받아 같은 요청 행을 찾는다. 이 인자는 모델에게
   보이는 JSON Schema에는 안 나온다.
3. 래퍼가 StructuredTool 검증 뒤의 실제 kwargs, 시작·끝 시각, 결과 또는 예외를 채운다.
   예외는 기록한 뒤 다시 올린다.
4. `ToolNode`가 돌려준 ToolMessage를 `finish_round()`가 `tool_call_id`로 맞춘다. 함수에
   진입하지 않은 unknown tool·인자 오류도 여기서 `error`가 완성된다. 현재
   `handle_tool_errors`에는 `ToolLimitExceeded`와 `ToolInvocationError`만 넣어, 모델의 인자
   오류는 고쳐 부를 수 있게 하되 DB 예외는 계속 태스크를 실패시킨다.

   ⚠️ **`ToolInvocationError`는 langgraph의 공개 API가 아니다.** `langgraph/prebuilt/__init__.py`가
   export하지 않아 `langgraph.prebuilt.tool_node`에서만 import되고 `__all__`에도 없다
   (1.2.2·1.2.11 실측). 동작은 확인했지만 **마이너 판올림에서 움직일 수 있다는 것을 알고
   쓴다.** 대안 둘 — ① 그 예외를 안 넣고 인자 검증 실패도 태스크 실패로 둔다(모델이 고쳐
   부를 기회를 잃는다), ② 상위 `ToolException`으로 잡는다(툴이 던지는 **모든**
   `ToolException`을 함께 삼켜 규칙 위반이 된다). 지금은 ①·②보다 이쪽이 낫다는 판단이고,
   판올림 때 이 줄을 먼저 본다.

`_charge`의 `ToolLimitExceeded`는 함수에 진입한 뒤의 거절이라 래퍼가 실제 인자와 예외를
남기고 ToolNode가 오류 ToolMessage로 바꾼다. DB 오류는 래퍼가 남긴 뒤 그대로 올라가 태스크를
죽인다.

**그때 남는 sibling은 두 종류다**(1.2절 `delivered`).

- 이미 시작된 sibling은 **취소되지 않고 끝까지 돈다.** `executor.map`이 tool_call을 전부 먼저
  submit하고 `with executor`가 `shutdown(wait=True)`라 기다린다. 래퍼가 `duration_ms`·`result`를
  정상으로 채운 뒤 `finish_round`가 `delivered = false`로 닫는다. **오류가 아니다** —
  결과는 진짜이고 모델만 못 봤다.
- 아직 시작 안 한 sibling은 `future.cancel()`로 실행되지 않는다. 이건 워커가 포화됐을
  때뿐이다(`max_concurrency` 지정, 또는 호출 수 > `min(32, cpu+4)`). 열린 요청 shell을
  `error_kind = 'cancelled'`로 닫는다.

기본 설정에 호출 서너 개면 **거의 항상 앞쪽**이다. async 경로(`asyncio.gather`)는 취소조차
하지 않아 언제나 앞쪽이다.

오류 종류는 문자열을 파싱하지 않는다. 요청한 이름이 등록 툴에 없으면 `unknown_tool`,
등록 툴인데 래퍼 진입 없이 오류 ToolMessage가 왔으면 `validation`, 래퍼가 본
`ToolLimitExceeded`는 `limit`, 그 밖의 함수 예외는 `execution`, 실행조차 못 한 sibling은
`cancelled`다.

**툴은 문자열만 반환한다 — 불변으로 둔다.** 원장이 `tool_call_id`로 요청과 결과를 잇는데,
툴 함수가 `ToolMessage`를 **직접** 반환하면 `_normalize_tool_response`가 그 안의
`tool_call_id`를 덮어쓰지 않아 조인이 조용히 깨진다(실측). 지금 툴 14개는 전부 문자열을
돌려주므로 안전하고, 원장이 그 사실에 기대게 되므로 여기 적어 둔다.

**공통 래퍼가 `**kwargs`인 것은 부수 효과가 하나 더 있다.** `StructuredTool.from_function`은
`args_schema`와 함수 시그니처를 **대조하지 않아서**, 스키마에만 있고 함수에 없는 인자는
호출 시 `TypeError`다. 그것은 `ValidationError`가 아니라 `ToolInvocationError`로 감싸이지도
않아 **그대로 태스크를 죽인다.** `**kwargs` 래퍼가 그 실패 모드를 구조적으로 없앤다 —
개별 시그니처로 되돌리지 않는다.

기록은 툴박스의 리스트에 쌓고 `tool_calls`와 `round_count` 프로퍼티로 낸다.
**툴박스는 DB에 쓰지 않는다.** 읽기 전용 툴 셋이라는 성격을 유지하고, 저장 시점은
부르는 쪽이 정한다.

### 2.2 대화 행은 흐름이 쓴다

- 생성(`forecast`·`review`·`nxt_review`) — `thesis.common.ThesisRun.build_and_store`.
  세 DAG이 전부 이 메서드를 지난다.
- 해설(`narration`) — `thesis/review.py`의 (지평, 슬롯) 루프. 반복마다 툴박스와
  `FollowupNarrator`를 새로 만드는 자리가 곧 대화 하나다.

그래프 호출 전에 `start_llm_run`으로 running 행을 커밋한다. 그 뒤를
**`try`/`except`/`finally`로 감싸** 정상 종료는 succeeded, Python 예외는 failed로 finish하고
툴박스의 call 목록을 함께 쓴다. 실패한 대화가 안 남으면 "모델이 상한에 걸려 죽은 날"이
원장에서 사라져 패턴 분석이 성공한 실행만 보게 된다 — 그게 지금 상태다.

실패하면 LangGraph final state를 못 받을 수 있으므로 finish의 `tool_rounds`는 state가 아니라
`toolbox.round_count`를 쓴다. SIGKILL·전원 장애처럼 finally 자체가 못 돈 경우에는 처음
커밋한 running 행만 남고 메모리의 call detail은 보장하지 않는다.

**원장 쓰기는 판단 저장과 같은 트랜잭션이 아니다.** 원장이 못 써졌다고 추론을 버리면
안 되고, 반대로 추론 저장이 실패해도 "무엇을 봤나"는 남아야 한다. running 원장을 먼저
커밋하고 `llm_run_id`를 받아, 대화 finish 뒤 `store_theses`에 넘긴다.

## 3. 세 흐름의 대화 단위

| `kind` | 대화 하나의 범위 | `run_date` | `horizon_days` |
| --- | --- | --- | --- |
| `forecast` | (세션 날짜, 슬롯)의 모든 대상 | 그 세션 | NULL |
| `review` | 장후 리뷰의 모든 대상 | 그 세션 | NULL |
| `nxt_review` | 애프터마켓 리뷰의 모든 대상 | 그 세션 | NULL |
| `narration` | (원 추론일, 지평, 슬롯)의 모든 대상 | **원 추론일** | 1·3·5 |

해설이 지평마다 갈리는 이유는 `FollowupNarrator` docstring에 있다 — "툴 조회의 기준 시각이
지평마다 달라 한 대화에 섞을 수 없다". `run_date`를 실행일이 아니라 원 추론일로 두는 것은
`thesis`와 같은 축으로 조인하기 위해서다. 실행일은 `as_of_at`과 `dag_run_id`가 말한다.

`kind`를 슬롯에서 유도하지 않고 컬럼으로 두는 이유: 같은 `post_close` 슬롯에 생성 대화와
해설 대화가 둘 다 있다. 슬롯만으로는 못 가른다.

## 4. 이 원장으로 답하는 질문

목적이 분석이므로 무엇을 어떻게 읽는지를 여기 적어 둔다. 집계 테이블은 만들지 않는다 —
전부 조회 한 방이다.

**아래 전부 `status = 'succeeded'`만 센다.** `running`은 종료 여부를 모르고
`tool_calls`가 실제보다 작을 수 있어, 섞으면 상관을 아래로 끌어당긴다. `failed`를 따로 보는 것은
"무엇이 죽었나"를 물을 때이고 정확도 상관과는 다른 질문이다.

- **툴 호출 패턴의 변화** — `tool_name`별 호출 수를 `kind`·`prompt_version`·`llm_model`·주
  단위로 센다. **`kind`를 반드시 건다** — 생성 대화와 해설 대화는 프롬프트가 달라 부르는
  툴도 다르고, `prompt_version`의 판 번호 체계도 다르다(해설은 `<판>/<변형>`). 판을 올린 뒤 모델이 안 부르게 된 툴이 있으면 프롬프트가 그것을 가린 것이다.
- **보고도 안 쓴 것** — `thesis_tool_call.delivered = true`인 `result`에서 나온 `ref` 집합과 `thesis_evidence`의
  `evidence_ref` 집합의 차. 어떤 종류가 늘 버려지는지가 툴을 줄일 근거다.
- **많이 본 실행이 더 정확했나** — `thesis_llm_run.tool_calls`(또는 `tool_result_chars`)와
  `thesis_outcome.brier_score`·`return_error_pct`의 상관. `thesis.llm_run_id`가 그 조인을
  가능하게 하는 유일한 칸이다.
- **상한이 판단을 깎았나** — `error_kind = 'limit'`인 대화가 만든 추론의 Brier를
  나머지와 비교한다. 지금은 이 구분 자체가 DB에 없다.
- **어떤 정보가 같이 나타나나** — 한 대화 안의 `tool_name`·`arguments` 조합과 결과의
  동시 출현. 툴을 묶을지(`TUNING.md` 5절의 "툴 그룹") 정하는 재료다.

위 항목은 인용·상관·동시 출현을 답한다. 특정 결과가 결론을 **바꿨다**는 인과는 이 원장
하나로 답하지 않는다. 같은 입력에서 툴 하나만 가린 ablation 실행이 있어야 한다.

## 5. 만들지 않는 것

- **LLM 메시지 전문(프롬프트·응답 원문)** — 그 자리는 LangSmith다. 저장소 규칙이
  "추적은 `LANGSMITH_*`로 켠다. 코드에 추적 호출을 심지 않는다"이고, 프롬프트는
  `input_state` + `prompt_version`으로 재구성된다. 툴 층은 재구성이 **불가능**해서
  여기 남기는 것이다 — 그 비대칭이 이 단계의 경계다.
- **토큰 사용량·비용** — 제공처마다 필드가 다르고 LangChain 응답 메타데이터에 실려 오는
  모양이 갈린다. 비용을 물어야 할 때 별도로 본다.
- **집계 테이블·대시보드** — 이 단계는 원본만 남긴다. 조회 API와 화면은
  [14-web-ui.md](14-web-ui.md)가 같은 테이블을 직접 읽는다.
- **평가·추출 흐름(`DocumentAssessor`·`ExpectationExtractor`)** — 툴을 안 쓴다. 이 원장은
  툴 호출을 남기려는 것이라 대상이 아니다.
- **원장 보존 기간 정책** — 크기가 제약이 아니라는 것이 이 단계의 전제다. 필요해지면
  `result`만 비우는 것이 파티션보다 먼저다.

## 6. 12단계와의 관계

[12-api.md](12-api.md)의 상세 응답에는 `llm_run` 요약과 id가 붙는다. 툴 목록·단건 결과는
[14-web-ui.md](14-web-ui.md)의 `/api/llm-runs`가 실행 단위로 제공한다. 대화 하나가 여러
thesis를 만들기 때문에 같은 호출 배열을 thesis마다 복제하지 않는다.

**13이 12보다 먼저 나가는 것이 낫다.** 12를 먼저 만들면 상세 응답 모델을 곧 다시 고친다.

## 7. 산출물

| 파일 | 무엇 |
| --- | --- |
| `apps/models/analysis/thesis.py` | `ThesisLlmRun`·`ThesisToolCall`과 enum 셋(`LlmRunKind`·`LlmRunStatus`·`ToolCallErrorKind`), `Thesis.llm_run_id`·`ThesisOutcome.narration_run_id` |
| `migrations/versions/<신규>.py` | 수기 리비전 하나. 11단계 리비전 뒤에 얹는다. 올릴 창은 [11-expected-return.md](11-expected-return.md) 7절과 같다 — `thesis`·`thesis_outcome`에 FK 칸을 더하므로 그쪽도 잠근다 |
| `airflow/modules/thesis/domain.py` | `LlmRunKind`·`LlmRunStatus`·`ToolCallErrorKind`(Airflow 쪽 vocabulary 복제) |
| `airflow/modules/thesis/toolbox.py` | `begin_round`·`finish_round`, `_record` 래퍼, `tool_calls`·`round_count`, `ToolCallRecord` 모델 |
| `airflow/modules/thesis/store.py` | `start_llm_run`·`finish_llm_run`, `store_theses`에 `llm_run_id` |
| `airflow/modules/thesis/common.py` | `build_and_store`가 대화를 열고 닫는다(`finally` 포함) |
| `airflow/modules/thesis/review.py` | 해설 루프가 대화를 열고 닫는다 |
| `airflow/sql/postgres/thesis_llm_run/{insert,update_finish}.sql`, `thesis_tool_call/insert.sql` | |
| `tests/models/test_analysis_models.py`·`tests/migrations/test_thesis_schema.py`·`tests/modules/test_thesis_pipeline.py` | 테이블·CHECK와 성공·unknown tool·인자 오류·상한·DB 예외·running 행을 기록하는지 |

## 8. 남은 확인 (spike)

1. **running 원장을 먼저 커밋하는 것이 재시도에서 불필요한 행을 만드는지.** `build_and_store`가 이미
   저장된 추론을 보고 조기 반환하는 경로가 있다(`existing_theses`). 그 경로에서는 대화가
   아예 없으므로 원장도 열지 않아야 한다.

### 확인 끝 (2026-08-26 운영 DB 읽기 전용 실측 포함)

- **`try_number`는 DAG을 하나도 안 고쳐도 된다.** 모듈 진입점이 이미 `get_current_context()`를
  부르고 거기서 `dag_run_id`를 꺼낸다(`thesis.forecast.build`, `thesis.review`의 진입점 셋,
  `thesis.intraday`, `thesis.nxt_review`). **같은 자리에서 `context["ti"].try_number`를 함께
  꺼내** `run(...)`·`build_and_store(...)`에 넘기면 끝이다. 재시도 대화가 서로 구분돼야
  한다는 것은 사용자 결정(2026-08-26)이고, `dag_run_id`는 재시도에도 같아서 이 칸이
  없으면 구분할 방법이 없다.
- **`result` 전문 저장이 만드는 중복은 작다.** `document.body`가 **전부 비어 있다**
  (2,663행 평균 0자, 최대 0자 — 수집이 `metadata_only`다). 툴이 싣는 것은 제목 42자와
  요약 271자뿐이라 문서 하나당 약 313자다. `recent_documents` 한 번이 12,793자였던
  실측(`TUNING.md` 5절)과 맞는다. 중복 자체는 의도다 — `document`는 upsert로 덮어써서
  그때 값을 복원할 수 없고, 원장의 `result`가 그 시점 스냅샷의 유일한 사본이 된다.
- **지금 테이블 크기**(2026-08-26): `document` 6.5MB, `thesis_evidence` 272kB,
  `thesis` 176kB, `thesis_outcome` 88kB, `thesis_precedent` 40kB. 원장이 하루 ~1MB로
  자라면 몇 달 만에 이 표에서 가장 큰 테이블이 된다. 크기가 제약이 아니라는 전제는
  그대로지만 그 사실은 알고 시작한다.
- **생성 흐름 넷이 전부 `ThesisRun.build_and_store`를 지난다**(2026-08-26 확인).
  `thesis/forecast.py`·`thesis/review.py`·`thesis/intraday.py`·`thesis/nxt_review.py`가
  각각 `self._run.build_and_store(...)`를 부른다. 계측 자리는 정말 하나다.
- 모델 요청 인자는 `AIMessage.tool_calls`, 실제 함수 인자는 공통 래퍼에서 각각 잡는다.
  둘 중 하나를 골라 버리지 않는다.
- 잠긴 `langchain-core`의 `InjectedToolCallId`를 쓰면 제공처 call id가 모델용 tool schema에
  노출되지 않으면서 공통 래퍼에 들어온다. 툴마다 별도 계측 코드를 넣을 필요가 없다.
