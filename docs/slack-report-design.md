# Slack 정기 리포트 설계

- 날짜: 2026-08-18
- 상태: 구현 완료

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

구현이 끝난 상태이고 이 문서는 왜 그렇게 만들었는지를 남긴다. 코드는 `airflow/modules/briefing/`과
`airflow/dags/slack_*_briefing.py`에 있다.

## 1. 기존 설계와의 관계

[경제 문서 아카이브 설계](economic-document-archive-design.md)의 3단계(`market_daily_brief`)와
4단계(카테고리 분석가·툴·`market_report`)는 미구현 상태다. 이 기능은 그 둘과 **별개**다.

- **3단계 테이블을 기다리지 않는다.** `market_daily_brief`는 거래일당 1행이라 하루 여러 번
  도는 장중 주기와 맞지 않는다. 이 리포트는 원천 테이블을 직접 조회한다. 3단계가 생겨도
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

**요약 실패는 발송을 막지 않는다. 대신 채널에 남는다.** DAG 태스크가 `ConnectionError`·
`LlmError`·`CommentError`를 잡아 `(None, 사유)`를 돌려주고, `blocks.comment_blocks`가
`⚠️ 요약 생성 실패: …` context 줄을 그린다. 로그만 남기고 조용히 빠지면 요약이 원래 없는
리포트(0건·올그린)와 구분되지 않고, 그 상태가 며칠 이어져도 아무도 모른다. 이건
`.claude/CLAUDE.md` 오류 처리 절의 "로그는 예외를 대체하지 않는다"를 따른 것이다.

그래프 안의 repair는 형식 교정이고, "그래도 리포트는 나간다"는 판단은 DAG의 몫이다.
태스크의 성패는 Slack 전송만 정한다: `SlackError`면 `AirflowFailException`,
`ConnectionError`면 올려서 Airflow가 재시도한다.

**부를 값이 없으면 아예 안 부른다.** 2부의 평가 0건, 3부의 올그린이 그렇다. 쓸 값이 없는데
요약을 시키면 없는 이야기를 지어낸다. 이건 실패가 아니라 정상 흐름이라 경고 줄도 안 붙는다.

### 2.3 파트 모듈 — `airflow/modules/briefing/`

`modules/collectors/`처럼 패키지로 둔다.

| 파일 | 역할 |
| --- | --- |
| `table.py` | 고정폭 표. 폭을 글자 수가 아니라 **표시 칸 수**로 센다(한글은 두 칸) |
| `blocks.py` | Block Kit 조각(header·section·표 섹션·context·요약 블록)과 `08/18(화) 12:30 KST` 시각 표기 |
| `comment.py` | 세 리포트가 함께 쓰는 LLM 요약 |
| `market.py` | 1부. 한국장·미국장 **둘 다** |
| `documents.py` | 2부 |
| `ops.py` | 3부 |

파트 파일 셋은 같은 네 함수 모양을 갖는다.

```python
def collect_summary(connection: Connection, now: datetime) -> XxxSummary
def render_blocks(summary: XxxSummary, comment: str | None, error: str | None = None) -> list[dict[str, Any]]
def render_text(summary: XxxSummary) -> str      # Slack text fallback 한 줄
def comment_input(summary: XxxSummary) -> str    # LLM에 줄 압축 JSON
```

- 요약 모델은 `ConfigDict(frozen=True)` Pydantic, 시각은 `AwareDatetime` UTC.
- 쿼리는 `airflow/sql/postgres/<테이블>/*.sql`에 두고 `read_sql`로 읽는다. 조회 구간은
  모듈이 계산해 파라미터로 바인딩하고 문자열 조립을 하지 않는다.
- 연결은 `Protocol`로 받는다(`market_session.py`와 같은 방식). Airflow를 import하지 않아
  테스트가 배포 환경 없이 돈다.
- **KST 변환은 `render_*` 안에서만 한다.** Slack은 프론트엔드가 없는 출력이라 백엔드
  변환 대상이고, IANA `Asia/Seoul`(`KST_TIMEZONE`)을 쓴다. DB와 요약 모델은 UTC를 유지한다.
- **요일은 `blocks.WEEKDAY_NAMES` 표에서 온다.** `strftime("%a")`는 실행 환경의 `LC_TIME`을
  타서 컨테이너 로케일이 바뀌면 조용히 `Tue`가 된다.

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

