# 20단계 — 수집 데이터 조회 API와 화면 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** 지금 매일 쌓고 있는 수집 데이터 — 시세 봉, 지표 시계열, 문서·공시, 수급·신용·
공매도 — 를 웹 화면에서 직접 읽는다. 14단계가 만든 화면은 **추론**만 보여 주고, 그 추론이
딛고 선 **원자료**를 보는 자리가 지금 없다.

**Architecture:** 14단계의 앱을 그대로 늘린다. `apps/api/`에 리소스 넷(`quotes`·
`indicators`·`documents`·`positioning`)을 층 넷 규칙대로 더하고, `frontend/`에 화면과
차트 컴포넌트를 더한다. **새 상주 서비스도 새 저장소도 없다.** 시계열 차트만 새 의존성
하나(uPlot)를 들인다.

**Tech Stack:** FastAPI, SQLAlchemy async, Pydantic, React, TypeScript, Vite, uPlot

**Spec:** [12-api.md](12-api.md), [14-web-ui.md](14-web-ui.md),
[../../collection/kis-semiconductor-minute-bars.md](../../collection/kis-semiconductor-minute-bars.md),
[../../collection/us-macro-indicators.md](../../collection/us-macro-indicators.md)

## Global Constraints

- **읽기 전용이다.** 12단계의 `read_only: true` 별칭만 쓰고 재수집·수정·삭제 API를
  만들지 않는다. 수집을 다시 돌리는 손잡이는 Airflow UI이지 이 화면이 아니다.
- **14단계의 층 규칙을 그대로 따른다.** `routes/` → `service/` → `repository/`,
  리소스마다 파일 하나씩 네 폴더에, `__init__.py`는 재수출만.
- **JSON API 시각은 UTC `Z`다.** 시간대 변환은 화면이 한다.
- 화면 상태(심볼·기간·간격·필터)는 전부 URL query string에 둔다.
- `dangerouslySetInnerHTML`을 쓰지 않는다. 문서 본문도 text로 렌더링한다.
- **통합(`UN`) 시세를 만들지 않는다.** 국내 종목은 KRX와 NXT가 따로 체결되므로 화면이
  거래소를 반드시 고르게 한다(2026-08-18 결정. `stock_bar`의 자연키가 거래소를 한 축으로
  갖는 이유와 같다).
- **다운샘플링을 조용히 하지 않는다.** 간격은 요청 파라미터이고, 서버가 임의로 점을
  솎아 내지 않는다.
- 새 테이블·마이그레이션을 만들지 않는다. 이미 쌓인 것을 읽기만 한다.
- 차트 라이브러리는 **하나뿐이다**(uPlot). icon·CSS framework는 여전히 넣지 않는다.

---

- 상위: [README.md](README.md)
- 날짜: 2026-08-27
- 상태: **전 판 구현 완료**(2026-08-27). **저장소의 테이블이 전부 화면까지 닿는다**
  (구현 전 26/49). 2026-08-28에 main의 새 수집을 이어받아 **54개가 됐고 여전히 0개가
  안 닿는다**(7절 Task 7). 마이그레이션이 없어 코드 배포만으로 뜨지만 **프런트가 이미지
  안에 굽히므로 `just build-api`가 먼저다.**
- 산출물: `apps/api/`의 리소스 일곱(`quote`·`indicator`·`document`·`positioning`·
  `event`·`collection`·`causal`)과 라우트 서른하나, `frontend/`의 화면 열과 `ChartView`·
  `DataTable`·`DatasetBrowser`·`Pager`·`Boundary`·`labels`, API 테스트 52·화면 테스트 62,
  14단계 문서의 "chart 안 넣는다" 조항 정정, **Sentry opt-in 전환**(6절)

## 0. 왜 지금인가

**Grafana를 걷어내면서 원자료를 보는 화면이 통째로 사라졌다.** 14단계 7절이 그것을 미리
적어 뒀다 — 대시보드 열여덟 중 시장 추론을 보는 것은 하나도 없었고(국채 곡선 다섯, 시세
여섯, DART 공시·문서 평가·투자자 수급·시장 움직임·포지셔닝), "제거하면 그 화면들은 대체
없이 사라진다"가 그때의 판단이었다. 이 문서가 그 자리를 처음으로 메운다.

**대시보드를 1:1로 이식하지 않는다.** 그 열여덟은 Grafana 패널 문법에 맞춰 자란 것이고,
여기서는 우리 API 계약과 화면 규칙 위에 다시 짠다. 옮기는 것은 **무엇을 보고 싶었나**이지
패널 배치가 아니다.

### 0.1 지금 무엇이 쌓여 있나 (2026-08-27 운영 DB 읽기 전용 실측)

| 도메인 | 테이블 | 행 | 구간 |
| --- | --- | --- | --- |
| 종목 분봉 | `stock_bar` | 533,921 | 2025-08-17 → 오늘 (KRX 190,355 / NXT 337,079 / NYSE 3,688 / NASDAQ 2,807) |
| 지수선물 분봉 | `index_future_bar` | 57,296 | 2026-08-16 → 오늘 |
| 환율 분봉 | `fx_bar` | 56,770 | 2026-08-16 → 오늘 |
| 원자재 분봉 | `commodity_bar` | 50,491 | 2026-08-16 → 오늘 |
| 크립토 분봉 | `crypto_bar` | 37,559 | 2026-08-16 → 오늘 |
| 지수 분봉 | `index_bar` | 14,874 | 2026-08-17 → 오늘 |
| 채권선물 분봉 | `bond_future_bar` | 11,429 | 2026-08-16 → 오늘 |
| 금리 분봉 | `rate_bar` | 97 | 2026-08-17 → 2026-08-26 |
| 일봉 (kind 일곱) | `*_daily` | 64,610 | **2016-08-15 → 어제** |
| 지표 관측값 | `indicator_observation` | 501 | 2026-02-01 → 2026-08-26 (제공처 여섯, 계열 46) |
| 문서 | `document` | 3,099 | 2009-07-20 → 오늘 (출처 23, 그중 enabled 21) |
| 문서 태그 | `document_indicator` / `document_instrument` | 4,814 / 1,654 | 2026-08-18 → 오늘 |
| 종목 수급 일별 | `stock_investor_trade_daily` | 3,784 | 2018-12-10 → 어제 |
| 장중 수급 스냅샷 | `market_investor_flow_snapshot` | 2,718 | 2026-08-18 → 오늘 |
| 시장 움직임 스냅샷 | `market_movement_snapshot` | 1,452 | 2026-08-18 → 오늘 |
| 융자잔고 순위 | `krx_credit_balance_ranking_daily` | 1,400 | 2026-08-18 → 어제 |
| 기술적 신호 | `technical_signal` | 1,025 | 2026-08-24 → 2026-08-26 |
| 애널리스트 의견 | `stock_analyst_opinion` | 262 | 2026-01-02 → 2026-08-10 |
| 증시자금 | `krx_market_funds_daily` | 107 | 2026-03-20 → 2026-08-25 |
| 대차·신용·공매도 | `krx_*_daily` 넷 | 78 | 2026-08-12 → 오늘 |
| 수집 원장 | `source_record` | 20,618 | 2026-08-16 → 오늘 |

**분봉이 압도적이고 일봉이 십 년치다.** 그래서 화면 설계의 첫 제약이 "몇 점을 그리느냐"다.

### 0.2 마스터가 정하는 필터 값

```text
quote_symbol (35)
  index(12)        KOSPI KOSDAQ KOSPI200 SP500 NASDAQ SOX VIX NIKKEI225 HSI SSE_COMP TAIEX RUSSELL2000
  index_future(6)  KOSPI200_FUT KOSDAQ150_FUT SP500_FUT NASDAQ100_FUT DOW_FUT RUSSELL2000_FUT
  fx(5)            USDKRW USDJPY JPYKRW USDCNH DXY
  commodity(4)     GOLD SILVER COPPER WTI
  equity(4)        005930 000660 TSMC_ADR SK_HYNIX_ADR
  crypto(2)        BTC ETH
  rate(1)          US10Y
  bond_future(1)   US10Y_FUT

indicator_series (46)
  government_bond  US(4) JP(6) GB(3) DE(9) KR(4) XM(11) FR·IT·ES(각 1, 월간)
  money_market     KR(1)  CD91D
  price_index      US(2)  CPI_M PPI_M
  activity         US(3)  UNEMPLOYMENT_M RETAIL_SALES_M NONFARM_PAYROLL_M

instrument (2)     005930 삼성전자 · 000660 SK하이닉스 (둘 다 is_watched)
```

