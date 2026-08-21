-- 추론 툴 `market_funds`가 쓴다. 증시자금 종합의 최근 며칠치.
--
-- 고객예탁금과 신용융자잔고는 "살 돈이 늘고 있나 줄고 있나"를 보는 값이라 한 시점보다
-- 추이가 중요하다. 그래서 최신 한 행이 아니라 며칠치를 준다.
--
-- **cutoff는 `created_at`이다.** 영업일 값이 그날 저녁에 들어오므로 `business_date`로만
-- 걸면 아직 모르는 값을 본 것으로 읽는다.
SELECT business_date,
       index_close,
       index_change,
       customer_deposit,
       customer_deposit_change,
       credit_loan_balance,
       unsettled_amount,
       turnover_ratio
FROM krx_market_funds_daily
WHERE created_at <= %(as_of_at)s
ORDER BY business_date DESC
LIMIT %(days)s