| 섹션 | 거르는 기준 | 내용 |
| --- | --- | --- |
| 헤더 | — | KST 시각(요일 포함) + 장 상태(개장 전/장중/마감 후) |
| 국내 지수·선물 | `country = 'KR'` | 코스피·코스닥·선물. 등락은 `(close - previous_close) / previous_close` — `quote_bar.previous_close`가 이미 있어 전일 세션 서브쿼리가 필요 없다 |
| 장중 해외 | 미국 `index_future` + 아시아(`JP`·`TW`·`HK`·`CN`) | **한국장 시간에도 값이 움직이는 것만 넣는다.** 미국 현물 지수는 이 시간에 닫혀 있어 어제 종가를 오늘 값처럼 보이게 하므로 뺀다 |
| 환율 | `BRIEFING_CURRENCIES` | USD·JPY·EUR·CNY의 매매기준율과 직전 고시일 대비 |
| 수급 | — | 시장별 외국인·기관·개인 순매수(억원) |
| 등락 종목 수 | — | 상승·보합·하락 |
| 💬 요약 | — | LLM 요약(§2.2). 입력에 국내와 장중 해외가 함께 들어가 요약이 자연스럽게 양쪽을 엮는다 |
| context | — | 섹션별 데이터 기준 시각(KST). 어느 값이 얼마나 묵었는지 보이게 |

### 3.2 미국장 아침 브리핑

```python
SCHEDULE = "0 8 * * 2-6"  # KST 화~토 08:00 = UTC 월~금 23:00
```

미국 정규장 마감은 KST 05:00(서머타임)/06:00이고, 아침 수집 DAG들(07:00~07:40의
FRED·yahoo 일봉)이 끝난 뒤가 08:00이다. 미국 휴장일은 `us_equity_open_day`
(`market_session.py`)로 확인해 건너뛴다. 화~토인 이유: KST 월요일 아침에는 직전
미국 세션이 없다.

| 섹션 | 거르는 기준 | 내용 |
| --- | --- | --- |
| 헤더 | — | "미국장 마감" + 세션 날짜(뉴욕 기준) + 발송 시각(KST) |
| 미국 지수·선물 | `country = 'US'` | 밤사이 마감 값과 등락. 여기서는 현물도 넣는다. 방금 닫힌 값이라 최신이다 |
| 주요국 10년 금리 | `kind = 'government_bond' AND maturity_months = 120` | 계열별 최근 2건을 pivot해 bp 델타. 미국 중심이지만 밤사이 갱신된 다른 나라도 함께 나온다. **제공처가 늘어도 마스터가 흡수하므로 쿼리를 안 고친다** |
| 전일 국내 | `country = 'KR'` | 전일 코스피·선물 마감. 조합 요약의 입력이자 같은 화면에서 대조할 값이다 |
| 환율 | `BRIEFING_CURRENCIES` | 마지막 고시 |
| 수급 | — | 전일 마지막 스냅샷 |
| 💬 조합 요약 | — | LLM 요약(§2.2). 입력에 밤사이 미국 값·금리와 전일 한국 값을 **함께** 넣는다. 이것이 이 리포트를 따로 두는 이유다 |
| context | — | 미국 세션 날짜와 각 값의 기준 시각(KST 병기) |

### 3.3 조회는 한 번, 렌더링이 고른다

**두 리포트가 `MarketSummary` 하나와 `collect_summary` 하나를 공유한다.** 무엇을 그릴지는
`MarketScope`(`KOREA`/`US`)가 정한다. 리포트마다 쿼리를 좁히지 않는 이유는 심볼이 수십 개,
계열이 십여 개라 좁혀서 아낄 것이 없기 때문이고, 그 덕에 미국장 리포트의 요약이 밤사이
미국 값과 전일 한국 값을 **한 입력에서** 본다.

쿼리 다섯 개가 파일로 있다.

| SQL 파일 | 하는 일 |
| --- | --- |
| `quote_bar/select_latest_briefing_bars.sql` | 심볼마다 마지막 봉 하나(`DISTINCT ON`), `quote_symbol` 조인으로 이름·종류·나라. 나라로 거르지 않고 전부 받아 파이썬이 나눈다 |
| `exchange_rate/select_latest_with_previous.sql` | 통화마다 마지막 고시와 직전 **고시일**의 마지막 회차. 하루에 여러 회차가 있어 "전일 대비"는 회차가 아니라 날짜로 갈라야 한다 |
| `indicator_observation/select_latest_pair.sql` | 계열마다 최신·직전 값. `kind`·`maturity_months`로 좁히는 것이 핵심이다. 한 테이블에 물가지수와 소매판매가 함께 있어 안 걸면 단위가 다른 값이 한 표에 섞인다 |
| `market_investor_flow_snapshot/select_latest.sql` | 시장별 마지막 수급 스냅샷 |
| `market_movement_snapshot/select_latest.sql` | 시장별 마지막 등락 종목 수 |

