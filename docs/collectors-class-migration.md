# 수집기 클래스 전환

`.claude/CLAUDE.md`의 "클래스와 함수를 가르는 기준"을 저장소에 적용하는 진행 문서다.
목표 형태, 전환 현황, 남은 순서를 여기 한 곳에 둔다. 새 수집기는 처음부터 이 형태로 쓴다.

## 왜 나누는가

**상태를 쥔 동작은 클래스로 묶고, 상태 없는 변환은 함수로 둔다.**

자격 증명·토큰·DB 연결·기준 시각처럼 여러 호출에 걸쳐 안 변하는 값이 함수마다 인자로 다시
들어가고 있으면 그것이 신호다. 지금 KIS 수집기는 `token`·`app_key`·`app_secret` 세 개를
fetch 함수마다 받고, DAG이 그것을 **47개 호출 지점**에 다시 실어 나른다. 값이 하나 늘거나
발급 방식이 바뀌면 그 47곳이 전부 바뀐다.

반대로 파싱·정규화·계산은 감쌀 상태가 없다. 그것을 클래스로 만들면 메서드가 전부
`@staticmethod`가 되고, 그건 모듈이지 클래스가 아니다.

## 목표 형태

기준 구현 둘이다.

- `airflow/modules/collectors/analyst/kis_opinion.py`의 `KisAnalystOpinionCollector`
  — 자격 증명을 쥔 API 수집기.
- `airflow/modules/collectors/document/naver_research.py`의 `NaverResearchCollector`
  — 목록과 상세를 두 번 받는 HTML/JSON 수집기.

```python
class KisPositioningCollector:
    """한 실행이 객체 하나다. 토큰은 발급 횟수 제한이 있어 DAG이 한 번 받아 넘긴다."""

    def __init__(self, token: SecretStr, app_key: SecretStr, app_secret: SecretStr) -> None:
        self._token = token
        self._app_key = app_key
        self._app_secret = app_secret

    def fetch_short_sale(self, stock: str, start: date, end: date) -> ShortSaleFetch: ...

    def store_short_sale(self, connection: Connection, fetch: ShortSaleFetch) -> int: ...

    def _call(self, path: str, tr_id: str, query: dict[str, str]) -> dict[str, Any]: ...

    @staticmethod
    def _rows(payload: dict[str, Any], key: str) -> list[dict[str, Any]]: ...
```

규칙 다섯 가지다.

1. **생성자는 그 실행 동안 안 변하는 것만 받는다.** 자격 증명과 토큰이다. 종목·구간처럼
   호출마다 바뀌는 것은 메서드 인자다.
2. **`connection`은 생성자가 아니라 `store` 메서드 인자다.** 조회와 저장의 트랜잭션 경계를
   DAG이 쥐고 있어야 하기 때문이다. `fetch` 실패로 재시도를 판단하고 성공한 것만
   트랜잭션 안에서 저장한다.
3. **Pydantic 모델은 모듈 수준에 둔다.** 수집기 클래스 안에 중첩하지 않는다 — 테스트와
   DAG과 다른 모듈이 import한다.
4. **파싱·정규화는 `@staticmethod`이거나 모듈 함수다.** 클래스 안에 두는 편이 읽기 좋으면
   `@staticmethod`, 그 클래스의 관심사가 아니면 모듈 함수다. `watched_stocks(connection)`이
   후자다 — KIS와 무관하게 마스터 테이블만 본다.
5. **전송 계층은 모듈 함수로 남긴다.** `kis.py`의 `send_get`·`issue_token`·`access_token`은
   수집기별 상태가 아니다. 기준 구현도 `send_get`을 함수로 부르고 각 수집기의 `_call`이
   그 위를 감싼다.

## 폴더 구조

목표는 도메인별로 나누는 것이다.

```
airflow/modules/collectors/
    market/      지수·선물·환율·종목 시세
    document/    뉴스·공시·리서치 문서
    indicator/   금리·물가 등 지표 시계열
    calendar/    거래일·결제일
    analyst/     투자의견·목표주가
```

**하위 패키지의 `__init__.py`는 재수출하지 않는다.** 한 수집기의 의존성이 없는 환경에서
관계없는 DAG이 import 오류로 죽는다.

