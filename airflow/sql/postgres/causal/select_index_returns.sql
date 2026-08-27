-- 국내 지수의 실현 등락. 대상 주 안의 변화와 그 뒤 T+1·T+5다.
--
-- **`as_of_at` cutoff를 걸지 않는다.** cutoff는 "그 시점에 알 수 있었던 것"을 자르는 장치인데
-- 실현 등락은 **일부러 미래를 보는 값**이다. 게다가 반응 주에 휴장이 있으면 T+5가 cutoff
-- 뒤로 밀려 그 대상이 통째로 빠진다. 근거(문서·공시)만 cutoff를 걸고 가격은 확정값을 본다.
--
-- 조회 끝(`scan_end`)을 반응 주 금요일이 아니라 넉넉히 잡는 이유도 같다 — 휴장이 겹쳐도
-- T+5가 항상 잡혀야 한다. 저장 대상은 어디까지나 T+1·T+5 두 값이다.
--
-- 반환 단위는 percent다(`market_causal_path.return_unit`). 금리는 bp라
-- `select_indicator_returns.sql`이 따로 있다.
WITH bars AS (
    SELECT symbol AS code, business_date, close
    FROM index_daily
    WHERE symbol = ANY(%(codes)s)
      AND business_date BETWEEN %(week_start)s AND %(scan_end)s
      AND close IS NOT NULL
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
           bars.close,
           row_number() OVER (PARTITION BY bars.code ORDER BY bars.business_date) AS rn
    FROM bars
    JOIN week_edges ON week_edges.code = bars.code
    WHERE bars.business_date > week_edges.last_day
)
SELECT week_edges.code,
       round((week_last.close / NULLIF(week_first.close, 0) - 1) * 100, 4) AS week_change,
       round((t1.close / NULLIF(week_last.close, 0) - 1) * 100, 4) AS t1_change,
       round((t5.close / NULLIF(week_last.close, 0) - 1) * 100, 4) AS t5_change
FROM week_edges
JOIN bars AS week_first
  ON week_first.code = week_edges.code AND week_first.business_date = week_edges.first_day
JOIN bars AS week_last
  ON week_last.code = week_edges.code AND week_last.business_date = week_edges.last_day
LEFT JOIN after AS t1 ON t1.code = week_edges.code AND t1.rn = 1
LEFT JOIN after AS t5 ON t5.code = week_edges.code AND t5.rn = 5
ORDER BY week_edges.code
