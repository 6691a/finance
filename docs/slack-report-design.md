# Slack 정기 리포트 설계

- 날짜: 2026-08-18
- 상태: 초안

## 0. 목적과 범위

수집 DAG들이 쌓아 둔 데이터를 정해진 시간에 Slack 채널로 보내는 **경량 브리핑**이다.
리포트는 세 종류이고 각각 별도 채널로 간다. 우선순위 순서다.

| 순서 | 리포트 | 채널 env | 내용 |
| --- | --- | --- | --- |
| 1부 | 시장 데이터 요약 — 한국장·미국장 2종 | `SLACK_CHANNEL_MARKET` | 한국장: 장중·마감 통계. 미국장: 다음날 아침 마감 요약 + 한국 값과 조합한 평가 |
| 2부 | 뉴스·문서 평가 요약 | `SLACK_CHANNEL_DOCUMENT` | 신규 평가 문서 집계와 `value_score` 상위 문서 |
| 3부 | 수집 운영 현황 | `SLACK_CHANNEL_OPS` | `source_record` 기반 수집 성공·실패·무소식 |

본문은 **SQL 집계를 고정 템플릿의 표로 렌더링한 것**이고, LLM은 그 표의 값을 근거로
**요약**을 쓴다. 요약 길이는 한 단락으로 고정하지 않고 내용에 맞게 문장·불릿을 쓰되
상한만 둔다. 숫자는 전부 SQL이 만들고 LLM은 숫자를 만들지 않는다
([경제 문서 아카이브 설계](economic-document-archive-design.md) §1의 경계와 같다).

이 문서는 구현 전 설계다. 구현은 §6의 마일스톤 순서(공통 기반+1부 → 2부 → 3부)로 진행한다.

## 1. 기존 설계와의 관계

[경제 문서 아카이브 설계](economic-document-archive-design.md)의 3단계(`market_daily_brief`)와
4단계(카테고리 분석가·툴·`market_report`)는 미구현 상태다. 이 기능은 그 둘과 **별개**다.

- **3단계 테이블을 기다리지 않는다.** `market_daily_brief`는 거래일당 1행이라 하루 3회
  장중 주기와 맞지 않는다. 이 리포트는 원천 테이블을 직접 조회한다. 3단계가 생겨도
  이 리포트의 입력을 바꿀 이유는 없다.
- **4단계로 자라지 않는다.** 4단계는 툴을 쥔 분석가들이 저장용 리포트(`market_report`)를
  만드는 산출물이고, 이 기능은 사람이 채널에서 훑는 운영 브리핑이다. 여기에 툴 호출,
  리포트 저장 테이블, 검증 파이프라인을 붙이고 싶어지면 그건 4단계 구현이지 이 기능의
  확장이 아니다.
- 저장하지 않는다. Slack 메시지가 산출물의 전부다. 재현이 필요한 리포트는 4단계가 담당한다.

## 2. 공통 기반 (0부)

모든 리포트가 공유하는 층이다. 1부와 같은 마일스톤에서 만든다.

### 2.1 전송 — `airflow/modules/slack.py`

Bot 토큰으로 `chat.postMessage`를 부른다. 호출은 **`slack_sdk.WebClient`**다.

slack-sdk는 Airflow 이미지에 없으므로 `compose/local/airflow/requirements.txt`에
`slack-sdk>=3.33`을 추가하고 **운영 Airflow 이미지에 먼저 반영한다** — langchain-xai가
들어간 경로와 같다. requirements.txt는 이미지 빌드가 읽는 목록이라 Compose·Dockerfile
자체는 건드리지 않는다. 백엔드 pyproject.toml에 없는 패키지이므로 새 줄로만 추가한다.

```python
def post_message(
    token: SecretStr,
    channel: str,
    *,
    text: str,
    blocks: Sequence[dict[str, Any]] | None = None,
) -> str:  # 메시지 ts 반환
```

- 모듈은 Airflow를 import하지 않는다(`modules/period.py`와 같은 이유 — 테스트가 배포
  환경 없이 돌아야 한다). slack_sdk import는 scrapling처럼 허용 의존성이다.
