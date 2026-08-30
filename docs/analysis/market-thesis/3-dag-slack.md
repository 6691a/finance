# 3단계 — DAG와 Slack: `dags/market_thesis_forecast.py`·`market_thesis_review.py`

- 상위: [README.md](README.md)
- 의존: [1-storage.md](1-storage.md), [2-agent.md](2-agent.md), [5-followup.md](5-followup.md)
- 상태: 구현 완료(2026-08-21). **운영 반영 완료**(2026-08-28 실측) — 7절의 선행 조건 둘은
  해소됐다. 운영 head 값은 [README.md](README.md) 상태 줄이 갖는다
- 산출물: `airflow/dags/market_thesis_forecast.py`·`market_thesis_review.py`,
  `airflow/modules/thesis/common.py`·`thesis/forecast.py`·`thesis/review.py`,
  렌더링 함수(지금 `airflow/modules/thesis/render.py`), 두 DAG 테스트, 렌더링 테스트
- **여기서 처음 운영에 발송된다.** 테스트 발송은 `slack_channel_test`로만 한다(프로젝트 규칙).
- `sync_graph` 태스크는 이 단계에 없다. [4-graph.md](4-graph.md)가 `build_thesis` 뒤에 붙인다.

## 1. 스케줄

```python
SCHEDULE = MultipleCronTriggerTimetable(
    "35 8 * * 1-5",  # KST 평일 08:35 장전 = UTC 일~목 23:35
    "30 20 * * 1-5", # KST 평일 20:30 장후 = UTC 월~금 11:30
    timezone=KST_TIMEZONE,
)
```

시각은 앞단 DAG의 데이터가 준비되는 때에 맞춘다.

- **장전 08:35**: 문서 수집이 매시 05분(`document_ingestion_hourly`), 평가가 매시 25분
  (`document_assessment_hourly`)이다. 08:20에 돌면 08:05 수집분이 아직 점수가 없어 근거
  후보에서 빠진다. 08:25 평가가 끝난 뒤라야 밤사이 기사가 전부 후보에 든다. 투자의견은
  `kis_analyst_opinion_daily`가 08:20에 넣는다(6단계, 재시도 5분 × 2라 08:35 안이다).
- **장후 20:30**: 종목 확정 종가는 `kis_investor_trade_daily`가 18:10에 넣는다
  (`stock_investor_trade_daily.close_price`). 지수 15:30 봉은 16:00에 확정이다. 18:10 뒤면
  되지만 선행 DAG 재시도(2회 × 10분) 여유를 두고 20:30에 둔다. 슬롯을 둘로 쪼개지 않는다.
- **시각은 전제이지 보장이 아니다.** 두 선행 DAG 모두 재시도가 있어(`document_assessment_hourly`
  `retries=3` + 백오프, `kis_investor_trade_daily` `retries=2, 10분`) 그 시각을 넘길 수 있다.
  그래서 `build_thesis` 맨 앞에 readiness guard가 있다(2절).
- **슬롯은 DAG가 정한다**(2026-08-21 분리). 초판은 logical time으로 판정했는데(정오 전 =
  pre_open) 그러면 슬롯이 실행자의 의도가 아니라 시계에서 나오고 수동 실행이 벽시계로
  떨어진다. 휴장 판정은 `krx_open_day` — 모르면 돌린다. 장전 창의 시작은 전 개장일
  15:30(달력을 되짚어 찾는다).
- **`as_of_at`은 슬롯이 정한다**(장전 = 당일 08:35 KST, 장후 = 당일 15:30 KST,
  애프터마켓 = 당일 20:00 KST). 벽시계를 쓰지 않는다 — 오후에 장전 슬롯을 clear해 다시
  돌려도 장중 정보로 아침 예측을 덮지 않는다(event-time cutoff까지, README 1절).
- DAG 인자: `max_active_runs=1`, `default_args={"retries": 3, "retry_delay": timedelta(minutes=10)}`.
  재시도 셋은 readiness guard가 선행 DAG의 지연을 기다리는 수단이다.
