-- 들어온 NXT 일봉과 이미 저장된 행의 종가가 어긋나는 거래일. **소급 조정 감지다.**
--
-- KRX판(`../stock_investor_trade_daily/select_close_conflicts.sql`)과 같은 규칙이고 표만 다르다. 수정주가는
-- 거래소와 무관하게 과거 전체를 새 기준으로 다시 쓰므로 NXT 표도 같은 날짜의 종가가 둘일 수 없다.
-- 종가 하나만 보고, 비교는 numeric끼리라 자릿수 표기 차이에 걸리지 않는다. 판단은 DAG가 한다.
--
-- **저장된 가장 최근 행은 비교하지 않는다.** KIS는 가장 최근 거래일 종가를 그 시점의 마지막 체결가로
-- 준다. NXT는 20:00 뒤에 받으면 마감가와 같지만 그 전에(수동 실행 등) 받은 값이 저장돼 있을 수 있어
-- KRX판과 같은 규칙을 그대로 둔다. 이유와 실측은 KRX판 주석에 있다.
SELECT stored.business_date
FROM stock_investor_trade_daily_nxt AS stored
JOIN unnest(%s::date[], %s::numeric[]) AS incoming(business_date, close_price)
  ON incoming.business_date = stored.business_date
WHERE stored.provider = 'kis'
  AND stored.stock_code = %s
  AND stored.business_date < (
      SELECT max(latest.business_date)
      FROM stock_investor_trade_daily_nxt AS latest
      WHERE latest.provider = stored.provider
        AND latest.stock_code = stored.stock_code
  )
  AND stored.close_price IS DISTINCT FROM incoming.close_price
ORDER BY stored.business_date
