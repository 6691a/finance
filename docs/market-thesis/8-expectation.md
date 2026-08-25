# 8단계 — 기대와 실제: 이벤트 기대치와 서프라이즈 판정

- 상위: [README.md](README.md)
- 날짜: 2026-08-24
- 상태: **1~3단계 구현 완료(2026-08-24), 운영 반영 전.** 리비전 `a4c9e1f7b3d6`을 올리고
  DAG을 unpause하는 것이 남았다. 4단계(컨센서스 수집기)는 출처 실측부터다. 검증은 11절.
- 의존: [2-agent.md](2-agent.md)(툴을 늘리는 자리), [6-analyst.md](6-analyst.md)(리서치
  리포트가 `document`로 들어오는 경로 — 기대치의 주 원천), `earnings_fact`(실적 실제값).
- 산출물: `apps/models/analysis/events.py`에 `StockEventClaim`·`StockEventExtraction`·
  `StockEventOutcome`과 enum 넷, 수기 리비전 `a4c9e1f7b3d6`,
  `airflow/modules/expectation_domain.py`·`expectation_extraction.py`·`expectation_judgment.py`
  (순수 함수와 저장 모양 / `ExpectationExtractor` / `ExpectationStore`와 렌더링),
  `airflow/dags/event_expectation_hourly.py`, `modules/llm.py`의 `expectation_model()`,
  SQL 아홉(`stock_event_claim/*` 넷, `stock_event_extraction/upsert.sql`,
  `stock_event_outcome/*` 둘, `document/select_pending_extraction.sql`,
  `earnings_fact/select_actual_for_judgment.sql`), `thesis.py`에 `event_surprises` 툴,
  테스트 넷. 컨센서스 수집기는 후행(6절)

## 0. 왜 — 기대치가 숫자로 안 쌓여서 "미달"을 판단하지 못한다

2026-08-22(금) 삼성전자가 주주환원 계획을 발표했고, 시장 기대치(리포트들의 추정 금액)에
못 미쳐 금요일 NXT 애프터마켓과 월요일 정규장에서 하락했다. 이 인과를 시스템은 만들지
못했다. 재료는 전부 들어오고 있는데도 그렇다.

| 무엇 | 어디 있나 | 무엇이 없나 |
| --- | --- | --- |
| "주주환원 X조 전망" (기대) | 리서치 리포트 `document.summary` — **산문** | 발표일에 대조할 숫자 행 |
| 발표 자체 (실제) | DART `disclosure_event`, 발표 기사 `document` | 발표 금액의 구조화 값 (실적만 `earnings_fact`가 있다) |
| 실적 실제값 | `earnings_fact` (잠정·정기, 원 단위 정규화) | 대조할 컨센서스(기대) 행 |
| "기대 대비 미달" | 어디에도 없다 | 판정 행과 그것을 보는 툴 |

즉 **한쪽이 있으면 다른 쪽이 없다.** 실적은 실제값만 있고 기대가 없다. 주주환원은 기대가
산문으로만 있고 실제도 산문이다. 이 단계는 그 빈 칸 둘을 채우고 대조 결과를 남긴다.

1. **기대치 주장 추출** — 이미 수집·평가된 문서에서 "누가 언제 어떤 이벤트에 어떤 값을
   기대했다"를 구조화해 쌓는다. 발표 기사에서는 실제값 주장도 같은 흐름이 뽑는다.
2. **서프라이즈 판정** — 실제값이 생기면 그 이벤트의 기대치들과 대조해
   `beat`/`meet`/`miss` 한 행을 남긴다. **판정에 LLM이 없다** — 숫자 비교는 코드가 한다
   (thesis 숫자 규칙과 같다). LLM은 추출 단계에만 있다.
3. **소비 셋** — thesis 툴(`event_surprises`), 판정 시 Slack 알림, 그리고 나중의 정확도
   집계(판정이 쌓이면 쿼리다).

**종목 범위는 `instrument.is_watched` 전부다.** 삼성전자 사례는 첫 검증일 뿐이다. 문서
수집이 이미 watched 종목만 받으므로(`documents.watched_tickers`) 추출 단계에 종목 필터를
따로 두지 않는다 — 들어온 문서에서 뽑으면 자동으로 전 추적 종목을 덮는다.

