# 6단계 — 애널리스트: 증권사 리포트와 투자의견

- 상위: [README.md](README.md)
- 날짜: 2026-08-22 (출처 조사는 2026-08-21)
- 상태: **구현 완료(2026-08-22), 운영 반영 전.** 사용자 결정으로 두 갈래(KIS 투자의견,
  네이버 리서치)를 함께 했다. 리비전 둘(`a1f3c7e9b2d4`, `c2d9e4f1a7b3`)을 올리고
  `kis_analyst_opinion_daily`를 unpause하는 것이 남았다. KIS spike 결과는 9절.
- 의존: [2-agent.md](2-agent.md)(툴을 늘리는 자리), [5-followup.md](5-followup.md)(리포트를
  6단계로 뺀 경위, 11절). 문서 수집 경로는 `docs/analysis/economic-document-archive-design.md`.
- 산출물: `apps/models/market.py`에 `StockAnalystOpinion`, `apps/models/content.py`의
  `SourceKind.RESEARCH`, 수기 리비전 둘, `airflow/modules/collectors/analyst/kis_opinion.py`의
  `KisAnalystOpinionCollector`, `airflow/dags/kis_analyst_opinion_daily.py`,
  `collectors/document/naver_research.py`의 `NaverResearchCollector`와 `ListingSource.enrich`, `documents.existing_external_ids`, `thesis.py`에 `analyst_opinions` 툴,
  SQL 넷(`stock_analyst_opinion/upsert.sql`·`select_thesis_recent.sql`,
  `document/select_existing_external_ids.sql`), 테스트 일곱 파일

## 0. 왜 — 전문가의 정리된 판단이 안 들어온다

**추론이 이 둘을 쓰는 경로는 둘이다.**

| 무엇 | 어디로 | 추론이 보는 법 |
| --- | --- | --- |
| KIS 투자의견·목표주가 | `stock_analyst_opinion` | `analyst_opinions(ticker)` 툴. 문맥이라 인용하지 않는다. 같은 날 같은 증권사 리포트가 있으면 그 요약이 `reason`으로 함께 온다 |
| 네이버 리서치 리포트 | `document` (`document_type=report`) | 기존 평가 경로를 그대로 탄다 — `document_assessment_hourly`가 점수·태그를 채우고 `recent_documents` 툴이 점수순으로 준다. `ref`가 붙어 **인용된다** |

두 경로 모두 장전(`market_thesis_forecast`)·장후(`market_thesis_review`)·사후 해설
(`FollowupNarrator`)이 같은 `ThesisToolbox`를 공유하므로 한 번에 열린다.


추론이 보는 것은 뉴스·공시·시세·수급이다([2-agent.md](2-agent.md) 1절의 툴 11개). 뉴스는
"무슨 일이 있었다"까지고, 그 사건이 종목 가치에 어떤 뜻인지는 애널리스트가 쓴다 — 목표주가를
올렸는지, 실적 리뷰에서 무엇을 봤는지, 시황 리포트가 오늘 어디를 보라고 하는지. 지금은 그 층이
뉴스에 섞여 들어오는 만큼만 잡힌다([5-followup.md](5-followup.md) 11절).

필요한 것은 둘이고 모양이 다르다.

1. **숫자** — 종목별 투자의견·목표주가와 그 변화. 구조화된 값이라 표로 저장하고 툴로 준다.
2. **글** — 리포트 요약. 종목분석뿐 아니라 시황·경제·채권 리포트가 지수 추론에 쓸 재료다.
   이것은 뉴스와 같은 문서라 `document` 경로로 흡수하면 평가·태깅·`recent_documents`가 그대로
   붙는다.

둘을 한 테이블에 넣지 않는다. 숫자는 종목·날짜로 조인하고 글은 점수로 고른다. 쓰임이 다르다.

## 1. 출처 조사 (2026-08-21 실측)

### 1.1 한국투자증권 OpenAPI — 숫자를 준다

공개 예제 저장소(`koreainvestment/open-trading-api`)에 둘이 있다.

| API | 경로 | tr_id | 입력 | 응답 필드(예제 매핑) |
| --- | --- | --- | --- | --- |
| 종목투자의견 | `/uapi/domestic-stock/v1/quotations/invest-opinion` | `FHKST663300C0` | `FID_COND_MRKT_DIV_CODE=J`, `FID_COND_SCR_DIV_CODE=16633`, `FID_INPUT_ISCD`(종목), `FID_INPUT_DATE_1/2`(YYYYMMDD) | `stck_bsop_date, invt_opnn, invt_opnn_cls_code, rgbf_invt_opnn, rgbf_invt_opnn_cls_code, hts_goal_prc, stck_prdy_clpr, stck_nday_esdg, nday_dprt, stft_esdg, dprt` |
| 증권사별 투자의견 | `/uapi/domestic-stock/v1/quotations/invest-opbysec` | `FHKST663400C0` | 위 + `FID_DIV_CLS_CODE`(0 전체/1 매수/2 중립/3 매도), `FID_COND_SCR_DIV_CODE=16634` | 위 + `stck_shrn_iscd, hts_kor_isnm, stck_prpr, prdy_vrss, prdy_vrss_sign, prdy_ctrt` |

