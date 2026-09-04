# 코스피 일일 전망 — 언제 무엇을 보고 무엇을 정하나

- 날짜: 2026-09-04
- 상태: **동결 중.** 운영 기동은 2026-09-03이고 프롬프트 판 4를 20영업일 동안 안 고친다.
- 무엇: **성적을 재는 방법과 그 결과로 내리는 결정.** 설계는
  [kospi-forecast.md](kospi-forecast.md)에 있고 이 문서는 "돌고 난 뒤"만 갖는다.
- 누가 읽나: 이 기능을 처음 보는 세션. **이 문서 하나로 채점이 끝나게 썼다** — 아래 SQL은
  복사해서 바로 돌아간다.

## 0. 왜 기다리나

**하루 이틀 성적으로는 아무것도 못 정한다.** 방향 적중률의 표준오차가 닷새 표본에서
22퍼센트포인트다. 60%가 나와도 40%가 나와도 우연과 구별되지 않는다.

그런데 이것을 모르고 판을 계속 고치면 표본이 판마다 쪼개져서 **영원히 아무것도 못 잰다.**
옛 시장 추론이 정확히 그렇게 죽었다 — 9일 동안 프롬프트 판이 12개 올라가 판당 표본이
4~12건이었고, 그 상태로는 어떤 변경이 좋았는지 나빴는지 말할 수 없었다.

그래서 **판을 얼리고 표본을 모은다.** 20영업일이 첫 판정 시점이다.

**동결이 막는 것은 프롬프트 문장과 모델이 보는 것뿐이다.** 버그는 코드로 고친다. 어느 쪽인지
헷갈리면 이렇게 가른다 — *고친 뒤에 모델의 답이 달라질 수 있나?* 그렇다면 동결 대상이다.

| 해도 되는 것 | 하면 안 되는 것 |
| --- | --- |
| import 경로·성능·리팩터링 | 프롬프트 문장 |
| 원장 칸 추가, 로그 | 툴 상한·요인 목록·툴 인자 |
| 채점 쿼리·대시보드 | 모델 교체, 온도·타임아웃 |
| 크래시 수정 | 검증 규칙(무엇을 버리나) |

**부득이 고쳤으면 `PROMPT_VERSION`을 올리고 그 날짜를 이 문서에 적는다.** 그러면 20영업일
시계가 거기서 다시 시작한다. 숨기면 원장이 거짓말을 한다.

## 1. 기준선 — 이 숫자 없이는 아무 성적도 못 읽는다

**절대값만 보면 "적중 60%가 좋은가"에 답할 수 없다.** 코스피는 그냥 매일 상승이라고 불러도
60%가 나온다.

실측(2026-09-02, `index_daily`의 KOSPI 132거래일):

| 기준선 | 값 | 뜻 |
| --- | --- | --- |
| **항상 상승** | **61.4%** | 매일 `up`이라고만 해도 이만큼 맞는다. **이것을 못 넘으면 정보가 0이다** |
| 전일 방향 지속 | 50.4% | 어제 오른 날은 `up`, 내린 날은 `down` |
| 하락 비율 | 38.6% | 모델이 `down`이라 한 날의 정밀도를 이것과 견준다 |
| \|등락률\| 중앙값 | 2.27% | 폭이 이보다 훨씬 좁으면 근거 없는 자신감이다 |
| \|등락률\| p90 | 6.49% | |
| 상승일 중앙값 | +2.11% | |
| 하락일 중앙값 | −3.25% | |

기준선은 **채점 표본이 아니라 긴 이력에서** 잰다. 스무 날의 상승 비율은 그 자체가 우연이다.

```sql
-- 기준선을 다시 잴 때. 표본을 늘리려면 LIMIT 없이 전 구간을 본다.
WITH moves AS (
    SELECT business_date,
           (close - lag(close) OVER (ORDER BY business_date))
           / lag(close) OVER (ORDER BY business_date) * 100 AS pct
    FROM index_daily WHERE symbol = 'KOSPI' AND provider = 'kis'
)
SELECT count(*) AS 거래일,
       round(count(*) FILTER (WHERE pct > 0) * 100.0 / count(*), 1) AS 상승비율,
       round(count(*) FILTER (WHERE pct < 0) * 100.0 / count(*), 1) AS 하락비율,
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY abs(pct))::numeric, 2) AS 폭중앙값,
       round(percentile_cont(0.9) WITHIN GROUP (ORDER BY abs(pct))::numeric, 2) AS 폭p90
FROM moves WHERE pct IS NOT NULL;
```