**폴더 이동은 클래스 전환과 분리한다.** 둘을 같은 커밋에 섞으면 diff에서 실제 변경이 안
보인다. 클래스로 옮기는 모듈만 그때 폴더로 넣고, 함수로 남는 모듈의 이동은 별도 커밋이다.

## 전환 현황

### 완료

| 모듈 | 클래스 | 시점 |
| --- | --- | --- |
| `collectors/analyst/kis_opinion.py` | `KisAnalystOpinionCollector` | 2026-08-22 |
| `collectors/document/naver_research.py` | `NaverResearchCollector` | 2026-08-22 |
| `collectors/kis_overseas_index.py` | `KisOverseasIndexCollector` | 2026-08-22 (폴더 이동은 아직) |
| `collectors/market/kis_investor_flow.py` | `KisInvestorFlowCollector` | 2026-08-22 |
| `collectors/indicator/fred.py` | `FredCollector` | 2026-08-23 |
| `collectors/indicator/ecos.py` | `EcosCollector` | 2026-08-23 |
| `collectors/calendar/kis_market_calendar.py` | `KisMarketCalendarCollector` | 2026-08-23 |
| `collectors/document/dart.py` | `DartCollector` | 2026-08-23 |

### 1단계 — 자격 증명을 인자로 도는 수집기 (남은 2모듈)

규칙이 정확히 겨냥한 것이고 이득이 가장 크다. 처음 8모듈 중 `kis_overseas_index`와
`kis_investor_flow`는 끝났다.

| 모듈 | 줄 | 새 클래스 | 생성자 | 클래스로 들어가는 것 |
| --- | --- | --- | --- | --- |
| `collectors/kis.py` | 1298 | `KisQuoteCollector` | token·app_key·app_secret | `fetch_bars`·`fetch_index_bars`·`fetch_index_price`·`fetch_stock_bars`·`store_bars`·`store_stock_bars`·`store_market_movement` |
| `collectors/kis_positioning.py` | 971 | `KisPositioningCollector` | 〃 | `_call` + `fetch_*` 6개 + `store_*` 6개 |

모듈 함수로 남는 것: `expiry_date`·`front_contract`·`parse_bars`·`parse_market_movement`·
`parse_observations`·`parse_financials`·`parse_provisional`·`us_session_date`·
`fold_us_settlement`·`normalized_report_name`·`is_provisional`·`periodic_report`·
`pending_earnings`·`session_days`와 `_day`·`_decimal`·`_int`·`_text` 계열 전부.
`kis.py`에는 `send_get`·`issue_token`·`access_token`·`TokenStore` Protocol이 남는다.

**순서**: `kis_overseas_index`(fetch 하나)로 모양을 굳혔다. `kis.py`(1298줄)를 마지막에 한다.
모듈 하나가 커밋 하나다.

남은 DAG 쪽 호출 지점: `kis_quote_intraday` 12, `kis_market_positioning_daily` 8,
`kis_stock_minute_bars_daily` 3. **스케줄·재시도·실패 판정은 건드리지 않는다.**

테스트는 8파일 ~3,950줄이 영향받는다. 대부분 호출 표현 치환이고,
`tests/collectors/test_kis_analyst_opinion.py`가 클래스 형태 테스트의 기준이다.

### 2단계 — 연결·기준 시각·레지스트리를 도는 흐름 코드

수집기가 아니지만 같은 규칙에 걸린다. 인자 개수가 신호다.