**시장 단위 이벤트(FOMC·CPI 예상치)는 범위 밖이다.** 원천(이코노미스트 서베이)과 실제값
테이블(`indicator_observation`)이 종목 이벤트와 전혀 달라 프레임워크를 공유하지 않는다.
필요가 관측되면 별도 단계로 한다.

## 1. 이벤트 식별 — 매칭이 이 설계의 핵심 문제

기대(리포트, 몇 주 전)와 실제(발표, 오늘)는 **다른 문서에서 다른 시점에** 온다. 자유
텍스트로 두면 잇지 못한다. 잇는 키는 셋이다.

```
(stock_code, event_type, period_key) + metric
```

- `event_type` — `StrEnum` + CHECK. 시작은 셋: `shareholder_return`(주주환원 정책·배당·
  자사주), `earnings`(실적), `guidance`(회사가 낸 전망). LLM에게는 이 목록을 프롬프트로
  주고 **목록 밖 값은 저장 전에 버린다** — `document_instrument` 태깅과 같은 패턴.
- `period_key` — 대상 기간 표기. `2026`(연간·정책연도), `2026Q2`, `2026H1` 세 형식만
  validator로 허용한다. `date.fromisoformat` 따위로 느슨하게 받지 않는다(ECB ISO 주 표기
  교훈과 같은 이유 — 형식을 먼저 본다).
- `metric` — event_type마다 닫힌 집합. CHECK 하나에 전체 합집합을 두고 조합 검증은
  Pydantic이 한다.

| event_type | metric | 저장 단위 |
| --- | --- | --- |
| `shareholder_return` | `total_return_amount`(총 환원액), `buyback_amount`(자사주), `dividend_total`(배당 총액), `dividend_per_share` | 원 |
| `earnings` | `revenue`, `operating_profit`, `net_income` — **`earnings_fact.metric`과 글자 그대로 같다** | 원 |
| `guidance` | `revenue`, `operating_profit` | 원 |

- 단위는 **metric이 정한다**(컬럼 주석에 명시). `unit` 컬럼을 두지 않는다 —
  `indicator_observation`의 교훈은 "단위는 계열마다 다르다"였고, 여기서는 계열이 metric이라
  metric 정의가 단위를 갖는 것으로 충분하다. 추출 시 모델이 준 표기(`9조`, `2.4조원`,
  `1,416원`)는 코드가 원으로 정규화하고, **모르는 표기는 그 주장만 버리고 건수를 로그로
  남긴다**(和暦 규칙과 같다 — 조용히 엉뚱한 자릿수로 저장하는 것보다 낫다).
- `earnings`의 metric을 `earnings_fact`와 같은 값으로 두는 이유: 판정 조인이 대응표 없이
  끝난다(`KrxMarket`이 `quote_bar.symbol`과 값을 맞춘 것과 같은 결정).

## 2. 테이블 셋

`apps/models/analysis.py`에 둔다 — thesis 계보의 분석 도메인이다. 스키마 지정 없음,
전 컬럼 한국어 주석, 리비전은 수기(공통 규칙).

