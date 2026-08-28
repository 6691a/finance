-- 금리 시계열의 **일별** 값. 사건 전 구간부터 반응 끝까지.
--
-- **여기서는 수준 값을 그대로 준다.** 실현 등락(`select_indicator_returns.sql`)은 bp 차이로
-- 접어 주지만, 선반영을 보려면 4.239에서 4.313으로 오른 경로 자체가 필요하다.
--
-- **`(provider, series_id)`로 건다.** `series_id`는 제공처 안에서만 고유해서 하나로 거는
-- 쿼리는 제공처가 늘어나면 조용히 틀린다(저장소 규칙).
--
-- **정책금리는 계단 함수다.** 한 주 내내 같은 값이면 그것이 사실이고, 값이 없는 것과 다르다.
SELECT observation_date AS business_date, value AS close
FROM indicator_observation
WHERE provider = %(provider)s
  AND series_id = %(code)s
  AND observation_date BETWEEN %(start)s AND %(end)s
  AND value IS NOT NULL
ORDER BY observation_date
