-- 추론 툴 `event_surprises`가 쓴다. 종목 하나의 최근 서프라이즈 판정을 최신 발표부터 준다.
--
-- 창의 끝은 `created_at`으로 자른다 — 이 행이 처음 들어온 시각이 "그때 알 수 있었던 것"이다.
-- 판정 DAG(매시 :45)가 장전 추론(08:35)을 넘겨 돌면 그날 장전에는 전 판정까지만 보인다.
-- `announced_at`으로 자르지 않는 이유는 그것이 발표 시각이라 판정이 나기 전에도 과거이기
-- 때문이다. 아직 판정하지 않은 발표를 판정된 것처럼 보이게 하면 안 된다.
--
-- 판정은 첫 성공본 불변이라 이 행은 나중에 바뀌지 않는다. 그래서 updated_at을 보지 않는다.
--
-- 주석에 퍼센트 기호를 쓰지 않는다. psycopg가 주석까지 훑어 플레이스홀더로 센다.
SELECT event_type,
       period_key,
       metric,
       expected_value,
       expectation_count,
       actual_value,
       surprise_pct,
       verdict,
       announced_at
FROM stock_event_outcome
WHERE stock_code = %(stock_code)s
  AND created_at <= %(as_of_at)s
ORDER BY announced_at DESC, id DESC
LIMIT %(limit)s