**전체 적중률을 성적으로 쓰지 않는다.** 61.4%가 상승인 시장에서 "적중 60%"는 정보가 없다는
뜻이다. 대신 **하락 호출의 정밀도**를 38.6%와 견준다 — 소수 클래스를 맞히는 것만이 실력이다.

## 2. 언제 무엇을 보나

| 시점 | 무엇 | 결정 |
| --- | --- | --- |
| **매일, 첫 주** | 돌긴 도나. 툴 상한. 잘림·약한 답·버린 이유 | 이상하면 코드로 고친다 |
| **20영업일** | **관계 수렴.** 요인별 인용 대비 적중. 메모 회전 | 요인 목록을 손볼지 |
| 60영업일 | 하락 호출 정밀도 대 38.6% | 프롬프트를 다음 판으로 올릴지 |
| 120영업일 | 전체 방향 대 61.4% | **접을지 정한다** |

20영업일은 2026-09-03 기동 기준으로 **대략 10월 초**다. 추석 연휴만큼 밀린다.

**20영업일이 첫 진짜 판정인 이유는 방향 적중률 때문이 아니다.** 그때도 채점이 60건 안팎이라
방향은 여전히 노이즈다. 볼 것은 **관계 수렴**이고, 그건 훨씬 빨리 드러난다(§4).

## 3. 매일 보는 것 — 돌긴 도나

```sql
-- 오늘 무엇이 나왔나. 슬롯 셋 + 관찰 하나가 있어야 한다.
SELECT run_date, slot, prompt_version AS 판, direction, expected_change_pct AS 기대,
       band_pct AS 폭, so_far_pct AS 그때까지, actual_change_pct AS 실제,
       hit AS 방향, within_band AS 폭적중,
       jsonb_array_length(reasons) AS 이유, rejected_reasons AS 버림, weak AS 약한답
FROM kospi_forecast
WHERE run_date >= current_date - 7
ORDER BY run_date DESC, slot;
```

```sql
-- 원장. 상한에 붙나, 잘렸나, 실패한 대화가 있나.
SELECT run_date, kind, slot, status, prompt_version AS 판,
       tool_rounds AS 왕복, tool_calls AS 툴, truncated AS 잘림, rejected AS 버림,
       observations_written AS 관찰, memories_written AS 메모신규,
       memories_kept AS 유지, memories_dropped AS 삭제,
       prompt_tokens, completion_tokens, left(coalesce(error, ''), 60) AS 오류
FROM kospi_llm_run
WHERE run_date >= current_date - 7
ORDER BY run_date DESC, id;
```

**빨간 신호 여섯.** 하나라도 보이면 그날 본다.

| 무엇 | 왜 문제인가 |
| --- | --- |
| `status = 'running'`이 남음 | 원장을 닫지 못하고 죽었다. "안 돌았다"와 구별이 안 된다 |
| `truncated = true` | 조사가 왕복 상한에서 잘렸다. 스스로 끝낸 것과 다르다 |
| `tool_calls`가 상한(25)에 붙음 | 요인을 다 못 본다. 첫날 15/15가 그래서 25로 올렸다 |
| `weak = true` | 검증이 이유를 전부 버렸다. Slack 머리도 `⚠ 근거 없는 답`이 된다 |
| `rejected`가 갑자기 늘어남 | 모델이 규칙을 못 맞추고 있다 |
| 슬롯이 빠짐 | 준비 검사에 걸렸거나 DAG이 죽었다 |

```sql
-- 이유의 출처. **`⚠ 출처없음`은 0이어야 한다**(판 4부터).
SELECT f.prompt_version AS 판, count(*) AS 이유,
       count(*) FILTER (WHERE r->>'factor' IS NOT NULL)    AS 요인,
       count(*) FILTER (WHERE r->>'memory_id' IS NOT NULL) AS 메모,
       count(*) FILTER (WHERE r->>'factor' IS NULL
                          AND r->>'memory_id' IS NULL)     AS 출처없음
FROM kospi_forecast AS f, jsonb_array_elements(f.reasons) AS r
GROUP BY 1 ORDER BY 1;
```

**`factor`가 NULL인 것 자체는 정상이다** — 메모를 인용한 이유가 그렇다. 0이어야 하는 것은
**둘 다 빈 것**이고, 판 3에 4건 있었고 판 4부터 0이다.

## 4. 20영업일 — 관계가 수렴하나

**이것이 이 설계의 핵심 가정이다.** "매일 관찰을 쌓으면 요인별 가중치가 실제 예고력에
수렴한다"가 맞는지를 본다. 아니면 나머지는 볼 것도 없다.

