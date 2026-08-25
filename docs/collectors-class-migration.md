# 수집기 클래스 전환

`.claude/CLAUDE.md`의 "클래스와 함수를 가르는 기준"을 저장소에 적용하는 진행 문서다.
목표 형태, 전환 현황, 남은 순서를 여기 한 곳에 둔다. 새 수집기는 처음부터 이 형태로 쓴다.

**제목은 수집기지만 범위는 저장소 전체다.** 1·3단계가 `airflow/modules/collectors/`를 보고,
2단계는 그 밖의 흐름 코드와 `apps/realtime/`까지 본다. **세 단계와 규칙 밖 정리까지 전부
끝났다(2026-08-25).** 이 문서는 이제 "왜 그렇게 나눴나"의 기록이고, 새 수집기·새 흐름
코드가 따라야 할 형태다.

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
| `collectors/market/kis_overseas_index.py` | `KisOverseasIndexCollector` | 2026-08-22 (이동 2026-08-25) |
| `collectors/market/kis_investor_flow.py` | `KisInvestorFlowCollector` | 2026-08-22 |
| `collectors/indicator/fred.py` | `FredCollector` | 2026-08-23 |
| `collectors/indicator/ecos.py` | `EcosCollector` | 2026-08-23 |
| `collectors/calendar/kis_market_calendar.py` | `KisMarketCalendarCollector` | 2026-08-23 |
| `collectors/document/dart.py` | `DartCollector` | 2026-08-23 |
| `collectors/market/kis_positioning.py` | `KisPositioningCollector` | 2026-08-23 |
| `collectors/market/kis_quote.py` | `KisQuoteCollector` | 2026-08-23 (`kis.py`에서 분리 2026-08-25) |

### 1단계 — 자격 증명을 인자로 도는 수집기 (완료, 2026-08-23)

규칙이 정확히 겨냥한 것이고 이득이 가장 컸다. 8모듈 전부 클래스다. DAG 호출 지점 47개에서
자격 증명 인자가 사라졌고 SQL은 한 글자도 바뀌지 않았다.

모듈 함수로 남긴 것: `expiry_date`·`front_contract`·`parse_bars`·`parse_market_movement`·
`parse_observations`·`parse_financials`·`parse_provisional`·`us_session_date`·
`fold_us_settlement`·`normalized_report_name`·`is_provisional`·`periodic_report`·
`pending_earnings`·`session_days`와 `_day`·`_decimal`·`_int`·`_text` 계열 전부.
`kis.py`에는 `send_get`·`issue_token`·`access_token`·`TokenStore` Protocol이 남았다.
`dart.py`의 전송 `_get`도 같은 이유로 모듈 함수다 — 클래스의 `_call`이 키를 붙여 그 위를 감싼다.

`kis.py`만 루트에 있다. 전송층과 Enum(`DomesticFuture`·`DomesticIndex`·`DomesticStock`·
`StockExchange`)을 모든 KIS 수집기와 DAG이 import하기 때문이고, `KisQuoteCollector`를
`market/kis_quote.py`로 떼어낸 뒤에도 그대로다. 어디서 갈랐는지는 3단계에 있다.

### 2단계 — 연결·기준 시각·레지스트리를 도는 흐름 코드 (완료, 2026-08-25)

수집기가 아니지만 같은 규칙에 걸리던 곳이다. 기준 구현은
`airflow/modules/thesis_nxt_review.py`의 `NxtAfterHoursReview`였다 — 그 클래스 docstring이
규칙을 그대로 적어 뒀다("연결과 세션 날짜가 상태다… 함수로 두면 인자에 매번 다시 들어간다",
"기준 시각 계산은 모듈 함수다"). 나머지는 전부 그것의 미적용판이었다.

신호는 **반복 인자 개수**였다. 모듈 최상위 함수의 인자 이름을 AST로 세서 두 번 이상
나오는 것만 봤다. 파일 줄 수는 커밋마다 낡으므로 신호로 쓰지 않는다.

