-- 추론 툴 `stock_investor_flows`가 확정 수급 옆에 붙여 주는 **장중 추정치.**
-- 종목마다 `as_of_at` 이전 마지막 스냅샷 하나.
--
-- 확정(`stock_investor_trade_daily`)과 반드시 갈라서 준다. 추정은 장중에 갱신되는 값이고
-- 마감 뒤 확정값과 어긋난다. 같은 칸에 담으면 모델이 그 차이를 모른 채 읽는다.
--
-- 창의 끝은 `collected_at`이다. 이 값을 우리가 언제 받았는지가 event time이다.
SELECT DISTINCT ON (stock_code)
       stock_code,
       business_date,
       source_time_code,
       collected_at,
       foreign_net_buy_qty,
       institution_net_buy_qty,
       total_net_buy_qty
FROM stock_investor_estimate_snapshot
WHERE provider = 'kis'
  AND stock_code = ANY(%(stock_codes)s)
  AND collected_at <= %(as_of_at)s
ORDER BY stock_code, collected_at DESC