- **증권사 이름 컬럼이 예제 매핑에는 없지만 실제 응답에는 있다** — `mbcr_name`(키움, 삼성,
  한국투자, 신한투자증권, 미래에셋, DB증권, 한화투자 같은 약칭). spike(9절)로 확인했고
  자연키에 들어간다.
- **`invest-opbysec`는 쓰지 않는다.** spike에서 `FID_INPUT_ISCD`가 종목코드인 것을 확인했지만
  같은 행에 조회 시점 현재가(`stck_prpr`, `prdy_vrss`)를 더해 줄 뿐이라 얻는 것이 없다.
- 리포트 본문은 없다. 숫자뿐이다.

### 1.2 네이버 — 공식 Open API에는 없고, 모바일 증권 내부 JSON이 있다

**`developers.naver.com`의 Open API에는 증권·리서치가 없다.** 검색(뉴스·블로그·웹문서),
데이터랩(검색어 트렌드·쇼핑인사이트), 파파고 등뿐이다.

대신 모바일 증권(`m.stock.naver.com`)이 화면을 그리려고 부르는 JSON API가 있다. 비공식이고
문서가 없지만 UTF-8 JSON이라 EUC-KR HTML(`finance.naver.com/research/*_list.naver`)을 파싱하는
것보다 훨씬 낫다.

| 용도 | URL | 응답 |
| --- | --- | --- |
| 목록 | `https://m.stock.naver.com/api/research/{category}?pageSize=N&page=1` | 배열. `researchId, title, brokerName, writeDate("2026-08-21"), readCount, endUrl`, 종목분석(`company`)만 `itemCode, itemName` |
| 상세 | `https://m.stock.naver.com/api/research/{category}/{researchId}` | `researchContent: {content(요약 HTML), attachUrl(PDF), opinion("Buy"), goalPrice("130000"), prevGoalPrice, priceAtWriteDate, …}` + `researchSummaries`(같은 종목의 다른 리포트 목록) |

`category`는 `company`(종목분석) · `industry`(산업분석) · `market`(시황정보) · `invest`(투자전략)
· `economy`(경제분석) · `debenture`(채권분석) 여섯이다. `marketinfo`는 404다.
2026-08-21 하루 건수는 종목 16 · 산업 12 · 시황 21 · 투자전략 12 · 경제 7 · 채권 6 = 74건이었다.
상세는 `company` 외 카테고리(`industry/45759`)에서도 같은 꼴로 확인했다.

**robots.txt가 막는다.** `finance.naver.com`과 `m.stock.naver.com` 모두 `User-agent: *`에
`Disallow: /`이고 `/research/`는 네이버 자체 봇(`yeti`)에만 열려 있다. **사용자가 감수하기로
결정했다(2026-08-21).** 그 결정은 `document_source`의 `terms_url`(robots.txt 주소)과
`terms_checked_at`(2026-08-21), 시드 리비전의 주석에 남긴다. 이용조건이 문제가 되면 코드가
아니라 `document_source.enabled`를 내리는 것으로 끝나야 한다 — 기존 출처와 같은 규칙이다.

### 1.3 증권사 리서치센터 직접 — 후순위

사마다 로그인·WAF·페이지 구조가 달라 수집기가 N개 된다. 네이버가 30여 사를 이미 모아 주므로
네이버로 시작하고, 네이버에 안 올라오는 증권사가 추론에 꼭 필요하다는 관측이 생기면 그때
한 곳씩 붙인다.

### 1.4 PDF 본문 — 범위 밖

`attachUrl`로 원문 PDF를 받을 수 있지만 `pypdf`가 Airflow 이미지에 없다. 의존성을 늘리려면
운영 이미지에 먼저 들어가야 한다(`.claude/CLAUDE.md` "Airflow와 공유하는 코드"). 요약 문단까지만
저장한다. `document_source.collection_mode`는 `feed_content`다 — 피드가 준 요약까지라는 뜻
그대로다.

## 2. 갈래 A — KIS 투자의견

### 2.1 테이블 `stock_analyst_opinion`

`apps/models/market.py` 끝에 둔다. 본보기는 `KrxStockShortSaleDaily`(종목·날짜 자연키,
`source_record_id` FK `ON DELETE RESTRICT`, `ix_<table>_source_record_id`).

| 컬럼 | 뜻 |
| --- | --- |
| `provider` | `kis` |
| `stock_code` | 종목코드 6자리 |
| `business_date` | 영업일자(`stck_bsop_date`, KST 날짜) |
| `broker_name` | 증권사 약칭(`mbcr_name`, KIS 표기 그대로) |
| `opinion`, `opinion_code` | 투자의견(`invt_opnn`, 제공처 자유 문자열 — 같은 응답에 `BUY`와 `매수`가 섞인다)과 구분코드 |
| `previous_opinion`, `previous_opinion_code` | 같은 증권사의 직전 투자의견과 구분코드 |
| `target_price` | 목표가(`hts_goal_prc`, 원, Numeric) |
| `previous_close` | 발표 전일 종가(`stck_prdy_clpr`, 원) |
| `gap_amount`, `gap_rate` | **발표 전일 종가 대비** 괴리(`stck_nday_esdg`, 원)·괴리율(`nday_dprt`, 퍼센트) |