```sql
stock_event_claim (              -- 문서·컨센서스가 낸 주장 한 건. append-only
    stock_code                   -- 종목코드. instrument로 FK를 걸지 않는다(태그 테이블 선례)
    event_type                   -- CHECK ('shareholder_return','earnings','guidance')
    period_key                   -- '2026' | '2026Q2' | '2026H1' (validator가 강제)
    metric                       -- CHECK (1절 합집합)
    claim_kind                   -- CHECK ('expectation','actual')
    value numeric(24,2)          -- 원 단위 정규화 값. earnings_fact와 같은 규칙
    value_low, value_high        -- NULL 허용. "9~10조" 같은 범위 기대. CHECK (low <= high)
    stated_at timestamptz        -- 주장 시점(UTC) = 문서 published_at(없으면 detected_at)
                                 -- 또는 컨센서스 조회 시각
    broker text NULL             -- 증권사·기관 표기(문서 제목 끝 낱말). 컨센서스면 NULL
    document_id NULL FK          -- 출처 문서. ON DELETE CASCADE
    source_record_id NULL FK     -- 컨센서스 수집 레코드. ON DELETE RESTRICT
                                 -- CHECK: 둘 중 정확히 하나만 NOT NULL
    UNIQUE (document_id, event_type, period_key, metric, claim_kind)  -- 문서당 이벤트·지표에 한 주장
)

stock_event_extraction (         -- 문서별 추출 원장. "이미 뽑았다"의 증거
    document_id FK UNIQUE        -- ON DELETE CASCADE
    extracted_content_hash       -- 추출 시점의 document.content_hash.
                                 -- 현재 값과 다르면 본문이 바뀐 것이라 다시 뽑는다
                                 -- (assessed_content_hash와 같은 장치)
    extracted_at, llm_model, prompt_version
    claim_count integer          -- 이 문서에서 저장된 주장 수. 0이 정상값이다
)

stock_event_outcome (            -- 이벤트·지표 하나의 판정. 첫 성공본 불변
    stock_code, event_type, period_key, metric
    expected_value numeric(24,2) -- 판정에 쓴 대표 기대치(원)
    expectation_count integer    -- 대조한 기대치 행 수. CHECK (> 0)
    actual_value numeric(24,2)   -- 실제 발표값(원)
    surprise_pct numeric(8,4)    -- (actual - expected) / |expected| × 100
    verdict                      -- CHECK ('beat','meet','miss')
    announced_at timestamptz     -- 실제값 원본의 발행·감지 시각(UTC)
    actual_ref text              -- 실제값 원본: 'earnings_fact:<id>' 또는 'document:<id>'
    dag_run_id text
    UNIQUE (stock_code, event_type, period_key, metric)   -- 멱등키
)
```

- **`stock_event_claim`은 INSERT만 한다.** 같은 증권사가 기대치를 올려 잡으면 새 문서의 새
  행이다 — 갱신 이력이 그대로 남고, "최신만 쓴다"는 판정 시점의 집계 규칙이다(4절).
- **`stock_event_outcome`은 `INSERT ... ON CONFLICT DO NOTHING`이다.** 발표 뒤 기대치
  행이 늦게 추출돼도 판정을 다시 내지 않는다 — thesis의 첫 성공본 불변과 같은 이유로,
  덮어쓰면 Slack으로 이미 나간 판정과 DB가 어긋난다. 잘못된 판정도 고치지 않는다.
- **0건 문서도 `stock_event_extraction`에 남는다.** "뽑았는데 없었다"와 "아직 안 뽑았다"가
  구분돼야 매시간 같은 문서를 다시 뽑지 않는다(`source_record` 0건 규칙과 같은 이유).
- `document_id`가 CASCADE인 것은 문서가 물리 삭제되지 않는 전제(dedup도 삭제 대신
  `canonical_document_id`)에서 형식적 선언이다. `source_record_id`는 저장소 공통 RESTRICT.

## 3. 추출 — `ExpectationExtractor` (LLM은 여기에만)

`airflow/modules/expectation_extraction.py`. `DocumentAssessor` 계보 — 컴파일된 LangGraph를 소유한
클래스, 응답은 Pydantic + `response_format` 강제, 프롬프트 조립·파싱은 `@staticmethod`.

- **대상 문서**: `assessed_at IS NOT NULL`이고 `document_instrument` 태그가 있는 문서 중
  `stock_event_extraction`에 없거나 `extracted_content_hash`가 현재와 다른 것.
  새 SQL `document/select_pending_extraction.sql`, `batch_size` 상수(50 시작).
  - 종목 태그를 조건으로 쓰는 이유: 종목 이벤트 기대치는 종목이 언급된 문서에만 있고,
    태그 없는 문서(시황·채권)까지 LLM에 넣으면 비용만 는다. 태그는 평가가 만드므로
    **평가 완료가 선행 조건이 된다** — 기존 `document_assessment_hourly`의 평가 스키마는
    건드리지 않는다(스키마를 넓히면 프롬프트 버전이 올라 전 문서 재평가가 돈다).
- **한 문서에서 기대와 실제를 함께 뽑는다.** 응답 스키마는
  `claims: [{event_type, period_key, metric, kind, value 또는 range, unit, broker?}]`.
  발표 기사가 곧 실제값의 원천이다("삼성전자, 주주환원 X조 발표"). 빈 `claims`가
  대부분이고 정상이다.