| 위치 | 전(반복 인자) | 후 |
| --- | --- | --- |
| `modules/thesis.py` | `connection` 12, `run_date` 6, `as_of_at` 4, `run_slot` 4, `registry` 3. `store_theses` 인자 11 | `ThesisStore(connection)` — 조회·저장 열두 개가 메서드. `connection` 0 |
| `modules/thesis_common.py` | `conn` 7(저장소 최다), `run_date` 3, `as_of_at` 3. `build_and_store` 인자 9 | `ThesisRun(connection, run_date, as_of_at)` — 반복 0, `build_and_store` 인자 6 |
| `modules/thesis_forecast.py` | `conn` 2, `run_date` 2 | `PreOpenForecast(connection, run_date)` — 반복 0 |
| `modules/thesis_review.py` | `conn` 2, `run_date` 3 | `PostCloseReview(connection, run_date)` — `run_date` 2(순수 시각 계산 둘) |
| `modules/expectation.py` | `connection` 3, `dag_run_id` 2, `prompt_version` 2 | `ExpectationStore(connection, prompt_version)` — `connection` 0 |
| `modules/assessment.py` | `connection` 3, `prompt_revision` 2. `store_assessment` 인자 8 | `AssessmentStore(connection, prompt_revision)` — 반복 0, `store` 인자 6 |
| `modules/briefing/market.py` | `connection` 6, `now` 5 | `MarketBriefingReader(connection, now)`(2026-08-24 선행) |
| `modules/briefing/ops.py` | `now` 4, `connection` 1 | `OpsBriefingReader(connection, now)` — `now` 2(렌더러) |
| `modules/slack.py` | `token` 2, 호출마다 `WebClient` 재생성 | `SlackClient(token)` — 클라이언트 하나를 들고 돈다 |
| `apps/realtime/service.py` | `_flush_timer` 인자 10, `settings` 3, `repository` 3, `heartbeat`·`approval_key`·`clock`·`sleeper` 각 2 | `KisConnection`(연결 한 번분) + `RealtimeService`(재접속 루프). `_flush_timer` 인자 0 |

전환 뒤 이 모듈들의 최상위 함수에 남은 반복 인자는 `row`·`summary`·`rows`·`scope`처럼
**호출마다 바뀌는 입력**뿐이다. `connection`·`conn`은 한 곳도 남지 않았다.

같이 정한 것:

- **`ThesisStore`의 생성자는 연결뿐이다.** 채점(`pending_grades`→`store_grade`)과 해설
  (`pending_narratives`→`store_narratives`)은 이 실행의 날짜가 아니라 *지난* 추론의 날짜를
  돌며 부른다. `run_date`·`run_slot`을 생성자에 담으면 그 값이 호출마다 거짓이 된다.
- **`dag_run_id`는 어디서도 생성자에 넣지 않았다.** 저장 한 번에만 쓰인다.
- **`connection`을 생성자가 받는다.** 수집기 규칙(`connection`은 `store` 메서드 인자)과
  반대인데, 흐름 코드는 트랜잭션 하나가 객체 하나이기 때문이다 — DAG이 문서마다 새 연결을
  열어 저장하므로 그 연결의 수명이 곧 객체의 수명이다. 수집기는 `fetch`와 `store`의
  트랜잭션 경계를 DAG이 쥐어야 해서 다르다.
- **렌더 함수는 그대로 함수다.** `render_blocks`·`render_text`와 `briefing/market.py`의
  `_*_section` 스무 개는 `summary` 하나를 받는 순수 변환이다. 클래스로 묶으면 전부
  `@staticmethod`가 된다.
- `modules/briefing/documents.py`는 대상이 아니다. DB를 만지는 최상위 함수가
  `collect_summary` 하나뿐이라 감쌀 상태가 없다.

### 3단계 — 폴더 이동 (완료, 2026-08-25)

`bbk.py`·`boe.py`·`ecb.py`·`ecb_irs.py`·`mof.py`·`nyse_calendar.py`·`yahoo.py`·
`documents.py`·`document_listings.py` 아홉은 **함수로 남는다.** `fetch(request)` +
`store(connection, response)`가 실행당 한 번씩만 불려서 생성자에 담을 것이 없다 —
클래스로 감싸면 규칙이 금지하는 쪽에 가깝다. 이들은 도메인 폴더로 옮기기만 했다.

`kis_overseas_index.py`(이미 클래스)도 `market/`으로 내려갔다.

옮긴 뒤의 구조:

```
airflow/modules/collectors/
    kis.py                      KIS 공용 층(인증·전송·식별자). 아래 다섯이 함께 쓴다
    analyst/kis_opinion.py
    calendar/kis_market_calendar.py  nyse_calendar.py
    document/dart.py  naver_research.py  documents.py  document_listings.py
    indicator/fred.py  ecos.py  bbk.py  boe.py  ecb.py  ecb_irs.py  mof.py
    market/kis_quote.py  kis_investor_flow.py  kis_positioning.py
           kis_overseas_index.py  yahoo.py
```