Block Kit: `header` → 섹션마다 제목 `section` + **`table` 블록** → `divider` →
요약 `section` → `context`. `text` fallback은 지수 둘과 환율/금리 하나를 담은 한 줄이다.

실제 출력은 이렇게 나온다.

```
## 📈 한국장 브리핑 · 08/18(화) 12:30 KST · 장중
*국내 지수·선물*
┌────────────────┬──────────┬──────────┬─────────────┐
│ 구분           │     종가 │     등락 │        기준 │
│ 코스피         │ 2,687.45 │ ▲ +0.82% │ 08/18 12:30 │
│ 코스피200 선물 │   361.20 │ ▲ +0.67% │ 08/18 12:29 │
└────────────────┴──────────┴──────────┴─────────────┘
```

**열은 Slack이 맞춘다.** 처음에는 코드 블록 안에서 `unicodedata.east_asian_width`로 칸 수를
세어 직접 맞췄는데(`briefing/table.py`), 실제 Slack에서 줄이 어긋났다. 코드 블록 글꼴인
`Monaco`/`Menlo` 계열에 한글이 없어 대체 글꼴로 떨어지고 그 자간이 ASCII의 정확히 두 배가
아니기 때문이다. 칸 수를 아무리 정확히 세어도 한글 수가 다른 줄끼리 밀린다. `▲`/`▼`가
ambiguous 폭이라 글꼴마다 한 칸이 되기도 두 칸이 되기도 하는 것은 그 위에 겹친 둘째 문제였다.
**맞출 수 없는 것을 맞추려 하지 않는다.** Slack 기본 `table` 블록이 클라이언트에서 열을
맞추므로 그 계산이 통째로 사라졌다. `table.py`는 지웠다.

`table` 블록에는 제목 칸이 없어 `blocks.table_section`이 제목 `section`과 표를 **두 블록**으로
돌려준다. 칸은 `raw_text`라 링크와 굵게가 들어가지 않는다. 그래서 문서 브리핑의 주요 문서와
운영 브리핑의 실패 목록처럼 링크가 필요한 자리는 표가 아니라 `section`으로 남는다.

### 3.4 모델이 판단할 수 있게 만든다

값 하나만 주면 모델은 `+0.82%`가 큰 값인지 알 수 없어 "올랐다"밖에 못 쓴다. **판단에
필요한 것은 데이터 접근 권한이 아니라 비교 기준이다.** 그래서 값마다 최근 구간과의 관계를
미리 계산해 `comment_input`에 함께 싣는다. 계산은 SQL과 `briefing/trend.py`가 하고 모델은
읽기만 한다 — §0의 경계 그대로다.

| 필드 | 뜻 |
| --- | --- |
| `move_percentile` | 이번 변화의 **크기**가 구간 안 변화들 중 몇 번째인가(0~100) |
| `streak_days` | 같은 방향이 이어진 날 수. 부호가 방향이다 |
| `window_low`/`window_high` | 구간의 최저·최고. 현재 값이 어느 끝에 붙었는지 보라고 준다 |
| `observations`, `thin` | 표본 수와 부족 표시 |

구간은 `TREND_LOOKBACK`(달력 30일)이다. 거래일 20일을 담으려면 주말·공휴일을 감안해 이만큼
봐야 하고, 더 늘리면 "요즘"이 아니라 "지난 분기"를 재게 되어 오늘의 움직임이 늘 평범해 보인다.

세 가지를 지킨다.

- **금리와 가격을 같은 자로 재지 않는다.** 금리는 변화폭(bp), 가격·환율은 퍼센트다. 금리에
  퍼센트를 씌우면 4.00 → 4.10과 0.40 → 0.50이 전혀 다른 크기가 되고, 유로 지역은 마이너스
  구간이 있어 부호까지 뒤집힌다(§8.2와 같은 규칙). 설계 문서가 말하는 로그 수익률 대신
  퍼센트를 쓰는데, 여기서 변화량을 쓰는 곳이 순위뿐이라 두 척도의 결과가 같기 때문이다.
  로그가 필요해지는 것은 상관·회귀를 낼 때이고 그건 4단계의 일이다.