- **검증이 추출의 절반이다.** enum 밖 event_type·metric은 그 주장만 버린다. period_key
  형식 위반, 단위 정규화 실패(모르는 표기), 값 없는 주장도 같다. 버린 건수는 로그.
  전부 버려져도 문서는 원장에 `claim_count=0`으로 남는다.
- `stated_at`은 모델이 아니라 코드가 채운다 — 문서의 `published_at`(없으면 `detected_at`).
  모델에게 시각을 만들게 하지 않는다.
- 모델은 `modules/llm.py`에 `expectation_model()`로 추가(`max_retries=0`). 문서 평가와
  같은 모델로 시작하되 함수를 나눠 둔다(한쪽만 바꾸고 싶어질 때 그 함수만 고친다).
- **실적의 실제값은 추출하지 않는다** — `earnings_fact`가 원본이다(4절). 프롬프트에서
  `earnings`+`actual` 조합을 금지하지는 않되 저장 전에 버린다. DART 파서가 이미 원문
  표에서 정확히 읽는 값을 기사 산문에서 다시 뽑으면 어긋난 쪽을 고를 수 없다.

## 4. 판정 — LLM 없음

추출이 끝난 뒤 같은 DAG의 다음 태스크가 돈다. 전부 SQL + 순수 함수다.

1. **실제값 수집**: 판정 없는 (이벤트, 지표) 중 실제값이 생긴 것을 모은다. 조회가 둘로
   갈린다 — 실적은 실제값이 다른 테이블에 있어 대상을 고르는 조건 자체가 다르다.
   - `earnings`: `stock_event_claim/select_pending_earnings_expectations.sql`이 "기대는
     있는데 판정이 없는" 키를 주고, 키마다
     `earnings_fact/select_actual_for_judgment.sql`로 실제값을 확인한다.
     `period_key → period_end` 변환은 순수 함수(`2026Q2 → 2026-06-30`), 기간 기준은
     `amount_basis_for`(분기·반기면 `period`, 연간이면 `cumulative`). 연결(CFS)을 별도
     (OFS)보다 우선하고 같은 범위 안에서는 최신 `rcept_no`(정정 공시) — 모델 docstring의
     조회 규칙 그대로다. **기간 기준이 안 맞는 행은 실제값으로 쓰지 않는다.** 분기 기대에
     사업연도 누계를 맞대면 서프라이즈가 자릿수로 나온다.
   - 그 외: `stock_event_claim/select_pending_judgment.sql`이 "actual 주장이 있는데 판정이
     없는" 키의 주장 전부를 준다(`resolve_actual`). 같은 키에 여러 실제 주장이 오면(여러
     기사) **값이 전부 같을 때만** 쓴다. 갈리면 판정하지 않고 로그로 남긴다 — ECB의
     "요청하지 않은 식별자가 섞이면 실패" 규칙과 같은 태도로, 조용히 한쪽을 고르지
     않는다. 다음 실행이 다시 본다(공시 기반 기사가 수렴하면 풀린다).
   - **발표 시각(`announced_at`)은 가장 이른 actual 주장의 `stated_at`이다.** 늦은 기사를
     쓰면 그 사이에 나온 회고 기사가 기대로 샌다(2단계의 컷이 그것에 걸린다).
2. **대표 기대치**(순수 함수 `aggregate_expectations`): `stated_at < announced_at`인
   기대 행만 쓴다 — **발표 뒤 리포트가 "기대치는 X였다"라고 회고한 것이 기대로 섞이면
   판정이 오염된다.** 컨센서스 행(`source_record` 출처)이 있으면 최신 컨센서스, 없으면
   증권사별 최신 행의 중앙값. broker NULL(기사 인용 기대치)도 한 표다.
   기대 행이 0이면 판정하지 않는다 — 행이 안 생기고, "기대가 없던 발표"는 그것대로
   사실이다(억지 판정이 더 나쁘다).
