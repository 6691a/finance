# 반환 타입 Pydantic 전환 계획

> 규칙의 원본은 [.claude/CLAUDE.md](../.claude/CLAUDE.md)·[.codex/AGENTS.md](../.codex/AGENTS.md)의
> "함수가 돌려주는 데이터 모양은 Pydantic 모델이다"다. 이 문서는 **아직 안 고친 곳의 목록과 순서**다.

**Goal:** 모듈 경계를 넘는 반환값에서 `dict[str, Any]`·`list[dict[str, Any]]`·`Mapping[str, Any]`를
없앤다. 그 값을 읽는 쪽이 무슨 키를 기대해도 되는지가 코드에 남게 하는 것이 목적이다.

**시작점:** 2026-08-24에 추론 관측 상태를 옮겼다. `thesis_common.observed_state`·`technical_state`,
`thesis.past_theses`, `NxtAfterHoursReview.observed_state`가 `airflow/modules/thesis_state.py`의
모델을 돌려준다. 그 모듈이 이 전환의 기준 구현이다.

---

## 1. 고치는 것과 안 고치는 것

**dict를 돌려준다고 전부 결함이 아니다.** 아래 넷은 그대로 둔다.

| 그대로 두는 것 | 왜 |
| --- | --- |
| **Slack 블록**(23개) | Slack API wire format이다. 모델로 감싸면 제공처 스펙이 바뀔 때 우리 모델도 따라 고쳐야 하고, `blocks.table_section`이 이미 조립을 한 곳에 모았다. LLM 툴 정의를 `StructuredTool`로 쓰면서 `{"type": "function", ...}` dict를 손으로 안 쓰는 것과 같은 판단이다 |
| **LangGraph 노드 반환**(`_call`·`_investigate`·`_tools`·`_answer`·`_repair`) | LangGraph의 상태 병합 계약이다. 노드는 "상태의 바뀐 칸만" 돌려주고 리듀서가 합친다. 상태 자체는 이미 `TypedDict`(`ThesisState`·`NarrativeState`·`AssessState`)로 선언돼 있다 |
| **JSON Schema 생성**(`schema.strict_json_schema`·`response_format`) | 제공처 wire format이다. Pydantic이 만들어 주는 것을 다시 Pydantic으로 감쌀 이유가 없다 |
| **외부 응답 원본 파싱**(`_parse_body`·`_json`·`_rows`) | 검증 **전** 단계다. 이 dict는 곧바로 `model_validate`로 들어간다. 중간에 한 겹 더 두면 같은 검증이 두 번이다 |

추가로 **`Protocol` 스텁**(`fetchone`·`fetchall` → `Any` 23개), **DB 커넥션 핸들**
(`_connection` → `Any` 16개), **HTTP 응답·헤더**, **`dict[K, 모델]` 매핑**(13개)도 그대로 둔다.
앞의 둘은 PEP 249 계약이고, 마지막은 값이 이미 모델이라 규칙에 이미 맞는다.

**고치는 것은 우리 도메인 값이 모듈 경계를 넘는 자리다.** 특히 **프롬프트·JSONB·XCom·Redis로
나가는 값**이 우선이다 — 거기서는 키 오타를 잡아 줄 것이 아무것도 없다.

---

## 2. 남은 것 (2026-08-24 전수 조사)

전체 `dict`·`Mapping`·`Any` 반환 애노테이션은 **126곳**이고, 위 규칙으로 거르면 **진짜 전환
대상은 20곳**이다. 그중 **절반이 `thesis.py` 한 파일**에 있다.

**`briefing/`에는 전환할 것이 하나도 없다.** dict 반환 30개가 전부 Slack 블록(23)·LangGraph
상태(2)·커서 스텁(5)이고, 도메인 값은 이미 `MarketSummary`·`QuoteChange` 같은 모델로 조립된다.
`_*_section` 11개가 그 경계를 한 함수에 가둔 모범이라 **전환의 목표 모양이 이것이다.**

역으로 `thesis.py`가 안 된 이유도 그래서 드러난다 — 거기서는 DB 행이 모델을 한 번도 거치지
않고 dict로 프롬프트까지 간다(`_fetch` → `_document_detail` → `json.dumps`).

### 2.1 우선순위 A — 프롬프트로 나가는 값

`ThesisToolbox`의 툴 응답을 만드는 상세 함수들이다. 값이 그대로 LLM 프롬프트에 실리는데
모델이 없다.

| 파일 | 함수 | 담는 것 |
| --- | --- | --- |
| `airflow/modules/thesis.py` | `_document_detail` | 문서 근거의 수치 스냅샷 |
| `airflow/modules/thesis.py` | `_opinion_detail` | 투자의견 한 건 |
| `airflow/modules/thesis.py` | `_macro_detail` | 창 변화 한 건 |
| `airflow/modules/thesis.py` | `_us_close_detail` | 미국장 마감 한 건 |
| `airflow/modules/thesis.py` | `_indicator_row` | 지표 계열 한 건 |
| `airflow/modules/thesis.py` | `_tool_row` | `Evidence` 하나를 툴 응답 칸으로 |
| `airflow/modules/thesis.py` | `_recent_signals` | 최근 매매 신호(2026-08-24에 추가) |

