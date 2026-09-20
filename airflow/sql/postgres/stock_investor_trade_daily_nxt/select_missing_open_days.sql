-- 응답이 덮은 구간 안에서 **KRX가 열었는데 NXT 일봉이 없는** 거래일.
--
-- KRX판(`../stock_investor_trade_daily/select_missing_open_days.sql`)과 같은 검사이고 표만 다르다.
-- **NXT 캘린더를 따로 수집하지 않는다.** NXT는 KRX 개장일에 열고 2025-03-24 이후 두 종목은 매 개장일에
-- 값이 있었다(2026-09-20 실측). 그 앞은 응답이 빈 행이라 수집기가 시작일 아래를 받지 않는다.
--
-- 개장 여부를 모르는 날(effective_open_day IS NULL)은 세지 않는다. DAG가 이 목록을 받아 태스크를 죽인다.
SELECT session.session_date
FROM market_session AS session
LEFT JOIN stock_investor_trade_daily_nxt AS daily
       ON daily.provider = 'kis'
      AND daily.stock_code = %s
      AND daily.business_date = session.session_date
WHERE session.market_code = 'KRX'
  AND session.session_date BETWEEN %s AND %s
  AND session.effective_open_day
  AND daily.id IS NULL
ORDER BY session.session_date