3. **분류**(순수 함수 `classify_surprise`): `|surprise_pct| <= MEET_BAND_PCT`(5.0)면
   `meet`, 아니면 부호로 `beat`/`miss`. **5.0은 실측이 아니다** — `FLAT_THRESHOLD_PCT`와
   같이 4주 뒤 판정 분포를 보고 조정한다([TUNING.md](TUNING.md)에 손잡이 추가).
   수식을 SQL에 넣지 않는 이유도 채점과 같다 — DB 없이 경계값을 테스트한다.
4. **Slack**: 이번 실행이 **새로 쓴** 판정만 발송한다(재실행 멱등 — `RETURNING`이 0행이면
   발송 없음). 채널은 `SLACK_CHANNEL_MARKET` 재사용(thesis와 같은 판단 — 시장 판단이라
   채널을 새로 안 만든다). 테스트 발송은 `slack_channel_test` + 🧪 머리표(프로젝트 규칙).

```
📐 기대 대비 발표
*005930* · 주주환원 2026 · 총 환원액
발표 8.00조 vs 기대 9.50조 (기대 4건) → ▼ 미달 -15.8%
```

**근거 링크를 싣지 않는다.** 판정 하나에 기대 문서가 여럿이라 어느 것을 걸어도 반쪽이고,
발표 문서는 같은 시간 브리핑이 이미 나른다. 필요해지면 `actual_ref`가 문서 ID를 들고 있다.

### DAG — `airflow/dags/event_expectation_hourly.py`

- `schedule="45 * * * *"  # 매시 45분(KST·UTC 동일 주기) — 수집 :05, 평가 :25 뒤`.
  평가가 끝난 문서만 집으므로 평가 지연은 다음 시간에 자연히 따라잡는다. readiness
  guard가 필요 없다 — thesis처럼 "이 시각의 데이터"가 아니라 "쌓인 것 중 안 뽑은 것"이
  대상이다.
- 태스크: `extract_claims >> judge_outcomes >> notify_slack`. 추출(LLM·비용 큼)과
  판정·발송(순수 조회)을 나누는 이유는 thesis가 `build_thesis`와 `notify_slack`을 나눈
  것과 같다 — Slack이 죽어도 LLM을 다시 부르지 않는다.
- 실패 판정: 항목별 실패 수집형. 문서 하나의 형식 오류는 모으고 계속 간다 — 그 문서는
  원장에 오르지 않아 다음 실행이 다시 집는다. **전부 실패했을 때만** 태스크를 죽인다
  (`document_assessment_hourly`와 같은 판정: 문서 하나의 문제가 아니라 프롬프트·모델
  문제라는 뜻이다). `RetryableLlmError`·`ConnectionError`는 성공분을 커밋한 뒤 올려
  Airflow가 재시도하게 하고, `LlmError`(인증·잘못된 요청)는 즉시 `AirflowFailException`.
  판정 태스크의 DB 오류는 그대로 태스크를 죽인다.
- `dag_display_name="📐 종목 이벤트 기대치·서프라이즈"`, `description`, `doc_md=__doc__`,
  Param `title`·`description`(프로젝트 규칙). LangChain import는 태스크 함수 안.

## 5. thesis 툴 — `event_surprises(ticker)`

[2-agent.md](2-agent.md)의 **문맥만 주는 툴**에 들어간다. 근거 레지스트리에 넣지 않는다 —
`thesis_evidence.evidence_kind` CHECK가 셋으로 닫혀 있고, 인용이 필요하면 모델이 발표
문서 자체를 `recent_documents`로 인용할 수 있다. 판정을 직접 인용하고 싶다는 관측이
쌓이면 그때 CHECK를 넓힌다(7단계가 슬롯 CHECK를 넓힌 선례).

- 반환: 그 종목의 최근 판정 행(이벤트·지표·기대·실제·서프라이즈·verdict·발표 시각)과,
  **아직 발표되지 않은 이벤트의 대표 기대치** — 장전 추론이 "오늘 발표가 나오면 기준선이
  얼마인가"를 알아야 서프라이즈를 해석한다.
- SQL은 새 파일 둘 `stock_event_outcome/select_thesis_recent.sql`·
  `stock_event_claim/select_thesis_pending.sql`(툴 SQL 재사용 금지 규칙). 창의 끝은
  `as_of_at` — 판정은 `created_at <= as_of_at`, 기대는 `stated_at <= as_of_at`.
  **판정을 `announced_at`으로 자르지 않는다.** 그것은 발표 시각이라 판정이 나기 전에도
  과거다 — 아직 판정하지 않은 발표가 판정된 것처럼 보이면 안 된다.
