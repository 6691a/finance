# 코스피 일일 전망의 조회 API와 화면

- 날짜: 2026-09-03
- 상태: **구현 완료, 배포 전**(2026-09-04 main 재반영). `pytest` 3,078개·`ruff`·`vitest` 166개·
  `tsc`가 통과하고 로컬 컨테이너에서 운영 DB·Neo4j를 읽어 열다섯 경로를 실측했다(§7).
- 대상: [kospi-forecast.md](kospi-forecast.md)가 만든 표 셋과 Neo4j 그래프를 읽는 화면.
  그 문서 §9.3의 삭제 순서 ④에 해당한다.
- 지우는 것: [market-thesis/](market-thesis/README.md) 12·14단계가 만든 추론 화면 전부와
  [market-causal-graph.md](market-causal-graph.md)의 인과 화면. 수집·시세·지표·문서·수급·
  사건·품질 중 **수집 계열 화면은 그대로 둔다** — 추론이 바뀐 것이지 수집이 바뀐 것이 아니다.

## 0. 무엇이 사라지고 무엇이 오나

새 추론은 대상이 코스피 하나, 답이 "방향 + 기대 등락률 ± 폭 + 이유 목록"이다. 옛 판의
대상 넷·확률 셋·지평 넷·자유 어휘 그래프가 전부 없어졌으므로 **그것을 그리던 화면도
같이 없어진다.** 이름만 바꿔 재사용하면 빈 칸이 남는다.

| 지금 | 무엇을 읽었나 | 다음 |
| --- | --- | --- |
| `/theses` · `/theses/:id` · `/theses/:id/graph` | `thesis`·`thesis_outcome` | **삭제** → `/forecast` |
| `/causal` · `/causal/:pathId` | `market_causal_*` + Neo4j `Event`·`Channel`·`Target` | **삭제** → `/relations` |
| `/runs` · `/runs/:id` · `/runs/:id/tool-calls/:seq` | `thesis_llm_run`·`thesis_tool_call` | **교체** → 같은 경로, `kospi_llm_run`·`kospi_tool_call` |
| `/quality` | `thesis_outcome` 채점 집계 | **교체** → `kospi_forecast`의 채점 칸 넷 |
| `/dashboard` | 위 넷의 첫 줄을 카드로 | **카드 넷을 새 리소스로 갈아 끼운다** |

API 쪽에서 사라지는 라우트는 열둘이다(`thesis` 셋, `causal` 일곱, `quality` 하나, 그리고
`thesis` 계열을 읽던 `llm_run` 셋은 교체). 리포지토리·서비스·스키마도 같은 이름으로
넷씩 사라지고 새 셋이 들어온다.

**운영 DB에 이미 데이터가 있다**(2026-09-03 실측). `kospi_forecast` 3행(오늘 슬롯 셋),
`kospi_llm_run` 13행, `kospi_tool_call` 188행. 옛 표도 아직 살아 있다(`thesis` 118행,
`thesis_tool_call` 1,105행, `market_causal_path` 51행) — 삭제는 `kospi-forecast.md` §9.3의
순서를 따르고 **이 작업은 화면과 API만 바꾼다.**

## 1. 새 리소스 셋

층 넷(`routes`·`service`·`repository`·`schemas`)에 리소스 이름 하나가 파일 하나씩이다.

### 1.1 `forecast` — 전망과 채점

| 라우트 | 주는 것 |
| --- | --- |
| `GET /api/forecasts` | 슬롯 행 목록. 최신순. 필터 `from`·`to`(KST 발행일)·`slot`·`graded`(채점 여부)·`hit` |
| `GET /api/forecasts/{run_date}/{slot}` | 그 한 건 전부 — `reasons`·`input_state`·채점 칸·원장 링크 |
| `GET /api/forecasts/accuracy` | 채점 집계. 슬롯별 적중률·밴드 안 비율·평균 오차 |

- **자연키가 `(run_date, slot)`이라 URL도 그것이다.** `id`를 쓰면 같은 슬롯을 두 번 본
  사람이 두 주소를 갖는다.
- 목록 행은 `run_date · slot · as_of_at · base_price · so_far_pct · direction ·
  expected_change_pct · band_pct · reason_count · weak · actual_change_pct · hit ·
  within_band · llm_run_id`다. `reasons`와 `input_state`는 **목록에 싣지 않는다** — 한 건이
  수 KB이고 문서 목록에서 이미 같은 실수를 했다(20단계 §8.6.2, `select *`가 6.8MB를 옮겼다).