### 0.3 차트 — uPlot을 들인다 (사용자 결정 2026-08-27)

**14단계 10절이 "chart·icon·CSS framework"를 만들지 않는 것 목록에 넣었다. 그중 chart만
뒤집는다.** 그때는 화면이 추론 상세와 주별 집계 표라 차트가 없어도 읽혔지만, 여기서는
분봉 53만 행이 대상이라 표로만 두면 **아무도 못 읽는다.** 그 문서의 해당 줄을 정정하고
이 문서를 가리키게 한다(6절).

| 후보 | 판단 |
| --- | --- |
| **uPlot 1.6.32** | 채택. MIT, **의존성 0**, 자체 `uPlot.d.ts`(`@types` 불필요), min IIFE 51KB. 시계열 전용이라 수만 점 줌·팬이 기본이다. |
| 인라인 SVG 직접 | 미채택. 축·틱·툴팁·줌을 우리가 다시 짜면 300~400줄이고, 그 줄들이 곧 우리가 안 만들기로 한 차트 라이브러리다. |
| Chart.js·ECharts | 미채택. 파이·레이더까지 든 범용 패키지라 우리가 쓸 것의 몇 배를 싣는다. ECharts는 min 1MB급이다. |
| Recharts | 미채택. React 컴포넌트 트리로 점을 그려서 수천 점부터 렌더가 무너진다. |

uPlot은 자체 CSS(`uPlot.min.css`)를 함께 싣는다. **그대로 쓰면 라이트 테마다** — import한
뒤 우리 토큰으로 덮는다(4.3절).

`react-uplot` 같은 래퍼를 넣지 않는다. Cytoscape를 감싸지 않은 것과 같은 이유이고,
`GraphView.tsx`가 이미 그 형태의 기준 구현이다.

## 1. 범위와 순서

**첫 판은 시세·지표다**(사용자 결정 2026-08-27). 행이 가장 많고 차트가 붙는 곳이라
여기서 차트 컴포넌트와 다운샘플 계약이 확정되면 나머지는 표에 가깝다.

| 판 | 무엇 | 화면 |
| --- | --- | --- |
| **1** | 시세 봉·일봉, 지표 시계열 | `/quotes` · `/indicators` |
| **2** | 문서·공시·실적 + **LLM 평가 결과 함께**(사용자 결정) | `/documents` |
| **3** | 수급·신용·대차·공매도 아홉 | `/positioning` |
| **4** | 수집 감시(`source_record` 2만 행)와 마스터 | `/collection` |
| **+** | 사건의 기대·실제·판정, 기술적 신호, 투자의견 | `/events` |

**판마다 배포할 수 있게 짰지만 한 번에 다 만들었다**(사용자 요청 2026-08-27,
"나머지 테이블도 다 랜더링"). 마지막 줄의 다섯은 판 넷에 안 들어가 있던 것으로,
행이 적고 전부 종목·날짜로 자르는 같은 질문이라 `/events` 하나로 묶었다.

### 1.1 무엇이 화면까지 닿나 (2026-08-27 실측)

구현 전후를 같은 방법으로 셌다 — "닿는다"의 판정은 추측이 아니라
**`apps/api/repository/`가 그 모델을 import 하는가**다. 조회 API를 거치지 않으면 화면에
나올 길이 없다.

```text
구현 전   26 / 49 테이블   (보이는 행 836,515 · 안 보이는 행 40,345)
구현 후   49 / 49 테이블   (안 보이는 행 0)
```

행 수로는 원래도 95%였다 — 분봉이 압도적이라 그렇고, **도메인으로는 절반이었다.**

## 2. API 설계

### 2.1 경로

```text
GET /api/quotes/symbols
GET /api/quotes/bars?kind=&symbol=&exchange=&interval=&from=&to=
GET /api/quotes/daily?kind=&symbol=&from=&to=
GET /api/indicators/series?kind=&country=
GET /api/indicators/observations?provider=&series_id=&from=&to=
```

2~4판과 사건. **행을 주는 라우트는 전부 `limit`·`offset`을 받는다**(2026-08-27 사용자
요구로 전 라우트에 확장). 아래 목록에서는 그 둘을 생략해 적는다:

```text
GET /api/documents?from=&to=&source=&min_score=&instrument=&indicator=&q=
GET /api/documents/{document_id}
GET /api/documents/sources
GET /api/documents/disclosures?from=&to=&stock_code=
GET /api/documents/earnings?stock_code=

GET /api/positioning/investor-flows?from=&to=&market=      # 장중, 축이 시각
GET /api/positioning/market-movement?from=&to=&symbol=     # 장중, 축이 시각
GET /api/positioning/stock-flows?from=&to=&stock_code=     # 확정, 축이 거래일
GET /api/positioning/estimates?from=&to=&stock_code=
GET /api/positioning/short-sale?from=&to=&stock_code=
GET /api/positioning/lending?from=&to=&stock_code=&market= # 배열 둘
GET /api/positioning/credit?from=&to=&stock_code=          # 축이 trade_date
GET /api/positioning/funds?from=&to=
GET /api/positioning/credit-ranking?standard_date=         # 하루를 고른다

GET /api/events/claims?from=&to=&stock_code=&claim_kind=
GET /api/events/outcomes?from=&to=&stock_code=
GET /api/events/extractions?from=&to=
GET /api/events/signals?from=&to=&symbol=&kind=
GET /api/events/analyst-opinions?from=&to=&stock_code=

GET /api/collection/health?hours=                          # 늦은 출처가 위
GET /api/collection/records?from=&to=&source=&status=
GET /api/collection/instruments                            # 기본 쪽 크기가 상한이다
GET /api/collection/sessions?from=&to=&market=

GET /api/causal/paths?from=&to=&target_kind=&target_code=&event_id=   # 축이 주(week)다
GET /api/causal/events?from=&to=
GET /api/causal/channels?from=&to=
GET /api/causal/paths/{path_id}                                      # 그 사건의 형제 경로까지
```

### 2.1.1 쪽은 예외 없이 붙인다 (2026-08-27 사용자 요구)

**행을 주는 라우트는 전부 쪽으로 낸다.** 처음에는 "행이 여섯뿐인 표에 쪽이 왜 필요하냐"로
`/documents/earnings`·`/documents/sources`·`/collection/instruments`·`/quotes/symbols`·
`/indicators/series`를 통째 응답으로 뒀는데, 그러면 부르는 쪽이 **어느 것이 쪽이고 어느
것이 전부인지 외워야 한다.** 표가 자라는 것은 우리가 정하는 것이 아니라 수집이 정한다.

- 응답 모양은 `Page[T]` 하나다(`items`·`limit`·`offset`·`has_more`). 래퍼를 리소스마다
  손으로 쓰면 어느 하나에서 `has_more`를 빠뜨리고 그 라우트만 조용히 전부를 준다.
- **`total`을 세지 않는다.** `count(*)`는 조회를 두 번 만들고 분봉 53만 행짜리 표에서는
  두 번째가 첫 번째보다 비싸다. `limit + 1`을 읽어 다음 쪽이 있는지만 본다.
- **정렬은 SQL이 한다.** `/collection/health`가 늦은 출처를 위로 올리는데, 쪽을 나눈 뒤
  Python에서 정렬하면 그 쪽 안에서만 늦은 것이 위로 온다.
- **면제는 넷뿐이고 이유가 있다** — `/quotes/bars`·`/quotes/daily`·
  `/indicators/observations`는 행 목록이 아니라 한 대상의 열 묶음이라 상한이 `MAX_POINTS`이고,
  `/indicators/curve`와 `/theses/quality`는 묶음·집계다. 이 목록은
  `tests/api/test_pagination.py`의 `EXEMPT`에 이유와 함께 있고, 그 밖의 라우트가 쪽 없이
  들어오면 그 테스트가 깨진다.
- 화면은 `Pager` 하나를 공유한다. 표 아래 가운데에 이전·번호·다음이고, **번호는 아는
  만큼만 그린다** — 지금 쪽까지는 확실하고 `has_more`면 한 쪽이 더 있다는 것까지다.
  마지막 쪽으로 건너뛰는 버튼은 없다(그리려면 총 건수를 알아야 한다). 깊이 들어가면
  앞을 `…`로 접되 1쪽은 남긴다 — 처음으로 돌아가는 것이 가장 흔한 이동이다.
  쪽은 URL query(`offset`)에 남고, 필터나 데이터셋을 바꾸면 첫 쪽으로 돌아간다.

### 2.1.2 수급은 제공처가 나눠 준 만큼 그대로 낸다 (2026-08-27 사용자 요구)

