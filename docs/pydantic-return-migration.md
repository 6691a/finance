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
| **툴 응답 조립**(`thesis._tool_row`·`_body`) | 모델을 JSON으로 바꾸는 **경계 그 자체**다. `_tool_row`는 `{"ref", "title", **detail}`로 `Evidence` 머리와 상세를 한 단으로 편다 — 상세가 다섯 종류라 모델 하나로 표현할 수 없고, 나누면 종류마다 머리 세 칸을 베껴 쓰게 된다. `_body`는 그 결과를 `json.dumps`할 뿐이다. Slack 블록과 같은 판단이다 |

추가로 **`Protocol` 스텁**(`fetchone`·`fetchall` → `Any` 23개), **DB 커넥션 핸들**
(`_connection` → `Any` 16개), **HTTP 응답·헤더**, **`dict[K, 모델]` 매핑**(13개)도 그대로 둔다.
앞의 둘은 PEP 249 계약이고, 마지막은 값이 이미 모델이라 규칙에 이미 맞는다.

**고치는 것은 우리 도메인 값이 모듈 경계를 넘는 자리다.** 특히 **프롬프트·JSONB·XCom·상태 파일로
나가는 값**이 우선이다 — 거기서는 키 오타를 잡아 줄 것이 아무것도 없다.

---

## 2. 남은 것 (2026-08-24 전수 조사, 같은 날 재조사로 정정)

전체 `dict`·`Mapping`·`Any` 반환 애노테이션은 **126곳**이고, 위 규칙으로 거르면 **진짜 전환
대상은 30곳 남짓**이다. 그중 **대부분이 `thesis.py` 한 파일**에 있다.

> **첫 조사가 20곳으로 셌던 이유.** 반환 애노테이션만 셌기 때문에 `_body`에 **인라인으로
> 적힌 dict**가 빠졌다. 그 dict들은 이름 있는 함수를 거치지 않고 툴 메서드 안에서 바로
> 조립돼 프롬프트로 나간다 — 전환하려는 이유가 가장 강한 자리인데 애노테이션이 없어서
> 계수에서 사라졌다. 2.1절이 그것을 포함한 목록이다.

**`briefing/`에는 전환할 것이 하나도 없다.** dict 반환 30개가 전부 Slack 블록(23)·LangGraph
상태(2)·커서 스텁(5)이고, 도메인 값은 이미 `MarketSummary`·`QuoteChange` 같은 모델로 조립된다.
`_*_section` 11개가 그 경계를 한 함수에 가둔 모범이라 **전환의 목표 모양이 이것이다.**

역으로 `thesis.py`가 안 된 이유도 그래서 드러난다 — 거기서는 DB 행이 모델을 한 번도 거치지
않고 dict로 프롬프트까지 간다(`_fetch` → `_document_detail` → `json.dumps`).

### 2.1 우선순위 A — 프롬프트로 나가는 값

`ThesisToolbox`가 툴 응답으로 만드는 값 전부다. 그대로 LLM 프롬프트에 실리는데 모델이 없다.
**모델은 새 모듈 `airflow/modules/thesis_tools.py`에 둔다**(4절 "모델을 어디에 두나").

#### (a) `Evidence.detail`이 되는 것 — 프롬프트 + JSONB

`thesis_evidence.detail` 컬럼에도 저장되므로 **키 이름을 그대로 두고 타입만 입힌다.**

| 만드는 곳 | 모델 이름(안) | 담는 것 |
| --- | --- | --- |
| `_document_detail` | `DocumentDetail` | 문서 근거의 수치 스냅샷 |
| `_recent_disclosures`의 인라인 dict | `DisclosureDetail` | 공시 한 건 |
| `_macro_detail` | `MacroDetail` | 창 변화 한 건 |
| `_us_close_detail` | `UsCloseDetail` | 미국장 마감 한 건 |
| `_recent_signals`의 인라인 dict | `SignalDetail` | 매매 신호 한 건(7키) |

