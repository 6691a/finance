-- 브리핑이 증시자금(예탁금·신용융자·미수금)의 최신 영업일과 그 전일을 읽는다.
-- 전일대비와 등락률은 세 항목 모두 전일 행과 비교해 파이썬이 계산한다.
-- API가 준 customer_deposit_change는 전일 행이 없는 수집 첫날의 대비책으로만 쓴다.
-- 두 행의 날짜를 모두 쓴다. 수집이 매일 도는 것이 아니라 둘째 행이 전일이 아닐 수 있고,
-- 표는 그 날짜를 직전 값 옆에 적는다.
SELECT business_date,
       customer_deposit,
       customer_deposit_change,
       credit_loan_balance,
       unsettled_amount
FROM krx_market_funds_daily
ORDER BY business_date DESC
LIMIT 2
