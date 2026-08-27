# 공시 알림 — `slack_disclosure_briefing`

- 상위: [slack-report-design.md](slack-report-design.md)
- 날짜: 2026-08-27
- 상태: **구현 완료**(2026-08-27). **마이그레이션이 없어 코드 배포만으로 뜬다.** 검증은
  `uv run pytest tests -q`(2,132건)와 `uv run ruff check`, 그리고 운영 DB 읽기 전용 실측(9절).
  테스트 채널로 실제 발송해 폴백 경로까지 확인했다.
- 의존: `dart_disclosure_intraday`(공시·실적 수집, 이미 운영 중), `disclosure_event`·
  `earnings_fact` 테이블, `modules/briefing/picks.py`(선별 패턴의 본보기),
  `modules/llm.py`의 `briefing_model()`
- 산출물: `airflow/dags/slack_disclosure_briefing.py`,
  `airflow/modules/briefing/disclosures.py`(조회·렌더 — LangChain 없음)와
  `disclosure_picks.py`(`DisclosurePicker` — 무거운 쪽),
  `airflow/modules/prompts/disclosure_picks.yaml`(프롬프트)와 `airflow/modules/prompt.py`(로더),
  `airflow/sql/postgres/disclosure_event/select_new_for_briefing.sql`,
  `airflow/sql/postgres/earnings_fact/select_by_rcept_no.sql`,
  `tests/dags/test_slack_disclosure_briefing.py`,
  `tests/modules/test_briefing_disclosures.py`·`test_briefing_disclosure_picks.py`·`test_prompt.py`,
  `test_import_weight.py`에 경계 검사 둘.
  **마이그레이션 없음** — 새 컬럼도 새 테이블도 만들지 않는다(3절).

## 0. 왜 — 2분마다 쌓이는데 사람이 보는 출구가 없다

`dart_disclosure_intraday`가 평일 07:00~20:58에 2분마다 삼성전자·SK하이닉스의 새 공시를
`disclosure_event`에 넣는다. 실적 공시면 `earnings_fact`에 숫자까지 뽑는다.

**그런데 그 행을 읽는 곳이 추론 툴 `recent_disclosures` 하나뿐이다.** 그건 모델이 근거로
쓰는 경로지 사람이 "방금 뭐가 올라왔나"를 보는 경로가 아니다. 실적 발표처럼 가격이 바로
움직이는 사건을 우리 시스템이 먼저 알고도 아무에게도 안 알린다.

## 1. 범위를 먼저 못 박는다 — 후보가 적다

**`disclosure_event`에는 두 종목만 들어온다.** 두 회사 합쳐 공시가 연 300~400건이면
**하루 평균 1~2건**이고 없는 날이 더 흔하다.

이 사실이 설계 셋을 정한다.

- **고르는 것이 아니라 전부 싣고 강조만 한다.** `slack_document_briefing`은 후보 수십 건에서
  몇 개를 골라 싣는다(상위 구간이 거의 동점이라 순서에 뜻이 없어서다). 공시는 버릴 이유가
  없다. 모델은 **무엇을 강조할지와 왜인지**만 정한다.
- **0건이면 아무 것도 보내지 않는다.** 문서 브리핑은 0건에도 보내 생존 신호를 겸하는데,
  여기서 그러면 하루의 대부분이 "오늘 공시 없음"이라 아무도 안 읽는다. 수집 생존은
  `slack_ops_briefing`이 이미 보고한다.
- **선별이 실패해도 메시지는 나간다.** 강조 없는 목록이 그대로 간다(6절). 후보가 적어
  선별의 값어치가 크지 않다는 것이 오히려 안전장치다 — LLM은 얹는 것이지 없으면 못 보내는
  부품이 아니다.

**수집 범위가 넓어지면(전 상장사·감시 종목 확대) 이 설계는 다시 본다.** 그때는 후보가 하루
수백 건이라 "전부 싣는다"가 성립하지 않고, `slack_document_briefing`처럼 자르는 축이 필요하다.
그건 이 문서의 범위가 아니라 새 수집 작업이다.

## 2. 언제 보내나 — 새 공시가 들어온 창만

하루 한 번 요약이 아니라 **감지 직후**다. 공시는 시간이 값어치다.

```python
SCHEDULE = "*/10 7-20 * * 1-5"   # KST 평일 07:00~20:50, 10분마다 = UTC 전일 22:00~11:50
```

- 수집이 2분마다이므로 감지에서 발송까지 지연은 **최대 12분**(수집 주기 + 발송 주기)이다.
- `catchup=False`, `max_active_runs=1`. 밀린 창을 나중에 몰아 보내지 않는다 — 지난 공시는
  알림이 아니다.