다섯을 유니온으로 묶어 `Evidence.detail`의 타입을 좁힌다.

#### (b) `_body`로만 나가는 것 — 프롬프트만

`Evidence`를 만들지 않는 툴 아홉이다. JSONB로 가지 않으므로 키 제약이 (a)보다 느슨하다.
**항목 모양에 이름이 붙은 것은 넷뿐이고**(`_indicator_row`·`_opinion_detail`·`_surprise_detail`·
`_pending_expectation_detail`) **나머지와 래퍼 dict 전부가 툴 메서드 안 인라인이다** — 그래서
첫 조사에서 빠졌다.

| 툴 | 지금 모양 | 모델 이름(안) |
| --- | --- | --- |
| `macro_indicators` | 래퍼 dict + `_indicator_row` 목록 | `IndicatorPayload` + `IndicatorDetail` |
| `market_investor_flows` | dict 목록(인라인) | `MarketFlowRow` |
| `market_breadth` | dict 목록(인라인) | `MarketBreadthRow` |
| `stock_investor_flows` | 래퍼 dict + 목록 둘(인라인) | `StockFlowPayload` + `StockFlowSettledRow`·`StockFlowEstimateRow` |
| `market_funds` | dict 목록(인라인) | `MarketFundsRow` |
| `daily_history` | 래퍼 dict + 봉 목록(인라인) | `DailyHistoryPayload`·`DailyHistoryEmptyPayload` + `DailyBarRow`·`AvailableSymbolRow` |
| `short_and_credit` | dict 목록(인라인) | `ShortCreditRow` |
| `analyst_opinions` | 래퍼 dict + `_opinion_detail` 목록 | `AnalystOpinionsPayload` + `OpinionDetail` |
| `event_surprises` | 래퍼 dict + `_surprise_detail`·`_pending_expectation_detail` 목록 | `EventSurprisesPayload` + `SurpriseDetail`·`PendingExpectationDetail` |

`daily_history`는 **분기마다 키 집합이 다르다.** 일봉이 없으면 `note`와 `available_symbols`가
붙고 `recent_signals`가 없다. 있으면 반대다. 한 모델에 전부 선택 칸으로 넣지 말고 **두 모델로
나눈다** — 선택 칸으로 두면 없는 쪽에 `null`이 실려 모델이 "값이 없는 관측"으로 읽는다.

`_recent_signals`의 **반환**(4키)은 `thesis_state.SignalObservation`과 키·순서가 같고
`str(date)`와 `model_dump(mode="json")`의 결과가 글자까지 같다. 무손실로 교체된다.
같은 함수가 만드는 `Evidence.detail`(7키)은 별개이고 그것이 (a)의 `SignalDetail`이다.

#### 조건부 키

**`None`이면 키 자체가 사라지는 칸이 넷 있다.** 그냥 `model_dump`하면 `"change_pct": null`이
새로 생겨 프롬프트와 JSONB 모양이 바뀐다.

| 모델 | 조건부 키 | 조건 |
| --- | --- | --- |
| `MacroDetail`·`UsCloseDetail` | `change_bp` / `change_pct` | `kind`가 `rate`면 앞, 아니면 뒤. 기준값이 0이면 **둘 다 없다** |
| `IndicatorDetail` | `change_bp` / `change` | 직전 관측이 있을 때만 |
| `OpinionDetail` | `reason` | 같은 날 같은 증권사 리포트 요약이 있을 때만 |

`@model_serializer(mode="wrap")`로 그 칸만 골라 뺀다. **`exclude_none=True`를 쓰지 않는다** —
`published_at`·`value`·`maturity_months`처럼 `null`로 남아야 하는 칸을 함께 지운다. 결측과
"해당 없음"은 다른 뜻이고 그 구분이 이 파일 전체의 규칙이다(`_number`의 docstring).

#### 함께 좁히는 것