- 자연키 `uq_stock_analyst_opinion_natural_key`는 `(provider, stock_code, business_date, broker_name)`.
- **괴리 값은 한 벌만 둔다.** KIS는 두 벌을 준다 — `stck_nday_esdg`·`nday_dprt`는 발표 전일
  종가 대비라 고정값이고, `stft_esdg`·`dprt`는 **조회 시점 현재가** 대비라 매일 바뀐다
  (실측: 전자 `231000 - 350000 = -119000`, 후자 조회 당시 현재가 `281500 - 350000 = -68500`).
  발표일 행에 조회 시점 값을 섞으면 되돌아보기 upsert마다 과거 행이 조용히 바뀐다.
  `krx_market_funds_daily`가 뜻이 불분명한 `prdy_ctrt`를 빼는 것과 같은 결정이다.
- `opinion`에 Enum·CHECK를 걸지 않는다. 제공처가 `매수`/`Buy`/`BUY`/`중립`을 섞어 보내는 값은
  외부 식별자다(타입 모델링 규칙). 구분코드(`opinion_code`)가 기계용이다.
- 주석은 전부 한국어, 단위(원·퍼센트)와 시간대(KST 날짜)를 적는다.
- 리비전은 수기로 쓴다(`6e09dafae6f8_add_thesis_tables.py` 형식). 운영 DB에 `makemigrations`를
  돌리지 않는다.

### 2.2 수집기 `airflow/modules/collectors/analyst/kis_opinion.py`

`KisAnalystOpinionCollector` 클래스다 — 자격 증명과 토큰이 상태라 종목마다 다시 넘기지 않는다
(`docs/convention/collectors-class-migration.md`). 검증 규칙은 `kis_positioning.py`를 따른다. `kis.py`에서 가져오는 것은 `access_token`,
`send_get`, 예외 타입뿐이다. `_call`·`_rows`·`_day`·`_decimal` 같은 private 헬퍼는 import하지 않고
같은 모양으로 다시 쓴다 — 수집기끼리 import하지 않는 규칙이다.

- 상수: `OPINION_PATH`, `OPINION_TR_ID = "FHKST663300C0"`, `OPINION_SCREEN = "16633"`,
  `SOURCE = "kis"`, `SOURCE_KEY = "invest_opinion"`.
- Pydantic `OpinionRow(frozen=True)`와 `from_payload`, 한 번의 조회 결과 `Fetch`, 그리고
  `KisAnalystOpinionCollector(token, app_key, app_secret)`의 `fetch(stock_code, start, end)`·
  `store(connection, fetch)`. 마스터만 보는 `watched_stocks(connection)`는 KIS와 무관해 모듈 함수다.
- **종목 목록은 Enum이 아니라 DB다.** `instrument.is_watched`를 `instrument/select_watched.sql`로
  읽는다 — `thesis.subjects`가 같은 SQL을 읽는다. 추적 종목이 늘 때 수집기를 고치지 않기
  위해서다. 기존 KIS 일별 수집기(`PositioningStock` Enum)와 다른 첫 사례라 모듈 docstring에
  이유를 적는다. **주의**: 이 SQL은 `market`을 거르지 않아 해외 상장 종목이 `is_watched`가 되면
  KIS 국내 API가 `rt_cd != 0`으로 답한다. 지금 watched는 005930·000660뿐이라 v1은 그대로 두고,
  그날이 오면 `select_watched_krx.sql` 하나로 `thesis.subjects`와 함께 고친다.
- 미래 날짜 행은 거부한다(`kis_positioning._reject_future_rows`와 같은 가드). 응답 헤더
  `tr_cont`가 `M`/`F`(다음 장 있음)면 `KisPayloadError`로 실패시킨다 — 잘린 응답을 조용히
  저장하지 않는다. 백필은 창을 줄여 돌린다. 연속조회가 실제로 필요하면
  `kis_market_calendar.py`의 `tr_cont` 루프를 따른다.
- `source_record`는 조회 한 번이 한 건이다: `("api", "kis", "invest_opinion")`, `payload=None`,
  `metadata`에 `stock_code`·구간·받은 행 수.
- upsert SQL은 `airflow/sql/postgres/stock_analyst_opinion/upsert.sql`,
  `krx_stock_short_sale_daily/upsert.sql` 형식(`provider` 리터럴, `ON CONFLICT (자연키) DO UPDATE`).

### 2.3 DAG `airflow/dags/kis_analyst_opinion_daily.py`

`kis_market_positioning_daily.py`를 본뜨되 아래가 다르다.

- `schedule="20 8 * * 1-5"  # KST 평일 08:20 = UTC 일~목 23:20`. 포지션 DAG는 전 영업일
  확정치라 화~토지만, **투자의견은 당일 아침 사건**이라 월요일 아침에도 돌아야 08:35 장전
  추론에 든다.