실측(2026-09-03, 128일 lead-lag)에서 **전날 값이 다음 날 코스피 방향을 맞힌 비율**:

| 요인 | 예고력 | 수렴 기대 |
| --- | --- | --- |
| **SOX**(필라델피아 반도체) | **68.8%** | **가중치가 뚜렷한 양수로** |
| USDKRW | 51.9% | 0 근처로 |
| US10Y | 51.2% | 0 근처로 |
| WTI | 50.0% | 0 근처로 |

**SOX만 기준선을 넘었다.** 관계 그래프가 작동한다면 스무 날 뒤 표에서 SOX가 위로,
나머지 셋이 0 근처로 가 있어야 한다. **아무것도 안 움직이면 가정이 틀린 것이다.**

Neo4j를 직접 읽는다(운영은 `bolt://neo4j:7687`이라 **컨테이너 안에서만** 닿는다).

```cypher
// 요인별 관측 수와 부호 합. 부호 합이 가중치의 방향이다.
MATCH (f:Factor)-[o:OBSERVED]->(:Index {code: 'KOSPI'})
RETURN f.code AS 요인, count(o) AS 관측,
       sum(CASE o.sign WHEN 'same' THEN o.strength ELSE -o.strength END) AS 부호합,
       min(o.date) AS 처음, max(o.date) AS 최근
ORDER BY 관측 DESC;
```

**부호 합은 가중치가 아니다.** 실제 가중치는 코드가 반감기 5일로 감쇠 평균해 만든다
(`kospi.domain.relation_weight`). 궤적까지 보려면 `notebooks/kospi_relations.py`가 날마다
다시 계산해 준다.

**함께 볼 것 둘.**

- **관측 커버리지.** 요인 15개 중 몇 개가 실제로 관측되나. 2026-09-03 기준으로 8개만 관측이
  있었고 7개(US10Y·DXY·KTB10Y·KRBASE·SP500·NASDAQ·VIX 일부)가 0이었다. 스무 날 뒤에도 0인
  요인이 많으면 **모델이 조용한 날에 게으른 것**이거나 그 요인이 실제로 안 움직인 것이다.
- **구성 종목이 표를 지배하나.** `SAMSUNG`·`SK_HYNIX`는 코스피 시총의 30% 안팎이라 **거의
  항상 같은 방향**이다. 첫날 부호 합 1·2위가 그 둘이었다(+14, +11). 이것이 §5의 판정으로
  이어진다.

```cypher
// 메모 회전. 만들기만 하고 안 지우면 프롬프트가 계속 부푼다.
MATCH (m:Memory)
RETURN count(*) AS 전체,
       count(CASE WHEN m.retired_on IS NULL THEN 1 END) AS 활성,
       count(CASE WHEN m.retire_reason = 'dropped' THEN 1 END) AS 모델이내림,
       count(CASE WHEN m.retire_reason = 'expired' THEN 1 END) AS 나이만료,
       count(CASE WHEN m.retire_reason = 'unreviewed' THEN 1 END) AS 미검토;
```

- 활성이 상한 20에 붙으면 새 메모가 조용히 버려진다(`memories_rejected`가 센다).
- `모델이내림`이 0이고 `나이만료`만 쌓이면 **모델이 판정을 안 하고 코드가 대신 치우는 것**이다.

## 5. 20영업일 — 요인이 값을 하나

**말로 정할 수 없는 질문 하나가 여기서 갈린다: 삼성전자·SK하이닉스를 요인에서 뺄 것인가.**

빼야 한다는 쪽: 둘이 코스피와 **동시에** 정해져서 장전에는 오늘 값을 못 본다. 어제 삼성은
이미 어제 코스피 안에 들어 있으니 내일에 대해 말해 주는 게 없다. 그런데 거의 항상 같은
방향이라 가중치는 최대치에 붙어 표를 지배한다.

두어야 한다는 쪽: 반도체가 실제로 한국 시장을 끈다. 시총 30%가 움직이면 지수가 움직이는
것은 산수가 아니라 인과이기도 하다.

**둘 다 논리로는 서 있다. 데이터가 답한다.**

