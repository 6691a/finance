-- 시장마다 마지막 등락 종목 수 스냅샷과 **직전 거래일의 마감 스냅샷**. 상한가·하한가는
-- 브리핑에 넣지 않는다.
--
-- 직전을 같은 날 앞 슬롯으로 잡지 않는 이유는 시장 수급 쿼리와 같다 — 표가 말하려는 것은
-- "어제와 견줘 상승 종목이 늘었나"이지 "한 시간 전보다 늘었나"가 아니다.
WITH sessions AS (
    SELECT symbol,
           observed_at,
           (observed_at AT TIME ZONE 'Asia/Seoul')::date AS session_date,
           rising_count,
           unchanged_count,
           falling_count,
           ROW_NUMBER() OVER (
               PARTITION BY symbol, (observed_at AT TIME ZONE 'Asia/Seoul')::date
               ORDER BY observed_at DESC
           ) AS recency
    FROM market_movement_snapshot
    WHERE observed_at >= %s
), closing AS (
    SELECT sessions.*,
           DENSE_RANK() OVER (PARTITION BY symbol ORDER BY session_date DESC) AS day_rank
    FROM sessions
    WHERE recency = 1
)
SELECT latest.symbol,
       latest.observed_at,
       latest.rising_count,
       latest.unchanged_count,
       latest.falling_count,
       previous.session_date,
       previous.rising_count,
       previous.unchanged_count,
       previous.falling_count
FROM closing AS latest
LEFT JOIN closing AS previous
       ON previous.symbol = latest.symbol
      AND previous.day_rank = 2
WHERE latest.day_rank = 1
ORDER BY latest.symbol