- `retries=2, retry_delay=5분`. 포지션 DAG의 1시간이면 08:35를 넘긴다.
- 자격증명은 환경변수 `KIS_APP_KEY`/`KIS_APP_SECRET`를 `SecretStr`로, 토큰은
  `access_token(Variable, …)` 캐시 — 기존 KIS DAG와 같다.
- Param은 `modules/period.py`의 `OBSERVATION_START/END_PARAM`·`LOOKBACK_DAYS_PARAM`,
  `resolve_observation_period(context)`. 기본 되돌아보기 7일 — 의견은 드문드문 나오고 전 주
  것을 다시 받아도 upsert라 무해하다.
- 휴장일 skip(`_skip_when_closed`), 종목 하나의 조회·저장이 트랜잭션 하나, 실패는 모아서 끝에
  하나라도 있으면 `AirflowFailException`(`kis_*` DAG의 판정 형태). 400/403/404는 즉시 실패.
- `dag_display_name="🎯 종목 투자의견·목표주가 (KIS)"`, `description`, `doc_md=__doc__`,
  Param `title`·`description`.

### 2.4 툴 `analyst_opinions(ticker)`

[2-agent.md](2-agent.md)의 "문맥만 주는 툴"에 들어간다. 근거 레지스트리에 넣지 않는다 —
투자의견은 인용할 출처가 아니라 시장 참여자의 관측이다. 리포트 자체는 `recent_documents`가
문서로 준다(3절).

- SQL은 새 파일 `stock_analyst_opinion/select_thesis_recent.sql`.
  **당일 행을 빼지 않는다.** `short_and_credit`이 당일을 빼는 것은 KIS가 장중에 0을 보내기
  때문이고, 투자의견은 아침에 나오는 당일 사건이 정상값이다.
- **사유를 함께 준다.** KIS는 숫자만 주고 왜 그 의견인지는 안 준다. 그 사유는 같은 증권사가
  같은 날 낸 리포트에 있고 그건 갈래 B가 `document`에 넣는다. 둘을 `LEFT JOIN LATERAL`로 잇고
  요약을 `reason` 칸에 싣는다(`MAX_OPINION_REASON_CHARS` 200자로 자른다 — 스무 건까지 온다).
  잇는 조건 셋:
  1. **날짜** — 리포트 `published_at`의 KST 날짜 = 의견 `business_date`.
  2. **종목** — 네이버 종목분석 제목이 `종목명: 제목 - 증권사` 꼴이라 `instrument.name`으로 맞춘다.
     `document_instrument` 태그를 쓰지 않는 이유는 그 태그가 **LLM 평가가 채우는 값**이라 평가
     전에는 비어 있기 때문이다. 마스터 이름은 수집 즉시 맞출 수 있다.
  3. **증권사** — KIS 약칭이 네이버 표기의 접두다(키움 ⊂ 키움증권, 한국투자 ⊂ 한국투자증권,
     한화투자 ⊂ 한화투자증권). 제목 끝의 ` - 증권사`를 찾는다.
  못 찾으면 `reason` 칸 자체가 없다(빈 문자열을 주면 모델이 "사유 없음"으로 읽는다). `LEFT`인
  이유는 네이버에 안 올라온 증권사·유료 전용 리포트가 있기 때문이다 — 숫자만이라도 준다.
  URL은 주지 않는다. 이 툴은 문맥이고, 인용할 `ref`가 붙은 같은 리포트는 `recent_documents`가
  따로 준다.
- 인자는 `ticker` 하나. 건수는 `MAX_TOOL_RESULTS`(20) 고정. 추적 목록 밖 ticker는
  `ToolLimitExceeded`로 거절하고 쓸 수 있는 목록을 메시지에 싣는다(`past_theses`의
  `subject_code` 처리와 같다).
- 반환: 영업일자, (증권사), 의견, 직전 의견, 목표가, 전일종가, 괴리율. 의견이 바뀐 행인지는
  모델이 `opinion != previous_opinion`으로 읽는다 — SQL이 따로 표시하지 않는다.
- `created_at <= as_of_at`로 자르므로 08:20 DAG가 재시도 끝에 08:35를 넘기면 그날 장전에는
  전날치까지만 보인다. 의도된 동작이다. `thesis_forecast.check_ready`는 문서 평가 진척만 보고
  이 테이블을 기다리지 않는다.
- `TOOL_DESCRIPTIONS`에 항목을 더하고, `recent_documents` 설명에 한 줄을 보탠다:
  "`source_slug`가 `naver_research_*`면 증권사 리서치 리포트다(제목 끝에 증권사, 종목분석은
  요약 첫머리에 투자의견·목표가)". 모델이 리포트를 뉴스와 같은 무게로 읽지 않게 하는 비용이
  한 줄이다.

## 3. 갈래 B — 네이버 리서치를 문서로 흡수

### 3.1 `document_source` 여섯 행과 `source_kind = research`