- `WebClient`는 호출 시점에 만들고 **retry 핸들러를 붙이지 않는다.** SDK가 먼저
  재시도하면 태스크 타임아웃 안에서 몇 번을 불렀는지 로그와 어긋난다 — LLM 클라이언트를
  `max_retries=0`으로 만드는 것과 같은 이유다. 재시도는 Airflow가 한다.
- 토큰은 `WebClient` 생성자에만 넘긴다. 예외 메시지에 토큰을 넣지 않는다.
- 오류 분류는 수집기 규칙과 같다. 모듈이 slack_sdk 예외를 우리 종류로 바꾸고
  재시도 판단은 DAG가 한다. 실패 코드는 `SlackApiError.response["error"]`에 있다.

| 상황 | 예외 |
| --- | --- |
| `SlackApiError` 중 `ratelimited`(HTTP 429)·`internal_error`·`service_unavailable`·`fatal_error`·`message_limit_exceeded` | `ConnectionError` (재시도 값어치 있음) |
| 그 밖의 `SlackApiError` — `invalid_auth`·`channel_not_found`·`not_in_channel`·`invalid_blocks` 등 | `SlackError` (재시도 무의미) |
| 응답이 없는 네트워크 오류(`SlackClientError` 등) | `ConnectionError` |

Slack은 실패를 HTTP 상태가 아니라 본문 `ok: false`와 `error` 코드로 알리는 제공처다.
ECOS의 `EcosResultError`와 같은 자리에 `SlackError`가 선다.

### 2.2 LLM 코멘트 — `airflow/modules/briefing/comment.py`

`BriefingCommentator`. `DocumentAssessor`의 축소판으로, LLM 코드 규칙
(LangChain 호출, LangGraph 흐름, Pydantic 형태)을 그대로 따른다.

- call + repair 2노드 `StateGraph`, 생성자에서 한 번 `compile()`. repair는 1회만 돈다.
  출력이 비었거나 `MAX_COMMENT_CHARS`(1200)를 넘으면 교정을 요청한다. 단락 수는
  제한하지 않는다 — 요약 형태(문장 몇 개든 불릿이든)는 모델에 맡기고 상한만 지킨다.
- 출력이 한국어 평문 요약이라 `response_format` 스키마는 걸지 않는다. 검증은
  `parse()`가 한다.
- 모델은 `modules/llm.py`의 `briefing_model()`이 정한다. 지금은 `document_model()`과 같은
  `ChatXAI`지만 함수를 분리해 두어 평가 모델과 따로 바꿀 수 있게 한다. `max_retries=0`.
- 입력은 집계가 끝난 요약의 직렬화 JSON뿐이다. 원시 행이나 SQL을 주지 않는다.
- 시스템 프롬프트 규칙: 집계 숫자만 근거로 요약한다. 눈에 띄는 변화 위주로 필요한
  만큼만 쓰고(불릿 허용), 표를 다시 읽어 주는 나열은 하지 않는다. 입력에 없는 숫자를
  만들지 않는다. 투자 조언을 하지 않는다.

**코멘트 실패는 발송을 막지 않는다.** DAG 태스크가 `ConnectionError`·`LlmError`·
`CommentError`를 잡아 `comment=None`으로 템플릿만 발송하고 경고 로그를 남긴다.
그래프 안의 repair는 형식 교정이고, "그래도 리포트는 나간다"는 판단은 DAG의 몫이다.
태스크의 성패는 Slack 전송만 정한다: `SlackError`면 `AirflowFailException`,
`ConnectionError`면 올려서 Airflow가 재시도한다.

### 2.3 파트 모듈 — `airflow/modules/briefing/`

`modules/collectors/`처럼 패키지로 두고 파트당 파일 하나다: `market.py`, `documents.py`,
`ops.py`. 세 파일이 같은 네 함수 모양을 갖는다.

```python
def collect_summary(connection: Connection, now: datetime) -> XxxSummary
def render_blocks(summary: XxxSummary, comment: str | None) -> list[dict[str, Any]]
def render_text(summary: XxxSummary) -> str      # Slack text fallback 한 줄
def comment_input(summary: XxxSummary) -> str    # LLM에 줄 압축 JSON
```