`stock_investor_trade_daily`는 **기관을 일곱으로 나눠** 준다(금융투자·투자신탁·사모펀드·
은행·보험·종금·기금). 처음에는 그중 둘(투신·연기금)만 응답에 실었는데, 그러면 화면에서
"연기금이 샀나 금융투자가 샀나"를 되물을 수 없다 — **제공처가 나눠 준 것을 우리가 합치면
그 정보는 되돌릴 수 없다.**

- 일곱을 전부 낸다. `institution_net_buy_qty`(기관계)는 **그 일곱의 합**이고 수집기가 그
  일치를 검증한다(2026-08-27 운영 DB 확인: 삼성전자 2026-05-29 합계 5,314,304 = 기관계).
- **기타법인·기타단체는 기관계 밖이다.** 함께 내되 세부와 더하지 않는다. 스키마 주석이
  그 경계를 밝힌다 — 더하면 기관계가 아니게 된다.
- 외국인도 **등록·미등록**으로 갈라 낸다(합이 외국인계와 같다. 같은 날 -1,049,959 +
  -11,782 = -1,061,741).
- 화면은 **표 둘로 나눈다.** 한 표에 스무 칸을 넣으면 가로로 밀려 어느 칸이 어느 주체인지
  읽을 수 없다. 첫 표가 가격과 3주체, 둘째 표가 같은 행의 기관 세부다. 캡션이 "앞의 일곱이
  기관계"라고 밝힌다.

### 2.1.3 공시·실적은 원문으로 이어진다 (2026-08-27 사용자 요구)

접수번호(`rcept_no`)만 보여 주면 사람이 DART에서 그 번호를 다시 찾아야 한다. **그 번호가
곧 주소다** — `https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcept_no}`.

- 주소는 **서비스가 만든다**(`apps/api/service/document.dart_url`). 화면이 문자열을 조립하면
  같은 규칙이 두 곳에 생기고, 제공처가 늘 때 한쪽만 고친다.
- **`provider`가 `dart`일 때만 만든다.** 다른 제공처의 접수번호를 DART 주소에 끼우면 없는
  문서로 가는 링크가 된다. 아니면 `null`이고 화면은 번호를 글자 그대로 찍는다.
- 컬럼을 더하지 않는다. `rcept_no`가 이미 자연키라 주소는 그것의 표현이다.
- **오늘 두 표는 전부 `dart`다**(2026-08-27 실측: 공시 46건, 실적 6건). 즉 두 탭의 행은
  빠짐없이 링크를 얻고, `provider` 가드가 막는 행은 하나도 없다. 그 가드는 지금 필요해서가
  아니라 제공처가 늘었을 때 조용히 깨진 링크가 생기지 않게 두는 것이다.
- 주소 틀이 저장소에 둘이다(브리핑의 `modules.thesis.domain.DART_VIEWER_URL`과 조회 API).
  두 트리가 서로를 import하지 않으므로 **중복을 허용하고 테스트로 대조한다**
  (`tests/api/test_dart_links.py`) — 수집기 상수를 대조하는 규칙과 같은 자리다.

**정적 경로가 동적 id보다 먼저다.** `/api/documents/sources`가 `/{document_id}`에 먹히면
422가 된다 — `quality`와 `symbols` 때와 같은 함정이고, 라우트 파일 안의 함수 정의 순서가
그 계약이다.

`/api/quotes`와 `/api/indicators`는 `/api/theses`·`/api/llm-runs`와 형제다. 정적 경로가
동적 id보다 먼저 등록되는 규칙(14단계 `quality` 사례)은 여기서도 같다 —
`routes/__init__.py`의 `routers` 튜플 순서가 계약이다.

### 2.2 어느 테이블을 읽나 — **뷰를 무조건 쓰지 않는다**

`quote_bar`·`quote_daily` 뷰가 kind별 물리 테이블을 UNION ALL 한다. 조회는 뷰를 써도
된다는 것이 프로젝트 규칙이지만 **종목만은 예외다.**

```sql
-- quote_bar 뷰의 종목 갈래: KRX·NYSE·NASDAQ만 태운다(뷰 주석). NXT가 통째로 빠진다.
```

NXT는 337,079행으로 **KRX보다 많다.** 뷰로 읽으면 그 절반이 조용히 사라지므로,
종목 봉은 `stock_bar`를 직접 읽고 `exchange`를 필수 파라미터로 받는다. 매크로 kind
(지수·선물·환율·원자재·크립토·금리·채권선물)는 뷰를 써도 되지만, **kind가 곧 물리
테이블이라 뷰를 거칠 이유가 없다** — kind → 테이블 매핑을 리포지토리가 갖는다.

`daily_series` 뷰(일봉 종가 + 지표 관측값 + 종목 종가를 한 축으로 편 것)는 **여러 계열을
겹쳐 그리는 화면**에만 쓴다. 단일 계열 조회는 원 테이블이 더 좁다. 1판은 그 화면이 없어
아직 안 쓴다.

**국내 종목 일봉은 `stock_daily`에 없다**(구현 중에 드러났다). `stock_investor_trade_daily`가
시가·고가·저가·종가를 수급과 함께 갖고 있어 다시 받지 않기로 한 결정이 있고
(`StockDaily` docstring), 컬럼 이름도 `open_price`처럼 다르다. `stock_daily`는 해외 상장
종목(TSMC ADR·SK하이닉스 ADR)용이다. 리포지토리가 `exchange == "KRX"`로 그 갈래를 가르고
부르는 쪽은 몰라도 된다 — 실측으로 005930의 일봉 1,892행이 전부 수급 테이블에 있다.

### 2.3 몇 점을 줄 것인가 — **`interval`은 요청 파라미터다**

분봉 하루가 종목 하나에 약 390행, 한 달이면 8,000행이다. 1년을 그대로 내면 10만 행이
JSON으로 나간다.

- **`interval`은 `1m`·`5m`·`15m`·`1h` 중 하나이고 기본은 `1m`이다.** 서버가
  `date_bin`으로 OHLCV를 재집계한다(open=첫 값, high=max, low=min, close=마지막 값,
  volume=합). Postgres에 first/last 집계가 없어 `array_agg(... ORDER BY ...)`의 양 끝을 쓴다.
  - **`1d`는 여기 없다**(구현 중 확정). 분봉의 축은 UTC 시각이고 일봉의 축은 그 시장의
    거래일이라, UTC 하루로 접으면 미국 지수의 하루가 이틀에 걸쳐 쪼개진다. 일봉은
    `/api/quotes/daily`가 확정 테이블에서 준다.
  - **`date_bin`의 원점도 timezone-aware여야 한다.** 기본 `DateTime`은
    `TIMESTAMP WITHOUT TIME ZONE`으로 나가고 `bar_at`이 aware라 Postgres가 둘을 못 뺀다.
    운영 DB에 한 번 돌려 보고서야 잡혔다(2026-08-27) — 가짜 연결 테스트는 통과했다.
- **점 상한은 5,000이고 넘으면 400이다.** 조용히 솎지 않는다 — 값이 거짓이 되는 것보다
  "구간을 좁히거나 간격을 넓혀라"가 낫다. 응답의 `truncated` 같은 칸을 두지 않는 것도
  같은 이유다(수집기 규칙의 "잘린 응답을 실패로 만든다"와 같은 판단).
  - **판정은 `limit + 1`을 읽어서 한다**(구현 중 확정). 구간과 간격으로 점 수를 미리
    계산하면 **거래가 없던 밤까지 세어**, 한 달치 1분봉이 실제로는 8천 점인데 4만 3천
    점으로 거절된다. 화면 쪽 사전 차단은 최악을 세도 되지만 서버는 실제를 세야 한다.
- 오류 본문은 무엇을 고쳐야 하는지 말한다: `요청 구간이 12,480점이다. 상한은 5,000이다.
  interval을 5m 이상으로 올리거나 구간을 좁혀라.`
- 화면은 구간을 고를 때 예상 점 수를 미리 계산해 상한을 넘는 조합을 **고르지 못하게**
  한다. 400을 화면에서 보는 일이 정상 흐름이 되면 안 된다.

`from`·`to`는 **UTC 시각**이다(분봉은 KST 날짜 경계가 뜻이 없다 — 미국 지수는 밤에
움직인다). 일봉과 지표는 `business_date`·`observation_date`라 **KST 날짜**다.
같은 이름의 파라미터가 리소스마다 축이 다르므로 각 라우트의 docstring이 그것을 밝힌다.

### 2.4 응답 모양 — **컬럼 지향이다**

