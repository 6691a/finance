-- 시장마다 마지막 수급 스냅샷 하나. 금액은 저장된 백만원 단위 그대로 주고 렌더링이 억원으로 줄인다.
SELECT DISTINCT ON (market_code)
       market_code,
       observed_at,
       foreign_net_buy_amount,
       institution_net_buy_amount,
       individual_net_buy_amount
FROM market_investor_flow_snapshot
WHERE observed_at >= %s
ORDER BY market_code, observed_at DESC