- `input_state`는 상세에서만 준다. **이 칸이 "그때 무엇을 보고 그렇게 말했나"의 원본**이다 —
  관계와 메모는 그래프가 원본이라 다음 날 바뀌기 때문에 여기 없으면 되짚을 수 없다.
- `accuracy`는 `count(*) FILTER (WHERE hit)` 형태의 집계 하나다. 표본이 셋뿐이라 **화면이
  표본 수를 반드시 함께 보인다.** 비율만 보이면 3건에서 나온 67%가 100건에서 나온 67%처럼
  읽힌다.

### 1.2 `relations` — 요인 관계와 메모 (Neo4j)

| 라우트 | 주는 것 |
| --- | --- |
| `GET /api/relations` | 요인 17개의 가중치 표. `weight · n_obs · 최근 방향 셋 · 마지막 관측일 · 마지막 관찰 문장` |
| `GET /api/relations/{factor}` | 그 요인의 관측 목록(최신순, 쪽 나눔). `date · sign · strength · note · llm_run_id` |
| `GET /api/relations/graph` | 그림용. `Factor` 노드 17 + `OBSERVED` 엣지(창 안) + `Index` 하나 |
| `GET /api/relations/memories` | 메모 목록. `retired=false`면 활성만, `true`면 내린 것만 |
| `GET /api/relations/memories/{memory_id}` | 메모 하나. 전망의 이유가 `memory_id`로 인용한 것을 여는 자리 |

- **가중치는 API가 계산하지 않는다.** `airflow/modules/kospi/domain.relation_weight`가
  원본이고 Airflow 트리는 `apps/`가 import하지 못한다(저장소 규칙). 그러므로 **같은 감쇠
  식을 `apps/api/service/relation.py`에 한 벌 더 두고 테스트가 둘을 대조한다** —
  `tests/realtime/test_kis_realtime.py`의 `*_match_the_airflow_collector`와 같은 형태다.
  상수 셋(`RELATION_HALF_LIFE_DAYS=5`·`RELATION_WINDOW=15`·`RELATION_LOOKBACK_DAYS=90`)이
  어긋나면 화면과 프롬프트가 다른 값을 말한다.
- Cypher는 `apps/api/repository/kospi_graph.py`가 갖고 `repository/graph.py`의
  `ensure_read_only()` 가드를 쓴다(`MATCH `로 시작, ` RETURN ` 포함, 쓰기 키워드 거부).
  **그 가드가 낱말 경계를 안 보고 있었다** — `m.created_on`이 `CREATE`로 읽혀 메모 조회가
  통째로 막혔고, 라벨 대조 테스트가 그것을 잡았다(2026-09-03). 지금은 정규식 `\b`다.
- **메모가 `/api/memories`가 아니라 `/api/relations/memories`다.** 층 넷에서 리소스
  이름이 같아야 한다는 규칙 때문이고, 메모는 관계로 담기지 않는 것을 담는 자리라 같은
  리소스로 읽는 것이 뜻에도 맞는다.
- Neo4j가 없으면 **503**이다. 지금 `causal_graph`가 그렇게 하고 있고 그 규칙을 옮긴다.
- 그림은 요인 17개가 코스피 하나를 가리키는 **별 모양**이다. 옛 인과 그래프처럼 다중 홉이
  아니라 깊이 1이라 기존 `GraphView`의 `breadthfirst`를 그대로 쓴다 — 중심을 root로 주면
  요인이 한 층에 둘러선다. 컴포넌트를 새로 만들지 않는다.

### 1.3 `runs` — 실행 원장 (교체)

경로(`/api/llm-runs`)와 화면(`/runs`)은 그대로 두고 **읽는 표만 바꾼다.** 지금 화면이
쓸모 있는 형태이고, 바뀌는 것은 칸 이름이다.

| 지금 | 다음 |
| --- | --- |
| `kind` = forecast/review/followup… | `kind` = `forecast`/`review` 둘 |
| `thesis_id` 링크 | `(run_date, slot)` → `/forecast/...` 링크 |
| 툴 호출 `thesis_tool_call` | `kospi_tool_call` — 칸은 거의 같다(`seq`·`round_no`·`tool_name`· `arguments`·`duration_ms`·`result_chars`·`error_kind`·`delivered`) |
| — | **새 칸 일곱**: `observations_written`·`memories_written`·`memories_rejected`· `memories_kept`·`memories_dropped`·`memories_unreviewed`·`memories_expired` |

