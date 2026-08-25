-- 응답이 덮은 구간 안에서 **KRX가 열었는데 일봉이 없는** 거래일.
--
-- 한 응답이 30 거래일을 담으므로 매일 도는 것만으로 실패한 날이 메워져야 한다. 메워지지
-- 않으면 그 구멍은 아무도 모르게 남는다 — 브리핑 표·차트·기술지표·thesis가 모두 이 테이블을
-- 본다. DAG가 이 목록을 받아 태스크를 죽인다.
--
-- 개장 여부를 모르는 날(effective_open_day IS NULL)은 세지 않는다. 캘린더 수집이 아직
-- 못 채운 구간 때문에 수급 수집을 붉게 만들 이유가 없다(market_session의 fail-open과 같다).
SELECT session.session_date
FROM market_session AS session
LEFT JOIN stock_investor_trade_daily AS daily
       ON daily.provider = 'kis'
      AND daily.stock_code = %s
      AND daily.business_date = session.session_date
WHERE session.market_code = 'KRX'
  AND session.session_date BETWEEN %s AND %s
  AND session.effective_open_day
  AND daily.id IS NULL
ORDER BY session.session_date