- 창은 `data_interval_start < detected_at <= data_interval_end`다. **벽시계가 아니라
  data interval이다** — 실행이 밀려도 창이 이어지고 겹치지 않는다.

`receipt_date`가 아니라 `detected_at`으로 거른다. 접수일은 날짜뿐이라 시각으로 자를 수 없고,
우리가 실제로 알 수 있었던 시점은 감지 시각이다(`select_recent.sql`과 같은 판단).

## 3. "이미 보냈나"를 DB에 안 남기는 이유

`disclosure_event.notified_at` 같은 칸을 더하면 발송 여부가 정확해진다. **안 만든다.**

- 리비전이 하나 늘고, 지금 운영 반영 대기가 이미 다섯이다.
- 창이 data interval이라 정상 실행에서는 한 공시가 정확히 한 창에만 든다.
- 태스크 재시도가 같은 창을 다시 보면 중복 발송이 가능하다. **저장소가 이미 허용하는
  수준이다** — `slack-report-design.md`가 "Slack이 메시지를 받은 뒤 응답만 유실된 경우의
  중복은 허용한다"고 못 박았다. 발송을 마지막 태스크에 두어 그 앞 단계의 재시도가 중복을
  만들지 않게 한다.

**대가는 하나다.** DAG를 pause 했다 풀면 그동안의 공시는 안 나간다(`catchup=False`). 알림에는
그게 맞는 동작이다. 누락이 실제로 문제가 되는 것이 관측되면 그때 `notified_at`을 만든다.

## 4. 조회 — 숫자는 SQL이 만든다

### 4.1 새 공시

`disclosure_event/select_new_for_briefing.sql`. 창 안의 행을 `detected_at` 오름차순으로 준다.
`select_recent.sql`을 재사용하지 않는다 — 그쪽은 추론 툴이 `as_of_at`까지 보는 쿼리이고
건수 상한과 종목 필터가 다르다(저장소 규칙: 툴 SQL과 브리핑 SQL을 겸용하지 않는다).

주는 칸: `rcept_no`, `stock_code`, `company_name`, `report_name`, `receipt_date`,
`detected_at`, `remarks`.

### 4.2 실적 숫자

`earnings_fact/select_by_rcept_no.sql`. 그 창의 `rcept_no` 목록으로 지표를 읽는다.

- **연결(`CFS`) 우선.** 없을 때만 별도(`OFS`)를 쓰고 그 사실을 표기한다.
- **`amount_basis = 'period'`**(해당 분기)를 싣는다. 누계는 안 싣는다 — 한 줄에 둘을 넣으면
  읽는 사람이 어느 쪽인지 못 가른다.
- YoY는 `prior_year_amount`가 있을 때만 계산한다. **없으면 칸을 비운다** — 0으로 메우지 않는다.
- 지표 순서는 `revenue` → `operating_profit` → `net_income` 고정이다.

**계산은 SQL과 순수 함수가 한다. 모델은 해석만 쓴다.** 숫자 비교에 LLM을 쓰지 않는 것은
`stock_event_*` 계열과 같은 규칙이다.

### 4.3 표기

- 금액은 원 단위 저장값을 조 단위로 줄여 쓴다(`74조 1,200억 원`). 단위를 반드시 붙인다.
- **`detected_at`은 "최초 감지"로 표시한다.** 공시 시각이 아니다 — DART가 분 단위 접수
  시각을 주지 않아 우리가 처음 본 시각이 상한이다. 표시 시간대는 KST다.
- 접수번호에 DART 뷰어 링크를 건다.
  `thesis_domain.DART_VIEWER_URL`을 그대로 import한다. 상수를 두 벌 두지 않는다 —
  `thesis_domain`은 LangChain을 import하지 않아 브리핑이 끌고 와도 무겁지 않다.

## 5. 선별 — `DisclosurePicker`

`picks.py`의 `DocumentPicker` 계보다. 흐름은 `call` → (형식이 깨지면) `repair` → `call`이고
교정은 한 번뿐이다. 그래프는 생성자에서 한 번 `compile()`한다.

**모듈을 둘로 가른다.** 조회·렌더(`disclosures.py`)는 DAG 파일이 최상단에서 import하므로
LangChain을 끌고 오면 DagBag 30초 타임아웃에 걸린다. 선별(`disclosure_picks.py`)만 무겁고
DAG이 태스크 안에서 늦게 읽는다. `tests/modules/test_import_weight.py`가 그 경계를 잰다 —
`documents.py`/`picks.py`가 같은 경계인데 그쪽은 `Pick` 하나 때문에 여전히 무거운 것을
끌고 온다. 여기서는 응답 모델까지 가벼운 쪽에 뒀다.