```sql
-- 요인을 인용한 전망이 실제로 더 맞았나.
-- **주의: 표본이 작으면 이 표는 "그 요인이 어느 전망에 실렸나"만 잰다.**
-- hit은 전망 단위 값이라, 맞은 전망 하나에 들어간 요인은 전부 100%가 된다.
-- 요인마다 20건은 쌓여야 읽을 수 있다(하루 3전망이면 20영업일에 대략 그만큼).
SELECT r->>'factor' AS 요인,
       count(*) AS 인용,
       round(avg(CASE WHEN f.hit THEN 1.0 ELSE 0 END) * 100, 1) AS 방향적중률,
       round(avg(CASE WHEN f.within_band THEN 1.0 ELSE 0 END) * 100, 1) AS 폭적중률
FROM kospi_forecast AS f, jsonb_array_elements(f.reasons) AS r
WHERE f.graded_at IS NOT NULL AND r->>'factor' IS NOT NULL
GROUP BY 1 HAVING count(*) >= 20
ORDER BY 3 DESC;
```

**판정 규칙**: `SAMSUNG`·`SK_HYNIX`를 인용한 전망의 방향 적중이 `SOX`를 인용한 것보다 **낮으면
요인에서 뺀다.** 높거나 같으면 그대로 둔다 — 그때는 제 짐작이 틀린 것이다.

빼기로 하면 따라오는 것: `FACTORS`에서 둘 제거, 기존 엣지 삭제, `PROMPT_VERSION` 올리고
시계 재시작. **20영업일 중간에 하지 않는다.**

## 6. 60·120영업일 — 접을지 정한다

```sql
-- 하락 호출의 정밀도. 기준선은 하락 비율 38.6%다.
SELECT direction AS 호출,
       count(*) AS 건수,
       count(*) FILTER (WHERE hit) AS 맞음,
       round(count(*) FILTER (WHERE hit) * 100.0 / count(*), 1) AS 정밀도
FROM kospi_forecast WHERE graded_at IS NOT NULL
GROUP BY 1 ORDER BY 1;
```

**`down` 정밀도가 38.6%를 못 넘으면 하락 호출에 정보가 없다.** 모델이 하락이라 한 날이
아무 날과 다르지 않다는 뜻이다.

```sql
-- 폭이 뜻을 갖나. 60~80%가 목표대다.
SELECT count(*) AS 채점,
       round(avg(CASE WHEN within_band THEN 1.0 ELSE 0 END) * 100, 1) AS 폭적중률,
       round(avg(band_pct), 2) AS 평균폭,
       round(avg(abs(actual_change_pct - expected_change_pct)), 2) AS 평균오차,
       round(avg(actual_change_pct - expected_change_pct), 2) AS 치우침
FROM kospi_forecast WHERE graded_at IS NOT NULL;
```

- **폭 적중률이 95%면 그것도 실패다.** 폭을 넓게만 부르면 구간이 아무 말도 안 한다.
- `치우침`이 양수면 과소추정(실제가 기대보다 크다), 음수면 과대추정이다. 그것이 프롬프트를
  고칠 방향이다.

**접는 조건.** 120영업일에 전체 방향 적중이 **61.4% 이하**이고 폭 적중률이 60% 미만이면
**전망을 접고 관찰만 남긴다.** 관계 그래프는 전망 없이도 값어치가 있다 — "무엇이 코스피를
움직였나"의 누적 기록이다.

## 7. 도구

**아래 셋은 `notebooks/`에 있고 `.gitignore` 대상이라 이 저장소에 없다.** 이 문서의 SQL만으로
채점이 되게 써 뒀으므로 스크립트가 없어도 막히지 않는다.

| 파일 | 무엇 | 대체 |
| --- | --- | --- |
| `kospi_score.py` | 기준선과 나란히 놓은 성적표 | §1·§6의 SQL |
| `kospi_relations.py` | 날짜별 가중치 궤적, 커버리지, 메모 회전 | §4의 Cypher(궤적은 못 낸다) |
| `kospi_forecast_debug.ipynb` | 프롬프트·툴 호출·결과를 눈으로 보는 노트북 | 없음 |

**노트북을 커밋하지 않는 이유는 출력에 앱키와 시세 응답이 남기 때문이다.** `.py` 셋은 출력을
안 담으므로 저장소로 옮길 수 있다 — 다음 세션이 판단한다.

## 8. 이 문서를 고쳐야 하는 때

- **판을 올렸으면** 그 날짜와 이유를 §0에 적고 20영업일 시계를 다시 시작한다.
- **기준선을 다시 쟀으면** §1의 표를 갈아 끼우고 잰 날짜를 적는다. 어림값을 적지 않는다.
- **판정을 내렸으면**(요인 제거, 접기, 계속) 무엇을 보고 그렇게 정했는지 숫자와 함께 남긴다.
  다음 사람이 같은 질문을 다시 열지 않게 하는 것이 이 문서의 절반이다.