| 위치 | 줄 | 신호 |
| --- | --- | --- |
| `modules/thesis.py` | 2721 | 클래스 셋(`ThesisToolbox`·`ThesisBuilder`·`FollowupNarrator`) 밖에 저장·조회 모듈 함수 ~20개. `store_theses(connection, run_date, run_slot, as_of_at, dag_run_id, drafts, registry, observed_state, llm_model, tool_rounds)` 인자 10개, `store_narratives` 8개, `horizon_returns` 7개. `ThesisStore(connection, run_date, run_slot, as_of_at, dag_run_id, registry)` 후보 |
| `modules/thesis_common.py` | 253 | `build_and_store(conn, run_slot, run_date, as_of_at, macro_window_start, targets, observed, dag_run_id)` 인자 8개. `observed_state`·`origin_day`·`previous_open_day`가 전부 `conn`을 다시 받는다 |
| `modules/thesis_forecast.py` / `thesis_review.py` | 99 / 219 | `check_ready`·`macro_window_start`·`_horizon_return`이 `conn`·`run_date`를 다시 받는다 |
| `modules/assessment.py` | 691 | `DocumentAssessor`·`AssessmentBatch` 밖에 `store_assessment(connection, document, assessment, instruments, indicators, model, assessed_at, prompt_revision)` 인자 8개, `pending_documents(connection, limit, prompt_revision)`, `load_candidates(connection)` |
| `modules/briefing/market.py` | 1082 | `collect_summary(connection, now)`·`collect_chart_series(connection, now, open_hour)`·`_market_funds(connection)`·`_rate_spreads(connection, since)` — `connection`·`now`가 계속 아래로 흐른다 |
| `modules/briefing/documents.py` | 301 | `collect_summary(connection, now, window_hours, candidate_documents)` |
| `modules/briefing/ops.py` | 389 | `collect_summary(connection, now)` → `_thesis_health(cursor, now)`·`silent_sources(activity, now)` |
| `apps/realtime/service.py` | 583 | `_flush_timer(aggregator, repository, source_record_id, previous_closes, delay_seconds, counters, heartbeat_extra, heartbeat, clock, sleeper)` 인자 10개, `run_connection(settings, registry, repository, approval_key, heartbeat)` |
| `modules/slack.py` | 121 | `post_message(token, ...)`·`upload_file(token, ...)` — `token`이 반복. 모듈이 작아 우선순위 낮음 |

`render_blocks`·`render_text`·`_quote_section` 같은 렌더 함수는 상태가 없다. **그대로 함수다.**

### 3단계 — 상태가 `connection` 하나뿐인 수집기

`bbk.py`(483)·`boe.py`(506)·`ecb.py`(486)·`ecb_irs.py`(521)·`mof.py`(532)·
`nyse_calendar.py`(267)·`yahoo.py`(992)·`documents.py`(529)·`document_listings.py`(285).

`fetch(request)` + `store(connection, response)`가 실행당 한 번씩만 불린다. 생성자에 담을
것이 없어서 클래스로 감싸면 **규칙이 금지하는 쪽에 가깝다.** 이 모듈들은 도메인 폴더로
옮기는 것만 남는다.

## 규칙 밖의 정리 대상

전환하면서 같이 처리할 후보다. 클래스 규칙 자체는 아니다.

- **`class Cursor(Protocol)` / `class Connection(Protocol)`이 18개 파일에 중복 정의**돼 있다.
  `modules/db.py` 한 곳으로 모을 후보.
- `modules/briefing/ops.py`의 `ExpectedSource(NamedTuple)` — 규칙은 "데이터 모양은 언제나
  Pydantic 모델"이다. 저장소에 남은 유일한 비-Pydantic 데이터 모양이다.

## 함수로 두는 것이 맞는 모듈

전환 대상이 아니다. 감쌀 상태가 없다.

`blocks.py`·`trend.py`·`chart.py`·`schema.py`·`sql.py`·`period.py`·`utility.py`·`upsert.py`·
`dedup.py`·`market_session.py`·`apps/realtime/frames.py`.

`modules/llm.py`는 규칙이 명시적으로 함수라고 못박은 자리다 — 모델 정의(`document_model`·
`briefing_model`·`thesis_model`)와 오류 분류(`classify`)는 감쌀 상태가 없다.

## 검증

DAG을 돌려서 확인하지 않는다.

```bash
uv run pytest tests -q
uv run ruff check apps airflow migrations tests
uv run pyrefly check
```

- 모듈 하나 옮길 때마다 그 모듈의 테스트만 먼저 돌린다.
- `SecretStr`이 생성자를 거쳐도 유지되는지 확인한다. 예외 메시지와 로그에 URL이 들어가지
  않는 것은 기존 테스트가 본다.
- SQL은 한 글자도 바뀌지 않는다. `airflow/sql/**` 파일과 upsert 파라미터 순서를 그대로
  옮기므로 운영 DB 확인이 필요한 변경은 없다.
- 마지막에 `graphify update .`로 그래프를 갱신한다.