- 요약 모델은 `ConfigDict(frozen=True)` Pydantic, 시각은 `AwareDatetime` UTC.
- 쿼리는 `airflow/sql/postgres/<테이블>/*.sql`에 두고 `read_sql`로 읽는다. 조회 구간은
  모듈이 계산해 파라미터로 바인딩하고 문자열 조립을 하지 않는다.
- **KST 변환은 `render_*` 안에서만 한다.** Slack은 프론트엔드가 없는 출력이라 백엔드
  변환 대상이고, IANA `Asia/Seoul`(`KST_TIMEZONE`)을 쓴다. DB와 요약 모델은 UTC를 유지한다.
- 고정폭 표 정렬(§3의 코드 블록 표)은 파트마다 다시 짜지 않고 패키지 공용 헬퍼
  하나로 둔다.

### 2.4 DAG — 4개 분리

`airflow/dags/slack_kr_market_briefing.py`, `slack_us_market_briefing.py`,
`slack_document_briefing.py`, `slack_ops_briefing.py`. 하나로 합치지 않는 이유는
스케줄과 휴장 판정이 서로 다르고(KRX 달력 vs 미국 달력), 시장 쿼리 버그가 운영
하트비트까지 침묵시키면 안 되기 때문이다. 운영 리포트는 나머지가 고장났음을 알려 줄
채널이다.

각 DAG는 태스크 하나(`send_briefing`)다: 쿼리 → 렌더 → 코멘트(best-effort) → 발송.
발송이 마지막이라 그 전 어디서 실패해도 재시도가 중복 발송을 만들지 않는다. 코멘트를
별도 태스크로 쪼개면 XCom과 `trigger_rule`이 필요해지고 LLM이 잠깐 흔들릴 때마다 빨간
태스크가 남는다.

공통 설정: `catchup=False`, `max_active_runs=1`,
`default_args={"retries": 1, "retry_delay": timedelta(minutes=2)}`,
`start_date=pendulum.datetime(..., tz=KST_TIMEZONE)`. cron은 DAG 상단 상수로 두고
KST와 UTC를 같은 줄 주석으로 병기한다. 주기가 아직 미정이므로 상수 한 줄만 바꾸면 된다.

### 2.5 설정과 비밀

`kis_quote_intraday._credentials()` 패턴 그대로, 태스크 시점에 `os.environ`에서 읽고
없으면 `AirflowFailException`이다.

| env | 형태 | 비고 |
| --- | --- | --- |
| `SLACK_BOT_TOKEN` | `SecretStr` | 봇 토큰. 로그·예외에 싣지 않는다 |
| `SLACK_CHANNEL_MARKET` / `_DOCUMENT` / `_OPS` | `str` | 채널 ID. 비밀은 아니지만 워크스페이스마다 달라 배포 설정이다 |

채널 ID를 코드 상수나 `Param`으로 두지 않는다. 상수면 저장소가 특정 워크스페이스에
묶이고, `Param`은 실행마다 바꾸는 손잡이지 배포 설정이 아니다.

### 2.6 중복 발송

**허용한다.** 발송이 태스크의 마지막 단계이고 `max_active_runs=1` + `catchup=False`라,
중복이 생기는 유일한 경로는 Slack이 수락한 뒤 응답이 타임아웃되는 경우다. 드물고,
생겨도 채널에서 바로 보인다. 발송 상태 테이블은 쓰기 경로와 스키마를 늘리는 값을 못
한다. 중복이 실제로 관측되면 그때 `(dag_id, data_interval_end)` 키 테이블로 막는다.

## 3. 1부 — 시장 데이터 요약 (한국장·미국장)

수집이 한국·미국을 포함해 글로벌이라 시장 브리핑을 **둘로 나눈다.**

- **한국장 브리핑** — 장중·마감 후에 보낸다.
- **미국장 아침 브리핑** — 미국 정규장은 KST 밤이라 장중 알림을 보내지 않는다.
  대신 다음날 아침에 밤사이 마감 결과를 보내고, 그 값과 전일 한국장 값을 **조합한
  평가**를 붙인다.