- `build_thesis`만 `execution_timeout=thesis.common.BUILD_TIMEOUT`(30분)이다. 요청 타임아웃은
  모델 호출 하나만 막고 한 빌드는 모델을 여러 번(왕복 3 + 답변 + 교정) 부르므로 태스크
  울타리가 따로 있어야 한다. 채점·해설은 밀린 날짜를 따라잡느라 길어질 수 있어 두지 않는다.

## 2. 태스크 — `build_thesis >> grade_followups >> narrate_followups >> notify_slack`

**5단계를 함께 구현했다**(2026-08-21). 초판은 `build_thesis >> notify_slack` 둘이고 채점이
`build_thesis` 안에 있었는데, [5-followup.md](5-followup.md) 7절대로 채점과 해설을 각자
태스크로 뺐다. 채점 경로가 둘이면 어느 쪽이 그 행을 썼는지 알 수 없다.

1. `build_thesis`:
   1. **readiness guard** — SQL 하나로 선행 데이터가 있는지 본다. 없으면 `ThesisNotReady`
      (일반 예외)를 올려 Airflow 재시도에 맡긴다. 재시도를 소진하면 그 슬롯은 없던 것으로
      남는다(다음 슬롯이 새로 쓴다). DAG 간 센서보다 싸고, 기준이 "시각"이 아니라 "데이터"다.
      - 장후: `stock_investor_trade_daily`에 `run_date` 행이 watched 종목 전부 있고,
        `index_bar`에 KOSPI·KOSDAQ의 `run_date` 15:30 봉이 있다.
      - 장전: `document.assessed_at`의 최댓값이 `as_of_at - 20분` 이후이거나, 직전 1시간에
        `detected_at` 문서가 0건이다(평가할 게 없었던 시간). 미평가 backlog 0건을 조건으로
        걸지 않는다 — 평가에 실패한 문서는 `assessed_at`이 NULL로 남는 설계라 backlog가
        0이 되지 않을 수 있다. **"직전 1시간 0건"은 최근 24시간 안에 `detected_at` 문서가
        하나라도 있어야 인정한다** — 그렇지 않으면 수집기 자체가 며칠째 멈춰 있어도 매번
        이 조건으로 통과해, 근거 없는 추론이 조용히 나간다(오류 처리 규칙 "조용한 성공을
        만들지 않는다"와 충돌).
   2. **기존 행 확인** — `thesis/select_by_run.sql`에 (run_date, run_slot) 행이 있으면 LLM을
      부르지 않고 그 행들로 넘어간다(첫 성공본 불변, [2-agent.md](2-agent.md) 5절).
   3. 관측 상태 계산(SQL) → ThesisBuilder 실행 → `store_theses`(insert, 한 트랜잭션).
   XCom으로 `{run_date, slot, written}`을 넘긴다. 이걸 읽는 것은 `notify_slack`(`run_date`와
   `slot`)과 `narrate_followups`(`run_date`만) 둘이다. `written`은 UI에서 보는 값이고 코드가
   읽지 않는다.
   **채점은 여기서 하지 않는다** — `grade_followups`가 한다.
2. `grade_followups`: **장후에만 돈다. LLM 없음.** `thesis_outcome/select_pending_grades.sql`
   (미채점 (추론, 지평) 전부, 날짜 제한 없음) → 지평별 목표 영업일
   (`market_session/select_nth_open_day.sql`) → `select_horizon_return.sql` →
   `classify_outcome`·`brier_score` → `insert_grade.sql`. 장후가 실패했던 날의 것도 여기서
   회수된다. 목표일 종가가 없으면 미채점으로 남기고 다음 실행이 다시 집는다.
3. `narrate_followups`: **장후에만 돈다.** 지평 T+1·3·5마다 원 추론일을 거슬러 찾아
   `select_pending_narratives.sql`로 대상을 모으고 `FollowupNarrator`를 한 번씩 부른다.
   **지평 하나가 실패해도 나머지는 돈다** — 그 지평만 없던 것으로 남는다.
4. `notify_slack`: 이번 실행의 현재 슬롯 하나만 `thesis/select_by_run.sql` +
   `thesis_evidence/select_top_by_thesis_ids.sql`(rank 상위 3)로 다시 조회해 Slack에
   보낸다(아침 예측을 저녁에 다시 알릴 필요는 없다). 오늘이 T+5인 추론이 있으면 되돌아보기
   섹션을 덧붙인다. LLM을 다시 부르지 않는다 — 순수 조회+포맷+발송.

**4단계 `sync_graph`가 쓸 XCom 슬롯 목록은 아직 만들지 않았다.** 그래프 소비자가 없어
지금 만들면 아무도 안 쓰는 값이 된다. 4단계에서 `build_thesis`의 반환값을 넓힌다.

`notify_slack`을 `build_thesis` 뒤로 뺀 이유: LangGraph 재추론(비용 큼)과 발송 실패를
분리한다. Slack이 잠깐 죽어도 `build_thesis`를 다시 돌리지 않는다.

## 3. 실패 판정

프로젝트 규칙 그대로.

- `build_thesis`: `LlmError`·`ThesisError` → `AirflowFailException`(재시도해도 같음),
  `RetryableLlmError`(429·5xx·네트워크)·`ThesisNotReady`는 그대로 올려 Airflow가 재시도.
  재시도가 LLM을 두 번 부르는 일은 없다 — 첫 성공본이 있으면 건너뛴다.
- `notify_slack`: `slack_kr_market_briefing.py`와 같다. `SlackError` → `AirflowFailException`
  (재발송해도 같은 결과), `ConnectionError` → 그대로 올려 재시도.
  **발송은 at-least-once다.** `slack.py`는 응답 없는 실패(`SlackClientError`)를
  `ConnectionError`로 올리는데, 서버가 메시지를 수락한 뒤 응답만 끊긴 경우도 여기 들어가
  재시도가 같은 메시지를 한 번 더 보낼 수 있다. 중복이 실제로 문제가 되면 그때
  `client_msg_id`(슬롯·dag_run_id로 고정)를 넣는다. 지금은 문서화로 끝낸다.

## 4. Slack 알림

**결론 하나와 그 판단에 쓴 근거**를 보인다. 근거 없이 확률만 보내면 읽는 사람이
검증할 수 없는 시장 서사가 된다 — "어떤 정보로 어떤 결론을 냈는가"가 이 기능의 목적
(README 0절)이라 Slack에서도 보여야 한다. 헤더는 `장전 전망`(pre_open) / `장후 리뷰`
(post_close) / `애프터마켓 리뷰`(post_nxt_close, [7-nxt-review.md](7-nxt-review.md)).
subject마다 `section()`(결론·이유)과 `context()`(근거) 둘:

```
*{label}*
*▼ 하락 62%*
> {down_reasoning}

📎 *판단 근거*
• <{url}|{title}>
    {mechanism}
• {title}
    {mechanism}
```

- **처음에는 세 확률·세 이유를 전부 그렸다**(사용자가 요청한 "오를 확률/이유, 내릴 확률/
  이유, 횡보 확률/이유" 그대로). 2026-08-25에 결론형으로 바꿨다. 확률이 균등 근처에 몰려
  있어(`pre_open` 12건 전부 최고 확률 0.32~0.44) 세 값을 나란히 두면 어느 것이 판단인지
  읽는 사람이 매번 골라야 했고, 반대 방향 이유가 결론과 같은 무게로 읽혔다. 보이지 않는
  확률과 이유는 `thesis` 테이블에 그대로 남고 채점(`brier_score`)은 셋을 다 쓴다 — 바뀐
  것은 표시뿐이다. 같은 커밋에서 프롬프트도 고쳤다(`PROMPT_VERSION` 5,
  [2-agent.md](2-agent.md) 4절) — 결론 하나만 보이려면 그 값이 맞아야 한다.
- **확률이 붙어 있으면 붙은 것을 전부 보인다.** 최고 확률에서 `VERDICT_TIE_GAP`(0.05)
  안에 있는 방향이 함께 나오고, 그때만 이유마다 `*▼*` 표시가 붙는다. 하락 41%·횡보 38%를
  하나로 접으면 모델이 고르지 못한 것을 우리가 대신 골라 준 셈이 된다. **0.05는 실측이
  아니라 시작값이다** — 매번 둘이 나오면 좁히고 한 번도 안 나오면 넓힌다.
- 근거는 `rank` 순이되 **결론 방향(`thesis_evidence.direction`)과 같은 것만** 상위 3개다.
  결론이 하나면 방향 표시를 생략하고, 둘이거나 폴백이면 `(하락)`처럼 밝힌다. 제목 아래
  줄에 `mechanism`을 붙여 "왜 그것이 근거인가"를 남긴다. URL이 있는 것(문서·공시)은
  `slack_document_briefing`과 같은 `<url|title>` 링크, 매크로는 제목만.
- **폴백**: 결론 방향의 근거가 0건이면 방향을 가리지 않고 상위 3개를 방향 표시와 함께
  보인다(모델이 `claims`에 안 담고 `evidence_refs`로만 올린 근거는 `direction`이 비어
  있다). 인용 자체가 0건이면 `📎 근거 없음 — 관측 상태만으로 추론`.
- 조회는 표시 개수(`SLACK_EVIDENCE_LIMIT` 3)가 아니라 `EVIDENCE_FETCH_LIMIT`(12)으로
  받는다. 방향으로 거른 뒤에도 3개가 남아야 하기 때문이다.
- subject 일부가 목록 밖 값으로 버려져 0건이면 "추론 결과 없음" 한 줄로 보낸다
  (`slack_document_briefing`의 0건 관례).
- 승인·보류 상태 머신을 두지 않는다는 원칙 그대로, 인터랙티브 버튼이나 스레드 피드백
  수집기를 만들지 않고, "이상하면 고치라"는 안내도 달지 않는다 — 틀린 판단은 틀린 채로
  남는 것이 기록이다.
- 렌더링 함수(`render_blocks`/`render_text`)는 추론 모듈이 갖는다(지금 `airflow/modules/thesis/render.py`)
  (`briefing/market.py`가 자기 도메인 렌더링을 갖는 것과 같다 — thesis는 정기 리포트
  3부작과 다른 도메인이라 `briefing/` 아래 두지 않는다).
- **Slack 한도**: 메시지당 블록 50개, `section` 텍스트 3,000자. 지금은 subject 4개 ×
  section 1개 + context 1개 + 헤더라 여유가 크다. 결론이 둘일 때의 이유 두 문장
  (각 ≤ 500자)과 근거 3개 × (제목 + 경로 200자)가 각각 3,000자·2,900자를 넘지 않도록
  `render_blocks`가 블록 단위로 자른다. watched 종목이 늘어 블록 50개에 가까워지면
  메시지를 나눈다 — 그때 일이다.

## 5. 환경

- 모델 키(`OPENAI_API_KEY` 또는 `XAI_API_KEY` — `thesis_model()`이 정하는 클래스가 스스로
  읽는다), `CONNECTION_ID` 연결.
- `SLACK_BOT_TOKEN`·`SLACK_CHANNEL_MARKET` — `slack_kr_market_briefing`·
  `slack_us_market_briefing`과 같은 채널을 재사용한다(thesis도 시장 판단이라 채널을 새로
  안 만든다. 채널을 분리하고 싶어지면 그때 `SLACK_CHANNEL_THESIS`를 추가한다).
- DAG 메타데이터: `dag_display_name`(이모지 + 한글 + 제공처), `description`, `doc_md=__doc__`,
  `Param`마다 `title`·`description`(프로젝트 규칙).
- LangChain·LangGraph import는 태스크 함수 안에서 한다(DagBag 30초 타임아웃).

## 6. 테스트

- `tests/modules/test_thesis_pipeline.py`에 추가 — 렌더링(`render_blocks`/`render_text`):
  슬롯별 헤더 분기, **결론 하나만 나오고 나머지 두 확률·이유는 안 나오는지**,
  **확률이 붙으면 붙은 것이 다 나오고 그때만 이유에 방향 표시가 붙는지**, 확률 퍼센트
  반올림, 근거가 결론 방향의 것만 `rank` 순 상위 3개인지·`mechanism`이 실리는지·
  맞는 방향이 없으면 방향 표시와 함께 폴백하는지·URL 있는 것만 링크·0건이면 "근거 없음",
  subject 0건일 때 "추론 결과 없음" 짧은 형태.
- `tests/dags/test_market_thesis_forecast.py`·`test_market_thesis_review.py`·
  `test_market_thesis_nxt_review.py` — 스케줄 고정(`35 8`·`30 20`·`0 21`),
  `max_active_runs=1`, 태스크 구조, `as_of_at`이 슬롯으로 고정되는지,
  **슬롯이 시계가 아니라 DAG로 갈리는지**(각 파일이 자기 모듈의 `SLOT`을 고정한다),
  `run_date`가 ISO 주 표기를 거절하는지, DAG 화면 메타데이터,
  `tests/dags/test_quote_intraday.py`의 `test_the_dags_stay_on_their_intended_schedules` 패턴.
- readiness guard(FakeConnection): 장후에 종가 행이 하나라도 빠지면 `ThesisNotReady`,
  장전에 `assessed_at` 최댓값이 오래됐고 직전 1시간 문서가 있으면 `ThesisNotReady`,
  문서가 0건이면 통과.
- 기존 행이 있으면 모델이 호출되지 않고 XCom 목록은 같은지.
- 렌더링 한도: section 3,000자 초과 시 잘림, 블록 수가 50 이하인지.

## 7. 배포 전 선행 조건 둘 — 둘 다 해소됐다(2026-08-28)

코드가 끝난 뒤 **운영에 나가려면 저장소 밖 작업이 둘 남아 있었다.** 아래는 그 기록이다.

1. ~~**운영 DB에 테이블이 없다.**~~ **해소.** 2026-08-21에는 `thesis`·`thesis_outcome`·
   `thesis_evidence` 셋 다 없고 `alembic_version`이 `c5f81d3a9b46`이었다. 2026-08-28
   읽기 전용 실측에서 운영 포인터가 체인 head까지 올라와 있고 `thesis`에 82행이 있다.
   (head 값의 원본은 [README.md](README.md) 상태 줄이다 — 여기 적으면 또 낡는다.)
2. ~~**`XAI_API_KEY`.**~~ **해소.** 2026-08-20에는 `compose/prod/airflow/.env`의 값이
   무효였다. 지금은 추론이 돌고 프롬프트 판 12까지 저장돼 있다.

`compose/**`와 `requirements.txt`는 이 단계가 건드리지 않는다. **운영 Airflow 이미지를
다시 빌드할 필요가 없다** — NAS clone에서 `git pull` 뒤 `just deploy-airflow`면 된다.

**`config.yaml`은 선행 조건이 아니다.** 한때 파싱이 깨져 있었으나 2026-08-21에 고쳤고
(`prod`는 `migration.enabled: false` + `read_only: true`), 애초에 `.gitignore` 대상이라
배포 산출물이 아니다. 막고 있던 것은 로컬에서 마이그레이션을 돌리는 것뿐이었다.

## 8. 배포 뒤

[README.md](README.md) 5절의 4주 검증 항목을 여기서부터 센다. **배포일이 0일이다.**
무엇을 언제 보고 어느 상수를 당기는지는 [TUNING.md](TUNING.md) 4절 캘린더에 있고,
자동으로 안 나오는 항목의 쿼리는 같은 문서 2절에 있다.
