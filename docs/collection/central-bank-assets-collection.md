# 중앙은행 자산 수집 설계

- 날짜: 2026-08-27
- 상태: **구현됨(2026-08-28).** 수집기 넷(FRED·ECOS 계열 추가, BoE 묶음 추가, 분데스방크
  `bbk_statement.py` 신규), DAG `central_bank_assets_weekly`, 리비전 `c9f1e4b70a25`.
  §3 실측 표가 채워져 있고 §6이 조회 창을 800일로 확정했다(구현 중 실측으로 바뀐 값이다).
- 목표: 중앙은행 여섯의 **대차대조표 총자산 잔액**을 `indicator_observation`에 쌓아,
  정책금리·국채와 같은 테이블에서 유동성 흐름을 읽을 수 있게 한다.
- 관련 원본:
  [CLAUDE.md](../../.claude/CLAUDE.md)의 `indicator_observation`·`indicator_series` 절,
  [정책금리 수집 설계](policy-rate-collection.md),
  [수집기 클래스 전환](../convention/collectors-class-migration.md)

## 0. 확정 결정

| 항목 | 결정 |
| --- | --- |
| 대상 | 중앙은행 **여섯** — 연준·유로시스템·일본은행·한국은행·분데스방크·영란은행 |
| 저장 | 기존 `indicator_observation`. 새 테이블을 만들지 않는다 |
| 분류 | `indicator_series.kind`에 **`balance_sheet`와 `balance_sheet_item`을 추가**한다(§4.2) |
| `maturity_months` | `NULL`. 대차대조표 잔액에는 만기 개념이 없다 |
| 저장값 | **잔액(level)만.** 증가율은 저장하지 않고 조회에서 낸다(§5) |
| 단위 | **제공처 표기를 통화별로 유지한다.** 한 통화로 환산하지 않는다(§4.3) |
| 영란은행 | 총자산(분기)과 준비금잔액(주간) **둘 다.** 우리가 합계를 만들지 않는다(§2.2) |
| 유로시스템 | FRED에서 받는다. 분데스방크 API가 같은 값을 주지만 provider를 갈랐다(§3.2) |
| 스케줄 | 주 1회(KST 월 09:20). **DAG 하나에 제공처마다 태스크 하나** — 넷이다(§6) |
| 조회 창 | **800일 하나.** 제공처마다 다르게 두지 않는다. 가장 밀린 계열에 맞춘다(§6) |
| 0건 | **실패다.** 800일 창의 0건은 발표 전이 아니라 제공처가 바뀐 것이다(§6) |
| 기존 DAG | `policy_rate_weekly`에 얹지 않는다. 새 DAG `central_bank_assets_weekly`다(§6) |
| 백필 | 최초 실행에서 이력 전체를 받는다. 7계열 × 27년이라 1만 행 미만이다(§6) |

## 1. 왜 필요한가

`indicator_observation`에는 지금 국채 40계열, CD 91일, 정책금리 다섯이 있다. **가격은 있고
수량이 없다.**

- **정책금리가 0에 붙어 있던 구간을 설명할 수 없다.** 2010년대와 코로나 국면에서 통화정책의
  강도를 가른 것은 금리가 아니라 자산 매입 규모였다. 정책금리만으로는 그 구간이 전부 같아 보인다.
- **QT를 잴 수 없다.** 연준 총자산은 2022년 이후 줄고 있고 그 속도가 장기금리와 위험자산에
  붙는다. 잔액이 없으면 "긴축이 얼마나 진행됐나"에 숫자를 댈 수 없다.
- **나라 사이 비교가 금리 한 축뿐이다.** 일본은행이 자산을 늘리는 동안 연준이 줄이면 그것이
  엔·달러와 국채 금리차에 남는다. 이 대조는 잔액 없이는 안 나온다.

**총자산은 정책금리와 같은 테이블에 들어가야 한다.** 나눠 두면 "이 중앙은행이 한 일 전부"를
보는 쿼리가 두 테이블을 UNION해야 하고, 국가 코드 규약이 두 벌이 된다.

## 2. 대상

### 2.1 중앙은행 여섯

국채를 받는 나라 아홉 가운데 자체 대차대조표를 갖는 통화당국은 여섯이다.