`_number(value) -> Any`도 같이 고친다. `Decimal`을 float으로 바꾸고 결측은 `None`으로 두는
함수인데 `Any`라서 위 모든 필드의 타입을 흐린다. `(Decimal | float | None) -> float | None`이면
충분하다. 다만 **지금은 `date`도 그대로 통과시킨다** — 호출 열두 곳이 정말 숫자 컬럼만
넘기는지 좁히기 전에 확인한다.

`_body`의 `default=str`(`thesis.py:1126`)과 `_store_evidence`의 `default=str`(`:2118`)도
이때 사라진다. 모델이 `model_dump(mode="json")`으로 정확히 바꾼다.

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
방식에 기대지 않는다. `slot`은 `RunSlot`이 아니라 `str`이다 — `thesis_state`는 `thesis.py`를
모듈 수준에서 import할 수 없다.

읽는 쪽은 둘뿐이다. `thesis_common.notify_slack`(`:423`)이 `run_date`와 `slot`을,
`thesis_review.narrate_followups`(`:142`)가 `run_date`만 쓴다. 둘 다 `date.fromisoformat`으로
문자열을 되돌리고 있으므로 `ThesisRunResult.model_validate(built)`로 받으면 그 두 줄이 없어진다.

**`written`은 어디서도 읽히지 않는다.** 그래도 남긴다 — Airflow UI의 XCom 화면에서 그 실행이
몇 건을 썼는지 보는 값이다. 그리고 `build()` 반환을 검사하는 테스트가 **하나도 없다**.
Task 2는 테스트부터 만든다.

### 2.3 우선순위 C — JSONB·heartbeat 파일로 나가는 나머지

`thesis.py` 밖에도 둘 있다. 프롬프트만큼 급하지는 않지만 저장되는 값이라 같은 이유가 붙는다.

| 파일:라인 | 함수 | 담는 것 | 경계 |
| --- | --- | --- | --- |
| `collectors/document/dart.py:416` | `parse_provisional` | 실적값 튜플 + 파싱 메타데이터 | `source_record.metadata` JSONB |
| `apps/realtime/service.py:410` | `heartbeat_extra` | `{session_id, **counters, late_ticks}` | heartbeat JSON **파일** |

`parse_provisional`은 반환이 `tuple[tuple[EarningsValue, ...], dict[str, Any]]`다 — 앞은 이미
모델이고 뒤만 dict다. 메타데이터 모델 하나를 더해 `tuple[모델들, 메타]`로 만든다.
두 칸(`unit_multiplier`, `statement_scope`)뿐이고 `statement_scope`는 `CFS`/`OFS` 둘로 닫혀 있어
`StrEnum`이다. `EarningsFetch.metadata`로 합쳐지는 5키 JSON 모양은 그대로 둔다.

**`heartbeat_extra`는 Redis가 아니다.** `apps/realtime/heartbeat.py:18`의 `write_heartbeat`가
`settings.heartbeat_path`에 원자적으로(tmp 쓰고 `replace`) JSON 파일을 쓰고 docker healthcheck가
그것을 읽는다(`heartbeat.py:28`). `apps/realtime/*`는 Redis를 import하지 않는다.

`counters`는 `run_connection` 안의 `dict[str, int]`이고 키 일곱이 정적으로 고정돼 있다
(`+=`로만 갱신되고 새 키를 넣는 코드가 없다). **반환만 모델로 만들고 `counters` 자체는 dict로
둔다** — 갱신이 제자리 증가라 frozen 모델과 맞지 않는다. 지금은 테스트가 `_flush_timer`에
2키짜리 `counters`와 `dict` 자체를 `heartbeat_extra`로 넣고 있어(`tests/realtime/
test_kis_realtime.py:530,572,546,585`) **실제 클로저가 한 번도 돌지 않는다.** 모델을 넣으면
그 픽스처가 실제 키 집합을 갖게 된다.