```json
{
  "kind": "index",
  "symbol": "KOSPI",
  "exchange": null,
  "interval": "5m",
  "provider": "kis",
  "points": 780,
  "times": ["2026-08-27T00:00:00Z", "..."],
  "open":  [3210.5, "..."],
  "high":  [3214.0, "..."],
  "low":   [3208.1, "..."],
  "close": [3212.7, "..."],
  "volume": [128400, "..."]
}
```

**행 지향(`[{time, open, ...}, ...]`)이 아닌 이유는 둘이다.** ① 5,000점이면 키 이름이
3만 번 반복돼 payload가 두 배가 된다. ② uPlot이 먹는 모양이 바로 이것이라 프런트가
전치할 일이 없다. 계약은 여전히 Pydantic 모델이고 필드마다 `description`을 단다 —
`list[float]` 여섯 칸도 모델이다.

**`times`는 ISO 8601 `Z` 문자열을 유지한다.** epoch 정수가 작지만 "일반 API 응답은
변환하지 않은 UTC ISO 8601과 `Z`"가 프로젝트 규칙이고, 5,000개 파싱은 브라우저에서
수 ms다. 규칙을 어길 값어치가 없다.

**`null`을 0으로 채우지 않는다.** 거래가 없던 분은 행 자체가 없고, 화면이 선을 잇지 않고
끊는다. 0으로 채우면 차트가 바닥으로 떨어지는 거짓 급락을 그린다.

### 2.5 심볼·계열 목록

`GET /api/quotes/symbols`는 `quote_symbol` 마스터에 **실제 데이터의 구간**을 붙여 준다.

```json
{"items": [
  {"kind": "index", "symbol": "KOSPI", "label": "코스피", "country": "KR",
   "bar_from": "2026-08-17T00:30:15Z", "bar_to": "2026-08-27T05:15:00Z",
   "daily_from": "2016-08-15", "daily_to": "2026-08-26", "bar_rows": 3120}
]}
```

**구간을 함께 주는 이유는 화면이 빈 기간을 고르지 못하게 하기 위해서다.** 마스터에만
있고 데이터가 0건인 심볼(`rate` 97행처럼 거의 안 쌓인 것)이 실제로 있다.

구현은 kind별 테이블 여덟 + 종목을 `UNION ALL`로 묶어 **한 왕복**에 센다. 뷰를 쓰지 않는
이유는 위와 같다 — 종목의 NXT가 빠진다.

`GET /api/indicators/series`도 같은 형태다. **`kind`를 반드시 걸 수 있어야 한다** —
국채 곡선 패널에 CD 91일이나 CPI가 섞이면 한 축에 단위가 다른 값이 올라간다
(프로젝트 규칙: "조회하는 쪽은 `kind`를 반드시 건다").

## 3. 화면

| 경로 | 화면 |
| --- | --- |
| `/quotes` | 심볼 목록. kind·국가 필터, 각 행에 수집 구간과 행 수 |
| `/quotes/:kind/:symbol` | 봉 차트 + 값 표. 간격·기간·거래소 선택 |
| `/indicators` | 지표 계열 목록. kind·country 필터 |
| `/indicators/:provider/:seriesId` | 관측값 라인 + 표 |
| `/indicators/curve` | **국가별 국채 곡선.** 만기를 x축에, 나라를 계열로 |
| `/collection` | (4판) 출처별 최신성과 실패 |
| `/causal` | 주간 사후 인과 그래프. 경로·사건·채널 셋을 표로 (2026-08-28) |
| `/causal/:pathId` | 그 사건의 경로 전부를 한 그래프로. 보고 있는 경로만 방향 색 (2026-08-28) |
| `/dashboard` | 첫 화면. 수집·실행·추론·인과·문서의 첫 줄만 모아 어디를 볼지 정한다 (2026-08-31) |

`/quotes/:kind/:symbol` 하나가 이 판의 본문이다.

- 위: 심볼 이름, 제공처, 거래소, **마지막 값과 그 시각**.
- 가운데: 차트. 종가 라인 + 거래량 막대. 간격 토글(`1m`·`5m`·`15m`·`1h`·`1d`), 기간
  프리셋(오늘·5일·1개월·3개월·1년·전체).
- 아래: 같은 데이터의 표. **차트만으로는 스크린리더가 값을 못 읽는다** — 관계 그래프에
  접근 가능한 목록을 함께 둔 것과 같은 규칙이다. 표는 최근 200행만 보이고 나머지는
  구간을 좁혀 읽는다.

**국내 종목은 거래소 선택이 필수다.** 기본값을 KRX로 두되 NXT 토글을 눈에 띄게 두고,
둘을 합친 선을 만들지 않는다. 같은 종목의 두 거래소를 **두 계열로 겹쳐** 볼 수는 있다 —
합치는 것과 겹치는 것은 다르다.

**차트 제목은 `대상 값 · 시장 · 날짜`다**(프로젝트 표기 규칙). 화면에는 시간대 토글이
없고 전부 KST로 그리되, 축 라벨 옆에 `KST`를 적는다.

## 4. 프런트 구조

```text
frontend/src/
  chart.ts                    # 응답 → uPlot 입력. 순수 함수
  components/ChartView.tsx    # uPlot lifecycle만
  components/Pager.tsx        # 쪽 이동. 아는 만큼만 번호를 그린다
  components/Boundary.tsx     # 렌더 예외를 화면에 남긴다(흰 화면 금지)
  pages/QuotesPage.tsx
  pages/QuoteDetailPage.tsx
  pages/IndicatorsPage.tsx
  pages/IndicatorDetailPage.tsx
  pages/CurvePage.tsx
```

**`useJson`은 답을 그 답이 온 경로와 함께 들고 있는다.** 경로만 바꾸고 데이터를 그대로 두면
새 응답이 오기 전 한 렌더 동안 **새 화면이 옛 응답을 읽는다.** 지우는 일을 effect에 맡기면
그 한 렌더를 못 막는다 — effect는 그린 뒤에 돌기 때문이다. 2026-08-27에 대차거래(배열 둘)
에서 공매도(`items` 하나)로 옮기다 흰 화면이 됐고, 그 자리를 hook 하나에서 막았다.

`graph.ts`/`GraphView.tsx`의 짝이다 — **모양을 바꾸는 것은 순수 함수, 라이브러리 수명은
컴포넌트**다. 상단 내비게이션은 셋(실행·판단·품질)에서 다섯(+시세·지표)이 된다.

### 4.1 `ChartView`

`GraphView`와 같은 계약이다.

- container가 생겼을 때 instance 하나를 만들고, 데이터가 바뀌면 `setData()`로 갱신한다.
  **새로 만들지 않는다.**
- unmount에서 `destroy()`한다.
- 크기는 `ResizeObserver`로 따라간다(uPlot은 스스로 반응하지 않는다).
- 커서 위치는 uPlot 내부 상태에만 두지 않고 선택된 시각을 React state로 올려, 아래 표가
  같은 행을 강조한다.

### 4.2 x축은 시각이 아니라 **순번**이다 (2026-08-27 사용자 지적)

**시장은 하루 종일 열리지 않는다.** 코스피는 09:00~15:30이고 나머지 17시간 반은 봉이
아예 없다. 주말이면 63시간이 빈다. x축을 실제 시각으로 두면 그 빈 시간이 차트 폭의
대부분을 먹고, 거래가 실제로 일어난 구간이 좌우로 짓눌린다.

실측(2026-08-24~27 코스피 5분봉 312점, 조회 API로 잰 값):

```text
가장 큰 공백        17.5시간  (08-24 15:30 → 08-25 09:00 KST)
시각 축 전체 폭     78.2시간
봉이 실제 있는 시간 26.0시간
→ 시각 축이면 차트의 67%가 빈 칸이다
```

그래서 x는 **0, 1, 2… 순번**이고 각 순번의 표시 이름을 `labels`가 따로 갖는다
(`chart.ts`의 `ordinal`·`labelAt`). 봉이 연달아 붙어 마감과 개장 사이가 사라진다.
금융 차트가 보통 이렇게 그린다.

**대신 잃는 것을 적어 둔다:** 순번 축에서는 한 시간의 공백과 사흘의 공백이 똑같이 한
칸이라 눈으로 구분되지 않는다. 그 대가로 축 라벨이 날짜를 함께 적는다(`08/27 09:05`) —
라벨이 `08/28`에서 `08/31`로 뛰면 그 사이가 주말이다.

**곡선(`/indicators/curve`)만 순번이 아니다.** 거기서는 만기 간격 자체가 뜻을 가져서,
2년과 10년 사이가 10년과 30년 사이보다 좁아야 기울기가 사실대로 보인다.