**`kis.py`만 루트에 남는다.** 그 판단은 이렇게 갈랐다:

- **`market/kis_quote.py`로 내려간 것** — 분봉·일봉 조회에만 쓰는 것. 차트 엔드포인트
  상수, 응답 모델과 파서(`parse_bars`·`parse_market_movement`·`_daily_index_*`), 봉 테이블
  upsert, `last_settled_close`, 그리고 `KisQuoteCollector`.
- **`kis.py`에 남은 것** — KIS를 부르는 **다섯 수집기가 함께 쓰는 층**. 토큰 발급·캐시
  (`issue_token`·`access_token`), 전송(`send_get`), 오류 종류(`KisHTTPError`·
  `KisResultError`·`KisPayloadError`), 식별자 Enum(`DomesticFuture`·`DomesticIndex`·
  `DomesticStock`·`StockExchange`)과 거래장 손잡이(`rest_exchanges`), 공용 봉 모델
  (`QuoteBar`)과 `_decimal`.
- 세션 창 상수(`SESSION_FIRST_BAR`·`MAX_STOCK_BAR_CALLS` 등)는 `StockExchange`의
  property가 읽으므로 **Enum과 같은 파일에 둔다.** 차트 엔드포인트 상수와 헷갈리기 쉬운데,
  이쪽은 거래장의 성질이지 조회 방식이 아니다.

`kis.py`는 수집기가 아니므로 도메인 폴더에 들어가지 않는다. 도메인 폴더는 "무엇을 수집하나"로
나뉘고, 이 파일은 "어떻게 부르나"다.

**테스트가 가짜를 끼우는 지점이 바뀌었다.** `KisQuoteCollector`가 `send_get`을 이름으로
import하므로 이제 `monkeypatch.setattr(kis_quote, "send_get", ...)`다. 형제 수집기가
이미 쓰던 방식과 같아졌다.

## 규칙 밖의 정리 대상 (완료, 2026-08-25)

클래스 규칙 자체는 아니지만 전환하면서 같이 처리한 둘이다.

- **`Cursor`/`Connection` Protocol이 20개 파일에 중복 정의**돼 있었다. `modules/db.py`
  한 벌로 모았다. 세 보던 것과 달리 스무 개가 **서로 달랐다** — `__exit__` 반환형이
  `object`와 `bool | None`으로 갈리고, `parameters`가 `tuple`·`Any`·`Sequence[Any]`로
  갈리고, `executemany`·`fetchone`·`fetchall`은 있는 곳과 없는 곳이 섞여 있었다.
  새 모듈이 가까운 파일에서 복사해 온 결과라 그 차이는 의도가 아니라 사고였다.
  - `Cursor`는 **여섯 메서드를 다 요구한다.** 모듈마다 좁히면 다시 스무 개가 된다.
  - `Connection`은 `cursor()`만 요구한다. 커밋 경계는 대부분 DAG이 `utility.atomic`으로
    쥔다. 스스로 커밋하는 `thesis.ThesisStore`와 `dedup.link_duplicates`만
    `TransactionalConnection`을 쓴다.
- **`briefing/ops.py`의 `ExpectedSource`가 `NamedTuple`이었다.** 규칙은 "데이터 모양은
  언제나 Pydantic 모델"이고, 저장소에 남은 유일한 비-Pydantic 데이터 모양이었다.
  `BaseModel` + `ConfigDict(frozen=True)`로 바꾸고 생성을 키워드 인자로 고쳤다.

## 함수로 두는 것이 맞는 모듈

전환 대상이 아니다. 감쌀 상태가 없다. **왜 아닌지를 같이 적는다** — 반복 인자만 보고
다시 후보로 올리는 일이 없어야 한다.

`blocks.py`·`trend.py`·`chart.py`·`schema.py`·`sql.py`·`period.py`·`utility.py`·`upsert.py`·
`apps/realtime/frames.py` — 순수 변환뿐이고 반복 인자가 없거나 값이다.

검토해서 제외한 것(2026-08-25):