같은 `counters`가 `close_session`의 `source_metadata`(`service.py:483-495`)로도 나가는데 키
집합이 달라(`reason`·`acks`·`active_channels`·`skipped_partial_bars`·`dropped_open_bars`가 더 있다)
같은 모델로 묶이지 않는다. 이번 범위 밖이다.

### 2.4 우선순위 D — `Any`가 과한 곳

dict는 아니지만 같은 이유로 타입이 없다. **한 줄씩이라 위 셋을 하며 지나는 김에 고친다.**

| 파일:라인 | 함수 | 현재 | 되어야 할 것 |
| --- | --- | --- | --- |
| `thesis.py:1427` | `_number` | `Any` | `float | None`. 2.1절에서 함께 한다 |
| `thesis_review.py:191` | `_horizon_return` | `Any` | `Decimal | None`. `item`도 `thesis.PendingGrade`로 좁힌다 |
| `thesis_nxt_review.py:138` | `targets` | `tuple[Any, ...]` | `tuple[Subject, ...]`. `TYPE_CHECKING` 블록으로 푼다 |
| `collectors/document/naver_research.py:255` | `enrich_listing` | `Any` | `tuple[FeedItem, ...]` |
| `dags/market_calendar_daily.py:148` | `_store` | `Any` | `TypeVar` 하나로 제네릭화 |

**`targets`는 순환 import 때문이 아니다.** `thesis.py`는 `thesis_common`·`thesis_forecast`·
`thesis_review`·`thesis_nxt_review` 넷 중 어느 것도 import하지 않는다 — 공유 모델은
`thesis_state.py`로 한 방향이다. 함수 안 import의 이유는 **DagBag 30초 타임아웃**이고
(`thesis_common.py:16`), 그것은 런타임 import만 막는다. `TYPE_CHECKING` 블록은 런타임에 돌지
않으므로 `Subject`를 그대로 쓸 수 있다.

**`enrich_listing`의 `Any`는 이미 있는 계약을 무력화한다.** 레지스트리
(`document_listings.py:93`)가 `Callable[[Connection, FeedSource, tuple[FeedItem, ...]],
tuple[FeedItem, ...]]`를 선언하고 있는데, 대입되는 어댑터가 `-> Any`라 그 계약이 검사되는
유일한 지점에서 검사가 꺼진다.

**`_store`는 위임 대상 셋의 반환 타입이 갈린다.** `store_domestic`·`store_calendar`는 `int`,
`store_overseas`는 `UsSettlement | None`이다. 그래서 구체 타입이 아니라 `TypeVar`다.
문법은 `apps/core/database.py:132`와 같은 고전 `TypeVar`를 쓴다.

### 2.5 안 고치는 내부 매핑

밖으로 나가지 않고 같은 모듈 안에서만 쓰는 것들이다. **급하지 않다.**

| 파일 | 함수 | 판단 |
| --- | --- | --- |
| `airflow/modules/thesis.py` | `registry` | 값이 `Evidence` 모델이라 이미 규칙에 맞는다. **안 고친다** |
| `airflow/modules/thesis.py` | `stored_outcomes` | 값이 `StoredOutcome` 튜플이라 맞는다. **안 고친다** |
| `airflow/modules/thesis.py` | `_snapshot_window` | SQL 파라미터 dict다. psycopg에 그대로 넘어간다. **안 고친다** |
| `airflow/modules/thesis.py` | `_tool_row`·`_body` | 모델을 JSON으로 바꾸는 경계다. 1절 표에 이유가 있다. **안 고친다** — 대신 `_body`의 **인자 타입**이 `Any`에서 `BaseModel | Sequence[BaseModel]`이 된다 |
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

- New: `airflow/modules/thesis_tools.py`
- Modify: `airflow/modules/thesis.py`, `tests/modules/test_thesis.py`
- [ ] `thesis_tools.py`를 만들고 2.1(a)의 다섯을 **현재 키를 글자 그대로** 옮겨 담는다.
      `Evidence.detail`을 그 다섯의 유니온으로 좁힌다.