- 기대치 쪽 대표값은 SQL이 중앙값으로 낸다(`percentile_cont`). 판정 경로가 행을 그대로
  주고 순수 함수가 집계하는 것과 다른데, 저쪽은 **저장할 값**이라 규칙을 테스트 가능한
  코드에 둬야 하고 이쪽은 모델에게 읽히는 문맥이기 때문이다. 주체별 최신만 세는 것
  (`DISTINCT ON`)은 양쪽이 같다.
- 인자는 `ticker` 하나. 추적 목록 밖은 `ToolLimitExceeded`(기존 `analyst_opinions`와 같은
  처리). 건수 상한 `MAX_TOOL_RESULTS`(20).
- **tool call 상한 주의**: 툴이 14개가 된다. `MAX_TOOL_CALLS` 12를 이미 넘어 있는 상태라
  ([2-agent.md](2-agent.md) 1절) 이 툴 추가로 더 벌어진다 — 상한 조정은
  [TUNING.md](TUNING.md)의 기존 항목을 따르고 여기서 건드리지 않는다.

## 6. 컨센서스 수집기 — 후행 단계

LLM 추출만으로 시작할 수 있지만, 실적·DPS는 정형 컨센서스가 있어 숫자가 더 정확하다.
**주주환원 총액 기대치는 정형 컨센서스가 없다** — 그건 계속 추출이 담당한다.

- **출처 실측(spike)이 선행한다** — [6-analyst.md](6-analyst.md) 1절 형식으로 이 문서에
  결과를 적은 뒤 구현한다. 후보: 네이버 모바일 증권 내부 JSON(리서치 수집과 같은 계열,
  종목 컨센서스 API 존재 여부·필드 실측 필요), FnGuide `comp.fnguide.com` 스냅샷 페이지.
  robots·terms 확인과 `document_source.terms_url` 기록 규칙은 네이버 리서치 선례(사용자
  결정 기록)를 따른다 — 다만 이건 문서 출처가 아니라 수집기라 기록 위치는
  `source_record.metadata`와 시드 리비전 주석이 된다.
- 수집기는 `collectors/analyst/` 아래 클래스로(도메인: 애널리스트 추정치). 종목 순회는
  `instrument/select_watched.sql`(투자의견 수집과 같은 SQL). DAG는 일 1회면 충분하다 —
  컨센서스는 분 단위로 안 움직인다.
- 저장은 `stock_event_claim`에 `claim_kind='expectation'`, `source_record_id` FK,
  `broker=NULL`. **같은 테이블, 출처 유형만 다르다** — 판정 로직(4절)이 컨센서스를
  우선하는 것으로 연결이 끝난다.
- 2절의 UNIQUE는 `document_id` 축이라 NULL인 컨센서스 행을 잡지 못한다. 컨센서스 쪽
  멱등키(예: 조회일 단위 UNIQUE 부분 인덱스 또는 upsert)는 spike에서 응답 갱신 주기를
  실측한 뒤 이 절에 확정한다 — 하루 한 번 수집이라 재실행 중복만 막으면 된다.
- 실패 판정은 종목별 태스크 매핑(`.expand`) — 종목 하나의 실패가 그 태스크의 실패이고
  재시도도 그 종목만 다시 돈다(`fred_*` 형태).

## 7. 테스트