| 중앙은행 | `country` | 비고 |
| --- | --- | --- |
| 연방준비제도 | `US` | |
| 유로시스템 | `XM` | ECB와 회원국 중앙은행의 연결 재무제표 |
| 일본은행 | `JP` | |
| 한국은행 | `KR` | |
| 분데스방크 | `DE` | **유로시스템의 부분집합이다.** 아래 참고 |
| 영란은행 | `GB` | 총자산은 분기, 준비금잔액은 주간(§2.2) |

**분데스방크를 유로시스템과 함께 두는 것은 중복이 아니다.** 유로시스템 총자산은 회원국
중앙은행의 합이고, 그 안에서 독일이 차지하는 몫과 TARGET2 잔액의 움직임은 별개의 값이다.
스페인·프랑스·이탈리아는 두지 않는다 — 정책금리를 `country='XM'` 하나로 둔 것과 같은 이유로,
지금 답해야 하는 질문에 값을 더하지 않는다. 필요해지면 계열을 늘리는 것은 Enum 한 줄이다.

### 2.2 영란은행만 계열이 둘이다

**BoE는 총자산을 주간으로 고시하지 않는다.** 실측으로 갈린 사실이다.

- 주간 Weekly Report(`RPW*` 11계열)는 발행권·준비금잔액·repo·채권보유·APF 대출·외환보유를
  항목으로 준다. **총계 줄이 없다.** BoE 자신이 "주간 보고는 대차대조표의 90% 이상"이라고
  말하는 것이 총계가 아니라는 뜻이다.
- 총자산(`RPQB75A`, Central Bank assets/liabilities total)은 **분기**이고 2026-08-27 시점의
  최신값이 **2025-03-31**이다. 17개월 지연이다.

**주간 항목을 더해 총자산을 만들지 않는다.** 그 값은 BoE가 고시한 값이 아니고, 90%만 담고
있어 다른 다섯 나라의 총자산과 같은 자로 비교하면 조용히 적게 나온다. 제공처가 준 값만
저장한다는 규칙([CLAUDE.md](../../.claude/CLAUDE.md) 수집기 절)의 직접 적용이다.

대신 **둘을 따로 저장한다.**

- `GBASSETS_Q` — 분기 총자산. 다른 다섯과 같은 자로 비교할 수 있는 유일한 값이다. 이력용이다.
- `GBRESERVES_W` — 주간 준비금잔액(`RPWB56A`). 총자산이 아니다. QE·QT가 준비금으로 먼저
  나타나므로 주간 추적은 이것으로 한다.

**둘의 `kind`를 가른다**(§4.2). 총자산만 보는 쿼리가 준비금을 집어삼키면 영국만 값이 작게
나오는데, 그 사고는 화면에서 안 보인다.

## 3. 계열 식별자는 실측이 선행 조건이다

**계열 ID는 추측하지 않고 실측했다.** 아래가 2026-08-27에 제공처에 직접 조회해 얻은 값이다.

### 3.1 실측 결과 (2026-08-27)

| 중앙은행 | 제공처 | 계열 식별자 | 주기 | 단위 표기 | 이력 시작 | 최신 관측 | 저장 `series_id` |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 연방준비제도 | fred | `WALCL` | 주(수요일 잔액) | `Mil. of U.S. $` | 2002-12-18 | 2026-08-19 = 6,745,699 | `FEDASSETS_W` |
| 유로시스템 | fred | `ECBASSETSW` | 주(금요일 잔액) | `Mil. of Euros` | 1999-01-01 | 2026-08-21 = 5,913,041 | `EAASSETS_W` |
| 일본은행 | fred | `JPNASSETS` | 월(말잔) | `100 Mil. Yen` | 1998-04-01 | 2026-07 = 6,442,957 | `JPASSETS_M` |
| 한국은행 | ecos | `103Y002` / `BCAA1` 자산합계 | 월(말잔) | `십억원` | 1970-01 | 2026-06 | `KRASSETS_M` |
| 분데스방크 | bbk | `BBBK11` / `D.TTA032` | 주(금요일 잔액) | `Millionen EURO` | 1999-01-01 | 2026-08-21 = 2,265,320 | `DEASSETS_W` |
| 영란은행 | boe | `RPQB75A` | **분기** | `sterling millions` | 2013-09-30 | **2025-03-31** = 861,868 | `GBASSETS_Q` |
| 영란은행 | boe | `RPWB56A` 준비금잔액 | 주(수요일 잔액) | `sterling millions` | 2006-05-24 | 2026-08-19 = 641,378 | `GBRESERVES_W` |

