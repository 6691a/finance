# 클래스 밀집 파일 분리 감사

> 최초 조사: 2026-08-25, `d57fe1e`
> 갱신: 2026-08-25, `c10c167` — 클래스 전환이 끝난 뒤 다시 측정했다.
> 범위: `apps/`, `airflow/`의 운영 Python 코드. 테스트와 마이그레이션 리비전은 집계에서 제외한다.
> 성격: 구현 계획이 아니라 분리 필요성·경계·위험을 기록한 감사 문서다.

이 문서는 클래스 전환 규칙과 수집기 폴더 이동을 다루는
[`docs/collectors-class-migration.md`](../collectors-class-migration.md)를 대체하지 않는다.
그 문서가 **클래스로 묶을 동작**을 정한다면, 이 문서는 **한 모듈 안의 서로 다른 변경 축을
어디서 파일로 가를지**만 정한다. 그 문서의 세 단계는 전부 끝났고, 여기 남은 것은 파일 분리다.

## 결론

분리 가치가 확인된 곳은 여섯 곳이었고 그중 하나(`collectors/kis.py`)는 절반이 끝났다.

LOC는 `c10c167` 기준이다. 클래스 전환이 이 파일들을 여럿 건드려 최초 조사 때와 다르다.

| 우선순위 | 파일 | LOC | 판정 |
| --- | --- | ---: | --- |
| P0 | `apps/models/market.py` | 1,916 | 세션·시세·기업 이벤트·포지셔닝·수급 모델을 패키지로 분리 |
| P0 | `airflow/modules/thesis.py` | 3,075 | 도메인·도구·생성·사후평가·렌더링을 역할 모듈로 분리 |
| P0 | `airflow/modules/briefing/market.py` | 1,498 | 조회 모델/reader와 무상태 renderer를 분리 |
| P1 | `apps/models/analysis.py` | 986 | thesis·stock event·technical signal 모델을 분리 |
| P1 | `airflow/modules/expectation.py` | 849 | LLM 추출과 결정론적 판정을 다음 기능 변경 때 분리 |
| 완료 | `airflow/modules/collectors/kis.py` | 1,622→537 | 전송층과 시세 수집을 갈랐다(2026-08-25). 남은 절반은 P0-3 |

`assessment.py`, `collectors/document/dart.py`, `thesis_tools.py`, `thesis_state.py`는 클래스가
많아 보여도 지금은 나누지 않는다. 클래스 수가 아니라 **서로 독립적으로 바뀌는 책임이 둘
이상인지**가 기준이다.

## 조사 근거

- Graphify 질의로 `collectors-class-migration.md`, 수집기 하위 패키지, 관련 DAG·테스트의
  연결을 먼저 확인했다.
- AST로 운영 소스 106개, 33,557 LOC, 최상위 클래스 458개를 집계했다. 파일 LOC 중앙값은
  210이다.
- 후보별 top-level symbol 구간, 역방향 import, 2026-07-01 이후 변경 이력을 대조했다.
- `Cursor`/`Connection` Protocol이 20개 파일에 반복돼 클래스 수를 파일마다 최대 2개씩
  부풀렸다. 따라서 클래스 개수만으로 분리 후보를 정하지 않았다. **이 중복은 2026-08-25에
  `airflow/modules/db.py` 한 곳으로 모였다.** 이제 클래스 수가 파일의 무게를 그대로 말하지만,
  이 문서의 표는 다시 세지 않고 LOC만 갱신했다.

분리 신호는 다음 중 둘 이상일 때만 유효하다.

1. 다른 이유로 변경되는 책임이 한 파일에 섞여 있다.
2. 공용 기반 코드 때문에 관계없는 소비자가 큰 모듈을 import한다.
3. 선택 의존성이나 무거운 import가 DAG 로딩 경계를 넘는다.
4. 이미 저장소에 같은 책임 경계를 표현하는 폴더·모듈 관례가 있다.
5. 이동 뒤에도 각 파일의 소비자와 검증 방법을 명확히 말할 수 있다.

## P0-1. `apps/models/market.py`

### 문제

이 파일은 다른 모델 파일(`content.py` 371 LOC, `reference.py` 295 LOC, `raw.py` 112 LOC)과
비교해도 명확한 이상치다. 내부 결합은 다음 묶음 안에 머물지만, 묶음끼리는 함께 바뀔 이유가
거의 없다.