이것은 "빈 값을 0으로 채우지 않는다"와 다른 이야기다. 없는 값을 지어내는 것이 아니라
**없는 시간을 그리지 않는 것**이고, 값의 배열은 그대로다.

### 4.3 다크 스타일

`import "uplot/dist/uPlot.min.css"`를 하고 그 위를 우리 토큰으로 덮는다. uPlot의 기본
축·격자·툴팁이 라이트 팔레트다. `styles.css`에 `.uplot` 스코프 블록 하나를 두고, 계열
색은 `ChartView`가 상수로 갖는다(`GraphView`가 Cytoscape 색을 상수로 가진 것과 같은
이유 — JS 객체라 `var(--ink)`가 안 통한다).

## 5. 성능

- **분봉 조회에 인덱스가 있는지 먼저 확인한다.** `stock_bar`는 자연키가
  `(provider, stock_code, exchange, bar_at)`이라 그 순서로 거는 조회는 이미 인덱스를 탄다.
  매크로 kind는 `(provider, symbol, bar_at)`이다. **`bar_at` 단독 범위 조회는 인덱스를
  못 타므로 화면이 심볼 없이 조회하지 못하게 한다.**
- `date_bin` 재집계는 DB에서 한다. 5,000행을 파이썬으로 접으면 세션을 그만큼 오래 쥔다.
- **응답 캐시를 만들지 않는다.** 분봉은 매분 바뀌고, 사용자가 한 자릿수다.
- 새 SQL은 **운영 DB에 읽기 전용으로 한 번 돌려 보고 넣는다**(프로젝트 규칙). 테스트는
  가짜 연결을 쓰므로 컬럼 이름과 조인 조건이 틀려도 통과한다.

## 6. 함께 고칠 문서

| 대상 | 무엇 |
| --- | --- |
| `apps/api/main.py`·`compose/prod/api` | **Sentry를 opt-in으로 바꿨다**(아래) |
| [14-web-ui.md](14-web-ui.md) 10절 | "chart·icon·CSS framework" 중 **chart를 뺀다.** 왜 뒤집혔는지와 이 문서 링크를 남긴다 |
| [12-api.md](12-api.md) 1절 | 엔드포인트 목록에 이 판의 다섯을 더한다 |
| [README.md](README.md) | 15단계 행과 상태 |
| 루트 `README.md` | `## 웹 화면` 절에 시세·지표 화면 |
| [../../collection-map.html](../../collection-map.html) | "최신성은 Airflow와 web UI에서 확인한다" → 4판에서 실제 경로를 적는다 |
| `.claude/CLAUDE.md`·`.codex/AGENTS.md` | 프런트 의존성이 넷에서 다섯이 된다 |

### 6.1 Sentry는 opt-in이다 (2026-08-27에 잡은 사고)

**개발 머신의 `config.yaml`은 운영 것의 사본이다.** 워크트리에 복사해 쓰는 것이 이
저장소의 관례라(`read_only` 별칭을 그대로 보려고) DSN과 `sentry_environment: production`이
함께 딸려 온다. 그 상태로 로컬에서 진입점을 돌리면 **개발 트래픽이 운영 Sentry 프로젝트에
production으로 찍힌다** — 에러뿐 아니라 트레이스(`traces_sample_rate: 0.1`)와 INFO 로그
(`enable_logs=True`)까지, `send_default_pii=True`로. 이 판을 만들며 로컬 API를 여러 번
띄우는 동안 실제로 그러고 있었다.

그래서 **판단을 설정 파일이 아니라 실행 환경에 둔다.**

- `apps/api/main.py`는 `SENTRY_ENABLED=1`일 때만 `sentry_sdk.init`을 부른다. DSN이 있어도
  그렇다 — 설정은 "어디로 보낼 수 있나"이고 이 변수는 "지금 보낼 것인가"다.
- 켜는 자리는 `compose/prod/api/docker-compose.yaml` 하나다.
- **안전한 쪽이 기본이다.** 반대로 두면 잊은 사람이 조용히 운영 데이터를 더럽힌다.
- 대신 운영에서 이 값이 빠지면 관측이 조용히 꺼지므로, 시작 로그가 어느 쪽인지 밝히고
  (`sentry is off — set SENTRY_ENABLED=1 to report`) `tests/config/test_api_stack.py`가
  운영 compose에 그 값이 있는지 검사한다.

**`apps/realtime/`은 아직 안 고쳤다.** 같은 덫이 그대로 있고, 고치면 그쪽 운영 compose도
함께 손봐야 해서 별도 판단으로 남긴다.

## 7. 구현 작업

### Task 1: 시세 조회 API

**Files:**

- Create: `apps/api/schemas/quote.py`, `apps/api/repository/quote.py`,
  `apps/api/service/quote.py`, `apps/api/routes/quote.py`
- Modify: 위 넷의 `__init__.py`, `apps/api/container.py`
- Test: `tests/api/test_quotes.py`

- [x] kind → 물리 테이블 매핑과 `interval` 재집계 조회문의 실패 테스트를 쓴다.
- [x] **종목은 `stock_bar` 직접 조회이고 `exchange`가 필수**임을 테스트로 못 박는다.
- [x] 점 상한 초과가 400이고 메시지가 고칠 방법을 말하는지 검사한다.
- [x] 심볼 목록이 마스터에 실제 수집 구간을 붙이는지 검사한다.
- [x] `uv run pytest tests/api -q`를 통과시킨다.
- [x] 새 SQL 다섯을 운영 DB에 읽기 전용으로 한 번 돌려 본다.

### Task 2: 지표 조회 API

**Files:**

- Create: `apps/api/schemas/indicator.py`, `apps/api/repository/indicator.py`,
  `apps/api/service/indicator.py`, `apps/api/routes/indicator.py`
- Test: `tests/api/test_indicators.py`

- [x] 계열 목록이 `kind`·`country`로 걸리는지, **`kind`를 안 걸면 단위가 섞인다**는 것을
  주석과 테스트로 남긴다.
- [x] 관측값 조회가 `(provider, series_id)` 둘을 함께 거는지 검사한다 — `series_id`
  단독은 제공처가 늘면 조용히 틀린다.
- [x] 국채 곡선 조회가 **두 나라 이상이 가진 만기로 좁히는지** 검사한다.
- [x] `uv run pytest tests/api -q`를 통과시킨다.

### Task 3: uPlot과 `ChartView`

**Files:**

- Modify: `frontend/package.json`, `frontend/src/styles.css`
- Create: `frontend/src/chart.ts`, `frontend/src/components/ChartView.tsx`
- Test: `frontend/src/chart.test.ts`, `frontend/src/components/ChartView.test.tsx`

- [x] 응답을 uPlot 입력으로 바꾸는 순수 함수 테스트를 먼저 쓴다(빈 구간이 `null`로
  남고 0이 되지 않는 것 포함).
- [x] instance를 하나만 만들고 unmount에서 `destroy()`하는지 검사한다(`GraphView`와
  같은 계약, jsdom이라 uPlot을 mock한다).
- [x] `ResizeObserver` 정리를 검사한다.
- [x] 다크 오버라이드를 넣고 `npm --prefix frontend test -- --run`을 통과시킨다.

### Task 4: 시세 화면

**Files:**

- Create: `frontend/src/pages/QuotesPage.tsx`, `frontend/src/pages/QuoteDetailPage.tsx`
- Modify: `frontend/src/App.tsx`, `frontend/src/types.ts`
- Test: `frontend/src/pages/quotes.test.tsx`

- [x] URL query round-trip과 거래소 선택 테스트를 쓴다.
- [x] **KRX와 NXT를 합친 계열을 만들지 않는다**는 테스트를 쓴다.
- [x] 상한을 넘는 조합이 고를 수 없게 막히는지 검사한다.
- [x] 차트와 같은 데이터의 접근 가능한 표가 함께 있는지 검사한다.

### Task 5: 지표 화면과 곡선

**Files:**

- Create: `frontend/src/pages/IndicatorsPage.tsx`,
  `frontend/src/pages/IndicatorDetailPage.tsx`, `frontend/src/pages/CurvePage.tsx`
- Test: `frontend/src/pages/indicators.test.tsx`

- [x] `kind`가 다른 계열이 한 축에 섞이지 않는다는 테스트를 쓴다.
- [x] 곡선 화면이 만기 순으로 정렬하고 `maturity_months`가 `NULL`인 계열을 빼는지
  검사한다.
- [x] `npm --prefix frontend test -- --run`을 통과시킨다.

### Task 6: 문서를 맞추고 전체를 검증한다

