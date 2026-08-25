-- **현물 둘만 본다.** 이 테이블에는 2026-08-22부터 선물·옵션·주식선물·ETF도 쌓이는데,
-- 그쪽은 수량이 주가 아니라 계약이고 금액 배율도 다르다. 한 표에 섞으면 읽는 쪽이
-- 더할 수 없는 값을 나란히 본다.
--
-- 시장마다 **마지막 스냅샷과 직전 거래일의 마감 스냅샷**을 함께 준다.
--
-- 직전을 "몇 분 전 스냅샷"으로 잡지 않는다. 이 값은 그날의 누적이라 같은 날 앞 슬롯과
-- 비교하면 "장중에 얼마나 더 샀나"가 되고, 표가 말하려는 "어제와 견줘 어떤가"와 뜻이
-- 다르다. 그래서 세션(KST 날짜)마다 마지막 스냅샷을 고른 뒤 그중 직전 세션을 붙인다.
--
-- 금액은 저장된 백만원 단위 그대로 주고 렌더링이 억원으로 줄인다.
WITH sessions AS (
    SELECT market_code,
           observed_at,
           (observed_at AT TIME ZONE 'Asia/Seoul')::date AS session_date,
           foreign_net_buy_amount,
           institution_net_buy_amount,
           individual_net_buy_amount,
           ROW_NUMBER() OVER (
               PARTITION BY market_code, (observed_at AT TIME ZONE 'Asia/Seoul')::date
               ORDER BY observed_at DESC
           ) AS recency
    FROM market_investor_flow_snapshot
    WHERE observed_at >= %s
      AND market_code IN ('KOSPI', 'KOSDAQ')
), closing AS (
    SELECT sessions.*,
           DENSE_RANK() OVER (PARTITION BY market_code ORDER BY session_date DESC) AS day_rank
    FROM sessions
    WHERE recency = 1
)
SELECT latest.market_code,
       latest.observed_at,
       latest.foreign_net_buy_amount,
       latest.institution_net_buy_amount,
       latest.individual_net_buy_amount,
       previous.session_date,
       previous.foreign_net_buy_amount,
       previous.institution_net_buy_amount,
       previous.individual_net_buy_amount
FROM closing AS latest
LEFT JOIN closing AS previous
       ON previous.market_code = latest.market_code
      AND previous.day_rank = 2
WHERE latest.day_rank = 1
ORDER BY latest.market_code