| 현재 구간 | 책임 |
| --- | --- |
| L24–224 | 지표 관측, 시장 세션, 거래소 vocabulary |
| L244–626 | 분봉·일봉 공통 컬럼과 자산군별 시세 모델 |
| L629–887 | 공시와 실적 모델 |
| L890–1478 | 시장 등락·신용·증시자금·공매도·대차 |
| L1481–1848 | 장중 추정 및 확정 투자자 수급 |
| L1851–1916 | 애널리스트 의견 |

직접 import하는 파일은 28개이고, `apps/models/__init__.py`가 31개 모델을 다시 노출한다.
파일을 나누는 것보다 **모든 모델이 Alembic metadata에 계속 등록되는지**가 더 큰 위험이다.

### 권장 구조

```text
apps/models/market/
    __init__.py       기존 `apps.models.market` import와 metadata 등록을 유지
    sessions.py       MarketCode, SessionVerifier, MarketSession
    series.py         IndicatorObservation, bar/daily mixin과 시세 모델
    fundamentals.py   DisclosureEvent, EarningsFact, StockAnalystOpinion과 enum
    positioning.py    KrxMarket, 신용·자금·공매도·대차 모델
    investor_flow.py  추정/시장/확정 투자자 수급 모델
```

`market.py`와 `market/`을 동시에 남기지 않는다. 한 변경에서 패키지로 전환하고,
`market/__init__.py`가 모든 하위 모델을 명시적으로 import하도록 한다.
`config.yaml`은 `apps.models`를 마이그레이션 모델 모듈로 읽으므로
`apps/models/__init__.py`의 등록 경로도 유지한다.

### 완료 조건

- `Base.metadata.tables`의 테이블 이름 집합이 이동 전후 동일하다.
- Alembic autogenerate가 테이블 삭제·재생성을 만들지 않는다.
- `__tablename__`, 제약 이름, 컬럼 순서, `table_options()`는 바꾸지 않는다.
- `tests/models/test_market_models.py`는 새 책임 경계와 같이 나누되 동작 단언은 바꾸지 않는다.

## P0-2. `airflow/modules/thesis.py`

### 문제

저장소에서 가장 큰 Python 파일이며 최근 약 두 달 동안 28개 커밋이 닿았다. 이미
`thesis_common.py`, `thesis_state.py`, `thesis_tools.py`, `thesis_forecast.py`,
`thesis_review.py`, `thesis_nxt_review.py`가 역할 접미사 관례를 만들었지만 핵심 파일에는 다섯
변경 축이 남아 있다.

| 현재 구간 | 책임 |
| --- | --- |
| L252–498 | 공통 enum, 점수 계산, 근거 식별 |
| L499–1546 | 도구 입력 모델, `ThesisToolbox`, 툴 결과 변환 |
| L1554–2231 | 답변/초안 모델, `ThesisBuilder`, 생성 결과 저장 |
| L2245–2842 | 채점, 사후 해설, `FollowupNarrator`, 결과 저장 |
| L2898–3090 | 저장 결과 조회와 Slack 렌더링 |

특히 `thesis_common.py`는 DagBag 30초 제한 때문에 LangChain·LangGraph를 함수 안에서 늦게
import한다. 잘못된 facade가 모든 하위 모듈을 다시 import하면 파일은 작아져도 이 경계는
악화된다.

### 권장 구조

```text
airflow/modules/
    thesis_domain.py      enum, Evidence/Subject, 점수·정규화 같은 경량 도메인 코드
    thesis_toolbox.py     도구 입력, ThesisToolbox, 툴 결과 변환
    thesis_generation.py  답변/초안 모델, 프롬프트, ThesisBuilder, 생성 결과 저장
    thesis_outcomes.py    채점, 사후 해설 모델/프롬프트, FollowupNarrator, 결과 저장
    thesis_render.py      저장 결과 조회와 Slack 렌더링
```

기존 `thesis_state.py`는 Airflow와 LangChain 사이의 경량 계약이고, `thesis_tools.py`는 작은
Pydantic 도구 응답 카탈로그이므로 그대로 둔다. 영구 호환 facade는 만들지 않는다. 현재
직접 import 파일이 5개뿐이므로 각 소비자가 필요한 역할 모듈을 직접 import하는 편이
의존성이 더 분명하다.

### 완료 조건

- `thesis_common.py`와 DAG의 모듈 import 단계가 LangChain·LangGraph 전체를 불러오지 않는다.
- SQL 상수와 프롬프트 revision 값은 이동만 하고 내용은 바꾸지 않는다.
- `tests/modules/test_thesis.py`(3,242 LOC)는 toolbox, generation, outcomes, render 경계로
  함께 나눈다.
