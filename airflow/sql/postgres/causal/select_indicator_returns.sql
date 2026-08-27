-- 금리 시계열의 실현 변화. 대상 주 안의 변화와 그 뒤 T+1·T+5다.
--
-- **단위가 bp다.** 다른 셋과 갈라 둔 이유가 그것이다 — `KTB10Y` 4.239 → 4.313은 +1.75%가
-- 아니라 +7.4bp이고, 퍼센트로 내면 KOSPI의 +10.77%와 한 칸에 들어가 크기 비교가 조용히
-- 무의미해진다. 저장하는 쪽이 `market_causal_path.return_unit`에 `basis_point`를 넣는다.
--
-- **`(provider, series_id)`로 건다.** `series_id`는 제공처 안에서만 고유해서 하나로 거는
-- 쿼리는 제공처가 늘어나면 조용히 틀린다(저장소 규칙).
--
-- `as_of_at` cutoff를 안 거는 이유와 `scan_end`를 넉넉히 잡는 이유는
-- `select_index_returns.sql`과 같다.
--
-- **정책금리는 계단 함수다.** 한 주 내내 같은 값이면 변화가 0이고 그것이 사실이다 —
-- 값이 없는 것과 다르므로 NULL로 바꾸지 않는다.
WITH bars AS (
    SELECT series_id AS code, observation_date AS business_date, value
    FROM indicator_observation
    WHERE provider = %(provider)s
      AND series_id = ANY(%(codes)s)
      AND observation_date BETWEEN %(week_start)s AND %(scan_end)s
      AND value IS NOT NULL
),
week_edges AS (
    SELECT code,
           min(business_date) AS first_day,
           max(business_date) AS last_day
    FROM bars
    WHERE business_date <= %(week_end)s
    GROUP BY code
),
after AS (
    SELECT bars.code,
           bars.value,
           row_number() OVER (PARTITION BY bars.code ORDER BY bars.business_date) AS rn
    FROM bars
    JOIN week_edges ON week_edges.code = bars.code
    WHERE bars.business_date > week_edges.last_day
)
SELECT week_edges.code,
       round((week_last.value - week_first.value) * 100, 4) AS week_change,
       round((t1.value - week_last.value) * 100, 4) AS t1_change,
       round((t5.value - week_last.value) * 100, 4) AS t5_change
FROM week_edges
JOIN bars AS week_first
  ON week_first.code = week_edges.code AND week_first.business_date = week_edges.first_day
JOIN bars AS week_last
  ON week_last.code = week_edges.code AND week_last.business_date = week_edges.last_day
LEFT JOIN after AS t1 ON t1.code = week_edges.code AND t1.rn = 1
LEFT JOIN after AS t5 ON t5.code = week_edges.code AND t5.rn = 5
ORDER BY week_edges.code