| 위치 | 겉보기 신호 | 제외 이유 |
| --- | --- | --- |
| `modules/technical.py` | `bars`·`period` 각 3 | 호출마다 바뀌는 **입력**이지 상태가 아니다. 규칙상 메서드 인자에 해당한다 |
| `modules/technical_signals.py` | — | 최상위 함수가 `detect_and_store` 하나. 반복 0 |
| `modules/market_session.py` | `connection` 3, `session_date` 3 | `krx_open_day`·`us_equity_open_day`가 `market_open_day`의 한 줄 래퍼다. DB를 만지는 건 하나뿐이고 각 DAG은 하나만 부른다 |
| `modules/dedup.py` | `connection` 3 | `link_duplicates`가 유일한 진입점이고 나머지는 순수 판정이다. DAG은 그 한 줄만 부른다 |
| `modules/llm.py` | — | 규칙이 명시적으로 함수라고 못박은 자리다. 모델 정의와 오류 분류는 감쌀 상태가 없고 **API 키를 우리가 안 쥔다** |
| `modules/briefing/market.py`의 렌더러 | `summary` 20 | 저장소 최다 반복이지만 `MarketSummary` 모델을 받는 렌더러 스무 개다. 클래스로 묶으면 전부 `@staticmethod`가 된다 — 규칙이 금지하는 쪽 |
| `modules/briefing/documents.py` | `summary` 8, `picks` 5 | 전부 렌더러·프롬프트 조립. DB를 만지는 최상위 함수는 하나 |
| `migrations/routing.py` | `include_table` 인자 6 | 전부 호출마다 바뀌는 값이다. 순수 함수로 두는 것이 파일 docstring에 적힌 의도다 |
| `migrations/env.py` | `database_alias` 3, `partitions` 2 | Alembic이 import 부수효과로 실행하고 `context`가 전역 싱글턴이라, 클래스로 감싸도 모듈 전역이 그대로 남는다 |
| `apps/realtime/heartbeat.py` | `path` 2 | `healthcheck`는 `config.yaml` 없이 도는 docker 진입점이고 테스트가 모듈 함수로 부른다 |
| `apps/core/database.py`의 `table_options` 계열 | `table` 반복 | `Table` 하나를 읽는 순수 함수. 묶으면 전부 staticmethod |
| `migrations/versions/**` | `_run(name)` 27파일 | 리비전은 불변 기록이다. 손대지 않는다 |

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

## 검토 기록

### 2026-08-25 — 2단계 완료

2단계 아홉 곳을 전부 클래스로 옮겼다. **SQL은 한 글자도 바뀌지 않았고** 인자 이동과
호출부 갱신뿐이다. `uv run pytest tests -q` 1953건 통과, `ruff`·`pyrefly` 0건.

같이 고친 것:

- 2단계 표의 신호 열을 **줄 수에서 반복 인자 개수로** 바꿨다. 줄 수는 커밋마다 낡는다.
- `modules/expectation.py`가 표에 없었다. 문서 작성 뒤 생겼고 `assessment.py`와 같은 모양이다.
- `thesis_nxt_review.py`의 `NxtAfterHoursReview`를 2단계 기준 구현으로 명시했다.
  그 커밋이 형제 모듈을 안 건드려서 세 슬롯 중 하나만 클래스인 비대칭이 남아 있었다.
- 인자 개수를 다시 셌다. `build_and_store` 8→9(`past` 추가), `store_theses` 10→11
  (`precedents` 추가)로 문서보다 늘어 있었다.
- Protocol 중복 18→20파일.
- `modules/briefing/documents.py`를 표에서 뺐고, `migrations/env.py`·`heartbeat.py`·
  `technical_signals.py`·`market_session.py`·`dedup.py`를 검토해 "함수로 두는 것이 맞는"으로
  판정했다.

남은 것은 3단계(수집기 아홉 모듈의 폴더 이동)와 규칙 밖 정리 둘(`modules/db.py`,
`ExpectedSource`)이다.

### 2026-08-25 — 3단계와 규칙 밖 정리 완료

폴더 이동 열 모듈, `kis.py` 분리, Protocol 20→1, `ExpectedSource` Pydantic화. **동작은
바뀌지 않았다** — 파일 위치와 import 경로, 타입 선언뿐이다. `uv run pytest tests -q`
1953건 통과, `ruff`·`pyrefly` 0건.

이 문서의 세 단계가 모두 끝났다. 남은 규칙은 문서 앞부분("목표 형태", "폴더 구조",
"함수로 두는 것이 맞는 모듈")이 갖는다.