- **수급은 부호가 이어진 날을 센다.** "외국인 5일 연속 순매도"는 금액이 계속 마이너스였다는
  뜻이지 금액이 매일 줄었다는 뜻이 아니다. 변화 방향으로 세면 순매도가 잦아드는 날 흐름이
  끊긴 것처럼 보인다. `trend.sign_streak`이 그 셈을 따로 한다.
- **표본이 짧다는 사실을 감추지 않는다.** 연휴 뒤나 새로 붙인 계열은 관측이 몇 개뿐이고 그
  위에서 낸 백분위는 잡음이다. 값을 숨기는 대신 `thin`을 실어 보내고 프롬프트가 "그 백분위를
  근거로 쓰지 마라"를 말한다(§8.3의 `MIN_MEANINGFUL_OBSERVATIONS`와 같은 판단).

이력이 없는 계열은 `trend`가 `null`이다. 새로 붙인 심볼 때문에 리포트가 죽으면 안 되고,
없는 것과 0을 구분해야 프롬프트가 "모른다"를 말할 수 있다.

**툴을 주지 않은 이유.** 모델이 스스로 데이터를 조회하게 하는 설계는
[경제 문서 아카이브 설계](economic-document-archive-design.md) §8에 이미 있고, 그 자리가
4단계다. 브리핑에 툴만 옮겨 붙이면 위험한 절반만 가져온다 — 툴이 낸 숫자를 믿을 수 있게
만드는 것은 §8.4의 `market_report` 저장과 `unsupported_numbers` 대조인데 Slack 메시지에는
저장도 검증도 없다. 표본 24일짜리 상관 0.9가 채널에 그대로 나가고 아무도 되짚을 수 없다.
게다가 요약자 하나에 모든 툴을 주는 것은 §8.1이 "얕게 훑는다"며 만들지 말라고 한 모양이다.
여기서 계산해 주는 값들은 4단계 툴도 결국 돌려줘야 할 것이라 버려지지 않는다.

## 4. 2부 — 뉴스·문서 평가 요약

`slack_document_briefing`, 기본 스케줄:

```python
SCHEDULE = "0 8 * * *"  # KST 매일 08:00 = UTC -1일 23:00
```

조회 창은 `assessed_at` 기준 최근 `WINDOW_HOURS`(기본 24)다. `published_at`이 아니라
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
| 소스별 현황 | `source_record/select_briefing_window.sql` | `GROUP BY source`: 실행 수, 성공, 실패, `record_count` 합, 마지막 `completed_at`. `(source, started_at)` 인덱스를 탄다 |
| 무소식 소스 | — (모듈 판정) | `EXPECTED_SOURCES` 상수와 대조 |
| 예외 둘 | `exchange_rate/select_freshness.sql` + 2부의 집계 쿼리 | 아래 참고 |
| 최근 실패 상세 | `source_record/select_recent_failures.sql` | 최근 5건: source, `source_key`, `started_at`, `metadata`의 오류 요지(300자로 자름) |
| 💬 요약 | — | 실패·무소식이 있을 때만. 올그린이면 "모든 수집 정상" 한 줄로 충분하다 |

**기대 소스 목록에 문서 피드를 넣지 않는다.** 피드 목록은 `document_source` 테이블이 정하고
수십 개라, 코드 상수와 DB가 어긋나는 순간 거짓 경보가 된다. `document_ingestion_hourly`는
`source_record.source`에 피드 slug를 그대로 남기므로, 표에서는 그 행들을 `문서 피드(N)` 한
줄로 접는다. 하나씩 그리면 표가 화면을 넘는다. 피드 건강은 2부가 본다.

`ExpectedSource(name, label, weekdays_only)`의 `weekdays_only`는 주말에 조용해도 정상인
곳이다. 국내 시장과 공시는 주말에 열지 않아 이 표시가 없으면 매주 토·일 거짓 경보가 뜬다.

`source_record`에 안 잡히는 둘은 각자 다른 신호로 본다.

- **환율**: `max(date)`가 `EXCHANGE_RATE_STALE_DAYS`(4일)보다 오래되면 멈춘 것으로 본다.
  주말·공휴일 연휴를 건널 만큼은 둔다.