- `SourceKind`에 `RESEARCH = "research"`를 더하고 `ck_document_source_kind`를
  `('official', 'media', 'research')`로 바꾼다. 리비전은 수기로, `a9e4b72c5d18`(KRX·FSS 시드)을
  본뜨되 `upgrade_default` 맨 앞에서 CHECK를 drop → create 한다.
- `FeedSource.document_type`이 `research → report`를 돌려준다(`official → press_release`,
  나머지 `article`). `DocumentType.REPORT`와 `ck_document_type`에 `report`가 이미 있어 `document`
  테이블은 바뀌지 않는다. 지금까지 `report`를 쓰는 행이 0건이었다.
- 시드:

| slug | name | feed_url |
| --- | --- | --- |
| `naver_research_company` | 네이버 증권 리서치 · 종목분석 | `https://m.stock.naver.com/api/research/company?pageSize=30&page=1` |
| `naver_research_industry` | … 산업분석 | `…/industry?pageSize=30&page=1` |
| `naver_research_market` | … 시황정보 | `…/market?…` |
| `naver_research_invest` | … 투자전략 | `…/invest?…` |
| `naver_research_economy` | … 경제분석 | `…/economy?…` |
| `naver_research_debenture` | … 채권분석 | `…/debenture?…` |

  공통: `source_kind=research`, `country=KR`, `language=ko`, `collection_mode=feed_content`,
  `terms_url=https://finance.naver.com/robots.txt`, `terms_checked_at=2026-08-21`, `enabled=true`.
- **slug 접미는 API 경로 이름 그대로다**(`market_info`가 아니라 `market`). 코드가 slug에서
  카테고리를 뽑아 상세 URL을 만든다.
- **`pageSize=30`**. 매시간 도는데 시간당 신규가 30을 넘지 않고(하루 74건), 첫 실행 백로그
  (6 × 100 = 600건 상세 요청과 평가 — 평가는 시간당 `batch_size` 50)가 뉴스 평가를 밀어내는
  것을 피한다. 더 받고 싶으면 `feed_url`의 숫자만 바꾼다. 정책은 DB에 있다.

### 3.2 수집기 — `collectors/document/naver_research.py`의 `NaverResearchCollector`

KRX(JSON 서블릿)·FSS(HTML 게시판)가 이미 같은 자리에 있다. 차이는 **상세 페이지를 한 번 더
받는다**는 것이고, 그것을 위해 `ListingSource`에 선택 단계 하나를 더한다.

```
ListingSource(slug, fetch, parse, enrich=None)
  fetch : FeedSource -> FeedResponse                             # 목록 한 번
  parse : bytes -> (FeedItem..., truncated)                      # 목록 → 항목
  enrich: (Connection, FeedSource, FeedItem...) -> FeedItem...   # 새 항목만 상세
```

- `fetch_naver_research`는 `fetch_fss`와 같다 — scrapling `Fetcher.get(impersonate=…)`,
  `CurlError → ConnectionError`, 비2xx → `DocumentHTTPError`.
- `parse_naver_research`는 Pydantic `NaverResearchItem`으로 `model_validate_json` 한다. 배열이
  아니면 `DocumentPayloadError`(HTML 안내 페이지가 200으로 오는 것을 막는다). **빈 배열은
  정상**이다(새벽·주말). `writeDate`는 `YYYY-MM-DD`만 받는다.
  - `external_id = str(researchId)`, `canonical_url = endUrl`(상대 경로면 `urljoin`),
    `published_at = writeDate의 KST 자정 → UTC`(시각이 없다),
    `title = f"{title} - {brokerName}"`, 종목분석은 `f"{itemName}: {title} - {brokerName}"`,
    `summary = None`(상세가 채운다). `readCount`는 어디에도 넣지 않는다 — 조회수가 바뀔 때마다
    `content_hash`가 바뀐다.
  - **증권사 이름을 `[대신증권]`처럼 대괄호 말머리로 넣지 않는다.** `dedup._LEADING_TAGS`가
    15자 이하 대괄호 말머리를 벗기고 비교한다. 같은 날(둘 다 KST 자정) 두 증권사가 비슷한
    제목("삼성전자: 3Q 프리뷰")을 내면 하나가 중복으로 묶여 평가·브리핑·`recent_documents`에서
    빠진다. 제목 끝의 낱말로 넣으면 `titles_duplicate`의 "양쪽에 상대에 없는 낱말" 가드가
    다른 문서로 판정한다.
