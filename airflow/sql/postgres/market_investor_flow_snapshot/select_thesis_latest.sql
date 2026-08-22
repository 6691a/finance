-- 추론 툴 `market_investor_flows`가 쓴다. 시장마다 `as_of_at` 이전 마지막 스냅샷 하나.
--
-- 금액은 저장된 단위(백만원) 그대로 준다. **툴은 단위를 바꾸지 않는다** — 억원으로 줄이는
-- 것은 사람이 읽는 Slack의 일이고, 모델에게는 컬럼 주석과 같은 단위로 줘야 다른 표와
-- 맞춰 읽을 수 있다.
--
-- **현물 둘만 본다.** 이 테이블에는 2026-08-22부터 선물·옵션·주식선물·ETF도 쌓이는데,
-- 그쪽은 수량이 주가 아니라 계약이고 금액 배율도 다르다. 한 표에 섞으면 읽는 쪽이
-- 더할 수 없는 값을 나란히 본다. 파생을 보여줄 때는 단위를 밝힌 자리를 따로 만든다.
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
  AND market_code IN ('KOSPI', 'KOSDAQ')
ORDER BY market_code, observed_at DESC