- [x] 6절 표의 문서를 고친다. **14단계 10절의 chart 조항 정정이 그중 핵심이다.**
- [x] 2판 이후(문서·수급·수집 감시)는 이 문서에 설계로 남기고 구현하지 않는다.
- [x] `npm --prefix frontend test -- --run`, `npm --prefix frontend run build`
- [x] `uv run ruff check apps tests`, `uv run pytest tests -q`
- [x] 브라우저에서 KOSPI 분봉·일봉, 삼성전자 KRX·NXT, 미국 국채 곡선을 확인한다.

## 8. 테스트 계약

- API route 집합과 client route 집합을 각각 리터럴로 대조한다.
- kind → 테이블 매핑이 `quote_symbol.kind`의 값 전부를 덮는지 검사한다. 마스터에 kind가
  늘면 이 테스트가 먼저 깨져야 한다.
- **종목 조회가 `quote_bar` 뷰를 타지 않는지** 조회문 문자열로 검사한다. 뷰를 타면 NXT
  337,079행이 조용히 빠진다.
- `interval` 재집계가 open=첫 값, close=마지막 값, volume=합인지 경계값으로 검사한다.
- 점 상한 초과가 200이 아니라 400인지, 메시지가 `interval`과 구간을 언급하는지 검사한다.
- 빈 분이 `null`로 남고 0이 되지 않는지 검사한다.
- `times`가 `Z`로 끝나고 `+00:00`이 아닌지 검사한다(14단계와 같은 규칙).
- `ChartView`는 instance를 하나만 만들고 unmount에서 `destroy()`한다.
- 지표 조회가 `provider`와 `series_id`를 함께 거는지 조회문으로 검사한다.

## 8.1 2~4판이 더한 테스트 계약

- 정적 경로(`/documents/sources`·`/disclosures`·`/earnings`)가 `/{document_id}`보다 먼저
  먹는지 **실제 요청으로** 검사한다.
- 문서 목록 응답에 `body`·`assessment`가 없고 상세에만 있는지 검사한다.
- `value_score`가 `null`인 문서가 0으로 바뀌지 않는지 검사한다.
- 태그 필터가 조인이 아니라 `IN` 서브쿼리인지 조회문으로 검사한다 — 조인하면 문서가
  태그 수만큼 복제된다.
- 대차거래 응답이 시장과 종목을 **배열 둘**로 내는지 검사한다.
- 융자 순위가 하루를 고르고 고를 수 있는 날짜를 함께 내는지 검사한다.
- 장중 스냅샷의 창이 KST 날짜에서 UTC 시각으로 바뀌는지 경계값으로 검사한다.
- `verdict`가 `null`인 판정이 화면에서 "보류"로 보이는지 검사한다.
- 추출 원장의 `claim_count = 0` 행이 살아 있는지 검사한다.
- 수집 레코드 조회문이 `payload`를 **SELECT 하지 않고** 존재 여부만 내는지 검사한다.
- 수집 요약이 `succeeded`·`failed`·`running`·`quarantined`를 따로 세는지 조회문으로 검사한다.
- `sentry_enabled`가 `"1"`에만 참인지 검사한다(`"true"`도 거짓이다).

### 8.2 쪽과 원문 링크가 더한 계약 (2026-08-27)

- **행을 주는 모든 라우트가 `limit`·`offset`을 받는지** 라우터를 훑어 검사한다
  (`tests/api/test_pagination.py`). 면제는 그 파일의 `EXEMPT`에 이유와 함께 적고, 목록에
  없는 경로가 남아 있으면 그것도 실패다 — 사라진 라우트의 면제가 남지 않게 한다.
- 수집 요약의 정렬이 **조회문에** 있는지 컴파일된 SQL로 검사한다. 쪽을 나눈 뒤 Python에서
  정렬하면 두 번째 쪽의 첫 행이 첫 쪽의 마지막보다 이를 수 있다.
- 화면: 쪽이 하나뿐이면 `Pager`를 아예 그리지 않고, 다음을 누르면 `offset`이 요청까지
  가며, 필터·탭·데이터셋을 바꾸면 첫 쪽으로 돌아가는지 검사한다.
- 화면: **필터 인자를 안 받는 데이터셋도** 쪽이 요청까지 가는지 검사한다. 쪽을 데이터셋의
  `path`에 맡기면 인자를 안 쓰는 데이터셋 하나가 조용히 첫 쪽에 갇힌다 — 개장 캘린더가
  실제로 그랬다(2026-08-28 사용자 보고). `withOffset`이 화면 쪽에서 붙인다.
- 화면: **경로가 바뀌면 옛 응답이 새 화면에 절대 안 닿는지** 검사한다(`useJson`이 답을 그
  답이 온 경로와 함께 들고 있다). 대차거래(배열 둘)에서 공매도(`items` 하나)로 옮길 때
  `rows.length`에서 죽던 것이 이 계약의 출처다(2026-08-27 사용자 보고). 지우는 일을
  effect에 맡기면 못 막는다 — effect는 그린 뒤에 돈다.
- 화면: 문서 화면이 **탭이 아니라 응답 모양으로** 무엇을 그릴지 정하는지 검사한다. 위
  계약이 이미 막지만, 탭 넷이 한 경로 접두를 나눠 쓰는 화면이라 겹벨트로 남긴다.
- 화면: 렌더 예외가 **흰 화면이 되지 않는지** 검사한다(`Boundary`). React는 예외를 만나면
  그 트리를 통째로 언마운트해서, 콘솔을 안 열면 "안 그려진다"로만 보인다.
- 공시·실적의 접수번호가 DART 원문 링크로 나가고 `rel`에 `noopener`가 붙는지 검사한다.
- 인과 상세가 **형제 경로를 함께** 내는지, 없는 id가 404인지, 정적 `/paths`가 `/{path_id}`를
  이기는지 검사한다. 그래프 쪽은 채널이 이름으로 공유되는지, 같은 코드라도 대상 종류가
  다르면 노드가 갈리는지, **지금 보는 경로에만** 방향 색이 붙는지를 순수 함수로 본다.
- 그래프: 쓰기 문장이 **예외로** 거절되는지, 우리 상수 쿼리가 그 검사를 통과하는지, 주 조회가
  `date`를 넘기는지(문자열이면 조용히 0건이다), 그래프 DB가 없을 때 빈 그래프가 아니라
  **503**인지 검사한다. 화면 쪽은 503을 폴백으로 읽는지와, 투영이 오면 주를 넘는 엣지까지
  그리는지를 본다.
- 인과: 대상에서 출발한 경로가 목록에서 **안 빠지는지**, 그 경로의 원인이 다른 경로의 대상
  노드에 이어 붙어 다중 홉이 되는지, 상세가 사건이 아니라 **주 전체**를 담는지 검사한다.
- 인과 상세가 `<kind>:<id>` 근거를 풀어 문서 제목·링크로 내는지, 모르는 종류에 링크를 안
  만드는지, 형제의 근거가 이 경로 밑에 안 섞이는지 검사한다.
- **저장 값의 한국어 이름은 `labels.ts` 한 곳이다.** 모르는 값은 감추지 않고 그대로 보인다 —
  빈 칸은 "값이 없다"로 읽혀 거짓이 된다.
- 종목 수급 응답이 **기관 세부 일곱을 전부** 싣고, 기관계가 그 합이며, 기타법인·기타단체가
  그 밖으로 남는지 검사한다. 화면은 그것을 표 둘로 나누는지 본다.

## 8.6 대시보드와 컬럼 격차 메우기 (2026-08-31)

"main과 지금 API가 무엇이 다른가"를 **기계로 재고** 그 격차를 메웠다. 재는 방법 둘:
`apps/api/repository/`가 그 모델을 import하는가(표 단위), 컬럼 이름이 `apps/api/` 어딘가에
나오는가(칸 단위). 나오지 않으면 그 값은 화면까지 갈 길이 없다.

- **표 하나가 통째로 안 닿고 있었다** — `document_attachment`(1,166행). 문서 상세에 첨부
  목록을 붙였다. **본문이 첨부에만 있는 출처가 있다**(한국은행·금감원·BOJ·네이버 리서치)
  — 그때 `body_status`가 `attachment_only`이고 그 문서에서 읽을 것은 그 목록이 전부다.
  **저장 경로는 내지 않는다**(마운트 안의 상대경로라 화면이 열 수 있는 주소가 아니다).