- [ ] 2.1(b)의 툴 아홉을 모델로 옮긴다. `daily_history`는 두 모델로 나눈다.
- [ ] 조건부 키 넷에 `@model_serializer(mode="wrap")`를 단다. `exclude_none`을 쓰지 않는다.
- [ ] `_recent_signals`의 반환을 `list[SignalObservation]`으로 바꾼다(모델이 이미 있다).
- [ ] `_body`가 `BaseModel | Sequence[BaseModel]`을 받게 하고 `default=str` 둘을 지운다.
- [ ] `_number`의 호출 열두 곳이 숫자만 넘기는지 확인한 뒤 `float | None`으로 좁힌다.
- [ ] `thesis_evidence.detail`과 각 툴 본문의 **키 집합이 안 바뀌는지** 테스트로 고정한다.
      픽스처를 맨 dict에서 모델로 바꾼다.

```bash
uv run pytest tests/modules/test_thesis.py -q
```

### Task 2: DAG 반환 모델 (우선순위 B)

- Modify: `thesis_forecast.py`, `thesis_review.py`, `thesis_nxt_review.py`, `thesis_common.py`,
  대응 DAG 셋, 대응 테스트 셋
- [ ] `build()` 반환을 검사하는 테스트를 **먼저** 만든다. 지금은 하나도 없다.
- [ ] `thesis_state.py`에 `ThesisRunResult(run_date: date, slot: str, written: int)`를 더한다.
- [ ] 세 `build()`가 그것을 돌려주고, DAG 태스크가 `model_dump(mode="json")`으로 XCom에 넣는다.
- [ ] 읽는 둘(`thesis_common.notify_slack`, `thesis_review.narrate_followups`)이
      `ThesisRunResult.model_validate(built)`로 받는다. `date.fromisoformat` 두 줄이 사라진다.

```bash
uv run pytest tests/dags -q
```

### Task 3: JSONB·heartbeat 파일 (우선순위 C) + `Any` 좁히기 (D)

- Modify: `collectors/document/dart.py`, `apps/realtime/service.py`, 2.4절 네 곳, 대응 테스트
- [ ] `parse_provisional`의 메타데이터 dict를 모델로 바꾼다. `statement_scope`는 `StrEnum`이고
      `source_record.metadata`에 저장되는 **JSON 모양은 그대로**여야 한다.
- [ ] `heartbeat_extra`의 **반환만** 모델로 바꾼다. `counters`는 dict로 둔다.
      `_flush_timer` 테스트의 2키 픽스처를 실제 키 집합으로 고친다.
- [ ] 2.4절의 나머지 넷을 좁힌다(`_number`는 Task 1에서 끝난다).

```bash
uv run pytest tests/collectors/test_dart.py tests/realtime tests/dags/test_market_calendar.py -q
```

### Task 4: 문서 갱신

- [ ] 이 문서의 표에서 끝난 줄을 지운다. 전부 지워지면 문서를 삭제하고 CLAUDE.md·AGENTS.md의
      "아직 dict를 돌려주는 코드가 남아 있다" 문단도 함께 지운다.
- [ ] `graphify update .`

---

## 4. 전환하며 지키는 것

- **키 이름을 바꾸지 않는다.** JSONB에 이미 저장된 행과 새 행이 달라지면 그것을 읽는 SQL이
  조용히 틀린다. 이름을 고쳐야 하면 그건 별도 결정이고 마이그레이션이 따라온다.
  참고로 `thesis_evidence.detail`은 지금 **쓰기 전용**이다 — `thesis_evidence/insert.sql:24`가
  유일한 사용처이고 `select_by_thesis_ids.sql`·`select_top_by_thesis_ids.sql` 둘 다 이 컬럼을
  SELECT하지 않는다. 그래도 키를 고정한다. 과거 행과 새 행이 다르면 나중에 이 컬럼을 처음
  읽는 쿼리가 그 사실을 모른 채 쓰인다.