- 파일 이동과 `ThesisStore` 같은 새 상태 객체 도입은 같은 변경에 섞지 않는다.

## P0-3. `airflow/modules/collectors/kis.py` (절반 완료)

> **2026-08-25에 첫 변경이 끝났다.** 공용 전송층이 `collectors/kis.py`(537 LOC)에 남고
> 시세 코드가 `collectors/market/kis_quote.py`(1,162 LOC)로 내려갔다. 아래 "남은 것"만
> 유효하고 나머지는 왜 그렇게 갈랐는지의 기록이다.

### 문제

클래스 23개 중 실제 동작 클래스는 `KisQuoteCollector` 하나다. 문제는 개수가 아니라 공용
전송층과 네 종류의 시세 흐름이 한 import 경로에 있다는 점이다.

- L136–382: 상품 catalog, Protocol, 오류, 계약월 계산
- L385–652: 공용 응답 모델, 토큰 발급, `send_get`
- L655–917: 분봉과 시장 등락
- L918–1009: 지수 일봉
- L1026–1622: 분봉·시장 등락·지수 일봉을 모두 가진 `KisQuoteCollector`

생산 코드 16개가 이 모듈을 import하지만 실제 `KisQuoteCollector` 소비자는 4개뿐이다.
나머지는 `KisPayloadError`, `KisResultError`, `access_token`, `Connection`, `send_get` 같은 공용
KIS client 계약 때문에 1,622 LOC 모듈에 연결된다. 이 문제는 기존
`collectors-class-migration.md`의 3단계와 같은 결론이다.

### 권장 구조

```text
airflow/modules/collectors/
    kis.py                    KIS catalog, Protocol, 오류, token/auth, send_get
    market/
        kis_quote.py          분봉·시장 등락 DTO/파서와 수집기
        kis_index_daily.py    지수 일봉 DTO/파서와 수집기
```

첫 변경은 공용 전송층을 루트 `kis.py`에 남기고 quote 코드를 `market/`으로 옮기는 기계적
이동만 한다. 지수 일봉을 별도 수집기로 가르는 것은 두 번째 변경으로 둔다. 수집기 하위
`__init__.py`는 재수출하지 않고 DAG이 실제 모듈을 직접 import한다.

### 완료 조건

- `send_get`, 토큰 갱신, 오류 종류와 메시지가 바뀌지 않는다.
- SQL 문자열과 upsert 파라미터 순서를 바꾸지 않는다.
- `tests/collectors/test_kis.py`(1,253 LOC)는 transport, quote, index-daily 경계로 함께 나눈다.
- 폴더 이동과 수집 알고리즘 변경을 같은 커밋에 넣지 않는다.

### 남은 것

- **지수 일봉을 별도 수집기로 가른다.** 지금은 분봉·시장 등락과 함께 `kis_quote.py`에 있다.
  계획대로 두 번째 변경이다.
- **`tests/collectors/test_kis.py`(1,256 LOC)가 아직 한 파일이다.** 소스가 둘로 갈렸는데
  테스트는 그대로라 어느 경계를 덮는지가 파일에서 안 보인다.

## P0-4. `airflow/modules/briefing/market.py`

### 문제

모듈 docstring이 말하듯 한국장/미국장 리포트 자체는 같은 데이터와 렌더러를 공유하므로 둘로
나누면 안 된다. 실제 경계는 시장별이 아니라 **조회와 표현**이다.

- L194–480: 조회 결과 DTO
- L483–850: 연결과 기준 시각을 가진 `MarketBriefingReader`
- L853–1513: 상태 없는 Slack renderer와 표시 helper

최근 약 두 달 동안 22개 커밋이 닿았고, 조회 SQL 변경과 표시 변경이 같은 파일에서 충돌한다.
렌더러를 클래스로 바꾸는 것은 해결책이 아니다.

### 권장 구조

```text
airflow/modules/briefing/
    market_data.py   SQL, 조회 DTO, MarketScope, MarketBriefingReader, 세션 계산
    market.py        render_blocks/render_text와 표시 helper
```

두 DAG과 `briefing/chart.py`는 필요한 모듈을 직접 import한다. `market.py`가 reader를 다시
노출하는 facade는 두지 않는다.

`MarketBriefingReader`는 이미 있다. **여기서 남은 것은 클래스 전환이 아니라 파일 분리뿐이다.**
`docs/collectors-class-migration.md`도 2026-08-25에 이 모듈을 완료로 옮겼다.

## P1 후보

### `apps/models/analysis.py`

세 aggregate가 한 파일에 있다.

