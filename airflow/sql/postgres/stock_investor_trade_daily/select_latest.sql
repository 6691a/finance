-- 종목마다 마지막 확정 거래일 하나. 장 마감 뒤 리포트가 읽는다.
-- 등락은 직전 거래일 종가와 비교한다. quote_bar와 달리 이 테이블에는 previous_close가 없어
-- LATERAL로 직전 행을 붙인다. 상장 첫날처럼 직전이 없으면 NULL이고 렌더링이 '-'로 그린다.
-- 종목명은 instrument 마스터에서 가져온다. 마스터에 없으면 종목코드를 그대로 쓴다.
SELECT DISTINCT ON (daily.stock_code)
       daily.stock_code,
       COALESCE(instrument.name, daily.stock_code) AS label,
       daily.business_date,
       daily.close_price,
       previous.close_price AS previous_close,
       daily.foreign_net_buy_qty,
       daily.institution_net_buy_qty,
       daily.individual_net_buy_qty,
       daily.securities_net_buy_qty,
       daily.investment_trust_net_buy_qty,
       daily.private_equity_net_buy_qty,
       daily.bank_net_buy_qty,
       daily.insurance_net_buy_qty,
       daily.merchant_bank_net_buy_qty,
       daily.pension_fund_net_buy_qty
FROM stock_investor_trade_daily AS daily
LEFT JOIN instrument
  ON instrument.ticker = daily.stock_code
LEFT JOIN LATERAL (
    SELECT prev.close_price
    FROM stock_investor_trade_daily AS prev
    WHERE prev.stock_code = daily.stock_code
      AND prev.business_date < daily.business_date
    ORDER BY prev.business_date DESC
    LIMIT 1
) AS previous ON TRUE
WHERE daily.business_date >= %s
ORDER BY daily.stock_code, daily.business_date DESC