- **시각 표기는 ISO 8601로 통일하고 `PROMPT_VERSION`은 안 올린다.** `default=str`을 지우면
  `datetime` 칸 넷(`announced_at`·`latest_stated_at`·`observed_at`·`collected_at`)이
  `"2026-08-21 05:00:00+00:00"`에서 `"2026-08-21T05:00:00Z"`가 된다. `date` 칸은 두 방식의
  결과가 글자까지 같아 안 바뀐다. 같은 payload의 `published_at`·`window_start`가 이미
  `isoformat()`이라 오히려 한 표기로 모인다. 키 집합이 그대로이므로 프롬프트 설계가 바뀐 것이
  아니라고 본다.
- **모델을 어디에 두나 — 툴 응답은 새 `thesis_tools.py`다.** `thesis_state.py`에 넣지 않는다.
  그 모듈이 따로 있는 이유는 `thesis.py`(LangChain)와 `thesis_common.py`(Airflow)가 서로를
  모듈 수준에서 import할 수 없어서인데, 툴 응답 모델은 `thesis.py`만 쓴다. 그렇다고 이미
  2950줄인 `thesis.py`에 250줄을 더 얹지도 않는다. `thesis_tools.py`는 pydantic과
  `modules.technical`만 import한다 — `thesis_state.py`와 같은 제약을 지킨다.
- **모양이 층을 섞으면 한 단 내린다.** `{"as_of_date": ..., "KOSPI": {...}}` 같은 것은 모델로
  표현할 수 없다. 2026-08-24에 `technical` 블록이 `subjects` 아래로 한 단 들어간 것이 그 예이고,
  그때 그 값을 읽는 SQL(기술지표 문서 14.4절)도 같은 커밋에서 고쳤다.
- **`json.dumps(..., default=str)`을 지운다.** 모델이 있으면 `model_dump(mode="json")`이
  `date`·`Decimal`을 정확히 바꾼다. `default=str`은 어느 칸이 언제 문자열이 됐는지를 감춘다.
- **한 커밋에 한 우선순위.** 프롬프트로 나가는 값이 바뀌면 `PROMPT_VERSION`을 올릴지 판단해야
  한다(모양이 그대로면 안 올린다 — 모델이 같은 JSON을 내면 모델 입력은 바뀌지 않는다).

---

## 5. 검토 기록

- **2026-08-24 작성.** 추론 관측 상태를 `thesis_state.py` 모델로 옮긴 직후, 남은 자리를
  전수 조사해 우선순위 A~D로 나눴다.
- **2026-08-24 재조사.** 구현 착수 전에 코드를 다시 훑어 일곱 곳을 고쳤다.
  1. 2.1에 `_surprise_detail`·`_pending_expectation_detail`이 빠져 있었다(이벤트 기대 기능이
     첫 조사 뒤에 들어왔다).
  2. `_tool_row`는 전환 대상이 아니라 wire 조립이다. 1절로 옮겼다.
  3. `_body`의 **인라인 dict 아홉 툴**이 계수에서 통째로 빠져 있었다. 반환 애노테이션만
     세었기 때문이다. 2.1(b)로 넣었다.
  4. `heartbeat_extra`의 경계는 Redis가 아니라 heartbeat JSON **파일**이다.
  5. `targets`의 `Any`는 순환 import가 아니라 DagBag 타임아웃 회피 지연 import 때문이다.
  6. `enrich_listing`의 `Any`가 `document_listings.py:93`의 계약 검증을 무력화한다는 사실을 적었다.
  7. `written`이 아무 데서도 안 읽히고 `build()` 반환 테스트가 0건이라는 사실을 적었다.
  같은 조사에서 범위(인라인 payload 포함)·시각 표기(ISO, 버전 유지)·모델 위치(`thesis_tools.py`)
  세 결정을 확정했다.