`_recent_signals`는 `thesis_state.SignalObservation`이 이미 같은 모양을 갖고 있다 — 그것을
그대로 쓰면 된다. 나머지 여섯은 `Evidence.detail`(JSONB로도 저장된다)과 툴 응답 둘로 나가므로
`thesis_state.py`에 모델을 더한다.

같은 경로의 `_number(value) -> Any`도 함께 좁힌다. `Decimal`을 float으로 바꾸고 결측은 `None`으로
두는 함수라 `float | None`이면 충분한데 `Any`라서 위 여섯의 필드 타입을 전부 흐린다.

**주의:** 이 값들은 `Evidence.detail`을 통해 `thesis_evidence.detail` 컬럼에도 들어간다. 모양을
바꾸면 과거 행과 새 행이 달라지므로, **키 이름은 그대로 두고 타입만 입힌다.**

**보너스:** `modules/schema.py`의 `strict_json_schema(model)`·`response_format(model, name)`이 이미
있다. 툴 응답을 모델로 만들면 그 스키마를 제공처 강제에 그대로 재사용할 수 있다.

### 2.2 우선순위 B — XCom으로 나가는 값

DAG 태스크의 반환값이다. Airflow가 직렬화해 XCom에 넣고 다음 태스크가 읽는다.

| 파일 | 함수 | 담는 것 |
| --- | --- | --- |
| `airflow/dags/market_thesis_forecast.py` | `build_thesis` | `{run_date, slot, written}` |
| `airflow/dags/market_thesis_review.py` | `build_thesis` | 같음 |
| `airflow/dags/market_thesis_nxt_review.py` | `build_thesis` | 같음 |
| `airflow/modules/thesis_forecast.py` | `build` | 위 셋이 그대로 돌려주는 원본 |
| `airflow/modules/thesis_review.py` | `build` | 같음 |
| `airflow/modules/thesis_nxt_review.py` | `build` | 같음 |

셋이 같은 세 칸을 돌려준다. `ThesisRunResult(run_date, slot, written)` 모델 하나로 묶는다.
**XCom 경계에서 `model_dump(mode="json")`을 부른다** — Airflow가 Pydantic 모델을 직렬화하는
방식에 기대지 않는다.

### 2.3 우선순위 C — JSONB·Redis로 나가는 나머지

`thesis.py` 밖에도 둘 있다. 프롬프트만큼 급하지는 않지만 저장되는 값이라 같은 이유가 붙는다.

| 파일:라인 | 함수 | 담는 것 | 경계 |
| --- | --- | --- | --- |
| `collectors/document/dart.py:416` | `parse_provisional` | 실적값 튜플 + 파싱 메타데이터 | `source_record.metadata` JSONB |
| `apps/realtime/service.py:410` | `heartbeat_extra` | `{session_id, **counters, late_ticks}` | Redis heartbeat JSON |

`parse_provisional`은 반환이 `tuple[tuple[EarningsValue, ...], dict[str, Any]]`다 — 앞은 이미
모델이고 뒤만 dict다. 메타데이터 모델 하나를 더해 `tuple[모델들, 메타]`로 만든다.

### 2.4 우선순위 D — `Any`가 과한 곳

dict는 아니지만 같은 이유로 타입이 없다. **한 줄씩이라 위 셋을 하며 지나는 김에 고친다.**

| 파일:라인 | 함수 | 현재 | 되어야 할 것 |
| --- | --- | --- | --- |
| `thesis.py:1354` | `_number` | `Any` | `float | None` |
| `thesis_review.py:191` | `_horizon_return` | `Any` | `Decimal | None` |
| `thesis_nxt_review.py:138` | `targets` | `tuple[Any, ...]` | 순환 import 회피용이다. `TYPE_CHECKING` 블록으로 풀 수 있는지 본다 |
| `collectors/document/naver_research.py:255` | `enrich_listing` | `Any` | 실제로는 `FeedItem` 튜플이다 |
| `dags/market_calendar_daily.py:148` | `_store` | `Any` | 위임한 콜러블의 반환. 제네릭이나 구체 타입으로 |

### 2.5 안 고치는 내부 매핑

밖으로 나가지 않고 같은 모듈 안에서만 쓰는 것들이다. **급하지 않다.**