- `enrich_naver_research(connection, source, items)`:
  0. **종목이 붙은 리포트는 `instrument.is_watched` 안의 것만 남긴다**(2026-08-22, 사용자
     결정). 종목분석은 하루 수십 건인데 대부분 우리가 보지 않는 종목이고, 그것까지 저장하면
     LLM 평가 비용만 늘고 `recent_documents`가 관심 밖 종목으로 채워진다. 실측(2026-08-22):
     `pageSize=30`에서 종목분석 30건 중 2건(SK하이닉스)만 남았다. 종목이 없는 리포트
     (시황·투자전략·경제·채권·산업분석)는 시장 전체 이야기라 그대로 받는다 — 카테고리를 통째로
     끄는 손잡이는 `document_source.enabled`다. **거르기가 상세 요청 앞이다.**
     종목코드는 `FeedItem.stock_code`가 목록에서 실어 오고 **저장하지 않는다** — 종목 태그의
     원본은 LLM 평가가 만드는 `document_instrument`이고, 이 칸은 수집 단계 필터 전용이다.
     추적 종목은 `documents.watched_tickers`가 `instrument/select_watched.sql`로 읽는다
     (추론 대상·투자의견 수집과 같은 SQL).
  1. 이미 있는 `(source_slug, external_id)`를 SELECT 한다 — 새 SQL
     `document/select_existing_external_ids.sql`, 함수 `existing_external_ids`는 출처 공통이라
     `documents.py`에 둔다.
  2. **있는 항목은 결과에서 뺀다.** 다시 upsert하지 않는다. `document/upsert.sql`은
     `content_hash`가 다르면 `summary`를 덮는데, 목록만으로 만든 항목은 summary가 None이라
     상세 요약을 지우고 `select_pending_assessment`가 재평가로 집는다.
  3. 새 항목마다 상세 `GET …/api/research/{category}/{researchId}`. 실패(`DocumentHTTPError`·
     `ConnectionError`·`DocumentPayloadError`)는 그대로 올린다 — DAG가 출처 단위로 격리하므로
     그 출처만 이번 시간 실패하고, 다음 시간에 같은 항목이 다시 "새 항목"이다.
  4. `summary = normalize_text(content)`. `documents.NOISE_PATTERNS`의 첫 규칙이 HTML 태그를
     벗기므로 파서가 따로 필요 없다. 종목분석이고 `opinion`·`goalPrice`가 있으면 앞에
     `투자의견 Buy · 목표가 130,000 (직전 110,400) · `를 붙인다. 금액이 숫자가 아니면
     `DocumentPayloadError`.
- `document_listings.LISTING_SOURCES`에 여섯 slug를 등록한다. 레지스트리가 콜러블을 들고 있어
  `fetch_listing`·`parse`·`enrich_listing` 클래스 메서드를 끼운다 — 출처마다 객체를 만들어 준다.

### 3.3 DAG 흐름 — `document_ingestion_hourly.collect_source`

```
listing.fetch → listing.parse
→ (연결 열고, atomic 바깥에서) listing.enrich      ← SELECT 하나 + 상세 HTTP N번
→ with atomic(connection): store_documents(...)
```

HTTP를 트랜잭션 안에 두지 않는다. `store_documents`는 손대지 않는다 — 새 항목만 들어오므로
그대로 저장된다. DAG 모듈 docstring의 "인증은 없다. 전부 공개 피드다"에 네이버와 robots.txt
결정을 한 줄 더한다.

### 3.4 선태깅은 하지 않는다 (필터와는 다른 이야기다)

목록이 주는 종목코드는 **거르는 데만** 쓴다(3.2의 0단계). `document_instrument`에 미리 넣지는
않는다.

종목분석은 목록이 종목코드를 주므로 수집 단계에서 태그를 넣을 수 있고, 평가 단계의
`document_instrument/upsert.sql`은 `ON CONFLICT DO NOTHING`이라 덮이지도 않는다. 그래도 빼는
이유:

1. `store_documents`는 `executemany`라 `document.id`를 손에 쥐지 않는다. 선태깅하려면 id를 다시
   SELECT하는 경로를 공용 저장 함수에 한 출처 때문에 넣어야 한다.
2. 평가기는 `document_instrument`를 입력으로 읽지 않는다. 선태깅이 태깅 품질에 기여하지 않는다.
3. 제목에 종목명이 있고 `assessment.filter_tags`가 `005930: 삼성전자` 같은 변형도 복원하므로
   LLM 태깅으로 충분하다. 후보 목록(`instrument.is_watched`) 밖의 종목은 어차피 버려진다.

`recent_documents`에서 리포트의 `tickers`가 비는 것이 실측되면 그때 연다.

## 4. 시간표 — 아침 리포트가 장전 추론에 드는 경계

| KST | 무엇 |
| --- | --- |
| 매시 :05 | `document_ingestion_hourly` — 네이버 목록·상세 수집 |
| 08:20 | `kis_analyst_opinion_daily` — 추적 종목 투자의견 |
| 매시 :25 | `document_assessment_hourly` — 평가·태깅 |
| 08:35 | `market_thesis_forecast` — 장전 추론 |

**08:05 이전에 네이버에 올라온 리포트만 당일 장전 추론에 든다.** 증권사 모닝 리포트의 상당수가
08:05~08:35에 올라오는데, 그것은 당일 장후 리뷰(20:30)와 다음날 장전에 든다. 수집 주기를
앞당길지는 운영 실측 뒤에 정한다([TUNING.md](TUNING.md) 5절). 주기를 당기면 평가 DAG도 같이
당겨야 하고, 평가는 LLM 호출이라 비용이 따라온다.

## 5. 테스트