실측에서 갈린 것 넷.

- **BoE에 주간 총자산이 없다**(§2.2). 이것이 이 설계에서 가장 큰 제약이다.
- **한국은행 총자산은 ECOS `103Y002`(한국은행 주요계정, 말잔)의 항목 `BCAA1`이다.**
  `자산합계`라는 이름으로 1970년부터 있고 단위는 십억원이다. 2026-08-27 시점 최신이 2026-06이라
  **두 달 지연**이다. 월별 확정 통계라 주 1회 수집으로 충분하다.
- **일본은행은 FRED가 유일한 실용 경로다.** 원본은 BOJ 시계열 검색이지만 API가 따로 있고,
  FRED `JPNASSETS`가 월말 잔액을 100억엔 단위로 1998년부터 준다. 단위 표기가
  `100 Mil. Yen`이라 다른 계열과 자릿수가 다르다 — §4.3의 이유다.
- **FRED와 분데스방크가 유로시스템에 같은 값을 준다.** 2026-08-21 유로시스템 총자산이
  FRED `ECBASSETSW` = 5,913,041, 분데스방크 `D.TTA082` = 5,913,041로 **글자 그대로 같았다.**
  둘 중 하나를 고르면 되고 대조는 언제든 다시 할 수 있다.

### 3.2 제공처를 고르는 기준

- **이미 붙은 제공처를 고른다.** FRED와 ECOS는 수집기가 있어 Enum 한 줄이면 계열이 는다.
  연준·유로시스템·일본이 FRED로 가는 이유다.
- **원본만 주는 값은 원본에서 받는다.** 한국은행 총자산은 FRED에 없고(`Central Bank Assets
  to GDP`는 연간 비율이다), 분데스방크 총자산도 FRED의 독일 계열은 전부 세계은행 기반 연간
  비율이다. 영국 총자산(`UKASSETS`)은 FRED에서 2014-09에 끊겼다.
- **유로시스템은 FRED로 받는다.** 분데스방크 API가 요청 하나로 독일과 유로시스템을 함께
  주지만, 그러면 유로시스템 값의 `provider`가 `bbk`가 되어 "유로 지역 값인데 제공처가 독일"로
  읽힌다. FRED에 두면 국채·물가와 같은 결이 유지된다. 두 값의 기준일이 하루 어긋날 수 있는데,
  주 1회 수집이라 그 지연은 보이지 않는다.

## 4. 저장 모양

### 4.1 테이블은 `indicator_observation` 그대로다

`(provider, series_id, observation_date)`가 자연키이고 `source_record_id`로 수집 계보를 잇는다.
새 컬럼도 새 테이블도 만들지 않는다.

주간 계열의 `observation_date`는 **잔액 기준일**이다. 발표일이 아니다. 연준은 수요일,
유로시스템·분데스방크는 금요일, BoE 준비금은 수요일이다. 월간 계열은 그 달 1일에 저장한다
(`JPASSETS_M`은 FRED가 이미 그 형태로 준다). 분기 계열은 분기 말일이다.

### 4.2 `kind`를 둘 추가한다

| `kind` | 무엇 | 계열 |
| --- | --- | --- |
| `balance_sheet` | 중앙은행 대차대조표 **총자산** | `FEDASSETS_W`·`EAASSETS_W`·`JPASSETS_M`·`KRASSETS_M`·`DEASSETS_W`·`GBASSETS_Q` |
| `balance_sheet_item` | 대차대조표의 **한 항목** | `GBRESERVES_W` |

**둘을 가르는 이유는 §2.2다.** 한 kind에 두면 "중앙은행 총자산 전부"를 묻는 쿼리가 영국의
준비금을 총자산으로 읽는다. 계열이 지금 하나뿐인 kind를 만드는 것이 과해 보이지만, 대안은
`series_id LIKE '%ASSETS%'` 같은 이름 규칙에 정확성을 거는 것이고 그것은 계열이 늘 때 조용히
깨진다. 다른 중앙은행의 준비금·발행권을 나중에 붙이면 그대로 이 kind로 온다.

`indicator_series.kind`에는 DB CHECK 제약이 걸려 있다. 마이그레이션에서 함께 갱신한다(§7).

