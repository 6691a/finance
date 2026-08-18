-- 통화마다 마지막 고시와 직전 고시일의 마지막 회차를 함께 준다.
-- 하나은행은 하루에 여러 회차를 고시하므로 "전일 대비"는 회차가 아니라 날짜로 갈라야 한다.
-- 직전 고시일이 없으면(첫 수집) 두 번째 값은 NULL이고 렌더링이 등락을 비운다.
WITH ranked AS (
    SELECT currency,
           date,
           round,
           exchange_standard_rate,
           DENSE_RANK() OVER (PARTITION BY currency ORDER BY date DESC) AS day_rank,
           ROW_NUMBER() OVER (PARTITION BY currency, date ORDER BY round DESC) AS round_rank
    FROM exchange_rate
    WHERE currency = ANY(%s)
      AND date >= %s
)
SELECT latest.currency,
       latest.date,
       latest.round,
       latest.exchange_standard_rate,
       previous.exchange_standard_rate
FROM ranked AS latest
LEFT JOIN ranked AS previous
       ON previous.currency = latest.currency
      AND previous.day_rank = 2
      AND previous.round_rank = 1
WHERE latest.day_rank = 1
  AND latest.round_rank = 1
ORDER BY latest.currency
