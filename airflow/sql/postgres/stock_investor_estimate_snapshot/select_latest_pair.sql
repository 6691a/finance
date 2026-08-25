-- 종목마다 마지막 갱신 슬롯과 **직전 거래일의 마지막 슬롯**.
--
-- 값은 그 시점까지의 당일 누적이라 슬롯이 클수록 최신이다. 슬롯 코드는 시각이 아니라
-- 회차라 문자열 정렬로는 '10'이 '9'보다 앞에 온다. 숫자로 캐스팅해 정렬한다. KIS가 숫자가
-- 아닌 코드를 보내면 여기서 터진다 — 조용히 엉뚱한 슬롯을 최신으로 고르는 것보다 낫다.
--
-- 직전은 같은 날 앞 슬롯이 아니라 **직전 거래일의 마감 슬롯**이다(시장 수급 쿼리와 같은 이유).
-- 종목명은 instrument 마스터에서 가져온다. 마스터에 없으면 종목코드를 그대로 쓴다.
WITH sessions AS (
    SELECT snapshot.stock_code,
           COALESCE(instrument.name, snapshot.stock_code) AS label,
           snapshot.business_date,
           snapshot.foreign_net_buy_qty,
           snapshot.institution_net_buy_qty,
           snapshot.total_net_buy_qty,
           snapshot.collected_at,
           ROW_NUMBER() OVER (
               PARTITION BY snapshot.stock_code, snapshot.business_date
               ORDER BY snapshot.source_time_code::int DESC
           ) AS recency,
           DENSE_RANK() OVER (
               PARTITION BY snapshot.stock_code
               ORDER BY snapshot.business_date DESC
           ) AS day_rank
    FROM stock_investor_estimate_snapshot AS snapshot
    LEFT JOIN instrument
      ON instrument.ticker = snapshot.stock_code
    WHERE snapshot.business_date >= %s
)
SELECT latest.stock_code,
       latest.label,
       latest.business_date,
       latest.foreign_net_buy_qty,
       latest.institution_net_buy_qty,
       latest.total_net_buy_qty,
       latest.collected_at,
       previous.business_date,
       previous.foreign_net_buy_qty,
       previous.institution_net_buy_qty,
       previous.total_net_buy_qty
FROM sessions AS latest
LEFT JOIN sessions AS previous
       ON previous.stock_code = latest.stock_code
      AND previous.day_rank = 2
      AND previous.recency = 1
WHERE latest.day_rank = 1
  AND latest.recency = 1
ORDER BY latest.stock_code