메모 칸 일곱은 `kind='review'`에만 값이 있다. 화면은 **`kind`에 따라 열을 가른다** — 전망
행에 빈 칸 일곱을 그리면 "0건"과 "해당 없음"이 같아 보인다.

## 2. 화면 셋

### 2.1 `/forecast` — 첫 화면이 여기다

지금 대시보드가 첫 화면인데, **추론이 코스피 하나로 좁아졌으니 "오늘 무엇이라고 말했나"가
곧 첫 화면**이다. 대시보드는 남기되 상단 카드가 이것을 가리킨다.

```
2026-09-03 (목)                                          [ 오늘 ] [ ← 이전날 ] [ 다음날 → ]

  08:35 장전    ▲ +1.20% ± 1.80%   기준 6,562.72 (전일 종가)      채점 대기
  11:35 장중    ▲ +0.60% ± 1.40%   기준 6,652.75 (현재가)  여기까지 +1.37%   채점 대기
  14:35 마감전  ▼ -0.40% ± 0.90%   기준 6,489.16 (현재가)  여기까지 -1.12%   채점 대기

  [슬롯 하나를 누르면 이유 목록과 관측 상태가 아래 열린다]
```

- **하루 셋을 한 화면에 세로로 둔다.** 슬롯끼리 비교하는 것이 이 기능의 핵심이다 — 장전이
  틀렸다는 것을 장중이 인정했는지가 한 줄에서 보여야 한다.
- 채점이 되면 슬롯 줄 오른쪽이 `실제 -2.31% · 방향 ✗ · 밴드 밖`으로 바뀐다.
- `weak = true`인 행에는 `⚠ 근거 없음`을 붙인다. Slack이 그렇게 하고 있고 화면도 같아야 한다.
- 이유 목록은 **저장된 순서 그대로**다(순서가 곧 중요도, `kospi-forecast.md` §6.1).
  `factor`가 있으면 그 요인 화면으로, `memory_id`가 있으면 그 메모로, `slot_ref`가 있으면
  같은 날 그 슬롯으로 링크한다. **이유 하나하나가 근거로 되짚어진다**는 것이 옛 화면과
  가장 다른 점이다.
- `input_state`는 접힌 채로 두고 펼치면 JSON을 그대로 보인다. 표로 예쁘게 그리지 않는다 —
  그 모양은 프롬프트 판마다 바뀌고, 화면이 모양을 알면 판이 오를 때마다 화면이 깨진다.

### 2.2 `/relations` — 요인이 코스피를 어떻게 움직였나

```
요인             가중치   관측   최근 방향        마지막      마지막 관찰
외국인 순매수     +0.80     12   같음·같음·같음   09-01      외국인 1.2조 순매수가 …
미국 10년물       -0.15      7   같음·반대·반대   08-29      금리 +8bp에도 반도체가 …
VIX               -0.33      3   반대·반대·반대   08-26      VIX 급등에 위험자산 …
필라델피아반도체     —        0   관측 없음          —         —
```

- **가중치와 최근 방향을 나란히 둔다.** 프롬프트가 그 둘을 같이 주는 이유(§3.3)가 화면에도
  그대로 적용된다 — 가중치 하나로는 "오래 일관된 -0.5"와 "막 뒤집히는 중인 -0.15"가 안 갈린다.
- 관측 0인 요인은 **"관측 없음"**으로 싣는다. 빈 칸으로 두면 "관계 없음"으로 읽힌다.
- 위쪽에 별 그래프를 둔다. 노드 크기가 `n_obs`, 엣지 색이 부호, 두께가 `|weight|`다.
- 요인을 누르면 `/relations/:factor`로 가고 관측이 날짜순으로 쌓인다. **관찰 문장은
  그 실행으로 링크한다** — 어느 대화가 그 문장을 냈는지가 원장에 있다.

### 2.3 `/memories` — 지금 무엇을 기억하고 있나

활성 메모 표(`text · 만든 날 · 검증 횟수 · 마지막 검증 · 연결 요인`)와, 토글로 지워진 메모
(`retired_on · retire_reason`)다. **지워진 것을 볼 수 있어야 한다** — 왜 지웠는지가 남아
있는 것이 이 기능의 설계 의도다(§4.4 "노드는 지우지 않는다").

## 3. 대시보드 카드

| 카드 | 지금 | 다음 |
| --- | --- | --- |
| 추론 | 최신 thesis 다섯 | **오늘의 슬롯 셋**과 채점 상태 |
| 인과 | 주간 경로 다섯 | **가중치 상위 다섯 요인** |
| 실행 | 최신 실행 다섯 | 그대로(표만 교체) |
| 품질 | thesis 채점 | **최근 20영업일 슬롯별 적중률**과 표본 수 |
| 수집·문서 | 그대로 | 그대로 |

