# 문서-구현 격차 백로그

- 날짜: 2026-08-25 (2026-08-19 최초 조사, 이후 상태 반영. A부류 여섯 항목은 같은 날 main 머지 완료)
- 상태: 백로그 — 항목 하나가 worktree/PR 하나다
- 조사 방법: `docs/` 전체를 코드·마이그레이션·테스트와 대조. 완료 판정은 실제 코드 확인
- 2026-08-25 추가 조사: 규칙 위반 축(조용한 성공)으로 `airflow/`·`apps/` 전수. G-27~G-31
- 2026-08-25 재대조: 남은 16항목의 "현상"을 main 코드와 다시 맞춤. G-18 해소, 4항목 현상 정정
- 2026-08-25부터 이 파일도 추적된다(`.gitignore`의 `docs/*` 삭제). 항목 상태 변화가 커밋에 남는다
- 착수 시점 검증: `uv run pytest tests -q` 1893 통과, `uv run ruff check apps airflow migrations tests` 통과

**ID는 고정이다.** 항목이 끝나도 번호를 재사용하지 않는다. 지시는 ID로 한다("G-01 해줘").
부류는 셋이다 — **A. 코드 보강**(구현됐지만 결함·구멍), **B. 미구현**(문서만 있고 코드가
없음), **C. 정리**(죽은 코드·스테일 문서). 여기에 **O. 관측**(코드가 아니라 재는 일)을 더한다.

---

## 작업 목록