| 테스트 | 복제할 패턴 |
| --- | --- |
| `tests/collectors/test_kis_analyst_opinion.py` | `test_kis_positioning.py` — 가짜 `send_get`, 모델 metadata ↔ upsert 컬럼 대조, 미래 날짜 거부, `rt_cd != 0`, `tr_cont` 잘림 |
| `tests/migrations/test_stock_analyst_opinion_schema.py` | `test_kis_market_positioning_schema.py` — 오프라인 SQL에서 CREATE TABLE·UNIQUE·FK·COMMENT |
| `tests/dags/test_kis_analyst_opinion_daily.py` | `test_kis_market_positioning_daily.py` — 스케줄 `20 8 * * 1-5`, Param 집합 |
| `tests/modules/test_thesis.py` | 툴 이름 집합, `_statement_key`에 `FROM stock_analyst_opinion`, "모든 툴 창의 끝은 `as_of_at`" 목록에 `analyst_opinions`, 목록 밖 ticker는 DB를 건드리지 않고 `ToolLimitExceeded` |
| `tests/collectors/test_document_listings.py` | 레지스트리 집합에 여섯 slug, `parse_naver_research`(실측 JSON 축약본: external_id·canonical_url·제목 형식·`published_at`), 배열 아님 → 실패, 빈 배열 정상, `enrich`는 FakeConnection + monkeypatch 상세로 "기존은 빠지고 새 것만 상세", 종목분석 summary 접두, HTML 제거 |
| `tests/collectors/test_documents.py` | `source_kind="research"` → `document_type == "report"`, `existing_external_ids` 파라미터 모양 |
| `tests/modules/test_dedup.py` | 같은 날 두 증권사의 같은 제목 리포트가 `titles_duplicate`에서 False |
| `tests/migrations/test_document_schema.py` | `ck_document_source_kind`에 `'research'` |

DAG 실행과 Slack 발송으로 검증하지 않는다. 새 툴 SQL과 `select_existing_external_ids.sql`은
운영 DB에 읽기 전용으로 한 번 돌려 본다 — `stock_analyst_opinion`은 사용자가 리비전을 올린 뒤.

## 6. 문서·규칙 갱신

- [README.md](README.md) 상태 줄과 2절 6행 → 이 문서 링크와 산출물.
- [2-agent.md](2-agent.md) "툴 11개" → 12, 문맥 툴 표에 `analyst_opinions`, "툴은 여기서 늘린다"
  문단에 리포트는 `recent_documents`로 들어온다는 한 줄.
- [3-dag-slack.md](3-dag-slack.md) 시간표에 08:20.
- [5-followup.md](5-followup.md) 11절 "증권사 리서치 리포트 소스는 없다" 정정.
- [TUNING.md](TUNING.md) 5절 6단계 항목과 "새 툴" 후보 문구.
- `.claude/CLAUDE.md`와 `.codex/AGENTS.md`(함께 갱신)에 규칙 셋:
  - 수집기 작성 — 목록 수집이 상세를 받을 때는 이미 있는 `(source_slug, external_id)`를 먼저
    빼고 새 항목만 받는다(`ListingSource.enrich`). 기존 항목을 목록 정보로 다시 upsert하면
    `content_hash`가 달라져 본문이 지워지고 재평가가 돈다.
  - `document` 계열 — 출처 고유 값(증권사, 목표가)은 `document`에 컬럼을 더하지 않고
    제목·summary에 넣는다. 제목 말머리에 대괄호를 쓰지 않는다 — `dedup`이 벗긴다.
  - 수집기 작성 — robots.txt가 막는 출처를 사용자 결정으로 수집할 때는 `terms_url`·
    `terms_checked_at`과 리비전 주석에 그 결정을 남긴다.

## 7. 구현 순서와 운영 반영

A → B 순서로 **한 워크트리(`feature-analyst`)에서** 했다. 리비전이 둘이라 `down_revision`을
직렬로 이었다(`6e09dafae6f8 → a1f3c7e9b2d4 → c2d9e4f1a7b3`).

운영 반영 순서(사용자):

1. `just migrate upgrade head` — `stock_analyst_opinion` 생성, `source_kind` CHECK 교체,
   `document_source` 여섯 행.
2. `kis_analyst_opinion_daily` unpause. 첫 실행은 기본 되돌아보기 7일이고, 과거를 채우려면
   `observation_start/end`를 한 달 단위로 넘긴다(`tr_cont` 잘림은 실패다).
3. 네이버 출처는 다음 `document_ingestion_hourly`부터 자동으로 돈다. 첫 시간에 6 × 30건
   상세 요청과 평가 백로그가 생긴다(8절 5).
4. `stock_analyst_opinion/select_thesis_recent.sql`을 운영 DB에 읽기 전용으로 한 번 돌린다.

## 8. 함정

1. **`[증권사]` 대괄호 접두** — dedup이 벗겨 같은 날 같은 종목 리포트가 중복으로 묶인다(3.2).
2. **기존 항목 재upsert** — summary가 지워지고 재평가가 돈다. `enrich`가 빼야 한다(3.2).
3. **괴리 두 벌** — `stft_esdg`·`dprt`는 조회 시점 값이라 저장하면 과거 행이 매일 바뀐다(2.1).
4. **`select_watched.sql`이 `market`을 안 거른다** — 해외 종목이 watched 되면 KIS가 거절한다(2.2).
5. **첫 실행 백로그** — 6 × 30 상세와 평가. 평가가 밀리면 `document_assessment_hourly`를
   `batch_size`를 키워 수동 트리거한다(사용자).