## 4. 계약

- **쪽 나누기는 전부 있다.** `Page[T]`(`items`·`limit`·`offset`·`has_more`)이고 `limit+1`을
  읽는다. `count(*)`를 세지 않는다. `tests/api/test_pagination.py`의 `EXEMPT`에 새 라우트를
  넣지 않는다 — 목록이면 예외 없이 쪽이 있다.
- **시각은 UTC ISO 8601 + `Z`로 그대로 준다.** KST 변환은 화면이 한다. 다만 `run_date`는
  이미 KST 날짜라 `date`로 준다.
- **`Decimal`은 JSON number로 내린다.** `apps/api/service/common.number()`가 그 자리다.
- **읽기 전용 별칭으로만 붙는다.** 지금 그렇고 그대로다.
- Neo4j 조회는 `ensure_read_only()`를 통과한 Cypher만.

## 5. 순서

1. **새 리소스 셋을 더한다**(`forecast`·`relations`·`memories`) — 옛것을 지우기 전에.
   중간 상태에서 둘 다 보이는 편이 낫다. 옛 화면이 갑자기 없어지면 무엇이 대신인지 모른다.
2. **`llm_run`을 새 표로 갈아 끼운다.** 경로와 화면 모양은 그대로.
3. **`quality`를 `kospi_forecast` 채점으로 바꾼다.**
4. **옛것을 지운다** — `thesis`·`causal` 리소스 넷과 화면 다섯, `frontend/src/causal.ts`,
   `graph.ts`의 옛 투영 부분, 그 테스트들.
5. 대시보드 카드와 상단 내비게이션을 새 화면에 맞춘다.

1~3이 끝나면 화면이 둘 다 도는 상태이고, 4에서 한 번에 걷어낸다. **4를 1과 같은 커밋에
두지 않는다** — 어느 쪽이 회귀를 만들었는지 못 가른다.

## 6. 안 하는 것

- **옛 테이블 drop 리비전은 여기서 안 만든다.** `kospi-forecast.md` §9.3이 그 순서를 갖고
  있고 DAG 기동이 먼저다.
- **Neo4j 옛 라벨 삭제도 안 한다.** 같은 이유다.
- **전망을 화면에서 다시 돌리는 버튼은 안 만든다.** 조회 API는 읽기 전용이다.
- **채점 값을 화면에서 고치는 경로도 없다.** 틀린 전망은 틀린 채로 남는다(§5.1).
- **`input_state`를 표로 그리지 않는다**(§2.1의 이유).

## 7. 구현 뒤 실측과 바뀐 판단 (2026-09-03)

로컬 컨테이너가 운영 DB(읽기 전용)와 Neo4j를 그대로 읽는다. 열다섯 경로 전부 200이고
가장 느린 것이 `/api/quotes/symbols` 0.48초, 새 경로는 전부 0.2초 아래다.

```
0.202s  /api/forecasts?from=2026-09-03&to=2026-09-03
0.040s  /api/forecasts/accuracy
0.052s  /api/forecasts/quality
0.034s  /api/forecasts/2026-09-03/midday
0.016s  /api/relations?limit=200
0.008s  /api/relations/graph
0.007s  /api/relations/memories
0.063s  /api/llm-runs/13
```

### 7.1 설계에서 바꾼 것 셋

- **메모 경로가 `/api/relations/memories`다**(§1.2). 층 넷의 리소스 이름을 맞추려는 것이다.
- **`quality`가 `/api/theses/quality`에서 `/api/forecasts/quality`로 옮겼다.** 라우터 등록
  순서(정적 경로가 먼저)는 그대로이고 `tests/api/test_routes.py`가 실제 요청으로 본다.
- **`accuracy`와 `quality`를 둘 다 둔다.** 앞은 창 하나의 슬롯별 합계(대시보드·전망 화면이
  쓴다), 뒤는 주·슬롯·모델·판을 키로 한 튜닝 표다. 같은 숫자를 두 번 세는 것이 아니라
  **묻는 질문이 다르다** — "지금 잘 맞히고 있나"와 "어느 판이 나은가".

### 7.2 실측이 잡은 결함 둘

- **읽기 전용 가드의 부분 문자열 매칭.** 위에 적었다. `MATCH (m:Memory) ... m.created_on`이
  거절됐다 — 화면이 503도 아니고 500으로 죽는 자리였다.
