-- 어느 거래일 직전의 종가 하나.
--
-- `quote_bar.previous_close`가 NOT NULL 이라 분봉을 저장하려면 이 값이 필요하다. 분봉 응답의
-- `output1`은 조회한 날짜와 무관하게 **지금 시세**를 담는다(실측). 과거 날짜를 백필하면서
-- 그 값을 쓰면 모든 봉에 오늘의 전일종가가 박힌다.
--
-- 그래서 우리가 이미 저장한 확정 일별값에서 읽는다. `kis_investor_trade_daily`가 먼저 돌아야
-- 한다. 없으면 그 거래일은 건너뛴다. 지어낸 분모보다 빈 구간이 낫다.
SELECT close_price
FROM stock_investor_trade_daily
WHERE provider = 'kis'
  AND stock_code = %s
  AND business_date < %s
ORDER BY business_date DESC
LIMIT 1