| 테스트 | 복제할 패턴 |
| --- | --- |
| `tests/migrations/test_stock_event_schema.py` | `test_thesis_schema.py` — offline `head_sql`: 테이블 셋, CHECK(이벤트·지표·claim_kind·verdict·출처 XOR·범위), UNIQUE, CASCADE/RESTRICT, 주석 |
| `tests/models/test_analysis_models.py` | `__all__` 등록, StrEnum 값과 CHECK 문자열 일치, `earnings` metric이 `EarningsMetric`과 같은 값인지 **대조** |
| `tests/modules/test_expectation.py` | `test_assessment.py`의 ScriptedModel — 추출 응답 검증(목록 밖 버림, period_key 형식, 단위 정규화 표: `9조`→9e12·`1,416원`→1416·모르는 표기 버림), 0건 원장, content_hash 변경 시 재추출 대상 |
| 〃 (판정 부분) | 순수 함수 — `aggregate_expectations`(컨센서스 우선, 증권사별 최신, `stated_at < announced_at` 컷, 0건이면 None), `classify_surprise` 경계값(±5.0), `period_end_for`(`2026Q2`→06-30, 형식 위반 실패), 실제 주장 불일치 시 판정 보류 |
| SQL ↔ 모델 대조 | `inserted_columns` 패턴 — claim·extraction·outcome insert, outcome에 `ON CONFLICT DO NOTHING`이 있고 `DO UPDATE`가 없는지 |
| `tests/dags/test_event_expectation_hourly.py` | 스케줄 `45 * * * *`, 태스크 구조, 화면 메타데이터, 실패 판정 형태 |
| `tests/modules/test_thesis.py` | 툴 이름 집합에 `event_surprises`, 목록 밖 ticker 거절, "모든 툴 창의 끝은 `as_of_at`" 목록 |

가짜 커서는 컬럼 이름과 조인 조건이 틀려도 통과한다. 그래서 **새 SQL 아홉을 실제
PostgreSQL에 대고 한 번 돌린다.** 운영 DB에는 아직 테이블이 없으므로 오프라인 리비전
SQL로 임시 로컬 DB를 만들어 거기에 EXPLAIN을 돌리고, 삼성전자 사례를 넣어 판정 흐름
전체를 재현한다(11절). 스크립트는 저장소 밖 스크래치패드에 둔다. DAG 실행으로 검증하지
않는다.

## 8. 함정

1. **자유 텍스트 → 이벤트 키 매칭 실패.** "중장기 주주환원 정책"이 `period_key`를 안
   가지면 그 주장은 버려진다. 버림 로그가 쌓이는 유형은 프롬프트 예시를 보강하거나
   event_type을 늘려서 푼다 — 키를 느슨하게 푸는 쪽으로 가지 않는다(느슨하면 매칭이
   조용히 틀린다).
2. **발표 후 회고를 기대치로 오인.** 추출은 못 가르고 판정의 `stated_at < announced_at`
   컷이 막는다(4절). `announced_at`이 늦게 잡히면(기사 감지 지연) 회고가 컷 안에 들 수
   있다 — 발표 시각의 원본을 공시(`disclosure_event.detected_at`)로 잡을 수 있는 이벤트는
   그쪽을 우선한다.
3. **실제값 주장이 기사마다 다르다.** "총 환원 8조"와 "배당+자사주 8.5조"처럼 집계 범위가
   다른 숫자가 실제로 온다. 값이 갈리면 판정하지 않는다(4절) — 이 보류가 자주 관측되는
   이벤트 유형은 metric 정의를 쪼개는 신호다.
4. **`MEET_BAND_PCT` 5.0은 근거 없는 시작값.** 판정 분포로 조정한다(4절).
5. **추출 LLM 비용.** 대상은 종목 태그 문서뿐이라 지금 물량(watched 2종목, 태그 문서
   하루 수 건)에서는 무시할 수준이다. watched가 크게 늘면 `batch_size`와 대상 조건
   (`value_score` 하한)이 손잡이다.
6. **컨센서스 페이지 구조 변경.** 표를 위치로 읽으면 칸 수 상수 검증 필수(수집기 공통
   규칙). 헤더 전체 대조(`mof.EXPECTED_HEADER` 패턴).
7. **정정 공시.** `earnings_fact`는 정정이 새 행이고 판정은 최신 `rcept_no`를 읽지만,
   **판정 행은 불변**이라 판정 뒤 정정은 반영되지 않는다. 정정으로 verdict가 뒤집히는
   사례가 관측되면 그때 재판정 정책을 정한다(지금 만들지 않는다).

## 9. 구현 순서 — worktree/PR 단위