- **문서 평가**: 대기 건수가 `ASSESSMENT_BACKLOG_LIMIT`(200)를 넘으면 밀린 것으로 본다.
  매시 배치가 처리하는 중이면 몇십 건은 항상 밀려 있어, 0을 기준으로 잡으면 매일 경보가 뜬다.

**올그린이어도 보낸다(하트비트).** 침묵이 정상 신호면 고장으로 인한 침묵과 구분할 수
없다. 하루 한 번은 견딜 만한 소음이다.

알려진 한계: `source_record`에 `running` 행을 쓰는 수집기가 없어, 매달린 DAG는 '실행
중'이 아니라 '부재'로 보인다. 무소식 섹션이 그 부재를 잡는 것까지가 이 리포트의 몫이고,
실행 중 상태가 필요해지면 Airflow 메타데이터를 봐야 한다.

## 6. 무엇이 테스트로 고정돼 있나

구현이 끝났다. 남은 테스트 파일은 이렇다.

| 테스트 | 무엇을 고정하나 |
| --- | --- |
| `tests/modules/test_slack.py` | 오류 분류표, 전달 인자, `retry_handlers=[]`, 예외 문자열에 토큰 미노출 |
| `tests/modules/test_briefing_comment.py` | 성공·교정 1회·재실패·`ConnectionError` 통과, 단락 여럿 허용 |
| `tests/modules/test_briefing_trend.py` | 연속 일수, 이상 움직임 백분위, 금리 bp vs 가격 퍼센트, 마이너스 금리, 표본 부족 표시, 수급의 부호 연속 |
| `tests/modules/test_briefing_market.py` | 나라·종류 분할(한국장에서 미국 현물 제외), 미국장 요약 입력에 한국 값 포함, 뉴욕 기준 세션 날짜, 요일 표기, 요약 입력의 추세 필드, 줄마다 붙는 기준 시각 |
| `tests/modules/test_briefing_documents.py` | 창이 `assessed_at` 기준, 0건 짧은 형태, 상위 문서 링크·점수 |
| `tests/modules/test_briefing_ops.py` | 무소식 판정(주말 예외 포함), 환율 신선도, 백로그 임계, 올그린 하트비트, 피드 접기 |
| `tests/dags/test_slack_market_briefing.py` | 네 DAG의 스케줄, 태스크 하나, 채널 분리, 설정 누락 시 즉시 실패 |

SQL은 수집기 테스트 컨벤션대로 **SELECT가 부르는 컬럼이 모델 metadata에 실제로 있는지**
대조한다. 컬럼 이름이 바뀌면 실행 전에 테스트가 먼저 깨진다.

Compose·Dockerfile은 건드리지 않았다. 새 의존성은 `requirements.txt`에 넣은
slack-sdk 하나뿐이고, `modules/`의 새 파일은 Airflow를 import하지 않는다.

## 7. 운영 준비물

- `compose/local/airflow/requirements.txt`에 `slack-sdk>=3.33` 추가 후 이미지 재빌드.
  운영 Airflow 이미지에도 같은 줄이 먼저 들어가야 배포할 수 있다.
- Slack 앱 생성, Bot Token Scopes에 `chat:write`.
- 채널 3개 생성 후 **각 채널에 봇 초대**(`/invite`).
  - 공개 채널은 `chat:write.public` 스코프가 있으면 초대 없이도 발송된다(2026-08-18 실측).
    다만 그 상태에서는 봇이 `conversations.history`로 자기 메시지를 되읽지 못하고
    `not_in_channel`을 받는다. 비공개 채널은 초대가 반드시 필요하다.
  - 초대가 없어 거절당하면 `not_in_channel`이고, 재시도해도 같은 결과라 태스크가 즉시 실패한다.
- `compose/local/airflow/.env`(운영은 해당 환경 변수 주입)에 키 4개:
  `SLACK_BOT_TOKEN`, `SLACK_CHANNEL_MARKET`, `SLACK_CHANNEL_DOCUMENT`, `SLACK_CHANNEL_OPS`.
- LangSmith 추적이 켜져 있으면 요약 입력(집계 JSON)이 외부로 나간다. 원문에 해당하는 것은
  2부 상위 문서의 제목과 `reason`뿐이지만, 추적 정책은 평가 DAG와 같은 기준을 따른다.
- 스케줄을 바꾸려면 각 DAG 파일 맨 위의 `SCHEDULE` 한 줄을 고친다. KST와 UTC를 같은 줄
  주석으로 병기한다.