### 4.3 단위는 환산하지 않는다

| `series_id` | `unit` |
| --- | --- |
| `FEDASSETS_W` | `Millions of Dollars` |
| `EAASSETS_W`·`DEASSETS_W` | `Millions of Euros` |
| `JPASSETS_M` | `Hundred Millions of Yen` |
| `KRASSETS_M` | `Billions of Won` |
| `GBASSETS_Q`·`GBRESERVES_W` | `Millions of Pounds` |

**한 통화로 맞추지 않는다.** 환산하면 환율 변동이 자산 증감으로 위장한다. 2022년 엔 약세
구간에서 달러 환산 BOJ 자산은 크게 줄지만 엔화 잔액은 늘고 있었다 — 두 값이 정반대 이야기를
한다. 비교는 §5의 증가율로 한다.

`indicator_observation.unit`은 제공처 표기가 아니라 정규화 표기라는 규칙에 따라 위 표의
영문 표기로 저장한다. `십억원`·`Millionen EURO`를 그대로 넣지 않는다.

### 4.4 증가율을 저장하지 않는다

**잔액만 저장한다.** 이유 둘이다.

- **주기가 갈린다.** 주간 넷, 월간 둘, 분기 하나다. 증가율을 저장하면 "무엇 대비"가 값에서
  사라지고, 주간 계열의 전주 대비와 월간 계열의 전월 대비가 한 컬럼에 섞인다.
- **되돌릴 수 없다.** 기준을 전주 대비에서 YoY로 바꾸면 저장한 증가율에서는 다시 못 만든다.
  잔액에서는 언제든 만든다.

이것은 `indicator_observation`이 국채 **금리**를 저장하고 금리차를 저장하지 않는 것과 같은
판단이다.

## 5. 증가율은 조회에서 낸다

**여섯을 나란히 놓을 수 있는 유일한 기준은 YoY %다.** 주간과 월간이 섞여도 1년 전 관측과
비교하면 같은 뜻이고, 통화가 달라도 비교된다.

```sql
-- 중앙은행 총자산 전년 대비 증가율.
-- 주간 계열은 1년 전 같은 요일 잔액, 월간은 1년 전 같은 달 잔액과 비교한다.
SELECT
    s.country_name,
    o.series_id,
    o.observation_date,
    o.value,
    round(
        (o.value / prev.value - 1) * 100,
        2
    ) AS yoy_percent
FROM indicator_observation AS o
JOIN indicator_series AS s
    ON s.provider = o.provider
   AND s.series_id = o.series_id
JOIN LATERAL (
    SELECT p.value
    FROM indicator_observation AS p
    WHERE p.provider = o.provider
      AND p.series_id = o.series_id
      AND p.observation_date
          BETWEEN o.observation_date - interval '1 year' - interval '10 days'
              AND o.observation_date - interval '1 year' + interval '10 days'
    -- `date - interval`은 timestamp라 `::date`를 빼면 `abs(interval)`이 없다고 죽는다.
    ORDER BY abs(p.observation_date - (o.observation_date - interval '1 year')::date)
    LIMIT 1
) AS prev ON TRUE
WHERE s.kind = 'balance_sheet'
ORDER BY o.observation_date DESC, s.country_name;
```

`±10일` 창은 주간 계열의 요일과 휴일 때문이다. 정확히 365일 전 행은 대개 없다.

**쿼리는 `kind`를 반드시 건다.** §4.2가 그것을 전제로 kind를 갈랐다.

## 6. DAG

**새 DAG `central_bank_assets_weekly`다.** `policy_rate_weekly`에 얹지 않는다 — 계열 목록만
늘리면 파일은 안 늘지만, 자산 수집 실패가 이미 성공한 정책금리 수집까지 죽이고 재시도가
정책금리를 다시 받는다. 이름도 어긋난다.

| 태스크 | 제공처 | 계열 |
| --- | --- | --- |
| `collect_fred` | fred | `FEDASSETS_W`·`EAASSETS_W`·`JPASSETS_M` |
| `collect_ecos` | ecos | `KRASSETS_M` |
| `collect_bbk` | bbk | `DEASSETS_W` |
| `collect_boe` | boe | `GBASSETS_Q`·`GBRESERVES_W` |

제공처가 넷이라 태스크도 넷이다. 하나가 실패해도 나머지는 저장되고 재시도도 실패한 제공처만
다시 호출한다. 한 태스크 안에서 `if provider == ...`로 갈리지 않는다.

