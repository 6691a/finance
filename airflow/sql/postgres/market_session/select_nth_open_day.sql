-- 어느 날짜부터 세어 N번째 KRX 개장일 하나. N은 0부터다.
--
-- `%s`(기준일)을 포함해 세므로 N=0이면 기준일 자신(개장일일 때), N=1이면 그 다음 개장일,
-- N=5면 다섯 번째 개장일이다. 지평 0이 "예측일 세션 하나"인 것과 맞물린다.
--
-- T+N의 N은 달력일이 아니라 영업일이다. **우리가 날짜를 세지 않는다** — 휴장일에서 어긋난다
-- (`kis_investor_trade_daily`가 응답이 준 거래일을 그대로 쓰는 것과 같은 판단).
-- 판정의 주인은 `market_session.effective_open_day`이고 그것을 채우는 것은
-- `market_calendar_daily`다.
--
-- **달력이 그날까지 안 채워졌으면 0행이다.** 부르는 쪽은 그 (추론, 지평)을 미채점으로
-- 남기고 다음 실행이 다시 집는다. 없는 날짜를 지어내지 않는다.
--
-- `effective_open_day`가 NULL인 날(아직 판정 못 함)은 세지 않는다. 개장일로 셌다가
-- 나중에 휴장으로 판정되면 이미 매긴 점수의 기준일이 틀려 있게 된다.
SELECT session_date
FROM market_session
WHERE market_code = 'KRX'
  AND session_date >= %s
  AND effective_open_day
ORDER BY session_date
OFFSET %s
LIMIT 1
