-- 장중 수집기가 그날 시세를 받을지 판단할 때 쓴다.
-- 행이 없거나 값이 NULL이면 호출자는 수집을 계속한다(fail-open).
SELECT effective_open_day
FROM market_session
WHERE market_code = %s
  AND session_date = %s