| 파일 | 함수 | 판단 |
| --- | --- | --- |
| `airflow/modules/thesis.py` | `registry` | 값이 `Evidence` 모델이라 이미 규칙에 맞는다. **안 고친다** |
| `airflow/modules/thesis.py` | `stored_outcomes` | 값이 `StoredOutcome` 튜플이라 맞는다. **안 고친다** |
| `airflow/modules/thesis.py` | `_snapshot_window` | SQL 파라미터 dict다. psycopg에 그대로 넘어간다. **안 고친다** |
| `airflow/modules/thesis_common.py` | `run_date_param` | Airflow `Param` dict다. 프레임워크 계약. **안 고친다** |
| `airflow/modules/assessment.py` | `_assess_one` | 문서 평가 결과 한 건이지만 LangGraph 상태 칸으로 바로 들어간다. `AssessState` `TypedDict`가 모양을 잡고 있다. **안 고친다** |
| `airflow/modules/thesis.py` | `_fetch`·`ToolArgs._drop_unreadable` | 미가공 DB 행과 pydantic before-validator다. 검증 **전** 단계다. **안 고친다** |
| `apps/core/config.py`·`database.py` | validator·`_connect_args_for` | 값이 이미 모델이거나 드라이버 인자 dict다. **안 고친다** |
| `collectors/bbk.py`·`document_listings.py` | `_column_indexes`·`_naver_research_sources` | 값이 `int`·`ListingSource`인 열린 키 매핑이다. 규칙에 맞는다. **안 고친다** |

---

## 3. 작업 순서

한 우선순위가 한 커밋이다. 각 단계는 테스트를 먼저 고친다 — 픽스처가 맨 dict면 프롬프트에
실릴 키가 테스트에서만 존재할 수 있다.

### Task 1: 툴 응답 모델 (우선순위 A)

- Modify: `airflow/modules/thesis_state.py`, `airflow/modules/thesis.py`, `tests/modules/test_thesis.py`
- [ ] `_recent_signals`가 `thesis_state.SignalObservation`을 돌려주게 한다(모델이 이미 있다).
- [ ] 나머지 여섯의 **현재 키를 글자 그대로** 옮긴 모델을 `thesis_state.py`에 더한다.
      `DocumentDetail`·`OpinionDetail`·`MacroDetail`·`UsCloseDetail`·`IndicatorDetail`·`ToolRow`.
- [ ] `Evidence.detail`의 타입을 좁힌다. 여러 종류가 들어오므로 `BaseModel`을 받는 유니온이거나,
      종류마다 `Evidence` 하위 타입을 두거나 — 어느 쪽이 나은지는 그때 판단한다.
- [ ] `thesis_evidence.detail`에 저장되는 JSON이 **바뀌지 않는지** 테스트로 고정한다.

```bash
uv run pytest tests/modules/test_thesis.py -q
```

### Task 2: DAG 반환 모델 (우선순위 B)

- Modify: `thesis_forecast.py`, `thesis_review.py`, `thesis_nxt_review.py`, 대응 DAG 셋, 대응 테스트 셋
- [ ] `thesis_state.py`에 `ThesisRunResult(run_date: date, slot: str, written: int)`를 더한다.
- [ ] 세 `build()`가 그것을 돌려주고, DAG 태스크가 `model_dump(mode="json")`으로 XCom에 넣는다.
- [ ] Slack 알림 태스크가 그 dict를 읽는 자리도 함께 고친다.

```bash
uv run pytest tests/dags -q
```

### Task 3: JSONB·Redis (우선순위 C) + `Any` 좁히기 (D)

- Modify: `collectors/document/dart.py`, `apps/realtime/service.py`, 2.4절 다섯 곳, 대응 테스트
- [ ] `parse_provisional`의 메타데이터 dict를 모델로 바꾼다. `source_record.metadata`에 저장되는
      **JSON 모양은 그대로**여야 한다.
- [ ] `heartbeat_extra`를 모델로 바꾼다. `counters`가 `**`로 펼쳐지고 있으니 그 모양부터 확인한다.
- [ ] 2.4절의 `Any` 다섯을 좁힌다. `targets`는 `TYPE_CHECKING` import로 풀리는지 먼저 본다.

### Task 4: 문서 갱신

- [ ] 이 문서의 표에서 끝난 줄을 지운다. 전부 지워지면 문서를 삭제하고 CLAUDE.md·AGENTS.md의
      "아직 dict를 돌려주는 코드가 남아 있다" 문단도 함께 지운다.
- [ ] `graphify update .`

---

## 4. 전환하며 지키는 것

- **키 이름을 바꾸지 않는다.** JSONB에 이미 저장된 행과 새 행이 달라지면 그것을 읽는 SQL이
  조용히 틀린다. 이름을 고쳐야 하면 그건 별도 결정이고 마이그레이션이 따라온다.
- **모양이 층을 섞으면 한 단 내린다.** `{"as_of_date": ..., "KOSPI": {...}}` 같은 것은 모델로
  표현할 수 없다. 2026-08-24에 `technical` 블록이 `subjects` 아래로 한 단 들어간 것이 그 예이고,
  그때 그 값을 읽는 SQL(기술지표 문서 14.4절)도 같은 커밋에서 고쳤다.
- **`json.dumps(..., default=str)`을 지운다.** 모델이 있으면 `model_dump(mode="json")`이
  `date`·`Decimal`을 정확히 바꾼다. `default=str`은 어느 칸이 언제 문자열이 됐는지를 감춘다.
- **한 커밋에 한 우선순위.** 프롬프트로 나가는 값이 바뀌면 `PROMPT_VERSION`을 올릴지 판단해야
  한다(모양이 그대로면 안 올린다 — 모델이 같은 JSON을 내면 모델 입력은 바뀌지 않는다).