DAG는 `slack_kr_market_briefing`, `slack_us_market_briefing` 둘이고 채널은 같은
`SLACK_CHANNEL_MARKET`이다. 같은 주제(시장)를 시간대만 나눠 보내는 것이라 채널을
쪼개지 않는다.

### 3.1 한국장 브리핑

```python
SCHEDULE = "30 12,16 * * 1-5"  # KST 평일 12:30·16:30 = UTC 월~금 03:30, 07:30
```

12:30은 오전장 요약, 16:30은 마감 후다. KRX 휴장일은 `krx_open_day`로 확인해
`AirflowSkipException`으로 건너뛴다(`kis_quote_intraday._closed_today` 패턴 —
달력을 모르면 발송을 계속한다). cron이 주말을 이미 빼므로 이 확인은 공휴일용이다.

| 섹션 | SQL 파일 | 내용 |
| --- | --- | --- |
| 헤더 | — | KST 시각 + 장 상태(장중/마감 후) |
| 국내 지수·선물 | `quote_bar/select_latest_change.sql` | `DISTINCT ON (provider, symbol)` 최신 bar(48시간 lookback), `quote_symbol` 조인으로 이름·종류. 등락은 `(close - previous_close) / previous_close` — `previous_close` 컬럼이 이미 있어 전일 세션 서브쿼리가 필요 없다. `provider = 'kis'` |
| 미국 선물(실시간) | 같은 파일 | `provider = 'yahoo'` 중 선물 심볼. 미국 선물은 한국장 시간에도 거래돼 `yahoo_quote_intraday`(5분 주기)가 실시간 값을 갖고 있다. 한국장과 같이 보라고 여기 둔다 |
| 환율 | `exchange_rate/select_latest_with_previous.sql` | 통화별(USD·JPY·EUR·CNY 상수) 최신 `(date, round)`의 `exchange_standard_rate` vs 직전 고시일 마지막 회차. window 함수로 한 번에 |
| 수급·등락 | `market_investor_flow_snapshot/select_latest.sql`, `market_movement_snapshot/select_latest.sql` | 시장별 최신 스냅샷의 외국인·기관·개인 순매수, 상승·보합·하락 종목 수 |
| 💬 요약 | — | LLM 요약(§2.2). 입력은 위 섹션 값 전부 — 미국 선물 실시간이 있어 요약이 자연스럽게 양쪽을 엮는다 |
| context | — | 섹션별 데이터 기준 시각(KST). 어느 값이 얼마나 묵었는지 보이게 |

### 3.2 미국장 아침 브리핑

```python
SCHEDULE = "0 8 * * 2-6"  # KST 화~토 08:00 = UTC 월~금 23:00
```

미국 정규장 마감은 KST 05:00(서머타임)/06:00이고, 아침 수집 DAG들(07:00~07:40의
FRED·yahoo 일봉)이 끝난 뒤가 08:00이다. 미국 휴장일은 `us_equity_open_day`
(`market_session.py`)로 확인해 건너뛴다. 화~토인 이유: KST 월요일 아침에는 직전
미국 세션이 없다.

| 섹션 | SQL 파일 | 내용 |
| --- | --- | --- |
| 헤더 | — | "미국장 마감" + 해당 세션 날짜(미국 영업일) |
| 미국 지수·선물 마감 | `quote_bar/select_latest_change.sql` | `provider = 'yahoo'`, 밤사이 마감 값과 등락 |
| 주요국 10년 금리 | `indicator_observation/select_latest_pair.sql` | `indicator_series` 조인, `kind = 'government_bond' AND maturity_months = 120`, 계열별 최근 2건 pivot → bp 델타. 미국 국채 중심이지만 밤사이 갱신된 다른 나라도 같이 나온다. 제공처 추가는 마스터가 흡수하므로 쿼리를 안 고친다 |
| 전일 한국장 복기 | 3.1과 같은 파일들 | 전일 코스피·선물 마감, 수급, 환율 마지막 고시. 조합 평가의 입력으로도 쓴다 |
| 💬 조합 평가 | — | LLM 요약(§2.2). 입력에 밤사이 미국 값과 전일 한국장 값을 **함께** 넣고, 프롬프트에 "미국 값 나열이 아니라 한국장 관점에서 밤사이 변화가 갖는 맥락을 요약하라"를 명시한다 |
| context | — | 미국 세션 날짜와 각 값의 기준 시각(KST 병기) |