| 순서 | 내용 | 상태 |
| --- | --- | --- |
| 1 | 저장 — 테이블 셋, 수기 리비전 `a4c9e1f7b3d6`, `stock_event_*` SQL, 순수 함수(단위 정규화·`period_end_for`·`aggregate_expectations`·`classify_surprise`), 스키마·함수 테스트 | 완료 |
| 2 | 추출·판정·Slack — `ExpectationExtractor`, `expectation_model()`, DAG, 렌더링, 테스트 | 완료 |
| 3 | thesis 툴 `event_surprises` — SQL 둘, Toolbox 추가, 테스트 | 완료 |
| 4 | 컨센서스 수집기 — 출처 spike 후 이 문서에 실측 절을 채우고 구현 | 미착수 |

**1~3을 한 워크트리(`feature-event-expectation`)에서 했다.** 리비전이 하나뿐이라
`down_revision`은 `b7e2f4a18c53`이다. 4는 판정 로직이 컨센서스 유무와 무관하게 돌게
설계돼 있어 언제든 붙는다.

운영 반영 순서(사용자):

1. `just migrate upgrade head` — 테이블 셋 생성.
2. `event_expectation_hourly` unpause. 첫 실행은 이미 평가된 종목 태그 문서의 백로그를
   `batch_size`(50)만큼 집는다. 그 백로그가 크면 `batch_size`를 낮춰 트리거한다.
3. 판정이 나오면 `SLACK_CHANNEL_MARKET`으로 발송된다. 첫 발송 전에 테스트로 보고 싶으면
   `slack_channel_test`로 🧪 머리표를 붙여 보낸다(프로젝트 규칙).
4. 새 툴 SQL은 리비전 적용 뒤 운영 DB에 읽기 전용으로 한 번 더 돌려 본다.

## 10. 만들지 않는 것

- **발표 일정 예측**(어닝 캘린더) — 판정은 실제값이 생길 때 반응하면 되고, "오늘 발표
  예정"을 알림하는 소비자가 아직 없다.
- **판정 재계산·정정 반영** — 첫 성공본 불변(8절 7).
- **시장 단위 이벤트** — 0절.
- **기대치 정확도 리더보드**(증권사별 적중률) — 판정과 claim이 쌓이면 쿼리다.
- **LLM 판정·해설** — 숫자 비교에 LLM을 쓰지 않는다. 서사가 필요하면 thesis review가
  발표 문서와 `event_surprises`를 보고 쓴다.

## 11. 구현 검증 (2026-08-24)

- `uv run pytest tests -q` 1774 passed, `uv run ruff check apps airflow migrations tests` 통과.
  새 테스트는 `test_expectation.py`(49), `test_stock_event_schema.py`(11),
  `test_event_expectation_hourly.py`(10)과 `test_thesis.py`·`test_analysis_models.py` 추가분이다.
- **SQL 아홉을 실제 PostgreSQL에서 EXPLAIN으로 확인했다.** 오프라인 리비전 SQL로 임시 DB를
  만들고(운영 DB는 건드리지 않았다) 조회 여섯과 쓰기 셋을 전부 돌렸다 — 전부 통과.
- **삼성전자 사례를 실제 DB에서 재현했다.** 기대 넷(대신 9.5조·키움 9.0조·한국투자 10조와
  대신의 옛 기대 7조)과 발표 8조를 넣고 `judge_pending`을 부르면:
  - 대표 기대치 9.5조(주체별 최신의 중앙값 — 대신의 옛 7조는 안 센다), 대조 4건,
    서프라이즈 **−15.7895%**, verdict **miss**.
  - Slack 문구: `발표 8.00조 vs 기대 9.50조 (기대 4건) → ▼ 미달 -15.8%`.
  - **재실행은 0건이다**(첫 성공본 불변). `stock_event_outcome` 행 수는 1로 유지됐다.
- **실적 경로도 실제로 확인했다.** 기대 10조 + `earnings_fact` 12조(CFS·period) →
  `beat` +20.0000%. 기간 기준(`period` vs `cumulative`)이 안 맞는 행은 실제값으로
  쓰이지 않는 것도 테스트가 잡는다.
- `event_surprises` 툴이 두 판정을 모두 돌려주는 것, 판정 뒤에는 그 이벤트가
  `pending_expectations`에서 빠지는 것을 같은 DB에서 확인했다.
- **DAG은 돌리지 않았다**(프로젝트 규칙). 운영 반영은 9절 순서대로 사용자가 한다.