**다른 점 하나**: 결과가 "실을 것"이 아니라 "강조할 것"이다.

```
입력  : 그 창의 새 공시 전부 + 붙어 있는 실적 숫자
출력  : { "highlights": [ { "rcept_no": "...", "reason": "한 줄" } ] }
```

- `allowed_ids`는 `rcept_no` 집합이다. 목록 밖 값은 버린다. 전부 버려지면 `PickError` —
  모델이 후보를 안 보고 답했다는 뜻이라 교정할 값어치가 있다.
- **`highlights: []`는 정상 응답이다.** 잡음 공시만 있는 날이 그렇다. 강조 없이 목록만 나간다.
- `reason`은 한 줄이다. 길이 상한을 코드 상수로 두고 그 값을 `Field(description=...)`에
  f-string으로 싣는다(저장소 규칙).
- 프롬프트에 **판단 근거를 요구한다**: 가격에 영향을 줄 만한 것인가(실적·자사주·증자·
  최대주주 변경·정정), 아니면 정기 보고인가(임원·주요주주 소유상황보고서 같은 것).

### 문장은 YAML이 갖는다

프롬프트는 `airflow/modules/prompts/disclosure_picks.yaml`이고 코드는 `read_prompt`로 읽는다
(`.claude/CLAUDE.md`의 "프롬프트는 코드가 아니다"). 문장은 흐름보다 자주 바뀌는데 한 파일에
두면 문장만 고친 변경도 코드 diff가 된다.

- 치환은 `string.Template`(`$이름`)이다. `str.format`이면 출력 예시의
  `{"highlights": [...]}` 중괄호에서 죽는다.
- `$max_reason_chars`는 `disclosures.MAX_REASON_CHARS`에서, `$number_style`은
  `llm.NUMBER_STYLE`에서 온다. **YAML에 숫자를 직접 적지 않는다** — 두 곳에 적으면 어긋난다.
- 파일은 import 시점에 읽고 검증한다. 칸이 빠지거나 오타가 나면 DagBag 단계에서 죽는다.

`llm.NUMBER_STYLE`을 프롬프트에 함께 싣는다 — 산문에 숫자가 들어가는 자리다.

## 6. 실패 판정

| 무엇 | 어떻게 |
| --- | --- |
| 창에 새 공시 0건 | **정상 종료.** LLM도 Slack도 부르지 않는다 |
| 조회 실패 | 그대로 올린다. 재시도 대상이다 |
| 선별 실패(`PickError`·`LlmError`) | **잡아서 강조 없이 보낸다.** 경고를 남기고 태스크는 성공이다 |
| 선별이 재시도할 값어치가 있는 오류(`ConnectionError`) | 같다 — 폴백한다. 알림을 늦추는 것보다 강조 없이 제때 가는 편이 낫다 |
| Slack 실패 | 올린다. 재시도가 중복을 만들 수 있으나 허용 범위다(3절) |

**단일 요청 형태다**(저장소의 DAG 실패 판정 셋 중 하나). 태스크 하나가 조회·선별·발송을
차례로 하고 판정할 항목별 실패가 없다.

## 7. Slack 메시지

`SLACK_CHANNEL_DOCUMENT`로 보낸다. 문서 브리핑과 같은 채널이다 — 공시도 읽을거리라 성격이
같고 배포 설정(env)을 안 늘린다.

```
📄 새 공시 2건 · 08/27(수)

⭐ 삼성전자 005930
   연결재무제표기준영업(잠정)실적(공정공시)
   매출 74조 1,200억 원 (YoY +12.3%) · 영업이익 9조 3,400억 원 (YoY +34.1%)
   시장 기대치를 웃도는 분기 실적이다
   최초 감지 08/27 08:31 KST · 접수 2026-08-27 · <DART 링크>

   SK하이닉스 000660
   임원·주요주주 특정증권등 소유상황보고서
   최초 감지 08/27 14:02 KST · 접수 2026-08-27 · <DART 링크>
```

- `⭐`가 모델이 강조한 것이고 이유 한 줄이 붙는다. 나머지는 목록으로 그대로 실린다.
- 실적 숫자가 붙은 공시만 숫자 줄이 생긴다.
- 블록 조립은 `modules/briefing/blocks.py`의 기존 헬퍼(`header`·`section`·`context`)를 쓴다.
  새 헬퍼를 만들지 않는다.

## 8. 테스트

`tests/modules/test_briefing_disclosures.py`