6. **`published_at`이 KST 자정** — `select_pending_assessment`의 `ORDER BY published_at DESC`에서
   같은 날 뉴스보다 뒤로 간다. 시간당 신규 50건 미만이면 무관하다.
7. **08:20 DAG가 08:35를 넘기면** 그날 장전에는 전날치까지 — 의도된 동작(2.4).
8. **401** — `KIS_UNRECOVERABLE_STATUSES`(400/403/404)에 없다. 기존 일별 DAG처럼 실패 목록에
   넣고 재시도에 맡긴다. 토큰 강제 재발급 경로는 v1에서 만들지 않는다.

## 9. KIS spike 결과 (2026-08-22, 운영 키, 읽기 전용)

스크립트는 저장소 밖 스크래치패드에 두고 `config.yaml`의 키로 토큰 1회 + GET 3회를 불렀다.

- `invest-opinion`(`005930`·`000660`, 최근 30일): `output` 키는
  `stck_bsop_date, invt_opnn, invt_opnn_cls_code, rgbf_invt_opnn, rgbf_invt_opnn_cls_code,
  mbcr_name, hts_goal_prc, stck_prdy_clpr, stck_nday_esdg, nday_dprt, stft_esdg, dprt`.
  **`mbcr_name`이 있다** → 자연키에 증권사가 들어간다(2.1). 삼성전자 5행, SK하이닉스 11행.
- `tr_cont`는 빈 문자열 — 30일 구간이 한 장이다. 수집기는 `M`/`F`가 오면 실패시킨다(2.2).
- `invest-opbysec`(`005930`): 종목코드 입력이 맞고 같은 행에 `stck_prpr`·`prdy_vrss`·
  `prdy_ctrt`(조회 시점 현재가)를 더해 줄 뿐이다. 쓰지 않는다(1.1).
- 값의 모양: `hts_goal_prc`는 `"350000"`(쉼표 없음), `nday_dprt`는 `"-34.00"`. `invt_opnn`은
  **같은 응답 안에 `BUY`와 `매수`가 섞여** 있었고 구분코드는 둘 다 `"2"`였다. 직전 의견
  구분코드가 `"3"`인데 직전 의견 문자열은 현재와 같은 `BUY`인 행이 있었다 — 코드 체계는
  해석하지 않고 그대로 저장한다.
- 괴리가 두 벌이다(2.1). 발표 전일 종가 대비만 저장한다.

## 10. 구현 검증 (2026-08-22)

- `uv run pytest tests -q` 1590 passed, `uv run ruff check` 통과.
- 네이버 여섯 카테고리 모두 실제 응답으로 `fetch → parse → enrich`를 돌렸다(읽기 전용 HTTP,
  가짜 연결). 목록 3건 중 "이미 있는" 1건이 빠지고 2건만 상세를 받았고, 종목분석 요약이
  `투자의견 Buy · 목표가 110,000 (직전 74,500) · …` 꼴로 나왔다. 다른 카테고리는 요약 문단만.
- `document/select_existing_external_ids.sql`을 운영 DB에 읽기 전용으로 돌려 `krx` 최신 두
  건이 그대로 돌아오는 것을 확인했다. `stock_analyst_opinion/select_thesis_recent.sql`은
  테이블이 운영에 생긴 뒤 돌린다.
- **개발 DB(localhost:15432)에 실제로 수집했다**(사용자 요청, DAG 없이 모듈 함수로).
  리비전 둘을 올린 뒤 KIS 투자의견 16행(삼성전자 5, SK하이닉스 11 — 증권사 10곳, `BUY`·`매수`
  혼재 그대로), 네이버 리포트가 `document_type=report`, `content_level=feed_content`로 들어갔다.
  종목분석 summary는 `투자의견 Buy · 목표가 130,000 (직전 110,400) · …` 꼴이다. 증권사가 스스로
  붙인 `[주식시황 위클리]` 말머리는 dedup이 벗기지만 제목 끝 증권사 낱말이 남아 같은 날 다른
  증권사와 구분된다.
- **추적 종목 필터를 넣고 여섯 카테고리를 `pageSize=30`으로 다시 받았다**: 종목분석 30건 중
  2건(SK하이닉스 자사주 소각 리포트 둘)만 남고 28건이 상세 요청 전에 빠졌다. 나머지 다섯
  카테고리는 30건씩 그대로 들어왔다(총 152건). 하루 분량은 종목 16 · 산업 12 · 시황 21 ·
  투자전략 12 · 경제 7 · 채권 6이라, 필터 뒤 하루 유입은 대략 60건 안쪽이다.
- 운영 DAG 실행과 Slack 발송은 하지 않았다. 첫 운영 실행은 사용자가 한다.