### 공통 렌더링

조회는 전부 `kind`·`provider`를 함께 건다(테이블 규칙). 요약 모델은 `QuoteChange`,
`FxChange`, `RateChange`, `FlowSnapshot`, `MovementSnapshot`을 공유하고, 리포트
단위로 `KrMarketSummary`·`UsMarketSummary`가 감싼다. 둘 다 `briefing/market.py`
한 파일이다 — 섹션 구성이 겹쳐서 파일을 쪼개면 사본만 생긴다.

Block Kit: `header`("📈 한국장 브리핑 · 8/18(월) 12:30 KST · 장중" /
"🌙 미국장 마감 · 8/17(현지) · 8/18(월) 08:00 KST") → 섹션별 `section`,
값은 **고정폭 코드 블록 표**로 → `divider` → 요약 `section` → `context`.
`text` fallback은 KOSPI와 USDKRW(한국장) 또는 S&P500과 US 10Y(미국장) 한 줄이다.

Slack mrkdwn에는 표 문법이 없어 코드 블록 안에서 열을 공백으로 정렬해 표를 만든다.
열 폭 계산은 `render_blocks`가 한다(값 자릿수에 맞춰 우측 정렬, ▲/▼ 부호 포함).

```
구분        종가        등락
KOSPI      2,687.45   ▲ +0.82%
K200선물     361.20   ▲ +0.95%
USDKRW     1,388.60   ▼ -0.31%
US 10Y       4.21%    ▲ +3.2bp
```

## 4. 2부 — 뉴스·문서 평가 요약

`slack_document_briefing`, 기본 스케줄:

```python
SCHEDULE = "0 8,17 * * *"  # KST 매일 08:00·17:00 = UTC -1일 23:00, 08:00
```

조회 창은 `assessed_at` 기준 최근 `WINDOW_HOURS`(기본 12)다. `published_at`이 아니라
`assessed_at`인 이유: 평가는 수집보다 늦게 따라오고, 이 리포트가 답하는 질문은
"파이프라인이 방금 무엇을 평가했나"다.

| 섹션 | SQL 파일 | 내용 |
| --- | --- | --- |
| 집계 | `document/select_briefing_summary.sql` | 창 안 신규 발견 수, 평가 수, 방향 분포(positive/negative/neutral), `assessed_at IS NULL` 백로그 |
| 상위 문서 5건 | `document/select_briefing_top.sql` | 창 안 평가 문서를 `value_score DESC, assessed_at DESC`로 5건. 제목, `source_slug`, 방향, 점수, `canonical_url`, `assessment->>'reason'`, `document_instrument` 티커 `array_agg` |
| 💬 요약 | — | 입력: 집계 + 상위 5건의 `{title, direction, value_score, reason, tickers}` |

`value_score`는 저장 단계에서 문서를 버리지 않고 리포트가 상위 N개를 고르는 데만 쓴다는
설계 그대로의 소비자가 이 쿼리다.

**0건이어도 보낸다.** "신규 평가 문서 없음 · 대기 N건" 한 줄 짧은 형태로 보내고 LLM은
부르지 않는다. `document_assessment_hourly`는 `source_record`를 남기지 않는 DAG라
이 메시지가 평가 파이프라인의 생존 신호를 겸한다.

## 5. 3부 — 수집 운영 현황

`slack_ops_briefing`, 기본 스케줄:

```python
SCHEDULE = "0 8 * * *"  # KST 매일 08:00 = UTC -1일 23:00
```

창은 실행 시각 기준 직전 24시간이다.

