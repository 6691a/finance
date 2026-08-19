-- 브리핑이 증시자금(예탁금·신용융자·미수금)의 최신 영업일과 그 전일을 읽는다.
-- 고객예탁금 전일대비는 API가 준 값(customer_deposit_change)을 쓰고,
-- 신용융자·미수금의 전일대비는 전일 행과 비교해 파이썬이 계산한다.
SELECT business_date,
       customer_deposit,
       customer_deposit_change,
       credit_loan_balance,
       unsettled_amount
FROM krx_market_funds_daily
ORDER BY business_date DESC
LIMIT 2
