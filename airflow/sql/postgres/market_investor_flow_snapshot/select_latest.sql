-- **현물 둘만 본다.** 이 테이블에는 2026-08-22부터 선물·옵션·주식선물·ETF도 쌓이는데,
-- 그쪽은 수량이 주가 아니라 계약이고 금액 배율도 다르다. 한 표에 섞으면 읽는 쪽이
-- 더할 수 없는 값을 나란히 본다. 파생을 보여줄 때는 단위를 밝힌 자리를 따로 만든다.
--
-- 시장마다 마지막 수급 스냅샷 하나. 금액은 저장된 백만원 단위 그대로 주고 렌더링이 억원으로 줄인다.
SELECT DISTINCT ON (market_code)
       market_code,
       observed_at,
       foreign_net_buy_amount,
       institution_net_buy_amount,
       individual_net_buy_amount
FROM market_investor_flow_snapshot
WHERE observed_at >= %s
  AND market_code IN ('KOSPI', 'KOSDAQ')
ORDER BY market_code, observed_at DESC