| 섹션 | SQL 파일 | 내용 |
| --- | --- | --- |
| 소스별 현황 | `source_record/select_briefing_window.sql` | `GROUP BY source`: 실행 수, 성공, 실패, `record_count` 합, 마지막 `completed_at`. `(source, started_at)` 인덱스를 탄다. 렌더링은 §3과 같은 고정폭 표 |
| 무소식 소스 | — (모듈 판정) | `ops.py`의 `EXPECTED_SOURCES` 상수와 대조. `ExpectedSource(source, label, weekdays_only)` — kis·dart·hana류는 평일 전용이라 주말 오탐을 막는다 |
| 예외 둘 | `exchange_rate/select_freshness.sql` + 2부 백로그 쿼리 | `source_record`에 안 잡히는 두 DAG. 환율은 `max(date)`를 최근 영업일과 비교, 평가는 백로그 건수와 최장 대기 시간 |
| 최근 실패 상세 | `source_record/select_recent_failures.sql` | 최근 실패 N건: source, `source_key`, `started_at`, `metadata`의 오류 요지(잘라서) |
| 💬 요약 | — | 실패·무소식이 있을 때만. 올그린이면 "모든 수집 정상" 템플릿 한 줄로 충분하다 |

**올그린이어도 보낸다(하트비트).** 침묵이 정상 신호면 고장으로 인한 침묵과 구분할 수
없다. 하루 한 번은 견딜 만한 소음이다.

알려진 한계: `source_record`에 `running` 행을 쓰는 수집기가 없어, 매달린 DAG는 '실행
중'이 아니라 '부재'로 보인다. 무소식 섹션이 그 부재를 잡는 것까지가 이 리포트의 몫이고,
실행 중 상태가 필요해지면 Airflow 메타데이터를 봐야 한다.

## 6. 구현 순서와 완료 조건

| 마일스톤 | 산출물 | 테스트 |
| --- | --- | --- |
| M1 | `modules/slack.py`, `llm.briefing_model()`, `briefing/comment.py`, `briefing/market.py`, 시장 SQL, `dags/slack_kr_market_briefing.py`, `dags/slack_us_market_briefing.py` | `test_slack.py`: 오류 분류표(`SlackApiError` 가짜 응답으로), 전달 인자 형태(monkeypatch `WebClient.chat_postMessage`), 예외 문자열에 토큰 미노출. `test_briefing_comment.py`: `ScriptedModel`로 성공·repair 1회·재실패·`ConnectionError` 통과. `test_briefing_market.py`: `FakeCursor`, SQL SELECT 컬럼을 모델 metadata와 대조(수집기 테스트 컨벤션), 블록 형태·KST 표기·코멘트 없는 경로, 미국장 조합 평가 입력에 한국 값 포함 |
| M2 | `briefing/documents.py`, 문서 SQL, `dags/slack_document_briefing.py` | `test_briefing_documents.py`: 상위 N 정렬·동점 처리, 0건 짧은 형태가 LLM을 건너뜀, 백로그 집계 |
| M3 | `briefing/ops.py`, 운영 SQL, `dags/slack_ops_briefing.py` | `test_briefing_ops.py`: 무소식 판정(주말 `weekdays_only` 포함), 환율 신선도 vs 영업일, 올그린이 하트비트를 렌더하고 코멘트를 건너뜀 |

전 마일스톤 공통: Compose·Dockerfile 무변경(새 의존성은 slack-sdk 하나, M1에서
requirements.txt로 추가하고 운영 이미지 반영을 선행), `modules/` 새 파일은
Airflow import 없음.

## 7. 운영 준비물

- `compose/local/airflow/requirements.txt`에 `slack-sdk>=3.33` 추가 후 이미지 재빌드.
  운영 Airflow 이미지에도 같은 줄이 먼저 들어가야 배포할 수 있다.
- Slack 앱 생성, Bot Token Scopes에 `chat:write`.
- 채널 3개 생성 후 각 채널에 봇 초대(`/invite`). 초대 없으면 `not_in_channel`로 죽는다.
- `compose/local/airflow/.env`(운영은 해당 환경 변수 주입)에 키 4개:
  `SLACK_BOT_TOKEN`, `SLACK_CHANNEL_MARKET`, `SLACK_CHANNEL_DOCUMENT`, `SLACK_CHANNEL_OPS`.
- LangSmith 추적이 켜져 있으면 코멘트 입력(집계 JSON)이 외부로 나간다. 원문 본문은
  2부 상위 문서의 제목·reason뿐이지만, 추적 정책은 평가 DAG와 같은 기준을 따른다.