- **칸 44개가 안 닿고 있었다.** 34개를 채웠고 10개는 안 내는 것이 맞다고 판정했다.
  - `stock_bar.is_final` → 분봉의 `확정` 열. 국내 종목 분봉은 WebSocket 잠정 봉이 먼저 들어오고
    REST가 나중에 덮는다. **재집계 버킷은 `bool_and`다** — 하나라도 잠정이면 고가·저가가 아직
    바뀔 수 있다. 실측으로 최근 5봉 중 4개가 잠정이었다.
  - 장중 수급의 매도·매수 여섯 칸 → **순매수 0이 "안 샀다"인지 "많이 사고 팔았다"인지** 가른다.
  - 신용잔고 신규·상환·공여율 열 칸, 공매도 누적 다섯 칸, 증시자금 넷, 대차 둘, 투자의견 코드
    둘, 캘린더 검증 셋, 수집 레코드의 메타 유무.
  - **안 내는 열**: 조회 파라미터(`universe_code`·`sort_code`·`period_days`), 내부 자연키
    조각(`chain_key`), 외래키(`verification_source_record_id`), 그리고 재집계로 뜻을 잃는
    `ingest_method`(버킷 안에서 섞인다 — 대신 `is_final` 집계를 낸다).
- **운영에 대고 나서야 잡힌 것 둘.**
  - `document.assessment`가 문자열이 아니라 **jsonb**다. 계약이 `str`이라 문서 상세가 통째로
    500이었다. 판이 바뀌며 칸이 늘어나는 값이라 모양을 고정하지 않고 원문 그대로 보인다.
  - `settled` 매핑이 **일봉 쪽으로 잘못 들어갔다.** Pydantic이 모르는 칸을 조용히 버려서
    응답도 테스트도 아무 말을 안 했다 — 그래서 그 자리를 검사하는 테스트를 따로 뒀다.

### 8.6.2 문서 목록이 느렸던 이유 (2026-08-31 사용자 보고)

화면에서 문서 탭만 눈에 띄게 느렸다. 재 보니 **문서 목록 1.13초, 나머지 라우트 전부
0.05~0.10초**였다.

원인은 조회 계획이 아니라 **옮기는 양**이다. `list_statement`가 `select(Document)`라
`body`(9.6MB)와 `assessment` jsonb(2.7MB)까지 TOAST에서 끌어와 DB가 API로 보내고, 서비스는
목록 계약에 그 둘을 안 실어서 **버린다.**

| 무엇 | 시간 | DB가 보낸 양 |
| --- | --- | --- |
| `select *`(고치기 전) | 0.648s | 약 6.8MB |
| 목록이 쓰는 칸만 | 0.011s | 59.6KB |

계획은 둘 다 `Execution Time: 0.575ms`다 — 인덱스도 정렬도 문제가 아니었다.
`document` 표 30MB 중 **TOAST가 22MB**이고, **문서 본문 수집이 붙기 전에는 그 칸이 비어
있어 이 값이 안 보였다**(2026-08-30 main 병합).

고침은 `load_only`로 목록이 쓰는 칸만 읽는 것이다. ORM 인스턴스가 그대로라 매핑은 안 바뀌고,
빠뜨린 칸을 나중에 건드리면 세션이 닫힌 뒤라 조용히 지나가지 않는다. **화면에서 1.13초가
0.073초가 됐다.** 회귀는 컴파일된 SQL에 그 칸이 없는지로 막는다.

**응답에서 빼는 것과 읽지 않는 것은 다르다.** 나머지 라우트도 같은 눈으로 봤는데, 큰 값을
가진 표는 `source_record.payload`(이미 칸을 골라 읽는다)뿐이었다.

### 8.6.1 첫 화면이 대시보드다

`/`가 `/runs`로 보내고 있었다. 실행 목록은 "무슨 일이 있었나"의 한 갈래일 뿐이라 수집·추론·
인과가 안 보였다. `/dashboard`가 그 자리를 받는다.

- **대시보드 전용 집계 라우트를 만들지 않는다.** 이미 있는 조회 다섯(수집 요약·실행·추론·
  인과 경로·문서)을 나란히 부르고 각각의 첫 줄만 읽는다. 전용 라우트를 만들면 그 라우트만
  아는 규칙이 생기고 화면이 바뀔 때마다 서버를 고쳐야 한다.
- **카드 제목이 그 화면으로 가는 링크다.** 여기서 답이 끝나는 것이 아니라 어디를 볼지 정하는
  자리다.
- **없는 것을 0으로 채우지 않는다.** "최근 24시간에 수집 기록이 없다"와 "실패 0건"은 다른
  말이고, `running`은 "지금 도는 중"과 "끊긴 것"을 이 기록만으로 가를 수 없어 세기만 한다.

## 8.5 그래프를 Neo4j에서 읽는다 (2026-08-30)

4단계가 운영에 서면서 **인과 그래프의 투영이 생겼다**([4-graph.md](4-graph.md)). 그림을
Postgres 경로 목록에서 조립하던 것을 그 투영에서 읽는 것으로 바꾼다.

- **왜 바꾸나.** 경로 목록은 **한 주에 갇힌다** — 응답이 그 주의 행이라, 주를 넘어 같은
  `Target`을 공유하는 사슬이 안 보인다. 실측으로 `SOX`에서 출발한 사슬이 08-10과 08-17 두
  주에 걸쳐 엣지 35개다. 그것이 그래프 DB를 들인 이유고(설계 §1) 화면이 그것을 보여야 한다.
- **Postgres가 원본이다.** 투영에는 실현 등락·근거·`input_hash`가 없다. 그래서 **그림만**
  Neo4j에서 오고 표와 숫자는 계속 경로 응답이 채운다.
- 라우트 둘: `GET /api/causal/graph?week=`(그 주)와
  `GET /api/causal/graph/targets/{kind}/{code}`(대상 하나의 사슬, 주를 넘는다).
  상세 화면이 그 둘을 라디오로 오간다.
- **가드 둘을 코드가 건다**(설계 §8.2). `week_start`로 자르지 않으면 시각이 역행하는 경로가
  나오고(`Target` 노드가 주를 넘어 하나다), `path_id`를 안 실으면 서로 다른 주장이
  `할인율`에서 섞인다. 사슬 조회는 `week_start` 단조 증가를 조건으로 건다.
- **Cypher는 우리가 갖고 문장을 검사한다**(설계 §8.1). community 판은 사용자가 하나뿐이라
  읽기 전용 계정을 줄 수 없다. `MATCH`로 시작해 `RETURN`이 있는 문장만 통과시키고
  `CREATE`·`MERGE`·`DELETE`·`SET`·`CALL` 등이 있으면 **예외로 거절한다** — 삼키고 빈 결과를
  내면 "그런 경로가 없다"와 "우리가 막았다"를 부르는 쪽이 구별 못 한다.
- **접속은 환경변수 셋**(`NEO4J_URI`·`NEO4J_USER`·`NEO4J_PASSWORD`)이고 셋이 다 있어야 켠다.
  반쯤 준 설정은 조회 시점 인증 실패가 되어 "그래프가 비었다"와 구별되지 않는다.
- **없으면 그래프 라우트만 503이다.** 화면은 그것을 오류가 아니라 폴백으로 읽고 경로
  목록으로 그 주만 그린다 — 로컬에서 Neo4j 없이도 개발할 수 있어야 한다. **빈 그래프로
  위장하지 않는다.**
- 실측(2026-08-30): 그 주 노드 21·엣지 61, `week_start`를 ISO 문자열로 비교하다 **0건이
  나왔다** — 투영이 Neo4j `Date`로 저장해서 `date`를 그대로 넘겨야 한다.

## 8.4 인과 로직이 바뀌어 화면을 다시 짰다 (2026-08-30)

main이 인과 그래프에 **출발점 둘**을 들였다(프롬프트 판 10, 리비전 `d51c9a7be402`). 그 변경이
조회 계약을 세 곳에서 깼고, 화면도 그만큼 다시 짰다.

| main이 바꾼 것 | 화면·API가 받은 것 |
| --- | --- |
| `market_causal_path.event_id`가 nullable, `source_target_*` 셋과 `source_key` 추가 | `CausalPathRow`에 `source_kind`와 원인 대상 셋. **사건 없는 경로가 정상이다** |
| `confidence`에 `endpoint_observed` 추가(셋) | 라벨 `문서가 말함` / `양 끝 값` / `해석` |
| 자연키가 `event_id`에서 `source_key`로 | 상세를 **주 단위**로 묶는다(아래) |
| `disclosure_event.body` | 공시 표의 `본문` 열. **본문은 안 싣는다** — 한 건이 만 자를 넘는다 |
| `thesis`의 축 세 칸·± 폭 두 칸, `thesis_outcome.predicted_band_pct` | 추론 상세의 기준가 문단과 크기 `±` 표기 |

