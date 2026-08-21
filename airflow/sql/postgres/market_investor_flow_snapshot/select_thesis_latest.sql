-- 추론 툴 `market_investor_flows`가 쓴다. 시장마다 `as_of_at` 이전 마지막 스냅샷 하나.
--
-- 금액은 저장된 단위(백만원) 그대로 준다. **툴은 단위를 바꾸지 않는다** — 억원으로 줄이는
-- 것은 사람이 읽는 Slack의 일이고, 모델에게는 컬럼 주석과 같은 단위로 줘야 다른 표와
-- 맞춰 읽을 수 있다.
--
-- 창의 끝은 `observed_at`이다. 이 테이블은 장중 누적치를 분 단위로 쌓으므로 관측 시각이
-- 곧 event time이다.
SELECT DISTINCT ON (market_code)
       market_code,
       observed_at,
       foreign_net_buy_amount,
       institution_net_buy_amount,
       individual_net_buy_amount,
       pension_fund_net_buy_qty,
       investment_trust_net_buy_qty
FROM market_investor_flow_snapshot
WHERE observed_at >= %(window_start)s
  AND observed_at <= %(as_of_at)s
ORDER BY market_code, observed_at DESC