- L23–81, L176–598: thesis vocabulary와 `Thesis*`
- L103–148, L601–880: stock-event vocabulary와 `StockEvent*`
- L882–986: `TechnicalSignalKind`, `TechnicalSignal`

`apps/models/analysis/` 패키지의 `thesis.py`, `events.py`, `technical.py`로 나누고
`__init__.py`에서 기존 이름을 명시적으로 노출하는 것이 맞다. `TechnicalSignal`이 쓰는
`ThesisDirection` 의존은 `technical -> thesis` 한 방향으로 유지한다. `EVENT_METRICS`와
`market.EarningsMetric`은 import로 합치지 않는다. 현재처럼 값을 중복하고 테스트로 일치를
검증해야 `apps.models` 초기화 순환을 만들지 않는다.

### `airflow/modules/expectation.py`

파일 docstring부터 추출과 판정의 두 층을 선언한다.

- L133–305: 기간·단위·집계·분류 순수 함수
- L308–681: LLM 추출, 검증, 저장
- L684–860: DB에서 실제값을 읽는 결정론적 판정과 Slack 표현

새 파일이라 변경 이력이 한 번뿐이므로 지금 이동만 하는 작업은 미룬다. 다음에 추출 프롬프트나
판정 규칙 중 하나를 바꿀 때 `expectation_extraction.py`와 `expectation_judgment.py`로 나눈다.
생산 소비자는 DAG 하나뿐이므로 호환 facade 없이 직접 import를 바꾸면 된다.

## 지금 나누지 않는 파일

| 파일 | LOC / 클래스 | 유지 이유 |
| --- | ---: | --- |
| `airflow/modules/thesis_tools.py` | 353 / 25 | 작은 Pydantic DTO 카탈로그이며 운영 소비자가 `thesis.py` 하나뿐 |
| `airflow/modules/thesis_state.py` | 178 / 11 | 관측 상태/XCom 계약을 모은 의존성 방화벽이며 9개 파일이 사용 |
| `airflow/modules/collectors/indicator/ecos.py` | 450 / 15 | 한 제공처의 wire model과 collector가 응집돼 있음 |
| `airflow/modules/assessment.py` | 691 / 14 | 두 LangGraph 클래스와 DTO가 하나의 평가 배치 흐름을 구성 |
| `airflow/modules/collectors/document/dart.py` | 852 / 13 | 한 인증·전송 계약 아래 공시와 실적 파서가 이미 함수 경계로 분리됨 |
| `airflow/modules/collectors/market/yahoo.py` | 992 / 18 | `market/` 이동은 2026-08-25에 끝났다. intraday/daily 경계 분리는 다음 기능 변경 때 다시 본다 |
| `airflow/modules/collectors/market/kis_positioning.py` | 943 / 9 | 한 KIS positioning 수집기의 요청/응답 모델이며 두 번째 소비자가 없음 |

DTO 한 개당 파일 하나를 만드는 방식은 사용하지 않는다. 한 collector만 소비하는 요청·응답
모델은 collector와 같이 둔다. 20개 파일의 `Cursor`/`Connection` 중복을 `modules/db.py`로
모으는 일은 이 감사의 파일 분리와 별개였고, 2026-08-25에 따로 끝났다.

## 권장 실행 순서

각 항목은 독립 변경으로 처리한다. 한 번에 여러 곳을 옮기지 않는다.

1. `briefing/market.py` 조회/렌더 분리로 가장 작은 패턴을 검증한다.
2. `apps/models/market.py`를 패키지로 옮기고 metadata 무변경을 검증한다.
3. `thesis.py`를 domain → toolbox → generation → outcomes → render 순으로 이동한다.
4. `apps/models/analysis.py`는 모델 파일 이동만 하는 독립 변경으로 처리한다.
5. `expectation.py`는 다음 기능 변경과 함께 두 변경 축을 가른다.
6. KIS 지수 일봉 수집기와 `test_kis.py` 분리는 남은 절반이라 언제 해도 된다(P0-3).

**3번은 `ThesisStore`가 이미 들어간 뒤의 파일을 옮긴다.** 클래스 전환과 파일 이동을 섞지
않기로 한 것이 지켜졌고, 순서는 전환이 먼저였다.

공통 검증 명령은 기존 문서와 같다.

```bash
uv run pytest tests -q
uv run ruff check apps airflow migrations tests
uv run pyrefly check
graphify update .
```

ORM 모델 이동에는 metadata 테이블 집합 대조를, thesis 이동에는 DagBag/import 시간 확인을,
수집기 이동에는 해당 collector 테스트를 각각 먼저 실행한다.