**스케줄은 KST 월요일 09:20**(`20 9 * * 1` = UTC 일 00:20)이다. `policy_rate_weekly`(09:00)와
같은 요일이되 시각을 벌린다. 그 시점이면 지난주 발표가 전부 끝나 있다 — 연준은 목요일
16:30 ET, 유로시스템·분데스방크는 화요일, BoE 주간 보고는 목요일이다.

**조회 구간은 `policy_rate_weekly`와 같은 순서로 정한다.** `params.observation_start` /
`observation_end`가 있으면 그대로 쓰고, 없으면 run 시각의 KST 날짜가 종료일, `lookback_days`
앞이 시작일이다. 멱등 키가 `(provider, series_id, observation_date)`라 겹쳐 받아도 행이 늘지
않는다.

**`lookback_days`는 800이다. 초안의 45가 틀렸다.** 구현 중 실제로 호출해 보고 바꾼 값이라
근거를 남긴다(2026-08-28).

- 45일 창으로 돌렸더니 `KRASSETS_M`이 **0건**으로 돌아왔다. 한국은행 총자산은 두 달 지연이라
  창이 통째로 마지막 발표(2026-06) 뒤에 놓인다. 게다가 ECOS는 그것을 데이터 없음
  (`INFO-200`)으로 답해서 **예외 없이 조용한 0건**이 된다 — 아무도 모르는 채로 매주 성공한다.
- 영국은 더 심하다. 총자산이 분기 고시에 17개월 지연이고, IADB는 행이 없는 구간에 CSV가
  아니라 HTML 오류 페이지를 HTTP 200으로 준다. 45일 창이면 태스크가 **매주 죽는다.**

**제공처마다 창을 다르게 두는 대신 하나를 넓게 잡았다.** 초안은 영국에만 하한을 두려 했는데,
같은 문제가 한국에도 있다는 것이 실측에서 드러났다. 계열마다 지연을 추적하는 상수를 늘리는
것보다 가장 밀린 계열에 맞춘 창 하나가 짧고, 창이 넓어서 손해 보는 것은 매주 800개 남짓의
멱등 upsert뿐이다. 800일이면 분기 경계가 여덟 번 들어가고 두 지연을 모두 덮는다.

**그러고도 0건이면 태스크를 죽인다.** 창이 이렇게 넓으면 0건은 "발표 전"이 아니라 제공처나
식별자가 바뀌었다는 뜻이다. FRED·ECOS는 계열마다, 분데스방크·BoE는 조회마다 센다. BoE는 한
조회가 계열 둘을 함께 받으므로 그 판정은 **둘 다 비었을 때만** 걸린다.

**그 밖의 실패 판정은 "하루 한 번 도는 확정 수집"과 같다 — 하나라도 실패하면 태스크를 죽인다.**
주 1회라 다음 실행이 같은 창을 다시 보지만 그게 한 주 뒤다. 계열이 둘 이상인 제공처는 항목별로
실패를 모아 이름과 사유를 함께 올린다(`;` 구분). 되돌릴 수 없는 오류(HTTP 4xx, 인증, 식별자)는
`AirflowFailException`으로 즉시 실패하고, 네트워크 오류는 그대로 올려 재시도한다(2회, 1시간 간격).

**백필은 최초 실행 한 번이다.**

```bash
airflow dags trigger central_bank_assets_weekly \
  --conf '{"observation_start": "1999-01-01", "observation_end": "2026-08-27"}'
```

주간 넷이 27년, 월간 둘이 더 길지만 전부 합쳐 1만 행 미만이다. 국채와 달리 **이력 전체가
값어치를 갖는다** — 자산 증가율은 그 자체로 통화정책 국면을 가른다.

## 7. 구현 범위