- 창 경계 — `data_interval_start`와 같은 `detected_at`은 빠지고 `end`와 같은 것은 든다.
- 목록 밖 `rcept_no`를 버린다. 전부 버려지면 `HighlightError`(`test_briefing_disclosure_picks.py`).
- `highlights: []`가 정상 응답으로 통과한다.
- 실적 렌더 — CFS 우선, `prior_year_amount`가 없으면 YoY 칸이 비고 0이 아니다.
- 금액 표기 — 조·억 단위와 천 단위 쉼표.
- `DART_VIEWER_URL`이 `thesis_domain`의 값과 같다.

`tests/dags/test_slack_disclosure_briefing.py`

- 0건이면 모델도 Slack도 부르지 않는다.
- 선별 실패(`HighlightError`·`ConnectionError`)가 폴백으로 떨어지고 Slack은 그대로 불린다.
- 조회가 실패해도 DB 연결은 닫힌다.
- Slack 실패는 올라온다.
- 스케줄·`catchup`·`max_active_runs`와 `dag_display_name`·`description`·`doc_md`가 비어 있지 않다.

## 9. 실측 (2026-08-27)

설계 당시 미확인이던 것을 운영 DB에 읽기 전용으로 돌려 확정했다. 새 SQL 둘도 같은 실행에서
검증했다(저장소 규칙 — 테스트는 가짜 연결을 쓰므로 컬럼 이름과 조인 조건이 틀려도 통과한다).

**하루 1~2건은 대체로 맞다.** 최근 60일에서 공시가 있던 날이 열다섯 남짓이고 대부분 하루
1~2건이다. 2026-08-21처럼 열다섯 건이 몰린 날도 있다.

**`report_name`은 잡음이 대부분이다.** 임원·주요주주 특정증권등 소유상황보고서와 그 정정이
과반이고, 그 사이에 자기주식취득결정·주식소각결정·조회공시요구 답변 같은 것이 섞인다.
5절 프롬프트의 "강조하지 않는다" 예시가 실제 분포와 맞다.

**초기 백필은 한 창에 몰린다.** 2026-08-16 05:25:23에 열일곱 건 넘게 같은 `detected_at`으로
들어와 있다. 수집을 처음 켠 날의 일회성 현상이지만 `MAX_DISCLOSURES`(20)가 그것을 받는다.
정상 운영에서는 2분 폴링이라 이렇게 몰리지 않는다.

**강조 폴백이 실제로 돌았다.** 로컬에 `XAI_API_KEY`가 없어 `briefing_model()` 생성이
`ValidationError`로 죽었고, 6절대로 강조 없이 목록이 그대로 나갔다. **이 알림은 키 없이도
산다**는 것이 실측으로 확인됐다.

그 과정에서 결함 하나를 고쳤다 — 실패 사유를 그대로 실으면 Pydantic 예외 전문(줄바꿈과
문서 URL 포함)이 공시보다 긴 블록이 된다. `MAX_ERROR_CHARS`(160)로 첫 줄만 자른다.

### 이 문서 밖의 발견 — `earnings_fact`의 값이 의심스럽다

반기보고서 `20260814003699`(삼성전자, 2026-06-30)에 붙은 `CFS`/`period` 값이
**매출 171조 4,994억 원 · 영업이익 89조 4,924억 원 · 순이익 71조 6,244억 원**이다.
영업이익률이 52%가 되고, 같은 접수번호의 `cumulative` 행은 매출 305조다. 삼성전자 반기
실적의 자릿수와 맞지 않는다.

**이 알림의 결함이 아니다** — 저장된 값을 그대로 그린다. 원인은 `collectors/document/dart.py`의
정기보고서 재무제표 파싱 쪽이고 별도 항목으로 올렸다. 그때까지 이 알림의 실적 줄은 그
값을 그대로 보여 준다.

## 10. 남은 확인 (spike)

- **`briefing_model()`은 `ChatXAI`(grok-4.6)이고 운영 `XAI_API_KEY`가 무효다**(2026-08-20 실측,
  [market-thesis/README.md](../analysis/market-thesis/README.md) 5절). **이 DAG는 그래도 죽지
  않는다** — 6절의 폴백이 강조 없는 목록을 보낸다. 키가 살아나면 강조가 붙기 시작한다.
  키 문제 자체는 이 문서의 범위 밖이다.


## 11. 이번 범위 아님

- 수집 종목 확대. 이 DAG는 `disclosure_event`에 있는 것만 읽는다.
- 공시 원문 본문 수집·요약. `report_name`과 `earnings_fact`까지다.
- 중요도 점수 컬럼. 상태 머신도 두지 않는다 — 문서와 같은 이유로 전부 보낸다.
- `recent_disclosures` 툴 수정. 추론 경로는 그대로 둔다.