| ID | 제목 | 부류 | 영향 | 크기 | 선행 |
| --- | --- | --- | --- | --- | --- |
| [G-30](#g-30-결측을-0으로-메우는-자리) | 결측을 0으로 메우기 | A | 높음 | 자리별 판단 | 실측 |
| [G-31](#g-31-무결과를-성공으로-넘기는-자리) | 무결과를 성공으로 넘기기 | A | 높음 | 자리별 판단 | 없음 |
| [G-28](#g-28-dag-실패-판정-누락) | DAG 실패 판정 누락 | A | 높음 | DAG 6 + 규칙 결정 | 없음 |
| [G-29](#g-29-예외-분류-뭉개기) | 예외 분류 뭉개기 | A | 중간 | DAG 9 + 모듈 4 | 없음 |
| [G-27](#g-27-실패-사유-소거) | 실패 사유 소거 | A | 중간 | DAG 8, 기계적 | 없음 |
| [G-11](#g-11-공시-선별-브리핑-slack_disclosure_briefing) | 공시 선별 브리핑 | B | 높음 | DAG 1 + 모듈 1 | 없음 |
| [G-26](#g-26-market-thesis-4단계--neo4j-투영) | thesis 4단계 Neo4j | B | 보류 | 모듈 1 + 태스크 1 | prod 인스턴스 |
| [G-25](#g-25-컨센서스-수집기--8단계의-후행) | 컨센서스 수집기 | B | 중간 | 수집기 1 + DAG | 출처 실측 |
| [G-16](#g-16-출처-시드-미완과-이용조건-확인) | 출처 시드·이용조건 | B | 중간 | 건당 리비전 1 | 조사 |
| [G-15](#g-15-문서-본문청크임베딩벡터-중복판정) | 본문·청크·임베딩 | B | 큼 | 여러 PR | G-16 |
| [G-12](#g-12-주간-종합-리포트) | 주간 종합 리포트 | B | 대기 | DAG 1 + SQL | 9월 중순 |
| [G-13](#g-13-market_daily_brief-일별-요약-테이블)·[G-14](#g-14-4단계-리포트-생성분석가툴market_report) | 일별 요약·리포트 생성 | B | 보류 | — | 재계획 |
| [G-10](#g-10-kis-프로그램매매-수집-전체) | KIS 프로그램매매 | B | 보류 | — | G-20 + 프로브 |
| [G-17](#g-17-sentry-metricsllm-토큰예산-지표) | 계측 지점 | B | 중간 | 결정 먼저 | 결정 |
| [G-08](#g-08-고아-모듈-trendpy) | `trend.py` 고아 | C | 낮음 | 삭제 또는 주석 | G-12 판단 |
| [G-24](#g-24-고아-모듈-appsrepositoriesbasepy) | `repositories/base.py` 고아 | C | 낮음 | 주석 한 줄 | 없음 |
| [G-19](#g-19-kis-semiconductor-minute-barsmd)·[G-21](#g-21-economic-document-archive-designmd) | 문서 둘 동기화 | C | 중간 | 문서 2 | 없음 |
| [G-20](#g-20-kis-program-tradingmd) | 프로그램매매 문서 재작성 | C | 보류 | 문서 1 | G-10 착수 시 |
| [O-1](#o-1-확률-보정prompt_version-5)~[O-4](#o-4-신호-셋-적중률rule_version-1) | 운영 관측 넷 | O | — | 쿼리 | 시간 |

---

---

## A. 코드 보강 — 조용한 성공

**출처**: 2026-08-25 전수 조사. `airflow/dags/` 33개, `airflow/modules/collectors/` 20개,
`airflow/modules/` 나머지, `apps/` 전부를 열어 확인했다.

`.claude/CLAUDE.md`의 "오류 처리" 절이 금지하는 것이 실제로 두 번 터졌다. **두 커밋 모두
발견된 DAG 하나만 고쳤고 같은 모양이 다른 곳에 남아 있다.** 아래 다섯 항목이 그 잔존분이다.

- `dc63c42` — `kis_investor_flow_intraday`가 `failures.append(name)`으로 사유를 버려
  태스크 실패 메시지에 "왜"가 없었다. 유량 제한·HTTP 5xx·DNS 실패가 같은 문장으로 보였다.
- `a73d228` — KIS의 `TIME LIMIT 00:00 ~ 15:40` 거절이 `KisResultError`로 뭉뚱그려져
  재시도 예산 둘을 전부 태운 뒤에야 죽었다. `KisTimeWindowError`와
  `collectors/kis.py:303`의 `result_error()`로 갈랐다.

**주석이 근거를 남긴 의도된 흐름은 아래에서 제외했다** — 휴장 구간 0건 수집(`source_record`는
남긴다), 문서 하나 실패 시 나머지 저장, `briefing/ops.py`의 올그린 발송, `market_session.py`의
"모르면 돌린다", `thesis.py`의 `handle_tool_errors=(ToolLimitExceeded,)`,
`apps/realtime/heartbeat.py`의 상태 파일 쓰기 실패.

### G-30. 결측을 0으로 메우는 자리

- **영향**: 높음 — "값 없음"과 "0"이 같아진다. 저장·화면·프롬프트 어디서도 되짚을 수 없다.
- **현상 (가장 위험)**: `airflow/modules/collectors/document/dart.py:620-621`.
  `int(payload.get("total_count") or 0)`이면 바로 아래 잘림 검사
  (`total_count > len(rows)`, `:624`)가 **항상 거짓**이 되어 무력화된다.
  `int(payload.get("total_page") or 1)`이면 1장만 받고 루프가 끝나 나머지 공시가 사라진다.
  규칙 "전체 건수와 받은 행 수를 대조한다"의 기준 구현 자체가 결측 한 칸에 죽는다.
- **현상 (표시)**: `airflow/modules/briefing/market.py:222-225` —
  `QuoteChange.change_percent`가 `previous_close` 결측·0을 **0.0%로 지어내** 브리핑 표에
  실제 등락처럼 싣는다. 같은 파일 `FlowSnapshot.change_percent`가 `None`을 주는 대조군이다.
- **현상 (감시)**: `airflow/modules/briefing/ops.py:225,258-259` —
  `document_counts[5] if ... else 0`, `backlog[0]/[1] if backlog else 0`.
  감시용 리포트라 조회가 비면 적체 0(=건강)으로 초록 위장된다.
- **현상 (수집)**: 빈 칸을 `0`/`Decimal(0)`으로 —
  `analyst/kis_opinion.py:105`(목표가·괴리율), `market/kis_investor_flow.py:249,259`,
  `market/kis_positioning.py:177`, `market/kis_quote.py:315`(빈 응답이 all-zero가 되어
  `closed` 판정으로 넘어가 "장이 닫혔다"로 읽힌다).
  `payload.get(...) or []`는 `document/dart.py:619`,
  `calendar/kis_market_calendar.py:212`. 토큰 만료 파싱 실패는
  `collectors/kis.py:442`가 `expires_in` 기본 86400으로 메운다.
- **현상 (추론 입력)**: `airflow/modules/thesis_common.py:183-188`,
  `thesis_nxt_review.py:208,218` — `if previous:` / `if bar.return_pct:`가 **0을 결측으로
  취급해** 그 지수·종목이 프롬프트에서 통째로 사라진다. 모델은 "데이터 없음"으로 읽는다.
- **낮음**: `airflow/modules/briefing/chart.py:258-259` — MACD 히스토그램 `None`을
  `value or 0`으로 0 막대·상승색으로 그린다(그림 한정).
- **선행**: dart 두 칸은 바로 고친다. **수집기 빈 칸은 실측이 먼저다** — 어느 칸이 진짜 0을
  갖는지(상장 전·거래정지 구간) 확인하지 않고 `None`으로 바꾸면 백필이 멈춘다.
- **검증**: dart는 `total_count`·`total_page` 키가 빠진 응답으로 잘림 검사가 살아 있는지,
  `change_percent`는 `previous_close=None`일 때 `None`을 주는지 테스트한다.

### G-31. 무결과를 성공으로 넘기는 자리

- **영향**: 높음 — 규칙 "조용한 성공을 만들지 않는다"의 직접 위반이다.
- **현상**: `airflow/modules/collectors/calendar/kis_market_calendar.py:472` —
  settlement UPDATE가 **0행이어도** warning만 남기고 `settlement`를 그대로 반환한다.
  `market_calendar_daily`는 "US settlement for ..."로 저장 성공 로그를 찍는다.
- `airflow/dags/kis_market_positioning_daily.py:239` — 판정이 예외 발생 여부뿐이라 12개
  조회가 전부 `rt_cd=0` + 0행으로 와도 "Stored 0 positioning rows"로 성공한다.
  `kis_investor_flow`는 all-zero를 실패시키는데 여기만 없다.
- `airflow/modules/collectors/market/kis_positioning.py:511` — 신용잔고는 결제일로 요청하고
  거래일로 거르는데, 패딩이 어긋나 `kept`가 0행이 되어도 검사 없이 0건 성공.
- `airflow/modules/collectors/calendar/kis_market_calendar.py:324` — `tr_cont`가 "더 있다"인데
  페이지 상한에서 `logger.info`만 남기고 정상 반환. 잘림이 호출자·DAG로 안 올라간다.
- `airflow/dags/document_ingestion_hourly.py:90` — `truncated`를 warning만 하고 저장·성공.
  `source_record.metadata`에는 들어가지만(`document/documents.py:462`) 아무도 읽지 않는다.
- `airflow/modules/collectors/document/dart.py:729` — 정기보고서 원문에서 지표를 하나도
  못 뽑으면 `continue` 후 `None`. 계보도 안 남고 실패로도 안 잡힌다.
- `airflow/modules/thesis.py:1418-1427` — 일봉 값이 계약을 깨면
  `except (TypeError, ValueError, ValidationError): return None`. 로그도 카운터도 없이
  기술지표가 프롬프트에서 사라진다.
- `airflow/modules/expectation.py:778-782` — `classify_surprise` 실패(기대치 0)를
  warning + `None`으로 흘리고 아무도 그 건수를 안 센다. 체계적 오류가 "새 판정 없음"으로 보인다.
- **낮음**: `airflow/modules/collectors/document/document_listings.py:234,244` —
  `nttId`·날짜 파싱 실패를 세지 않고 `continue`. 금감원 마크업이 바뀌면 항목이 조용히 줄어든다.
- **작업**: 자리마다 "0건이 정상인 구간인가"를 먼저 판단한다. 정상이면 그 근거를 주석으로
  남기고(휴장 0건 수집이 그 형태다), 아니면 실패로 만든다. **판단 없이 남기지 않는다.**
- **검증**: 0행 UPDATE·all-zero 응답·잘린 페이지 각각에 대해 태스크가 죽는 테스트.

### G-28. DAG 실패 판정 누락

- **영향**: 높음 — 규칙 "항목별 실패 수집 … 마지막에 반드시 판정한다"의 위반이다.
- **현상 (판정 자체가 없음)**: `airflow/dags/dart_disclosure_intraday.py:196` —
  `extract_earnings`가 `_extract` 실패를 **세지도 판정하지도 않고** `continue`한다.
  대기 공시 전부가 실패해도 `stored=0`으로 성공이다. `_extract`(`:213-233`)는
  `DartPayloadError`·`ConnectionError`·rate limit을 warning 뒤 `None`으로 뭉개
  부르는 쪽이 "대상 아님"과 "실패"를 구분할 수 없다.
- `airflow/modules/thesis_review.py:197-200` — `narrate_followups`가 `(지평, 슬롯)`마다
  `ThesisError`를 warning + `continue`. 전건 실패해도 `written=0`으로 성공이다.
- `airflow/modules/thesis_review.py:139-146` — `grade_followups`도 같은 자리에 판정이 없다.
  개별 `continue`에는 근거 주석이 있으나 "전부 실패면 죽인다"가 없다.
- `airflow/dags/technical_signal_daily.py:127` — `result.skipped`를 warning만 하고 판정이
  없다. `result.stored == 0`도 안 본다.
- **현상 (판정이 규칙과 어긋남)**: `airflow/dags/kis_equity_bar_reconcile.py:254` —
  `if failures and not succeeded`. 규칙은 `kis_*`가 **하나라도** 실패면 죽이는 것인데
  전부 실패일 때만 죽는다. 근거 주석이 없다.
- **결정할 것**: `yahoo_quote_daily.py:157`, `yahoo_quote_intraday.py:284`,
  `kis_quote_intraday.py:243`도 `if not succeeded`(전건 실패)만 본다. 이쪽은 분산 저장
  근거 주석이 있다. **규칙 문구를 고칠지 코드를 고칠지 이 항목에서 정하고**, 정한 쪽을
  `.claude/CLAUDE.md`·`.codex/AGENTS.md`의 "DAG의 실패 판정" 절에 반영한다.
- **기준 구현**: `document_assessment_hourly`·`event_expectation_hourly` —
  `AssessmentResult.retryable`(True/False/None)이 분류를 위로 올리고 DAG가
  non_retryable → `AirflowFailException`, retryable → `RetryableLlmError`,
  전건 실패 → `AirflowFailException`으로 판정한다.
- **검증**: 전건 실패·부분 실패 각각에서 태스크가 의도한 대로 죽거나 사는 테스트.

### G-29. 예외 분류 뭉개기

- **영향**: 중간 — `a73d228`이 만든 분류가 대부분의 DAG에 도달하지 않았다.
  규칙 "아래에서 분류해 놓고 위로 문자열만 보내면 그 분류는 존재하지 않는 것과 같다".
- **현상 (분류를 안 잡음)**: `KisTimeWindowError`를 잡는 DAG는 `kis_investor_trade_daily`
  하나뿐이다. 나머지는 `except (KisResultError, KisPayloadError)`로 뭉쳐 잡아 `failures`에
  넣고 재시도를 태운다 — `kis_overseas_index_close.py:165`,
  `kis_investor_flow_intraday.py:218`, `kis_market_positioning_daily.py:223`,
  `kis_quote_intraday.py:239,294`, `kis_analyst_opinion_daily.py:165`,
  `kis_stock_minute_bars_daily.py:244`, `kis_equity_bar_reconcile.py:237`,
  `kis_index_daily.py:165`, `market_calendar_daily.py:159,202,236`.
  **서브클래스라 `except KisTimeWindowError`가 앞에 와야 한다 — 순서가 계약이다.**
- **현상 (팩토리 우회)**: `result_error()`를 거치지 않고 직접 raise 하는 2곳. 여기서 온
  `TIME LIMIT` 응답은 여전히 분류되지 않는다 —
  `airflow/modules/collectors/calendar/kis_market_calendar.py:207`,
  `airflow/modules/collectors/kis.py:435`(access_token 발급. `error_code`/`error_description`로
  응답 스키마가 달라 의도적일 수 있다 — **판단해서 주석을 남긴다**).
- **현상 (타입을 문자열로)**: 결과 객체에 `str(error)`로 담아 상위가 종류로 갈라낼 수 없다 —
  `airflow/modules/collectors/market/kis_quote.py:855,970`,
  `airflow/modules/collectors/market/yahoo.py:634,904`(DAG는 `error is None`만 본다).
- **현상 (`apps/`)**: `apps/realtime/service.py:499-508` — `_first_cause`가
  `ExceptionGroup`의 **첫 예외만** 대표로 삼고 `raise cause from group`으로 나머지를 버린다.
  flush 타이머의 DB 오류가 watchdog의 `ConnectWindowClosed`에 가리면 세션이 `SUCCEEDED`로
  닫히고 재연결 루프도 `failure_streak`을 0으로 되돌린다.
- **작업**: 잡는 쪽에 분류를 도달시킨다. 결과 객체에 담아야 하면 문자열이 아니라 예외 객체나
  분류 플래그(`AssessmentResult.retryable`이 그 형태다)를 담는다.
- **검증**: `TIME LIMIT` 본문을 주는 가짜 응답으로 각 DAG가 재시도 없이 죽는지.

### G-27. 실패 사유 소거

- **영향**: 중간 — 태스크는 죽지만 **왜 죽었는지가 실패 메시지에 없다.** `dc63c42`가 고친
  그 모양이 8개 DAG에 그대로 있다. 기계적이라 어느 때나 한 PR로 끝난다.
- **현상**: `except ... as error: logger.warning(...); failures.append(<라벨>)`.
  로그에만 `error`가 있고 Airflow 실패 메시지·Slack ops 표에는 없다.

  | 파일 | 줄 |
  | --- | --- |
  | `airflow/dags/kis_investor_trade_daily.py` | 200, 211, 215 |
  | `airflow/dags/kis_market_positioning_daily.py` | 221, 225, 229 |
  | `airflow/dags/kis_analyst_opinion_daily.py` | 163, 167, 171 |
  | `airflow/dags/kis_stock_minute_bars_daily.py` | 242, 246, 250 |
  | `airflow/dags/kis_equity_bar_reconcile.py` | 235, 239, 243 |
  | `airflow/dags/kis_index_daily.py` | 163, 167, 171 |
  | `airflow/dags/document_ingestion_hourly.py` | 137, 141, 145, 152 |
  | `airflow/dags/dart_disclosure_intraday.py` | 162, 166 |

- **따라야 할 형태 둘**: `dc63c42`의 `failures.append(f"{name}({error})")` + 종합 메시지
  구분자 `;`(사유에 쉼표가 들어간다), 또는 `kis_quote_intraday.py:220-282`의
  `SymbolOutcome(symbol=..., status=..., error=...)` 구조체.
  **G-29와 같이 하면 구조체 쪽이 낫다** — 문자열로는 종류를 못 살린다.
- **검증**: 실패 하나를 주입해 `AirflowFailException` 메시지에 사유가 실리는지.

---

## B. 미구현

**2026-08-25 main 대조**: 아래 10항목 전부 **여전히 미구현**이다. 근거는 항목마다 적힌
문자열의 히트 0건이다(`slack_disclosure_briefing`·`sync_graph`·`fnguide`·`sentry_sdk.metrics`·
`market_daily_brief`·`document_chunk`·`pgvector`·`program_trade`·`H0STPGM0`). 그 사이 사실이
바뀐 넷(G-11·G-25·G-14·G-15)은 해당 절에 **정정**으로 달았다 — 항목이 끝난 것은 아니다.

### G-11. 공시 선별 브리핑 `slack_disclosure_briefing`

- **영향**: 높음 — 쌓인 데이터를 처음 쓰는 자리다
- **왜 LLM인가**: 상장폐지·유상증자·자사주 취득·최대주주 변경 같은 공시의 중요도는 점수
  컬럼이 없고 목록 전체를 놓고 상대 비교해야 갈린다. 문서 브리핑에서 상위 동점 구간의
  순서를 모델에 맡긴 것과 같은 상황이다.
- **현상**: DAG·모듈·`SLACK_CHANNEL_DISCLOSURE` env 전부 없다(2026-08-25 재확인,
  `slack_disclosure_briefing` 히트 0건).
- **정정(2026-08-25)**: "`disclosure_event`를 어느 리포트도 읽지 않는다"는 **이제 거짓이다.**
  `airflow/modules/thesis.py:406,703-705,1196-1201`이 `recent_disclosures` 툴로
  `airflow/sql/postgres/disclosure_event/select_recent.sql`을 읽고, market-thesis는 Slack으로
  나간다. **읽히긴 하되 선별 브리핑이 없다**로 현상이 바뀐다 — 착수 시 그 툴과 중복되지 않게
  범위를 다시 잡는다.
- **재료는 준비됨**: `DocumentPicker`의 allowed_ids 패턴
  (`airflow/modules/briefing/picks.py:133`)을 그대로 복제한다. 새 수집 DAG는 없다.
- **작업**: 하루치 공시 목록을 한 번에 모델에 주고 "주목할 공시 + 이유 한 줄"을 고르게
  한다. 건별 호출로 쪼개지 않는다. 실적 숫자(YoY·QoQ)는 SQL이 만들고 해석만 모델이
  쓴다. 선별 실패는 시간 역순 전체 목록 폴백이고 리포트는 그대로 나간다. 0건이면
  모델을 부르지 않되 메시지는 보낸다.
- **결정할 것**: 채널을 문서 채널과 같이 쓸지 `SLACK_CHANNEL_DISCLOSURE`를 새로 둘지.
- **검증**: 목록 밖 ID를 버리는 테스트, 0건 폴백 테스트, 선별 실패 폴백 테스트.

### G-26. market-thesis 4단계 — Neo4j 투영

- **영향**: 보류 — 유지 여부를 +4주에 다시 본다
- **현상**: `airflow/modules/graph.py` 없음. `sync_graph` 태스크·`sync_only` Param 없음.
  로컬 compose에는 neo4j 컨테이너가 있고 prod 인스턴스는 없다.
- **선행**: prod Neo4j 인스턴스는 **이 저장소 밖 작업**이다(NAS 컨테이너, Airflow 이미지
  재빌드). 외부 리뷰 2회가 보류를 권했고 사용자 결정으로 넣은 단계다.
- **상세**: [market-thesis/4-graph.md](market-thesis/4-graph.md).

### G-25. 컨센서스 수집기 — 8단계의 후행

- **영향**: 중간
- **현상**: `stock_event_claim`은 지금 LLM 추출 주장만 받는다. 정형 컨센서스 행
  (`broker=NULL`, `source_record_id` 있음)을 넣는 수집기가 없다.
- **선행**: **출처 실측이 먼저다** — KIS 종목 컨센서스 API의 존재 여부·필드,
  FnGuide 스냅샷 페이지. 실측 없이 착수하지 않는다.
- **결과**: 붙으면 대표 기대치가 중앙값에서 컨센서스로 바뀐다. 판정 로직은 그대로다 —
  같은 테이블, 출처 유형만 다르다. 주주환원 총액은 정형 컨센서스가 없어 계속 추출이 맡는다.
- **정정(2026-08-25)**: **소비 측은 이미 붙어 있다.** `airflow/modules/expectation.py:202-214`가
  `source_record_id is not None` 행을 최신 컨센서스 우선으로 고른다. 남은 것은 그 행을 넣는
  수집기뿐이고, 쓰기 경로는 지금 `expectation.py:687-702` 하나이며 `source_record_id`에
  리터럴 `None`(`:700`)을 넣는다.
- **주의**: 2절의 UNIQUE는 `document_id` 축이라 NULL인 컨센서스 행을 잡지 못한다.
  컨센서스 쪽 유일성은 따로 건다. 상세는
  [market-thesis/8-expectation.md](market-thesis/8-expectation.md) §6.

### G-16. 출처 시드 미완과 이용조건 확인

- **영향**: 중간 — G-15의 선행
- **현상**: 08-19 이후 출처는 늘었다(krx·fss, eia·census·boj, us_government, 네이버
  리서치 여섯, 인포맥스). 남은 것은 **`terms_checked_at`이 사실상 비어 있다는 것**이다 —
  네이버 리서치(`c2d9e4f1a7b3`)만 값을 넣었고 나머지 시드 리비전은 주석에
  "이용조건 확인도 하지 않았다"라고 적어 뒀다.
- **작업**: 출처 추가는 건당 리비전 1개(피드 URL 조사 포함). `terms_checked_at` 기입은
  **이용조건 확인 작업 자체가 본체**이고 `full_text`로 올릴 근거가 여기서 나온다.
- **순서**: 이용조건 확인 → `collection_mode` 상향 → G-15 본문 수집.

### G-15. 문서 본문·청크·임베딩·벡터 중복판정

- **영향**: 큼(여러 PR)
- **현상**: `document_chunk` 테이블, HNSW 인덱스, `langchain-text-splitters`,
  `full_text` 본문 수집, 벡터 코사인 + LLM 회색지대 판정, `update`/`repeated` 분류
  전부 0건. `document.body`는 항상 NULL
  (`airflow/modules/collectors/document/documents.py:498` — upsert 파라미터에 리터럴 `None`을
  넣고 `content_hash(…, None)`으로 해시까지 본문 없이 계산한다. 2026-08-25 경로 정정).
  paradedb 이미지와 `shared_preload_libraries=pg_search`는 준비됐는데
  `CREATE EXTENSION pg_search`·BM25 인덱스 마이그레이션이 0건이다.
- **순서**: 본문 수집 → 청크·임베딩 → 벡터 dedup. 이 순서로만 의미가 있다.
  본문 수집은 G-16(이용조건 확인)이 선행이다.
- **쪼개는 단위**: ① `CREATE EXTENSION` + BM25 인덱스 리비전, ② 본문 수집(출처 하나부터),
  ③ 청크·임베딩, ④ 벡터 dedup과 `relation`·`duplicate_of`·`evidence_chunk_ids` 3키.

### G-12. 주간 종합 리포트

- **영향**: 의도적 대기 — 착수 9월 중순 이후
- **현상**: 주간 DAG·주간 집계 SQL 없음.
- **시기**: 공시·실적 수집이 2026-08-16 시작이라 지금 만들면 첫 리포트가 빈약하다.
  몇 주치가 쌓인 뒤에 만든다.
- **연동**: G-11이 먼저 있으면 그 산출물을 재인용해 더 싸진다. `trend.summarize` 재사용
  전제라 G-08의 판단이 여기에 묶여 있다.

### G-13. `market_daily_brief` 일별 요약 테이블

- **영향**: 보류
- **현상**: 저장소 전체에서 문자열 0건. 거래일 1행 요약(환율·10년물·종목 종가·수급·
  공시 건수·상위 문서)이 archive-design §7에만 있다.
- **판단**: G-14의 입력이다. G-14를 브리핑 축(산출물이 Slack 메시지)으로 재편하면 이
  테이블의 필요 자체가 사라진다. **먼저 G-14를 정한다.**

### G-14. 4단계 리포트 생성(분석가·툴·`market_report`)

- **영향**: 보류
- **현상**: 카테고리 분석가와 `market_report` 테이블이 0건이다.
- **정정(2026-08-25)**: "툴 5종·`MAX_TOOL_CALLS` 전부 0건"은 **거짓이다.**
  `airflow/modules/thesis.py:151`에 `MAX_TOOL_CALLS = 12`가 있고 `:1142-1146`이 강제한다.
  툴은 `:699-777`에 14종, 인자 스키마는 `thesis_tools.py`가 갖는다. archive-design §8.2의
  "등록된 툴만·행 상한·호출 상한" 설계는 **market-thesis가 이미 채웠다.**
  남은 것은 카테고리 분석가 분할과 저장용 `market_report` 둘뿐이다.
- **판정**: 브리핑 쪽은 Slack 메시지가 산출물의 전부이고, 재현 가능한 저장용 리포트는
  archive-design 4단계의 몫으로 미뤄 뒀다. **의도적 보류**로 두되 archive-design에 그
  사실을 표기한다(G-21에서 함께).
  market-thesis가 사실상 이 자리를 대신 채우고 있는지부터 판단한다.

### G-10. KIS 프로그램매매 수집 전체

- **영향**: 보류
- **현상**: 모델·테이블·upsert SQL·수집기·DAG·WS 구독(`H0STPGM0`/`H0NXPGM0`)·테스트
  전부 0건. 유일한 흔적은 `apps/realtime/service.py`의 확장 여지 주석.
- **선행 게이트**: [kis-program-trading.md](kis-program-trading.md) §5 작업1(REST 응답의
  누적/증분 의미 프로브). 문서 스스로 "이 의미를 확정하기 전에는 실시간 프레임을 운영
  테이블에 쓰지 않는다"고 걸어 뒀다. 착수 전 G-20(문서 재작성)이 먼저다.

### G-17. Sentry metrics·LLM 토큰/예산 지표

- **영향**: 중간
- **현상**: `sentry_sdk.metrics` 사용 0건. archive-design §10의 "LLM 토큰 사용량·일 예산
  대비 사용률" 지표가 없다. 버려진 태그는 WARNING 로그로만 남고 집계 화면이 없다.
- **결정이 먼저다**: 계측 방식(Sentry metrics vs 로그 기반 Grafana). 그 다음에 지점을
  고른다 — LLM 토큰 사용량, 평가 배치 처리 건수, dedup 연결 건수, 추출 버림 사유 분포(O-2).

---

## C. 정리 — 죽은 코드와 스테일 문서

**2026-08-25 main 대조**: G-18은 해소돼 완료 표로 갔다. 남은 5항목은 그대로다 —
`trend.py`·`repositories/base.py` 둘 다 소비자 0에 대기 주석도 안 붙었고,
G-19·G-20·G-21 문서는 지적된 자리의 본문이 미갱신이다.

### G-08. 고아 모듈 `trend.py`

- **현상**: 브리핑 LLM 요약을 뺄 때(2026-08-19) 추세 계산도 걷어냈는데
  `airflow/modules/briefing/trend.py`가 남았다. 프로덕션 임포터 0 —
  `tests/modules/test_briefing_trend.py`만 import한다.
- **작업**: G-12까지 유보할 거면 `trend.py` 상단에 그 사실을 주석으로 못박고, 아니면
  테스트와 함께 삭제한다(G-12 구현 때 git에서 되살리면 된다). **둘 중 하나를 지금 고른다.**

### G-24. 고아 모듈 `apps/repositories/base.py`

- **현상**: 31줄(`SortDirection`, `InvalidPeriodError`, async 세션 팩토리)에 소비자 0.
  FastAPI 백엔드가 오면 쓸 자리인데 그 사실이 파일에 없다.
- **작업**: 파일 상단에 "FastAPI 도입 전까지 대기"를 주석으로 남기거나 삭제. G-08과 같은
  판단이라 한 PR로 묶어도 된다.

### G-19. `kis-semiconductor-minute-bars.md`

- §1·§5.1·§12·§14의 `krx_equity_bar`/`nxt_equity_bar`는 폐기된 이름이다. §14 검증 SQL은
  지금 돌리면 `relation does not exist`. 실제는 단일 `stock_bar` + `exchange` 축이다.
- §4.2 NXT 세션 3분할 표(애프터 15:40~)는 실측 정정(15:30~, 단일 창 08~20시)과 다르다.
- §3.3 저장 필드 표의 `previous_close = output1.stck_prdy_clpr`는 REST 경로도
  `stock_investor_trade_daily` 조회로 바뀌었다.
- §12 테스트 파일명·경로가 실제와 어긋난다.
- §8은 구현 노트(2026-08-25)가 붙었지만 본문은 그대로다. §9는 G-04의 기준 문서다.

### G-21. `economic-document-archive-design.md`

- §4의 "urllib로 충분 / SDK 안 씀 / `LLM_BASE_URL`·`LLM_API_KEY` env" 서술은
  LangChain+LangGraph 이행(2026-08-16)으로 전부 폐기됐다. §3 제외 범위의
  "LangGraph·LangSmith"는 셋 다 실사용 중이다.
- §6.5 응답 스키마 10키 중 `relation`·`duplicate_of`·`evidence_chunk_ids` 3키는
  미구현(G-15)인데 문서는 현재형이다. `prompt_version` 예시도 실제와 다르다.
- §6.2 `document` 컬럼: 문서 `source_id` vs 실제 `source_slug`, 실재하는 `detected_at`·
  `assessed_content_hash`가 목록에 없다.
- §7·§8에 "재계획 중, 유예"를 표기한다(G-13·G-14 판정 반영).

### G-20. `kis-program-trading.md`

§4.1·§5 작업4가 지목하는 `airflow/modules/collectors/kis_realtime.py`는 없다. 상주
수집기는 `apps/realtime/`으로 옮겼다. G-10 착수 전에 WS 구현 위치·프레임 registry
(`apps/realtime/frames.py`) 기준으로 재작성한다.

---

## O. 관측 — 코드가 아니라 재는 일

상세와 쿼리는 [market-thesis/TUNING.md](market-thesis/TUNING.md) 2·3·5절에 있다.
**한 번에 한 손잡이**를 지킨다.

### O-1. 확률 보정(`PROMPT_VERSION` 5)

2026-08-25에 프롬프트에 `## 확률` 절을 넣고 Slack을 결론 하나로 바꿨다. 실현 `flat`은
코스피 6.1%·코스닥 11.4%·005930 4.9%·000660 6.5%인데 모델은 30~36%를 줬다.
**첫 확인**: 다음 실행들의 `prob_flat`이 10% 아래로 내려오고 최고 확률이 0.5를 넘는가.
`FLAT_THRESHOLD_PCT`는 outcome이 더 쌓일 때까지 건드리지 않는다.

### O-2. 이벤트 추출 버림 사유 분포(8단계)

한 유형에 몰리면 프롬프트 예시나 `StockEventType`을 늘린다. `verdict` 분포에서 `meet`가
사실상 없거나 대부분이면 `MEET_BAND_PCT`를 조정한다. 실제값 주장 불일치로 판정이 보류되는
빈도가 잦은 이벤트 유형은 `metric` 정의를 쪼개는 신호다.

### O-3. 툴 14개 vs `MAX_TOOL_CALLS` 12

툴이 상한을 넘었다 — 모델이 툴마다 한 번씩도 못 부른다는 뜻이다. **상한에 붙는 실행이
보이면** `MAX_TOOL_CALLS`부터 올린다. 툴 그룹별 서브 에이전트는 만들지 않는다(발동
조건은 TUNING 5절).

### O-4. 신호 셋 적중률(`RULE_VERSION` 1)

`sma_cross`·`macd_cross`·`rsi_reversal`의 지평 T+1·5·20 적중률을 배포 4주 뒤
[market-technical-indicators.md](market-technical-indicators.md) 12.6절 SQL로 본다.
같은 시점에 `prompt_version` 2 대 3의 지평별 Brier도 본다 — 개선이 없으면 관측 상태의
push를 빼고 툴만 남긴다.

---

## 완료된 항목 (기록)

**ID가 `—`인 행은 백로그를 거치지 않고 발견 즉시 고친 것이다.** 여기 없으면 안 고친 것이다.

| ID | 제목 | 완료 |
| --- | --- | --- |
| G-18 | `slack-report-design.md` 전면 재작성(527행 → 130행). 스케줄·섹션 표·차트·테스트 표 전부 코드와 일치 확인 | 2026-08-25 **작업 트리 미커밋** |
| — | 조용한 성공 ① — `kis_investor_flow_intraday`의 `failures`에 사유를 싣는다. 같은 모양 8개 DAG는 [G-27](#g-27-실패-사유-소거)에 남았다 | 2026-08-25 `dc63c42` |
| — | 조용한 성공 ② — KIS `TIME LIMIT` 거절을 `KisTimeWindowError`로 갈라 즉시 실패. 이 분류를 잡는 DAG는 아직 하나뿐이라 [G-29](#g-29-예외-분류-뭉개기)에 남았다 | 2026-08-25 `a73d228` |
| G-01 | 종목 봉 장중 조정 — 별도 DAG `kis_equity_bar_reconcile`, 30분 백업 | 2026-08-25 `0d1d772` |
| G-03 | dedup ② — `content_hash` 동일 ±72시간 연결. 지금 운영 데이터에서 새로 잡는 쌍은 0 | 2026-08-25 `2115467` |
| G-04 | 백필 `days` 상한 31일 — `Param.maximum` + 태스크 검사 | 2026-08-25 `4fc8cd8` |
| G-07 | ops 표에 `마지막` 열(경과 시간). `성공`은 실행−실패라 싣지 않는다 | 2026-08-25 `4fc8cd8` |
| G-05 | `KIS_ENABLE_NXT_REST` — 기본 켜짐, 모르는 값은 실패. `rest_exchanges()` 한 벌 | 2026-08-25 `5425df4` |
| G-06 | 로컬 Airflow 이미지에 matplotlib·fonts-nanum. 정합 테스트 추가 | 2026-08-25 `5425df4` |
| G-02 | 재평가 기아 — 신규·재평가를 나눠 번갈아 집는다 | 2026-08-23 `5b85e85` |
| G-09 | 죽은 참조 3건(`llm.py`·`stock_bar/upsert.sql`·로컬 requirements) | 2026-08-23 `b0ac40a` |
| G-22 | `document-assessment-workflow.md` 흐름도에 dedup 태스크 | 2026-08-23 `b0ac40a` |
| G-23 | `us-macro-indicators.md` 고용 2계열 반영 | 2026-08-23 `b0ac40a` |

---

## 권장 착수 순서

**A부류가 다시 찼다** — 2026-08-25 조용한 성공 전수 조사(G-27~G-31). 그것부터 본다.

1. **G-30** — dart의 잘림 검사가 결측 한 칸에 무력화되는 것이 제일 위험하다.
   수집기 빈 칸은 실측 뒤로 미루고 dart 두 칸과 `change_percent`부터.
2. **G-31 → G-28** — 무결과·판정 누락. G-28은 `yahoo_*`/`kis_*` 판정 규칙 결정을 낀다.
3. **G-27 + G-29** — 한 PR로 묶는다. 사유를 구조체로 실으면 분류도 같이 산다.
4. **G-24 + G-08** — 고아 둘의 거취를 한 PR로 정한다. 각 10분 미만.
5. **G-11** — 공시 선별 브리핑. 새 수집 없이 쌓인 데이터를 처음 쓰는 자리.
6. **G-19 + G-21** — 큰 문서 둘. 이후 작업의 기준이 되므로 B부류 착수 전에.
   G-19는 §8·§11.1에, G-21은 §6.4에 구현 노트가 붙었을 뿐 지적된 자리의 본문은 그대로다
   (2026-08-25 재확인). G-18은 해소돼 완료 표로 갔다.
7. 나머지는 각 항목의 선행 조건 순서대로.
