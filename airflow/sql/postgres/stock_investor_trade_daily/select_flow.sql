-- 종목 하나의 일별 투자자 순매수. 누가 사고팔았나를 본다.
--
-- 수량(qty)과 대금(amount)을 섞지 않는다. **투자자별 대금은 백만원 단위이고 누적 거래대금은
-- 원 단위다**(`kis_investor_flow.py`). 여기서는 수량만 돌려준다.
SELECT
    business_date,
    close_price,
    foreign_net_buy_qty,
    institution_net_buy_qty,
    individual_net_buy_qty,
    pension_fund_net_buy_qty,
    investment_trust_net_buy_qty
FROM stock_investor_trade_daily
WHERE stock_code = %s
  AND (%s::date IS NULL OR business_date >= %s)
  AND (%s::date IS NULL OR business_date <= %s)
ORDER BY business_date DESC
LIMIT %s