| 자리 | 무엇 |
| --- | --- |
| `collectors/indicator/fred.py` | `FredSeries`에 3줄. 단위·kind는 계열별 선언이라 새 코드 없다 |
| `collectors/indicator/ecos.py` | 계열 하나. `103Y002`는 지금 쓰는 통계표와 다르므로 계열별 `STAT_CODE`가 필요하다 — `policy_rate` 때 이미 그렇게 바뀌었다 |
| `collectors/indicator/boe.py` | IADB CSV(`CSVF=CN`) 파서를 그대로 쓴다. 계열 Enum만 새로 — 지금 `BoeSeries`는 만기를 갖고 있어 자산 계열과 한 Enum에 못 든다 |
| `collectors/indicator/bbk_statement.py` | **새 수집기.** 기존 `bbk.py`는 `BBSIS`(금리구조) 전용이고 dataflow·키 모양·CSV 형식이 다르다 |
| `airflow/sql/postgres/indicator_observation/` | 기존 upsert 재사용 |
| `dags/central_bank_assets_weekly.py` | 새 DAG. 창 800일, 0건 판정 |
| `migrations/versions/` | `indicator_series.kind` CHECK에 `balance_sheet`·`balance_sheet_item` 추가, 시드 7행 |
| `tests/collectors/` | 계열 Enum과 마스터 시드 대조(`test_indicator_series_catalog.py`), 새 수집기 파싱 테스트 |

**분데스방크 Wochenausweis 수집기의 형태**(실측으로 확인한 것):

- 요청: `https://api.statistiken.bundesbank.de/rest/data/BBBK11/D.TTA032?startPeriod=…&endPeriod=…`
  에 `Accept: text/csv`. `format=csv`도 같은 답을 준다.
- 응답 CSV는 `;` 구분에 BOM이 붙고, 앞 여러 줄이 메타데이터(`Dezimalstellen`·`Dimension`·
  `Einheit`·`Kategorie`)다. 값 줄은 `YYYY-MM-DD;값;플래그` 형태다.
- **차원 이름이 `D`(일별)인데 값은 주 1회만 있다.** 나머지 날짜는 `.`에 플래그
  `Kein Wert vorhanden`이다. 값 없는 행을 저장하지 않는다.
- 헤더의 `Einheit`(=`EURO`)와 `Dimension`(=`Millionen`)을 매 응답마다 대조해서, 제공처가 단위를
  바꾸면 조용히 자릿수가 틀리는 대신 실패하게 한다.
- 인증이 없어 URL에 비밀이 없다. 예외 메시지와 로그에 URL을 그대로 남긴다(`mof.py`·`boe.py`와 같다).

## 8. 한계

- **영국 총자산은 17개월 지연이다**(§2.2). 다른 다섯과 같은 표에 놓을 때 기준일을 반드시
  함께 적는다 — 차트와 표 표기 규칙([CLAUDE.md](../../.claude/CLAUDE.md))의 "마지막 열은
  언제나 기준"이 여기 그대로 적용된다.
- **한국·일본은 월간이라 주 단위 사건에 못 붙는다.** 두 나라에 대해서는 "이번 주에 자산이
  늘었나"를 물을 수 없다.
- **분데스방크는 유로시스템의 부분집합이다.** 둘을 더하지 않는다.
- **증가율의 분모가 계열마다 다른 주기 위에 있다.** §5의 `±10일` 창이 그 차이를 흡수하지만,
  분기 계열(`GBASSETS_Q`)에서는 그 창이 의미가 약하다. 영국은 전년 동분기와 직접 비교한다.
- **BoE 0건 판정은 계열 단위가 아니다.** 한 조회가 총자산과 준비금을 함께 받고 저장 함수가
  합계만 돌려준다. 준비금이 주간이라, 총자산 이력이 통째로 사라져도 준비금이 채워 주면
  판정에 안 걸린다. 계열 단위로 세려면 DAG이 응답을 한 번 더 파싱해야 해서 지금은 두지
  않았다.

## 9. 실측 기록

| 날짜 | 무엇을 확인했나 |
| --- | --- |
| 2026-08-27 | 계열 좌표·주기·단위·이력을 제공처 여섯에 직접 조회해 확정(§3.1) |
| 2026-08-28 | 45일 창으로 수집기 넷을 실제 호출 → `KRASSETS_M` 0건. 창을 800일로 바꿈(§6) |
| 2026-08-28 | 800일 창으로 재확인 → 일곱 계열 전부 값이 돌아옴(115·114·26·25·114·4·114건) |
| 2026-08-28 | 백필(1999-01-01~) 뒤 운영 DB를 읽기 전용으로 확인 → 마스터 7행, 관측값 5,888행, `source_record` 6건 전부 `succeeded`. §5 쿼리를 실제로 돌려 `::date` 캐스트 누락을 잡았다 |
