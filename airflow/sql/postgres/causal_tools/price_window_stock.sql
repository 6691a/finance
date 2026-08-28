-- 국내 종목의 **일별** 종가. 사건 전 구간부터 반응 끝까지.
--
-- 국내 종목 일봉은 `stock_daily`가 아니라 `stock_investor_trade_daily`가 갖는다(수급과 함께).
-- `stock_daily`는 해외 상장 종목용이다.
--
-- 나머지 판단은 `price_window_index.sql`과 같다.
SELECT business_date, close_price AS close
FROM stock_investor_trade_daily
WHERE stock_code = %(code)s
  AND business_date BETWEEN %(start)s AND %(end)s
  AND close_price IS NOT NULL
ORDER BY business_date