- **`retire_reason`이 `drop`이 아니라 `dropped`다.** 화면 라벨을 `drop`으로 적어 뒀는데
  `labelOf`가 모르는 값을 그대로 보이므로 **화면이 죽지 않고 영문 코드만 보였다.** 운영
  응답을 눈으로 보고서야 알았다. 지금은 `tests/api/test_kospi_graph.py`가 `RunSlot`·
  `ObservationSign`·`RetireReason` 셋의 저장 값이 `frontend/src/labels.ts`에 전부 있는지
  대조한다.

### 7.3 남은 것

- **옛 테이블·모듈·Neo4j 라벨 삭제는 안 했다.** `kospi-forecast.md` §9.3의 순서를 따르고
  DAG 기동이 먼저다. 지금 상태는 화면과 API만 새 표를 읽는 것이다.
- `thesis*`·`market_causal_*` 표는 그대로 살아 있다(2026-09-03 실측: `thesis` 118행,
  `thesis_tool_call` 1,105행, `market_causal_path` 51행).

## 8. main 재반영 (2026-09-04)

main이 옛 추론·인과를 **코드와 표까지** 지웠고(리비전 `d5b8c204e7f1`) 수집·툴이 여럿 늘었다.
`frontend/`와 추론 밖 조회 리소스 일곱은 main에 없던 것이라 이 브랜치가 그대로 갖는다.

### 8.1 계약이 바뀐 자리 넷

| 무엇 | 어떻게 |
| --- | --- |
| `Factor`에 `KOSPI`(코스피 자체)가 늘었다 | 라벨을 더하고 **관계 표에서는 뺀다** — 지수가 자기와 같은 방향인 것은 언제나 참이라 엣지가 쌓이면 뜻 없는 값이 하나 박힌다. 원본도 `RELATION_FACTORS`로 같은 판단을 한다 |
| `IndicatorSeries.kind`에 `sentiment`가 늘었다 | 지표 화면의 종류 목록에 "심리지수"를 더한다. **`activity`와 가른다** — 설문이 만드는 값과 실물 합성은 틀리는 방식이 다르다 |
| `Instrument`에 `filing_entity_id`·`sector`가 늘었다 | 수집 화면의 종목 표에 두 열을 더한다. **`filing_entity_id`를 참·거짓으로 접지 않는다** — 발급 기관이 시장마다 달라(DART 고유번호 / SEC CIK) 번호 자체가 값이다 |
| 프롬프트 판이 4로 올랐다 | 화면은 판을 값으로만 보이므로 고칠 것이 없다. 품질 표의 키가 판이라 4와 3이 자동으로 갈린다 |

`recent_news`가 `total`·`shown`을 싣게 된 것은 툴 결과 JSON 안이라 화면이 그대로 보인다.

### 8.2 아시아 지수가 드러낸 결함 — 제공처가 키에서 빠져 있었다

main이 KIS로 아시아 지수 넷(HSI·NIKKEI225·SSE_COMP·TAIEX)을 받기 시작하면서 **Yahoo와 심볼이
겹쳤다.** 자연키가 `(provider, symbol)`인데 조회가 심볼로만 묶고 있어서 셋이 함께 틀렸다.

- **집계가 합쳐졌다.** 목록이 "Yahoo 716건 · KIS 716건"을 보였는데 실제는 합쳐서 716이었다.
  지금은 `(kind, provider, symbol)`로 묶어 **Yahoo 716 · KIS 0**으로 나온다 — KIS 분봉이
  아직 안 쌓였다는 사실이 그제야 보인다.
- **마스터 조회가 죽을 뻔했다.** `scalar_one_or_none()`이라 그 심볼에서 `MultipleResultsFound`다.
- **봉이 한 시계열로 섞였다.** 거래소를 안 고르면 KRX와 NXT가 섞이는 것과 같은 자리인데
  제공처에는 그 가드가 없었다. 지금은 `ProviderRequired`로 422를 내고 고를 수 있는 값을
  응답에 싣는다. 목록의 링크가 제공처를 미리 얹어 보통은 그 화면을 볼 일이 없다.

셋 다 **운영 응답을 눈으로 보고** 찾았다. 테스트는 제공처가 하나인 픽스처만 갖고 있었다.
지금은 `test_quotes.py`가 제공처 둘인 심볼을 갖고, 조회문이 실제로 `GROUP BY`에 제공처를
넣는지도 컴파일해서 본다 — 가짜 리포지토리는 키를 그냥 주기 때문에 그것만으로는 가짜 통과다.