- **가장 컸던 것은 조용히 사라지던 행이다.** 전 판의 `build_paths`가 "사건을 못 찾은 경로는
  안 낸다"였는데, 대상 출발 경로가 생기면서 그 규칙이 그것들을 통째로 삼켰다 — 실측으로
  그 주 24개 중 **8개**가 목록에서 빠졌을 것이다. 지금은 전부 낸다.
- **상세를 사건이 아니라 주로 묶는다.** `VIX → NASDAQ100_FUT → SOX → 005930`은 경로 넷이고
  사건으로 묶으면 그 사슬이 조각난 채로 보인다. 그래프가 이어지는 이유는 **대상 출발 경로의
  원인 노드가 다른 경로의 대상 노드와 같은 id**이기 때문이다 — 출발점을 새 사건으로 만들었다면
  `target:SOX`와 `event:SOX 상승`이 갈려 거기서 끊긴다(설계 §11.4가 그 이유로 칸을 나눴다).
- **출발점의 방향은 `위로`가 아니라 `하락`이다.** 경로가 민 쪽(`sign`)과 원인 대상이 실제로
  움직인 쪽(`source_sign`)은 다른 값이라 화면의 말도 다르다.
- `endpoint_observed`는 **대상 출발만** 가질 수 있다(DB CHECK). 사건 출발은 원인 쪽이 문서라
  값으로 대조할 것이 없다. 화면은 그 뜻을 "양 끝 값"으로 적고, 상세 머리글이 **양 끝을 본
  것이지 사이를 본 것이 아니다**를 밝힌다.

## 8.3 main의 새 수집을 이어받는다 (2026-08-28)

이 워크트리가 58 커밋 뒤처져 있었다. main이 그동안 더한 것 중 **화면이 못 보던 것**을 잇는다.

| main이 더한 것 | 화면에 닿는 자리 |
| --- | --- |
| 인과 그래프 테이블 넷(`market_event`·`market_channel`·`market_causal_path`·`market_causal_step`) | **새 리소스 `causal`과 화면 `/causal`** |
| 지표 종류 다섯(`policy_rate`·`tips_rate`·`credit_spread`·`balance_sheet`·`balance_sheet_item`) | `/indicators` 종류 필터. **목록은 서버가 준 종류를 따라간다** |
| `index_future_daily.contract_code` | 일봉 표의 `월물` 열. **지수선물에만 온다** |
| `thesis_llm_run`의 토큰 넷·대상 수 둘·`run_slot` nullable | 실행 목록의 열 셋과 상세의 정의 목록 |
| `market_causal_evidence`(2026-08-28 오후) | 인과 상세의 **이 경로가 든 근거** 목록 |

- **`run_slot`이 nullable이 됐다.** 인과 그래프 실행은 슬롯이 없다(축이 주다). 응답 계약이
  `str`이던 자리라 그대로 뒀으면 그 실행 하나가 전체 목록을 500으로 만든다.
- **목록은 표, 상세는 그래프다**(2026-08-28 사용자 요구로 상세를 더했다). 목록에서 체인을
  누르면 `/causal/:pathId`로 가고 거기서 **한 사건이 한 판**으로 그려진다 — 그 사건이 그 주에
  뻗은 경로 전부를 한 그래프에 놓고 지금 보는 경로만 방향 색으로 강조한다. 경로 하나만
  그리면 직선 하나라 그림이 말해 주는 것이 없다(실측: 사건 1번의 형제가 8개다).
  - **채널 노드는 이름으로 공유한다.** 두 경로가 `통화정책 기대`를 함께 거쳤다는 사실이
    이 그래프의 요점이고, 경로마다 노드를 새로 만들면 그 공유가 사라진다.
  - **대상 노드는 `종류:코드`가 id다.** `US10Y`(시세)와 `KTB10Y`(지표)처럼 저장소가 다르면
    같은 코드라도 다른 대상이다.
  - canvas 옆에 같은 내용의 표를 둔다 — 스크린리더가 canvas를 못 읽는다. 관계 그래프
    화면과 같은 규칙이다.
  - **근거는 상세에만 싣는다.** `market_causal_evidence`의 `ref`가 `<kind>:<id>` 문자열이고
    외래키가 없다(근거가 문서·공시·신호 셋에 흩어져 있다). **그 규약은 응답이 한 번만
    푼다** — 화면이 문자열을 쪼개면 규약이 두 곳에 생기고, 종류가 늘 때 한쪽만 고친다.
    문서는 제목을 붙여 주고(`document:189`만으로는 못 읽는다) 화면 상세로, 공시는 DART
    뷰어로 잇는다. 모르는 종류는 **링크를 만들지 않는다** — 없는 곳으로 가는 링크는 없는
    것보다 나쁘다.
  - 근거가 0건인 경로는 "없다"고 적는다. 그것도 사실이고, 감추면 `confidence`가 무엇에
    기대고 있는지 되짚을 수 없다.
  - **노드 크기를 숫자로 못 박지 않는다**(`width: "label"` + `padding`). 44px 원에 한국어
    채널 이름을 넣으면 글자가 원 밖으로 샌다(2026-08-28 사용자 보고). 중심 노드도 크기가
    아니라 **테두리와 글자 크기**로 드러낸다 — 크게 만들면 그 안에서 다시 넘친다.
- **화면이 "인과의 증명이 아니다"를 먼저 말한다.** `confidence`가 `observed`(함께 관찰)와
  `plausible`(해석)뿐인데, 표만 보면 화살표가 인과로 읽힌다.
- 실현 등락은 **행마다 단위가 다르다**(`percent`·`basis_point`). `KTB10Y`의 7bp와 KOSPI의
  10%가 한 칸에 들어가면 크기 비교가 조용히 무의미해져서, 값과 단위를 함께 그린다.

## 9. 만들지 않는 것

- Grafana 대시보드 열여덟의 1:1 이식
- 새 테이블·마이그레이션·집계 테이블
- 재수집·수정·삭제 API (수집 손잡이는 Airflow UI다)
- 응답 캐시·WebSocket·실시간 스트리밍
- 통합(`UN`) 시세, KRX와 NXT를 합친 계열
- 알림·임계값·경보 (그 자리는 Slack 브리핑과 Sentry다)
- 사용자별 대시보드 저장, 차트 주석
- 차트 라이브러리 둘째 개
- 조용한 다운샘플링

## 10. 완료 조건

- **저장소의 테이블 49개가 전부 화면까지 닿는다.** 안 닿는 것이 0개다.
- `/quotes`에서 심볼 서른다섯을 kind별로 찾고, 각 심볼의 수집 구간과 행 수를 본다.
- `/quotes/index/KOSPI`가 분봉과 일봉을 간격 토글로 그린다. 일봉은 2016년까지 간다.
- 삼성전자를 **KRX와 NXT 각각** 볼 수 있고 둘이 합쳐지지 않는다.
- `/indicators`에서 계열 46을 `kind`·`country`로 좁히고, 국채 곡선을 나라별로 겹쳐 본다.
- 상한을 넘는 조합은 화면이 미리 막고, 그래도 들어온 요청은 400과 고칠 방법을 낸다.
- 차트마다 같은 데이터의 접근 가능한 표가 있다.
- **행을 주는 라우트가 예외 없이 쪽으로 나오고**, 목록 화면에 이전·다음이 있으며 쪽이
  URL에 남는다. 면제 넷은 테스트에 이유와 함께 적혀 있다.
- 공시·실적의 접수번호를 눌러 DART 원문으로 간다.
- frontend test/build, ruff, 전체 Python test가 통과한다.

## 11. 남은 확인

1. **`date_bin` 재집계의 실제 지연을 재지 않았다.** `stock_bar` 53만 행에서 1년치를
   `1d`로 접는 조회가 몇 ms인지 운영 DB에서 확인한 뒤 상한 5,000을 조정한다.
2. **분봉 보존 기간을 정하지 않았다.** 지금 12일치에 53만 행이라 1년이면 1,600만 행이다.
   화면이 아니라 수집 쪽 결정이지만, 화면이 "전체" 프리셋을 주는 순간 그 질문이 온다.
3. `rate_bar`가 97행뿐이다(2026-08-17 → 08-26). 수집이 도는지 확인이 필요하고, 안 도는
   것이면 화면에서 그 kind를 빼는 것이 아니라 수집을 고치는 것이 맞다.
4. `stock_analyst_opinion`이 2026-08-10에서 멈춰 있다. 위와 같은 성격의 확인이다.
5. 2판의 문서 검색(`q=`)을 어떻게 할지 정하지 않았다. DB가 ParadeDB(`pg_search`)라
   `@@@` 연산자를 쓸 수 있지만, `ILIKE`로 시작하고 느려지면 그때 옮기는 편이 싸다.
